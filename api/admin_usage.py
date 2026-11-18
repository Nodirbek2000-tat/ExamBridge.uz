"""
How many times each mock test and each separate part was done, for the admin lists.

"times" counts finished attempts (a student who did it twice counts twice);
"students" counts the different students. Each list asks once per kind, so a
long list costs a couple of grouped queries, not one per row.
"""
from django.db.models import Count

NONE = {'times': 0, 'students': 0}


def _grouped(queryset, field, times=Count('id'), students=Count('user', distinct=True)):
    rows = (queryset.exclude(**{f'{field}__isnull': True})
            .values(field).annotate(times=times, students=students))
    return {row[field]: {'times': row['times'], 'students': row['students']} for row in rows}


def ielts(skill):
    """(by part id, by test id) for 'reading' or 'listening'."""
    from ielts.models import IELTSAttempt
    done = IELTSAttempt.objects.filter(status=IELTSAttempt.Status.COMPLETED)
    part_field = 'reading_passage' if skill == 'reading' else 'listening_section'
    # a test holds reading and listening alike: its band tells which one this attempt was
    band = 'reading_band' if skill == 'reading' else 'listening_band'
    return _grouped(done, part_field), _grouped(done.filter(**{f'{band}__isnull': False}), 'test')


def ielts_tasks(skill):
    """By task id for 'writing' or 'speaking': one attempt answering a task counts once."""
    from ielts.models import SpeakingResponse, WritingResponse
    model = WritingResponse if skill == 'writing' else SpeakingResponse
    return _grouped(model.objects.all(), 'task',
                    times=Count('attempt', distinct=True), students=Count('attempt__user', distinct=True))


def cefr(skill):
    """(by part id, by test id) for 'reading' or 'listening'."""
    from cefr.models import CEFRAttempt
    done = CEFRAttempt.objects.filter(status=CEFRAttempt.Status.COMPLETED)
    part_field = 'reading_passage' if skill == 'reading' else 'listening_section'
    kind = CEFRAttempt.AttemptType.READING if skill == 'reading' else CEFRAttempt.AttemptType.LISTENING
    return _grouped(done, part_field), _grouped(done.filter(attempt_type=kind), 'test')
