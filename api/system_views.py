"""
System health endpoints — Celery, Redis status for admin panel
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
import redis
from django.conf import settings
from django.utils import timezone


@api_view(['GET'])
@permission_classes([IsAdminUser])
def system_health(request):
    """Check Celery workers and Redis status."""
    health = {
        'timestamp': timezone.now().isoformat(),
        'redis': {'status': 'unknown'},
        'celery': {'status': 'unknown', 'workers': []},
    }

    # Redis check
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        info = r.info()
        health['redis'] = {
            'status': 'ok',
            'connected_clients': info.get('connected_clients'),
            'used_memory_human': info.get('used_memory_human'),
            'uptime_in_seconds': info.get('uptime_in_seconds'),
        }
    except Exception as e:
        health['redis'] = {'status': 'error', 'detail': str(e)}

    # Celery workers check
    try:
        from config.celery import app as celery_app
        inspect = celery_app.control.inspect(timeout=2.0)
        active = inspect.active() or {}
        stats_data = inspect.stats() or {}

        workers = []
        for worker_name, tasks in active.items():
            w_stats = stats_data.get(worker_name, {})
            workers.append({
                'name': worker_name,
                'active_tasks': len(tasks),
                'total_tasks': w_stats.get('total', {}),
            })

        health['celery'] = {
            'status': 'ok' if workers else 'no_workers',
            'worker_count': len(workers),
            'workers': workers,
        }
    except Exception as e:
        health['celery'] = {'status': 'error', 'detail': str(e)}

    return Response(health)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def celery_tasks(request):
    """List active and reserved Celery tasks."""
    try:
        from config.celery import app as celery_app
        inspect = celery_app.control.inspect(timeout=2.0)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}

        all_active = []
        for worker, tasks in active.items():
            for t in tasks:
                all_active.append({
                    'worker': worker,
                    'task_id': t.get('id'),
                    'task_name': t.get('name'),
                    'started': t.get('time_start'),
                    'state': 'ACTIVE',
                })

        all_reserved = []
        for worker, tasks in reserved.items():
            for t in tasks:
                all_reserved.append({
                    'worker': worker,
                    'task_id': t.get('id'),
                    'task_name': t.get('name'),
                    'state': 'RESERVED',
                })

        return Response({'active': all_active, 'reserved': all_reserved})
    except Exception as e:
        return Response({'error': str(e)}, status=500)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def platform_stats(request):
    """Rich platform statistics for admin dashboard."""
    from accounts.models import User
    from tests_app.models import Test, TestAttempt, TestResult
    from ielts.models import IELTSTest, IELTSAttempt
    from cefr.models import CEFRTest, CEFRAttempt
    from django.db.models import Count, Q
    from datetime import timedelta

    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # ── User stats ────────────────────────────────────────────────────────────
    total_users   = User.objects.count()
    premium_users = User.objects.filter(is_premium=True).count()
    users_today   = User.objects.filter(created_at__date=today).count()
    users_week    = User.objects.filter(created_at__gte=week_ago).count()
    users_month   = User.objects.filter(created_at__gte=month_ago).count()

    # ── SAT mock stats — unique users per test ────────────────────────────────
    sat_mocks_qs = (
        Test.objects
        .annotate(unique_users=Count('attempts__user', distinct=True))
        .filter(unique_users__gt=0)
        .order_by('-unique_users')[:15]
    )
    sat_mocks = [
        {
            'id': t.id,
            'label': f"{t.get_month_display()} {t.year} — Form {t.form}",
            'type': t.test_type,
            'unique_users': t.unique_users,
        }
        for t in sat_mocks_qs
    ]

    # ── IELTS mock stats — unique users per test ──────────────────────────────
    ielts_mocks_qs = (
        IELTSTest.objects
        .annotate(unique_users=Count('attempts__user', distinct=True))
        .filter(unique_users__gt=0)
        .order_by('-unique_users')[:15]
    )
    ielts_mocks = [
        {
            'id': t.id,
            'label': t.title,
            'type': t.test_type,
            'unique_users': t.unique_users,
        }
        for t in ielts_mocks_qs
    ]

    # ── CEFR mock stats — unique users per test ───────────────────────────────
    cefr_mocks_qs = (
        CEFRTest.objects
        .annotate(unique_users=Count('attempts__user', distinct=True))
        .filter(unique_users__gt=0)
        .order_by('-unique_users')[:15]
    )
    cefr_mocks = [
        {
            'id': t.id,
            'label': t.title,
            'type': getattr(t, 'level', '') or '',
            'unique_users': t.unique_users,
        }
        for t in cefr_mocks_qs
    ]

    # ── Section popularity (unique users per platform) ─────────────────────────
    sat_unique   = TestAttempt.objects.values('user').distinct().count()
    ielts_unique = IELTSAttempt.objects.filter(test__isnull=False).values('user').distinct().count()
    cefr_unique  = CEFRAttempt.objects.filter(test__isnull=False).values('user').distinct().count()

    # ── Recent activity — last 10 completions across all sections ─────────────
    recent_sat = list(
        TestAttempt.objects
        .filter(status='COMPLETED')
        .select_related('user', 'test', 'result')
        .order_by('-started_at')[:6]
    )
    recent_ielts = list(
        IELTSAttempt.objects
        .filter(status='COMPLETED', test__isnull=False)
        .select_related('user', 'test')
        .order_by('-started_at')[:6]
    )
    recent_cefr = list(
        CEFRAttempt.objects
        .filter(status='COMPLETED', test__isnull=False)
        .select_related('user', 'test')
        .order_by('-started_at')[:6]
    )

    activity = []
    for a in recent_sat:
        try:
            total = a.result.math_score + a.result.english_score
        except Exception:
            total = None
        activity.append({
            'user': a.user.email,
            'exam_type': 'SAT',
            'label': f"{a.test.get_month_display()} {a.test.year}" if a.test else '—',
            'score': total,
            'band': None,
            'date': a.started_at.isoformat(),
        })
    for a in recent_ielts:
        activity.append({
            'user': a.user.email,
            'exam_type': 'IELTS',
            'label': a.test.title if a.test else '—',
            'score': None,
            'band': float(a.overall_band) if a.overall_band else None,
            'date': a.started_at.isoformat(),
        })
    for a in recent_cefr:
        activity.append({
            'user': a.user.email,
            'exam_type': 'CEFR',
            'label': a.test.title if a.test else '—',
            'score': round(a.score_percent) if a.score_percent else None,
            'band': a.level_achieved or None,
            'date': a.started_at.isoformat(),
        })

    activity.sort(key=lambda x: x['date'], reverse=True)
    activity = activity[:10]

    # ── Total attempts per section ─────────────────────────────────────────────
    sat_total_attempts   = TestAttempt.objects.filter(status='COMPLETED').count()
    ielts_total_attempts = IELTSAttempt.objects.filter(status='COMPLETED').count()
    cefr_total_attempts  = CEFRAttempt.objects.filter(status='COMPLETED').count()

    return Response({
        # User overview
        'total_users':   total_users,
        'premium_users': premium_users,
        'users_today':   users_today,
        'users_week':    users_week,
        'users_month':   users_month,

        # Tests overview
        'tests_completed':  sat_total_attempts + ielts_total_attempts + cefr_total_attempts,
        'tests_in_progress': TestAttempt.objects.filter(status='IN_PROGRESS').count(),

        # Section popularity
        'section_popularity': [
            {'section': 'SAT',   'unique_users': sat_unique,   'attempts': sat_total_attempts},
            {'section': 'IELTS', 'unique_users': ielts_unique, 'attempts': ielts_total_attempts},
            {'section': 'CEFR',  'unique_users': cefr_unique,  'attempts': cefr_total_attempts},
        ],

        # Per-mock unique user counts
        'sat_mocks':   sat_mocks,
        'ielts_mocks': ielts_mocks,
        'cefr_mocks':  cefr_mocks,

        # Recent activity
        'recent_activity': activity,
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_leaderboard(request):
    """
    XP leaderboard — top users ranked by XP across all sections.
    XP formula:
      SAT full mock completed  × 100 XP
      IELTS mock completed     × 80  XP
      CEFR mock completed      × 60  XP
    Efficient: 3 aggregate queries, then Python sort.
    """
    from accounts.models import User
    from tests_app.models import TestAttempt
    from ielts.models import IELTSAttempt
    from cefr.models import CEFRAttempt
    from django.db.models import Count

    # Count completions per user (3 queries total)
    sat_map = dict(
        TestAttempt.objects
        .filter(status='COMPLETED', is_individual=False)
        .values('user_id')
        .annotate(c=Count('id'))
        .values_list('user_id', 'c')
    )
    sat_ind_map = dict(
        TestAttempt.objects
        .filter(status='COMPLETED', is_individual=True)
        .values('user_id')
        .annotate(c=Count('id'))
        .values_list('user_id', 'c')
    )
    ielts_map = dict(
        IELTSAttempt.objects
        .filter(status='COMPLETED', test__isnull=False)
        .values('user_id')
        .annotate(c=Count('id'))
        .values_list('user_id', 'c')
    )
    cefr_map = dict(
        CEFRAttempt.objects
        .filter(status='COMPLETED', test__isnull=False)
        .values('user_id')
        .annotate(c=Count('id'))
        .values_list('user_id', 'c')
    )

    # All users who have at least 1 attempt
    active_ids = set(sat_map) | set(sat_ind_map) | set(ielts_map) | set(cefr_map)
    users = User.objects.filter(id__in=active_ids).values(
        'id', 'email', 'first_name', 'last_name', 'username', 'is_premium', 'is_staff'
    )

    rows = []
    for u in users:
        uid = u['id']
        sat_full = sat_map.get(uid, 0)
        sat_ind  = sat_ind_map.get(uid, 0)
        ielts_c  = ielts_map.get(uid, 0)
        cefr_c   = cefr_map.get(uid, 0)
        xp = sat_full * 100 + sat_ind * 30 + ielts_c * 80 + cefr_c * 60
        name = f"{u['first_name']} {u['last_name']}".strip() or u['username'] or u['email'].split('@')[0]
        rows.append({
            'user_id':        uid,
            'name':           name,
            'email':          u['email'],
            'is_premium':     u['is_premium'],
            'is_staff':       u['is_staff'],
            'xp':             xp,
            'sat_full':       sat_full,
            'sat_individual': sat_ind,
            'ielts':          ielts_c,
            'cefr':           cefr_c,
        })

    rows.sort(key=lambda x: x['xp'], reverse=True)

    # Assign ranks
    for i, row in enumerate(rows):
        row['rank'] = i + 1

    return Response({'leaderboard': rows[:100]})
