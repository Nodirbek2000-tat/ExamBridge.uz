"""
Olympiads: a test that a whole audience starts at the same moment.

Staff (admin: all students, classes or groups; teacher: own groups) pick a
day, a time, a duration and one test — any task type, a full mock included,
from the center's own tests or ExamBridge's. Every invited student is told.
Five minutes before the start students see a waiting room; at the start the
test opens for everyone, and the ranking is built from each participant's
own result (centers/progress.py resolvers): higher score first, then whoever
finished faster.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CenterMembership, Group, GroupMembership, LearningCenter, Olympiad, OlympiadParticipant
from .progress import RESOLVERS, TASK_KINDS
from .views import center_role, has_role, notify

SOON = timedelta(minutes=5)          # the waiting room opens this long before the start
LOOKBACK = timedelta(minutes=5)      # an attempt counts if started after starts_at minus this


def _name(u):
    return f'{u.first_name} {u.last_name}'.strip() or u.email


def phase(o, now=None):
    now = now or timezone.now()
    if now < o.starts_at - SOON:
        return 'upcoming'
    if now < o.starts_at:
        return 'soon'
    if now < o.ends_at:
        return 'live'
    return 'finished'


def _audience_label(o):
    a = o.audience or {}
    if a.get('all'):
        return 'All students'
    if a.get('classes'):
        return 'Classes ' + ', '.join(a['classes'])
    if a.get('groups'):
        names = Group.objects.filter(id__in=a['groups']).values_list('name', flat=True)
        return 'Groups ' + ', '.join(names)
    return ''


# ── results ───────────────────────────────────────────────────────────────────

def sync_results(o, force=False):
    """Look up the result of everyone who started (at most every 15 s per olympiad)."""
    key = f'olympiad:sync:{o.id}'
    if not force and cache.get(key):
        return
    cache.set(key, 1, 15)
    resolver = RESOLVERS.get(o.task_type)
    if not resolver:
        return
    since = o.starts_at - LOOKBACK
    for p in o.participants.filter(started_at__isnull=False, scored=False).select_related('student'):
        try:
            found = resolver(p.student, o.task_ref_id, o.task_ref_kind, since)
        except Exception:
            found = None
        if not found:
            continue
        p.finished_at = found.get('at') or p.finished_at or timezone.now()
        p.score = found['score']
        p.score_detail = found['detail']
        p.scored = bool(found['scored'] and found['score'] is not None)
        p.save(update_fields=['finished_at', 'score', 'score_detail', 'scored'])


def leaderboard(o):
    classes = dict(CenterMembership.objects.filter(center=o.center, role='student')
                   .values_list('user_id', 'grade'))
    letters = dict(CenterMembership.objects.filter(center=o.center, role='student')
                   .values_list('user_id', 'class_letter'))
    rows = []
    for p in o.participants.select_related('student'):
        elapsed = int((p.finished_at - o.starts_at).total_seconds()) if p.finished_at else None
        rows.append({
            'student_id': p.student_id, 'name': _name(p.student),
            'school_class': f"{classes.get(p.student_id)}{letters.get(p.student_id, '')}" if classes.get(p.student_id) else '',
            'state': ('scored' if p.scored else 'scoring' if p.finished_at else
                      'testing' if p.started_at else 'waiting' if p.entered_at else 'absent'),
            'score': p.score, 'detail': p.score_detail, 'elapsed': elapsed,
        })
    ranked = sorted((r for r in rows if r['state'] == 'scored'),
                    key=lambda r: (-r['score'], r['elapsed'] if r['elapsed'] is not None else 10 ** 9))
    for i, r in enumerate(ranked, 1):
        r['rank'] = i
    rest = [r for r in rows if r['state'] != 'scored']
    order = {'scoring': 0, 'testing': 1, 'waiting': 2, 'absent': 3}
    rest.sort(key=lambda r: (order[r['state']], r['name']))
    return ranked + rest


def _summary(o, now=None):
    parts = o.participants
    return {
        'id': o.id, 'title': o.title, 'description': o.description,
        'starts_at': o.starts_at.isoformat(), 'ends_at': o.ends_at.isoformat(),
        'duration_minutes': o.duration_minutes, 'phase': phase(o, now),
        'task': {'type': o.task_type, 'id': o.task_ref_id, 'kind': o.task_ref_kind, 'title': o.task_title},
        'audience': o.audience, 'audience_label': _audience_label(o),
        'created_by': {'id': o.created_by_id, 'name': _name(o.created_by)},
        'participants': parts.count(),
        'entered': parts.filter(entered_at__isnull=False).count(),
        'finished': parts.filter(finished_at__isnull=False).count(),
    }


# ── staff ─────────────────────────────────────────────────────────────────────

def _staff_center(request, center_id):
    center = LearningCenter.objects.filter(id=center_id).first()
    if not center:
        return None, None, Response({'error': 'Not found'}, status=404)
    role = center_role(request.user, center)
    if role not in ('director', 'admin', 'teacher') and not request.user.is_staff:
        return None, None, Response({'error': 'Forbidden'}, status=403)
    return center, role, None


def _teacher_q(center, teacher):
    """A teacher sees the olympiads they organised and those their own students took part in."""
    from .views import teacher_student_ids
    return Q(created_by=teacher) | Q(participants__student_id__in=teacher_student_ids(teacher, center))


def _invitees(center, role, user, audience):
    """Student ids for an audience, or an error string."""
    students = CenterMembership.objects.filter(center=center, role='student', is_active=True)
    if role == 'teacher':
        own = set(Group.objects.filter(center=center, teacher=user).values_list('id', flat=True))
        groups = {int(g) for g in audience.get('groups') or []}
        if not groups or not groups <= own:
            return None, 'Choose from your own groups'
        return set(GroupMembership.objects.filter(group_id__in=groups).values_list('student_id', flat=True)), None
    if audience.get('all'):
        return set(students.values_list('user_id', flat=True)), None
    if audience.get('classes'):
        q = Q()
        for label in audience['classes']:
            label = str(label).strip().upper()
            digits = ''.join(ch for ch in label if ch.isdigit())
            if not digits:
                return None, f'Unknown class {label}'
            q |= Q(grade=int(digits), class_letter=label[len(digits):])
        return set(students.filter(q).values_list('user_id', flat=True)), None
    if audience.get('groups'):
        groups = Group.objects.filter(center=center, id__in=audience['groups'])
        return set(GroupMembership.objects.filter(group__in=groups).values_list('student_id', flat=True)), None
    return None, 'Choose who takes part'


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def olympiads(request, center_id):
    center, role, err = _staff_center(request, center_id)
    if err:
        return err
    if request.method == 'GET':
        qs = center.olympiads.select_related('created_by')
        if role == 'teacher':
            qs = qs.filter(_teacher_q(center, request.user)).distinct()
        now = timezone.now()
        return Response([_summary(o, now) for o in qs[:100]])

    if role not in ('admin', 'teacher') and not getattr(request.user, 'can_write_admin', False):
        return Response({'error': 'Admins and teachers run olympiads'}, status=403)
    d = request.data
    title = (d.get('title') or '').strip()
    starts_at = parse_datetime(d.get('starts_at') or '')
    if starts_at and timezone.is_naive(starts_at):
        starts_at = timezone.make_aware(starts_at)
    try:
        # no time given → the test's own length (centers/durations.py)
        from .durations import minutes_for
        duration = int(d.get('duration_minutes') or minutes_for(d.get('task_type'), d.get('task_ref_id'),
                                                                 d.get('task_ref_kind') or '') or 60)
    except (TypeError, ValueError):
        duration = 0
    task_type, kind = d.get('task_type'), d.get('task_ref_kind') or ''
    if not title:
        return Response({'error': 'Give the olympiad a title'}, status=400)
    if not starts_at or starts_at < timezone.now() - timedelta(minutes=1):
        return Response({'error': 'Pick a start time in the future'}, status=400)
    if not 5 <= duration <= 300:
        return Response({'error': 'Duration must be 5–300 minutes'}, status=400)
    if task_type not in TASK_KINDS or kind not in TASK_KINDS[task_type] or not d.get('task_ref_id'):
        return Response({'error': 'Pick a test'}, status=400)
    students, problem = _invitees(center, role, request.user, d.get('audience') or {})
    if problem:
        return Response({'error': problem}, status=400 if role != 'teacher' else 403)
    if not students:
        return Response({'error': 'Nobody is in that audience'}, status=400)

    with transaction.atomic():
        o = Olympiad.objects.create(
            center=center, created_by=request.user, title=title[:200], description=(d.get('description') or '').strip(),
            starts_at=starts_at, duration_minutes=duration, task_type=task_type, task_ref_id=int(d['task_ref_id']),
            task_ref_kind=kind, task_title=(d.get('task_title') or '')[:200], audience=d.get('audience') or {},
        )
        OlympiadParticipant.objects.bulk_create([OlympiadParticipant(olympiad=o, student_id=s) for s in students])
        when = timezone.localtime(starts_at).strftime('%d %b, %H:%M')
        for student in get_user_model().objects.filter(id__in=students):
            notify(student, center, 'olympiad', f'Olympiad: {o.title}', f'{when} · {o.task_title} · {duration} min')
    return Response(_summary(o), status=201)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def olympiad_detail(request, center_id, olympiad_id):
    center, role, err = _staff_center(request, center_id)
    if err:
        return err
    qs = center.olympiads.select_related('created_by').filter(id=olympiad_id)
    if role == 'teacher':
        qs = qs.filter(_teacher_q(center, request.user)).distinct()
    o = qs.first()
    if not o:
        return Response({'error': 'Not found'}, status=404)
    if request.method == 'DELETE':
        if o.created_by_id != request.user.id and not has_role(request.user, center, 'admin'):
            return Response({'error': 'Only the organiser or the admin can cancel it'}, status=403)
        o.delete()
        return Response({'deleted': True})
    sync_results(o)
    return Response({**_summary(o), 'server_now': timezone.now().isoformat(), 'board': leaderboard(o)})


# ── students ──────────────────────────────────────────────────────────────────

def _mine(request, olympiad_id):
    p = (OlympiadParticipant.objects.select_related('olympiad', 'olympiad__created_by', 'olympiad__center')
         .filter(olympiad_id=olympiad_id, student=request.user).first())
    return p


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_olympiads(request):
    """Upcoming, live and recent olympiads of the logged-in student."""
    now = timezone.now()
    entries = (OlympiadParticipant.objects.filter(student=request.user, olympiad__starts_at__gte=now - timedelta(days=30))
               .select_related('olympiad', 'olympiad__created_by').order_by('olympiad__starts_at'))
    out = []
    for p in entries:
        o = p.olympiad
        out.append({**_summary(o, now), 'me': {'entered': bool(p.entered_at), 'started': bool(p.started_at),
                                               'finished': bool(p.finished_at), 'score': p.score,
                                               'detail': p.score_detail}})
    return Response({'server_now': now.isoformat(), 'olympiads': out})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_olympiad(request, olympiad_id):
    p = _mine(request, olympiad_id)
    if not p:
        return Response({'error': 'Not found'}, status=404)
    o = p.olympiad
    now = timezone.now()
    data = {**_summary(o, now), 'server_now': now.isoformat(),
            'me': {'entered': bool(p.entered_at), 'started': bool(p.started_at), 'finished': bool(p.finished_at)}}
    if p.started_at or phase(o, now) == 'finished':
        sync_results(o)
        board = leaderboard(o)
        mine = next((r for r in board if r['student_id'] == request.user.id), None)
        data['me'].update({'finished': bool(mine and mine['state'] in ('scored', 'scoring')),
                           'score': mine and mine['score'], 'detail': mine and mine['detail'],
                           'rank': mine and mine.get('rank'), 'state': mine and mine['state']})
        # everyone sees the full ranking once the olympiad is over; before that, only the top
        data['board'] = board if phase(o, now) == 'finished' else [r for r in board if r.get('rank')][:10]
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def enter_olympiad(request, olympiad_id):
    p = _mine(request, olympiad_id)
    if not p:
        return Response({'error': 'Not found'}, status=404)
    if not p.entered_at:
        p.entered_at = timezone.now()
        p.save(update_fields=['entered_at'])
    return Response({'entered': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def begin_olympiad(request, olympiad_id):
    """Open the test — only between the start and the end."""
    p = _mine(request, olympiad_id)
    if not p:
        return Response({'error': 'Not found'}, status=404)
    o = p.olympiad
    ph = phase(o)
    if ph in ('upcoming', 'soon'):
        return Response({'error': 'not_started', 'starts_at': o.starts_at.isoformat()}, status=400)
    if ph == 'finished':
        return Response({'error': 'finished'}, status=400)
    now = timezone.now()
    p.entered_at = p.entered_at or now
    p.started_at = p.started_at or now
    p.save(update_fields=['entered_at', 'started_at'])
    return Response({'test': {'type': o.task_type, 'id': o.task_ref_id, 'kind': o.task_ref_kind, 'title': o.task_title}})
