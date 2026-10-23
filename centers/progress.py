"""
Assignment progress: finds the result of an assigned test and marks the
student's submission complete.

Every exam stores its results differently (IELTS attempts, CEFR attempts,
CEFR writing/speaking responses, SAT attempts) and writing/speaking are
scored by AI some time after the student submits. Instead of hooking every
submit endpoint, the open submissions are checked against the exam tables
whenever a student or a teacher looks at them (`sync_submissions`). Only
results produced after the task was assigned count, so an old attempt never
completes a new task.
"""
import logging

from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

# What each task type lets a teacher pick. `kind` tells the student's Start
# button which exam runner to open.
TASK_KINDS = {
    'sat_mock': ('test',),
    'sat_english': ('test',),
    'sat_math': ('test',),
    'ielts_reading': ('mock', 'passage'),
    'ielts_listening': ('mock', 'section'),
    'ielts_writing': ('task',),
    'ielts_speaking': ('task',),
    'cefr_reading': ('mock', 'passage'),
    'cefr_listening': ('mock', 'section'),
    'cefr_writing': ('test',),
    'cefr_speaking': ('test', 'task'),
    'cefr_test': ('test',),
}


def _found(attempt_id, score=None, detail='', scored=True, at=None):
    # `at` = when the student finished, so a late finish shows as late.
    return {'attempt_id': attempt_id, 'score': score, 'detail': detail, 'scored': scored, 'at': at}


def _band(value):
    return float(value) if value is not None else None


# ── SAT ──────────────────────────────────────────────────────────────────────

def _sat_mock(user, ref, kind, since):
    from tests_app.models import TestAttempt
    a = (TestAttempt.objects
         .filter(user=user, test_id=ref, status='COMPLETED', is_individual=False, started_at__gte=since)
         .select_related('result').order_by('-finished_at').first())
    if not a:
        return None
    result = getattr(a, 'result', None)
    if result is None:
        return _found(a.id, at=a.finished_at)
    return _found(a.id, result.total_score,
                  f'{result.total_score} (R&W {result.english_score} · Math {result.math_score})',
                  at=a.finished_at)


def _sat_section(section_type):
    def resolve(user, ref, kind, since):
        from tests_app.models import TestAttempt
        a = (TestAttempt.objects
             .filter(user=user, test_id=ref, status='COMPLETED', is_individual=True,
                     current_module__section__section_type=section_type, started_at__gte=since)
             .order_by('-finished_at').first())
        if not a:
            return None
        total = a.answers.count()
        correct = a.answers.filter(is_correct=True).count()
        return _found(a.id, round(correct / total * 100) if total else None, f'{correct}/{total} correct',
                      at=a.finished_at)
    return resolve


# ── IELTS ────────────────────────────────────────────────────────────────────

def _ielts_reading(user, ref, kind, since):
    from ielts.models import IELTSAttempt, ReadingPassage
    qs = IELTSAttempt.objects.filter(user=user, status='COMPLETED', started_at__gte=since,
                                     reading_band__isnull=False)
    if kind == 'mock':
        # A mock attempt is marked complete after every passage, so it only
        # counts once every passage of the test has answers.
        need = ReadingPassage.objects.filter(test_id=ref).count()
        qs = (qs.filter(test_id=ref)
              .annotate(done=Count('reading_answers__question__passage', distinct=True))
              .filter(done__gte=need))
    else:
        qs = qs.filter(reading_passage_id=ref)
    a = qs.order_by('-started_at').first()
    if not a:
        return None
    band = _band(a.reading_band)
    return _found(a.id, band, f'Band {band}', at=a.finished_at)


def _ielts_listening(user, ref, kind, since):
    from ielts.models import IELTSAttempt, ListeningSection
    qs = IELTSAttempt.objects.filter(user=user, status='COMPLETED', started_at__gte=since,
                                     listening_band__isnull=False)
    if kind == 'mock':
        need = ListeningSection.objects.filter(test_id=ref).count()
        qs = (qs.filter(test_id=ref)
              .annotate(done=Count('listening_answers__question__section', distinct=True))
              .filter(done__gte=need))
    else:
        qs = qs.filter(listening_section_id=ref)
    a = qs.order_by('-started_at').first()
    if not a:
        return None
    band = _band(a.listening_band)
    return _found(a.id, band, f'Band {band}', at=a.finished_at)


def _ielts_writing(user, ref, kind, since):
    from ielts.models import WritingResponse
    r = (WritingResponse.objects
         .filter(attempt__user=user, task_id=ref, created_at__gte=since)
         .order_by('-created_at').first())
    if not r:
        return None
    if r.ai_band is None:
        return _found(r.attempt_id, None, 'Being scored', scored=False, at=r.created_at)
    band = _band(r.ai_band)
    return _found(r.attempt_id, band, f'Band {band}', at=r.created_at)


