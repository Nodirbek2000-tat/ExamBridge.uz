"""
Admin dashboard statistics — who is online, who came, what they took.

A "mock" is a whole test (SAT full test, IELTS/CEFR test, full mock exam).
"Practice" is everything smaller: one SAT module, one IELTS/CEFR passage or
listening section. The two are always counted apart.

Lives under /api/system/, so accounts/admin_access.py gives it the SYSTEM area.
"""
from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Max, Q
from django.db.models.functions import Coalesce, ExtractHour, Lower, TruncDate
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import User, UserDailyVisit
from accounts.presence import ONLINE_WINDOW, online_since
from cefr.models import CEFRAttempt, CEFRTest
from fullmock.models import FullMock, FullMockAttempt
from ielts.models import IELTSAttempt, IELTSTest
from tests_app.models import Test, TestAttempt

COMPLETED = 'COMPLETED'
IN_PROGRESS = 'IN_PROGRESS'
ALLOWED_DAYS = (7, 30, 90)
ANALYTICS_CACHE_SECONDS = 60
ONLINE_LIST_LIMIT = 100
MOCK_ATTEMPTS_LIMIT = 200
VISITORS_PAGE_SIZE = 20
# An attempt nobody finished stays IN_PROGRESS for ever, so "taking a test now"
# only trusts ones started this recently. The longest exam is about 3 hours.
RUNNING_ATTEMPT_WINDOW = timedelta(hours=4)

# Some older code paths complete an attempt without setting finished_at.
DONE_AT = Coalesce('finished_at', 'started_at')

# mock / practice: which attempts of the model are which (practice None = no such thing)
SOURCES = (
    {'section': 'SAT', 'model': TestAttempt,
     'mock': Q(is_individual=False), 'practice': Q(is_individual=True)},
    {'section': 'IELTS', 'model': IELTSAttempt,
     'mock': Q(test__isnull=False), 'practice': Q(test__isnull=True)},
    {'section': 'CEFR', 'model': CEFRAttempt,
     'mock': Q(test__isnull=False), 'practice': Q(test__isnull=True)},
    {'section': 'FULL', 'model': FullMockAttempt, 'mock': Q(), 'practice': None},
)

# related: extra joins for the label. attempts_filter: applied to attempts of one mock.
MOCK_SECTIONS = {
    'SAT': {'mock': Test, 'attempt': TestAttempt, 'fk': 'test',
            'only': Q(attempts__is_individual=False), 'attempts_filter': Q(is_individual=False),
            'avg': 'attempts__result__total_score', 'related': ['user', 'result']},
    'IELTS': {'mock': IELTSTest, 'attempt': IELTSAttempt, 'fk': 'test',
              'only': Q(), 'attempts_filter': Q(),
              'avg': 'attempts__overall_band', 'related': ['user']},
    'CEFR': {'mock': CEFRTest, 'attempt': CEFRAttempt, 'fk': 'test',
             'only': Q(), 'attempts_filter': Q(),
             'avg': 'attempts__score_percent', 'related': ['user']},
    'FULL': {'mock': FullMock, 'attempt': FullMockAttempt, 'fk': 'mock',
             'only': Q(), 'attempts_filter': Q(),
             'avg': None, 'related': ['user']},
}


def _user_row(user):
    return {
        'id': user.id,
        'name': user.get_full_name() or user.username,
        'email': user.email,
        'is_premium': user.is_premium,
    }


def _test_label(test):
    return f"{test.get_month_display()} {test.year} — Form {test.form}"


def _mock_label(section, mock):
    return _test_label(mock) if section == 'SAT' else mock.title


def _attempt_label(section, attempt):
    """What this attempt was, in a few words."""
    if section == 'SAT':
        label = _test_label(attempt.test)
        return f'{label} (module)' if attempt.is_individual else label
    if section == 'FULL':
        return attempt.mock.title
    if attempt.test_id:
        return attempt.test.title
    part = attempt.reading_passage or attempt.listening_section
    return getattr(part, 'title', '') or 'Practice'


def _attempt_related(section):
    if section == 'SAT':
        return ['test']
    if section == 'FULL':
        return ['mock']
    return ['test', 'reading_passage', 'listening_section']


def _attempt_score(section, attempt):
    """Display value of a finished attempt, or None."""
    if section == 'SAT':
        result = getattr(attempt, 'result', None)
        return result.total_score if result else None
    if section == 'IELTS':
        return float(attempt.overall_band) if attempt.overall_band is not None else None
    if section == 'CEFR':
        if attempt.level_achieved:
            return attempt.level_achieved
        return f'{round(attempt.score_percent)}%' if attempt.score_percent is not None else None
    return None


def _latest_attempts(user_ids, *filters):
    """{user id: newest matching attempt across all sections}."""
    latest = {}
    for src in SOURCES:
        section = src['section']
        rows = (
            src['model'].objects
            .filter(*filters, user_id__in=user_ids)
            .select_related(*_attempt_related(section))
            .order_by('-started_at')
        )
        seen = set()
        for a in rows:
            if a.user_id in seen:
                continue
            seen.add(a.user_id)
            known = latest.get(a.user_id)
            if known is None or a.started_at > known['started_at']:
                latest[a.user_id] = {
                    'section': section,
                    'label': _attempt_label(section, a),
                    'status': a.status,
                    'started_at': a.started_at,
                }
    return latest


