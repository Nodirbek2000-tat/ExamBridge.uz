"""
XP from AI lessons. Nothing is stored: it is counted from the answers and the
lessons finished, so it can never drift from what the student really did.

    a right answer           +10   (+5 when the hint was opened first)
    a partly right one        +5   (written answers the AI teacher marked "partly")
    finishing a lesson       +50
"""
from collections import defaultdict

from django.db.models import Count, Q

from .models import LessonAnswer, LessonProgress

CORRECT = 10
WITH_HINT = 5
PARTLY = 5
FINISH = 50


def for_answer(answer):
    if answer.is_correct:
        return WITH_HINT if answer.hint_used else CORRECT
    if answer.verdict == 'partly':
        return PARTLY
    return 0


def by_lesson(user, lesson_ids=None):
    """{lesson_id: XP} for this student, for the given lessons or all of them."""
    answers = LessonAnswer.objects.filter(user=user)
    finished = LessonProgress.objects.filter(user=user, completed_at__isnull=False)
    if lesson_ids is not None:
        answers = answers.filter(question__lesson_id__in=lesson_ids)
        finished = finished.filter(lesson_id__in=lesson_ids)
    totals = defaultdict(int)
    for lesson_id, is_correct, hint_used, verdict in answers.values_list(
            'question__lesson_id', 'is_correct', 'hint_used', 'verdict'):
        totals[lesson_id] += for_answer(LessonAnswer(is_correct=is_correct, hint_used=hint_used, verdict=verdict))
    for lesson_id in finished.values_list('lesson_id', flat=True):
        totals[lesson_id] += FINISH
    return dict(totals)


def by_user():
    """{user_id: XP} for every student, for the leaderboard: three counting queries."""
    totals = defaultdict(int)
    rows = LessonAnswer.objects.values('user_id').annotate(
        right=Count('id', filter=Q(is_correct=True, hint_used=False)),
        hinted=Count('id', filter=Q(is_correct=True, hint_used=True)),
        partly=Count('id', filter=Q(verdict='partly')),
    )
    for row in rows:
        totals[row['user_id']] += row['right'] * CORRECT + row['hinted'] * WITH_HINT + row['partly'] * PARTLY
    finished = LessonProgress.objects.filter(completed_at__isnull=False).values('user_id').annotate(n=Count('id'))
    for row in finished:
        totals[row['user_id']] += row['n'] * FINISH
    return dict(totals)