def _ielts_speaking(user, ref, kind, since):
    from ielts.models import SpeakingResponse
    r = (SpeakingResponse.objects
         .filter(attempt__user=user, task_id=ref, created_at__gte=since)
         .order_by('-created_at').first())
    if not r:
        return None
    if r.ai_band is None:
        return _found(r.attempt_id, None, 'Being scored', scored=False, at=r.created_at)
    band = _band(r.ai_band)
    return _found(r.attempt_id, band, f'Band {band}', at=r.created_at)


# ── CEFR ─────────────────────────────────────────────────────────────────────

def _cefr_attempt(field_for_kind):
    def resolve(user, ref, kind, since):
        from cefr.models import CEFRAttempt
        field = field_for_kind.get(kind, 'test_id')
        a = (CEFRAttempt.objects
             .filter(user=user, status='COMPLETED', started_at__gte=since, **{field: ref})
             .order_by('-finished_at').first())
        if not a:
            return None
        detail = f'{a.correct_count}/{a.total_count} correct'
        if a.level_achieved:
            detail += f' · {a.level_achieved}'
        score = round(a.score_percent) if a.score_percent is not None else None
        return _found(a.id, score, detail, at=a.finished_at)
    return resolve


def _cefr_scored_response(model_name):
    def resolve(user, ref, kind, since):
        from cefr import models as cefr_models
        model = getattr(cefr_models, model_name)
        r = (model.objects
             .filter(user=user, test_id=ref, submitted_at__gte=since)
             .exclude(status='IN_PROGRESS')
             .order_by('-submitted_at').first())
        if not r:
            return None
        if r.status != 'READY' or r.score is None:
            return _found(r.id, None, 'Being scored', scored=False, at=r.submitted_at)
        level = f' · {r.level}' if r.level and r.level != 'BELOW' else ''
        return _found(r.id, r.score, f'{r.score}/75{level}', at=r.submitted_at)
    return resolve


def _cefr_speaking(user, ref, kind, since):
    if kind == 'task':  # legacy practice task, runs on IELTS speaking
        return _ielts_speaking(user, ref, kind, since)
    return _cefr_scored_response('CEFRSpeakingResponse')(user, ref, kind, since)


RESOLVERS = {
    'sat_mock': _sat_mock,
    'sat_english': _sat_section('ENGLISH'),
    'sat_math': _sat_section('MATH'),
    'ielts_reading': _ielts_reading,
    'ielts_listening': _ielts_listening,
    'ielts_writing': _ielts_writing,
    'ielts_speaking': _ielts_speaking,
    'cefr_reading': _cefr_attempt({'mock': 'test_id', 'passage': 'reading_passage_id'}),
    'cefr_listening': _cefr_attempt({'mock': 'test_id', 'section': 'listening_section_id'}),
    'cefr_test': _cefr_attempt({}),
    'cefr_writing': _cefr_scored_response('CEFRWritingResponse'),
    'cefr_speaking': _cefr_speaking,
}


def find_result(sub):
    """The result that completes this submission, or None."""
    a = sub.assignment
    resolver = RESOLVERS.get(a.task_type)
    if not resolver or a.task_ref_id is None:
        return None
    try:
        return resolver(sub.student, a.task_ref_id, a.task_ref_kind, a.created_at)
    except Exception:
        logger.exception('assignment result lookup failed: submission %s', sub.id)
        return None


def sync_submissions(qs):
    """Complete open submissions whose test is done; fill in late AI scores.

    `qs` is any AssignmentSubmission queryset. Returns how many changed.
    """
    from .views import notify  # views imports this module

    changed = 0
    open_or_unscored = qs.filter(status__in=['pending', 'overdue']) | qs.filter(
        status='completed', score__isnull=True, score_detail='Being scored')
    for sub in open_or_unscored.select_related(
            'assignment', 'assignment__center', 'assignment__assigned_by', 'student'):
        found = find_result(sub)
        if not found:
            continue
        newly_done = sub.status != 'completed'
        if not newly_done and not found['scored']:
            continue  # still waiting for the AI score
        sub.status = 'completed'
        sub.attempt_ref_id = found['attempt_id']
        sub.score = found['score']
        sub.score_detail = found['detail']
        if newly_done:
            sub.submitted_at = found['at'] or timezone.now()
        sub.save(update_fields=['status', 'attempt_ref_id', 'score', 'score_detail', 'submitted_at'])
        changed += 1

        a = sub.assignment
        who = sub.student.get_full_name() or sub.student.email
        if newly_done:
            notify(a.assigned_by, a.center, 'submission', f'{who} completed: {a.task_title}',
                   f"Result: {found['detail'] or 'N/A'}", assignment=a, submission=sub)
        elif found['scored']:
            notify(a.assigned_by, a.center, 'submission', f'{who} — result ready: {a.task_title}',
                   f"Result: {found['detail']}", assignment=a, submission=sub)
    return changed


def overdue_cutoff(qs):
    """Mark open submissions past their deadline as overdue."""
    return qs.filter(status='pending', assignment__deadline__lt=timezone.now()).update(status='overdue')

