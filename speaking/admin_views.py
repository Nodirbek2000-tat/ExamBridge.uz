"""
Speaking game — staff leaderboards: who reads best and who reads most, per lesson level.

  GET admin/leaders/              every level
  GET admin/leaders/?level=B1     one level

Only READY (scored) attempts count, and an attempt counts for the level of the lesson read.
For each level:
  best        top 10 learners by their best accuracy at that level; ties: more attempts first,
              then whoever reached that score earlier
  active      top 10 learners by attempts at that level; ties: the one who read most recently
  active_30d  the same over the last 30 days only (best, attempts and last attempt all from those days)
A row: user id, name, e-mail, is_staff, best accuracy, when and with which engine ('azure' | 'whisper')
that best attempt was scored, attempts, last attempt.

Every board is one query (window functions: one row per learner = their best attempt, with their
attempt count and last attempt), so a whole page is at most 1 + 3 × levels queries.
"""
from datetime import timedelta

from django.db.models import Count, F, Max, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import LEVELS, SpeakingAttempt

TOP = 10
RECENT_DAYS = 30
USER_FIELDS = ('user__id', 'user__first_name', 'user__last_name', 'user__email', 'user__contact_email',
               'user__signup_method', 'user__is_staff')


def _has_engine():
    """'engine' comes with the Azure scoring; an attempt without it was scored by Whisper."""
    return any(f.name == 'engine' for f in SpeakingAttempt._meta.concrete_fields)


def _per_learner(level, since=None):
    """One row per learner with a READY attempt at `level` (since `since`): their best attempt
    (the earliest one with their highest accuracy), annotated with n (attempts) and last (latest)."""
    qs = SpeakingAttempt.objects.filter(status=SpeakingAttempt.READY, lesson__level=level)
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    learner = [F('user_id')]
    fields = ('id', 'accuracy', 'created_at', *USER_FIELDS) + (('engine',) if _has_engine() else ())
    return (qs.select_related('user').only(*fields)
            .annotate(pick=Window(RowNumber(), partition_by=learner,
                                  order_by=[F('accuracy').desc(), F('created_at').asc(), F('id').asc()]),
                      n=Window(Count('id'), partition_by=learner),
                      last=Window(Max('created_at'), partition_by=learner))
            .filter(pick=1))


def _row(rank, a):
    u = a.user
    return {
        'rank': rank, 'user_id': u.id, 'name': u.full_name, 'email': u.public_email, 'is_staff': u.is_staff,
        'best_accuracy': a.accuracy, 'best_at': a.created_at, 'engine': getattr(a, 'engine', '') or 'whisper',
        'attempts': a.n, 'last_attempt': a.last,
    }


def _board(qs):
    return [_row(k, a) for k, a in enumerate(qs[:TOP], 1)]


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_leaders(request):
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    totals = {r['lesson__level']: r for r in SpeakingAttempt.objects.filter(status=SpeakingAttempt.READY)
              .values('lesson__level').order_by()
              .annotate(learners=Count('user', distinct=True), attempts=Count('id'),
                        learners_30d=Count('user', distinct=True, filter=Q(created_at__gte=since)),
                        attempts_30d=Count('id', filter=Q(created_at__gte=since)))}
    levels = [{'level': lv, **{k: (totals.get(lv) or {}).get(k, 0)
                               for k in ('learners', 'attempts', 'learners_30d', 'attempts_30d')}}
              for lv in LEVELS]

    asked = (request.query_params.get('level') or '').upper()
    if asked and asked not in LEVELS:
        return Response({'error': f'"level" quyidagilardan biri bo‘lsin: {", ".join(LEVELS)}'}, status=400)
    boards = {}
    for row in levels:
        lv = row['level']
        if asked and lv != asked:
            continue
        empty = {'best': [], 'active': [], 'active_30d': []}
        if not row['attempts']:
            boards[lv] = empty
            continue
        boards[lv] = {
            'best': _board(_per_learner(lv).order_by('-accuracy', '-n', 'created_at', 'id')),
            'active': _board(_per_learner(lv).order_by('-n', '-last', 'id')),
            'active_30d': (_board(_per_learner(lv, since).order_by('-n', '-last', 'id'))
                           if row['attempts_30d'] else []),
        }
    return Response({'levels': levels, 'boards': boards, 'top': TOP, 'days': RECENT_DAYS, 'since': since})
