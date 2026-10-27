"""
How long a test takes, in minutes — so lessons and olympiads get the right
time without anyone typing it.

The test's own time_limit is used where the model has one (it is what the
exam screen counts down from). Otherwise the real exam's timing is used:

  IELTS  Listening 30 · Reading 20 per passage (60 full) · Writing Task 1 20,
         Task 2 40 · Speaking 11–14 (≈5 per part)
  CEFR   per the test's own time limit; Speaking ≈3–5 per part (≈15 full)
  SAT    Reading & Writing 32 per module, Math 35 per module,
         full test 134 + a 10-minute break
"""

SPEAKING_PART = {1: 5, 2: 4, 3: 5}
CEFR_SPEAKING_PART = {'1.1': 3, '1.2': 4, '2': 4, '3': 5}


def minutes_for(task_type, ref_id, kind=''):
    try:
        return _minutes(task_type, ref_id, kind)
    except Exception:
        return None


def _minutes(task_type, ref_id, kind):
    if task_type == 'ielts_reading':
        from ielts.models import ReadingPassage
        if kind == 'mock':
            limits = list(ReadingPassage.objects.filter(test_id=ref_id).values_list('time_limit', flat=True))
            return sum(limits) or 60
        return ReadingPassage.objects.get(id=ref_id).time_limit or 20
    if task_type == 'ielts_listening':
        return 30 if kind == 'mock' else 10
    if task_type == 'ielts_writing':
        from ielts.models import WritingTask
        t = WritingTask.objects.get(id=ref_id)
        return t.time_limit or (20 if t.task_type == 1 else 40)
    if task_type in ('ielts_speaking', 'cefr_speaking') and kind == 'task':
        from ielts.models import SpeakingTask
        t = SpeakingTask.objects.get(id=ref_id)
        return 14 if t.test_type == 'MOCK' else SPEAKING_PART.get(t.part, 5)
    if task_type == 'cefr_reading':
        from cefr.models import CEFRReadingPassage, CEFRTest
        if kind == 'mock':
            return CEFRTest.objects.get(id=ref_id).time_limit or 60
        return CEFRReadingPassage.objects.get(id=ref_id).time_limit or 20
    if task_type == 'cefr_listening':
        from cefr.models import CEFRListeningSection, CEFRTest
        if kind == 'mock':
            return CEFRTest.objects.get(id=ref_id).time_limit or 35
        return CEFRListeningSection.objects.get(id=ref_id).time_limit or 10
    if task_type == 'cefr_writing':
        from cefr.models import CEFRWritingTest
        return CEFRWritingTest.objects.get(id=ref_id).time_limit or 60
    if task_type == 'cefr_speaking':
        from api.cefr_speaking import parts_of
        from cefr.models import CEFRSpeakingTest
        return sum(CEFR_SPEAKING_PART.get(p, 4) for p in parts_of(CEFRSpeakingTest.objects.get(id=ref_id))) or 15
    if task_type == 'sat_mock':
        return 144
    if task_type == 'sat_english':
        return 32
    if task_type == 'sat_math':
        return 35
    if task_type == 'center_quiz':
        from .models import Quiz
        return Quiz.objects.get(id=ref_id).time_minutes
    if task_type in ('ielts_full_mock', 'cefr_full_mock'):
        from fullmock.sections import steps_for
        return sum(s['minutes'] for s in steps_for(task_type.split('_')[0]))
    return None
