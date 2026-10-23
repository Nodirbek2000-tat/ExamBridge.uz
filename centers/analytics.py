"""
Numbers behind the center screens: what each student did with their tasks
and lessons, on how many days they studied, and how their scores moved.

All functions take already-loaded submissions (with `assignment` selected)
so one screen costs a handful of queries, not one per student.
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

DONE, LATE, MISSED, PENDING = 'done', 'late', 'missed', 'pending'


def task_state(sub):
    """done / late / missed / pending for one submission."""
    deadline = sub.assignment.deadline
    if sub.status == 'completed':
        if deadline and sub.submitted_at and sub.submitted_at > deadline:
            return LATE
        return DONE
    if sub.status == 'overdue' or (deadline and deadline < timezone.now()):
        return MISSED
    return PENDING


def lesson_key(sub):
    """Tasks of one lesson count as one lesson; a lone task is its own."""
    a = sub.assignment
    return ('l', a.lesson_id) if a.lesson_id else ('a', a.id)


def lesson_state(states):
    """Fold the task states of one lesson into one state."""
    if all(s in (DONE, LATE) for s in states):
        return LATE if LATE in states else DONE
    if MISSED in states:
        return MISSED
    if any(s in (DONE, LATE) for s in states):
        return 'partial'
    return PENDING


def activity_days(user_ids, since):
    """{user_id: set of local dates} on which each user worked on any exam."""
    from cefr.models import CEFRAttempt, CEFRSpeakingResponse, CEFRWritingResponse
    from ielts.models import IELTSAttempt
    from tests_app.models import TestAttempt

    days = defaultdict(set)
    if not user_ids:
        return days
    sources = (
        (IELTSAttempt, 'started_at'), (CEFRAttempt, 'started_at'), (TestAttempt, 'started_at'),
        (CEFRWritingResponse, 'started_at'), (CEFRSpeakingResponse, 'started_at'),
    )
    for model, field in sources:
        rows = model.objects.filter(user_id__in=user_ids, **{f'{field}__gte': since}).values_list('user_id', field)
        for uid, at in rows:
            days[uid].add(timezone.localtime(at).date())
    return days


def summarize(subs, study_days=0):
    """Task and lesson counts for one student's submissions."""
    states = [task_state(s) for s in subs]
    by_lesson = defaultdict(list)
    for s, st in zip(subs, states):
        by_lesson[lesson_key(s)].append(st)
    lessons = [lesson_state(v) for v in by_lesson.values()]

    finished = sum(1 for st in states if st in (DONE, LATE))
    return {
        'total': len(states),
        'done': finished,
        'on_time': states.count(DONE),
        'late': states.count(LATE),
        'missed': states.count(MISSED),
        'pending': states.count(PENDING),
        'rate': round(finished / len(states) * 100) if states else None,
        'lessons': {
            'total': len(lessons),
            'done': sum(1 for st in lessons if st in (DONE, LATE)),
            'partial': lessons.count('partial'),
            'missed': lessons.count(MISSED),
            'pending': lessons.count(PENDING),
        },
        'study_days': study_days,
    }


def growth(subs):
    """First vs latest score per task type (only types with 2+ scores)."""
    scored = defaultdict(list)
    for s in sorted(subs, key=lambda s: s.submitted_at or timezone.now()):
        if s.status == 'completed' and s.score is not None:
            scored[s.assignment.task_type].append(s.score)
    out = []
    for task_type, scores in scored.items():
        if len(scores) < 2:
            continue
        out.append({
            'type': task_type, 'first': scores[0], 'last': scores[-1],
            'delta': round(scores[-1] - scores[0], 1), 'count': len(scores),
        })
    return out


def weekly(subs, weeks=6):
    """Completion per week (by the week a task was given), oldest first."""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    buckets = {d: [0, 0] for d in starts}
    for s in subs:
        given = timezone.localtime(s.assignment.created_at).date()
        week = given - timedelta(days=given.weekday())
        if week in buckets:
            buckets[week][0] += 1
            if s.status == 'completed':
                buckets[week][1] += 1
    return [
        {'week': d.isoformat(), 'given': g, 'done': n, 'rate': round(n / g * 100) if g else None}
        for d, (g, n) in buckets.items()
    ]


def daily_active(days_by_user, days=14):
    """How many students studied on each of the last `days` days."""
    today = timezone.localdate()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out.append({'date': d.isoformat(), 'students': sum(1 for ds in days_by_user.values() if d in ds)})
    return out