# ── Live ──────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def live_stats(request):
    """Who is online right now and which of them are inside a test."""
    online = User.objects.filter(last_seen_at__gte=online_since())
    users = list(online.order_by('-last_seen_at')[:ONLINE_LIST_LIMIT])
    running = _latest_attempts(
        [u.id for u in users],
        Q(status=IN_PROGRESS),
        Q(started_at__gte=timezone.now() - RUNNING_ATTEMPT_WINDOW),
    )
    return Response({
        'online_now': online.count(),
        'in_test_now': len(running),
        'visitors_today': UserDailyVisit.objects.filter(date=timezone.localdate()).count(),
        'window_minutes': int(ONLINE_WINDOW.total_seconds() // 60),
        'users': [
            {**_user_row(u), 'last_seen': u.last_seen_at, 'activity': running.get(u.id)}
            for u in users
        ],
    })


# ── Analytics ─────────────────────────────────────────────────────────────────

def _section_row(src, today_start, week_start, month_start):
    qs = src['model'].objects.all()
    done = Q(status=COMPLETED)
    mocks = qs.filter(src['mock']).annotate(done_at=DONE_AT).aggregate(
        started=Count('id'),
        completed=Count('id', filter=done),
        users=Count('user', distinct=True),
        completed_today=Count('id', filter=done & Q(done_at__gte=today_start)),
        completed_week=Count('id', filter=done & Q(done_at__gte=week_start)),
        completed_month=Count('id', filter=done & Q(done_at__gte=month_start)),
        started_today=Count('id', filter=Q(started_at__gte=today_start)),
        last_completed=Max('done_at', filter=done),
    )
    practice = 0
    if src['practice'] is not None:
        practice = qs.filter(src['practice'], status=COMPLETED).count()
    return {'section': src['section'], **mocks, 'practice_completed': practice}


def _daily_counts(queryset, date_expr):
    """{date: row count} for a queryset, grouped by one date expression."""
    rows = queryset.annotate(day=date_expr).values('day').annotate(n=Count('id'))
    return {r['day']: r['n'] for r in rows}


def _funnel():
    """How far registered users got: started a test → finished one → came back."""
    started, finished = set(), Counter()
    for src in SOURCES:
        started.update(src['model'].objects.values_list('user_id', flat=True).distinct())
        rows = src['model'].objects.filter(status=COMPLETED).values('user_id').annotate(n=Count('id'))
        for r in rows:
            finished[r['user_id']] += r['n']
    return {
        'registered': User.objects.count(),
        'started_test': len(started),
        'completed_test': len(finished),
        'completed_two_plus': sum(1 for n in finished.values() if n >= 2),
    }


def _build_analytics(days):
    today = timezone.localdate()
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    month_start = today_start - timedelta(days=29)
    range_start = today_start - timedelta(days=days - 1)

    sections = [_section_row(s, today_start, week_start, month_start) for s in SOURCES]

    # ── Daily trend — every completion and start, mocks and practice alike ────
    new_users = _daily_counts(User.objects.filter(created_at__gte=range_start), TruncDate('created_at'))
    active = {
        r['date']: r['n']
        for r in UserDailyVisit.objects.filter(date__gte=range_start.date())
        .values('date').annotate(n=Count('id'))
    }
    started, completed, hours = Counter(), Counter(), Counter()
    for src in SOURCES:
        qs = src['model'].objects
        started.update(_daily_counts(qs.filter(started_at__gte=range_start), TruncDate('started_at')))
        completed.update(_daily_counts(
            qs.filter(status=COMPLETED).annotate(done_at=DONE_AT).filter(done_at__gte=range_start),
            TruncDate('done_at'),
        ))
        by_hour = (
            qs.filter(started_at__gte=range_start)
            .annotate(hour=ExtractHour('started_at')).values('hour').annotate(n=Count('id'))
        )
        for r in by_hour:
            hours[r['hour']] += r['n']

    trend = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        trend.append({
            'date': day.isoformat(),
            'new_users': new_users.get(day, 0),
            'active_users': active.get(day, 0),
            'started': started[day],
            'completed': completed[day],
        })

    # ── Visitors ──────────────────────────────────────────────────────────────
    def visitors(since):
        return (
            UserDailyVisit.objects.filter(date__gte=since)
            .values('user_id').distinct().count()
        )

    first_visit = UserDailyVisit.objects.order_by('date').values_list('date', flat=True).first()

    return {
        'days': days,
        'visitors': {
            'today': visitors(today),
            'yesterday': UserDailyVisit.objects.filter(date=today - timedelta(days=1)).count(),
            'week': visitors(today - timedelta(days=6)),
            'month': visitors(today - timedelta(days=29)),
            'tracking_since': first_visit.isoformat() if first_visit else None,
        },
        'mocks': {
            'completed_today': sum(s['completed_today'] for s in sections),
            'completed_week': sum(s['completed_week'] for s in sections),
            'completed_month': sum(s['completed_month'] for s in sections),
            'completed': sum(s['completed'] for s in sections),
            'started': sum(s['started'] for s in sections),
            'started_today': sum(s['started_today'] for s in sections),
            'practice_completed': sum(s['practice_completed'] for s in sections),
            'last_completed': max(
                (s['last_completed'] for s in sections if s['last_completed']), default=None,
            ),
        },
        'sections': sections,
        'trend': trend,
        'hours': [hours[h] for h in range(24)],
        'funnel': _funnel(),
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def analytics_stats(request):
    """Totals, per-section numbers and the daily trend for the last ?days=7|30|90."""
    try:
        days = int(request.query_params.get('days', 30))
    except ValueError:
        days = 30
    if days not in ALLOWED_DAYS:
        days = 30
    return Response(cache.get_or_set(
        f'admin:analytics:{days}', lambda: _build_analytics(days), ANALYTICS_CACHE_SECONDS,
    ))


# ── Mocks ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def mock_stats(request):
    """Every mock of ?section=SAT|IELTS|CEFR|FULL: how many took it, how it went."""
    section = request.query_params.get('section', 'SAT').upper()
    conf = MOCK_SECTIONS.get(section)
    if conf is None:
        return Response({'detail': 'Unknown section.'}, status=400)

    only = conf['only']
    done = only & Q(attempts__status=COMPLETED)
    annotations = {
        'started_count': Count('attempts', filter=only),
        'completed_count': Count('attempts', filter=done),
        'unique_users': Count('attempts__user', filter=only, distinct=True),
        'completed_users': Count('attempts__user', filter=done, distinct=True),
        'last_taken': Max('attempts__started_at', filter=only),
    }
    if conf['avg']:
        annotations['avg_score'] = Avg(conf['avg'], filter=done)

    mocks = conf['mock'].objects.annotate(**annotations).order_by('-unique_users', '-started_count', '-id')
    results = []
    for m in mocks:
        avg = getattr(m, 'avg_score', None)
        results.append({
            'id': m.id,
            'label': _mock_label(section, m),
            'type': getattr(m, 'test_type', '') or getattr(m, 'level', '') or '',
            'unique_users': m.unique_users,
            'completed_users': m.completed_users,
            'started': m.started_count,
            'completed': m.completed_count,
            'last_taken': m.last_taken,
            'avg_score': round(float(avg), 1) if avg is not None else None,
        })
    return Response({'section': section, 'results': results})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def mock_attempts(request, section, mock_id):
    """Who took one mock, newest first."""
    section = section.upper()
    conf = MOCK_SECTIONS.get(section)
    if conf is None:
        return Response({'detail': 'Unknown section.'}, status=400)

    attempts = (
        conf['attempt'].objects
        .filter(conf['attempts_filter'], **{f"{conf['fk']}_id": mock_id})
        .select_related(*conf['related'])
        .order_by('-started_at')
    )
    return Response({
        'count': attempts.count(),
        'results': [
            {
                'id': a.id,
                'user': _user_row(a.user),
                'status': a.status,
                'score': _attempt_score(section, a) if a.status == COMPLETED else None,
                'started_at': a.started_at,
                'finished_at': a.finished_at,
            }
            for a in attempts[:MOCK_ATTEMPTS_LIMIT]
        ],
    })


# ── Visitors ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def visitor_stats(request):
    """Users by how recently they were here. ?q= searches, ?page= pages."""
    # last_login fills in the months before last_seen_at existed.
    users = (
        User.objects
        .annotate(seen=Coalesce('last_seen_at', 'last_login'), visit_days=Count('daily_visits'))
        .filter(seen__isnull=False)
        .order_by(F('seen').desc(), '-id')
    )
    q = request.query_params.get('q', '').strip()
    if q:
        users = users.annotate(email_lower=Lower('email')).filter(
            Q(email_lower__contains=q.lower())
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )

    page = Paginator(users, VISITORS_PAGE_SIZE).get_page(request.query_params.get('page'))
    rows = list(page.object_list)
    ids = [u.id for u in rows]

    finished = Counter()
    for src in SOURCES:
        counts = (
            src['model'].objects
            .filter(user_id__in=ids, status=COMPLETED)
            .values('user_id').annotate(n=Count('id'))
        )
        for r in counts:
            finished[r['user_id']] += r['n']
    latest = _latest_attempts(ids)
    since = online_since()

    return Response({
        'count': page.paginator.count,
        'page': page.number,
        'pages': page.paginator.num_pages,
        'results': [
            {
                **_user_row(u),
                'last_seen': u.seen,
                'online': bool(u.last_seen_at and u.last_seen_at >= since),
                'joined': u.created_at,
                'visit_days': u.visit_days,
                'tests_completed': finished[u.id],
                'last_test': latest.get(u.id),
            }
            for u in rows
        ],
    })
