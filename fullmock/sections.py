"""
The sections of a full mock, in real exam order, and everything that
depends on the exam: which tests can fill a section, whether a picked test
is ready, which section attempt belongs to a step, and how a finished step
is scored.

IELTS : Listening → Reading → Writing Task 1 → Writing Task 2 → Speaking
CEFR  : Listening → Reading → Writing → Speaking
"""
import math

from django.db.models import Count

# `type` / `kind` are the same names the student app uses to open a test
# (centers/taskTypes.js startTest), so the full mock reuses that code.
STEPS = {
    'ielts': [
        {'key': 'listening', 'label': 'Listening', 'field': 'ielts_listening', 'type': 'ielts_listening',
         'kind': 'mock', 'minutes': 30, 'skill': 'listening'},
        {'key': 'reading', 'label': 'Reading', 'field': 'ielts_reading', 'type': 'ielts_reading',
         'kind': 'mock', 'minutes': 60, 'skill': 'reading'},
        {'key': 'writing1', 'label': 'Writing Task 1', 'field': 'ielts_writing1', 'type': 'ielts_writing',
         'kind': 'task', 'minutes': 20, 'skill': 'writing'},
        {'key': 'writing2', 'label': 'Writing Task 2', 'field': 'ielts_writing2', 'type': 'ielts_writing',
         'kind': 'task', 'minutes': 40, 'skill': 'writing'},
        {'key': 'speaking', 'label': 'Speaking', 'field': 'ielts_speaking', 'type': 'ielts_speaking',
         'kind': 'task', 'minutes': 14, 'skill': 'speaking'},
    ],
    'cefr': [
        {'key': 'listening', 'label': 'Listening', 'field': 'cefr_listening', 'type': 'cefr_listening',
         'kind': 'mock', 'minutes': 35, 'skill': 'listening'},
        {'key': 'reading', 'label': 'Reading', 'field': 'cefr_reading', 'type': 'cefr_reading',
         'kind': 'mock', 'minutes': 60, 'skill': 'reading'},
        {'key': 'writing', 'label': 'Writing', 'field': 'cefr_writing', 'type': 'cefr_writing',
         'kind': 'test', 'minutes': 60, 'skill': 'writing'},
        {'key': 'speaking', 'label': 'Speaking', 'field': 'cefr_speaking', 'type': 'cefr_speaking',
         'kind': 'test', 'minutes': 15, 'skill': 'speaking'},
    ],
}


def steps_for(exam):
    return STEPS.get(exam, [])


def step_by_key(exam, key):
    return next((s for s in steps_for(exam) if s['key'] == key), None)


# ── what can fill a section (admin picker) ───────────────────────────────────

def _has_audio(obj):
    return bool(getattr(obj, 'audio_file', None) or getattr(obj, 'audio_url', ''))


def _listening_issues(test, sections):
    if _has_audio(test) or (sections and all(_has_audio(s) for s in sections)):
        return []
    missing = sum(1 for s in sections if not _has_audio(s))
    return [f'Audio missing ({missing} of {len(sections)} parts)']


def options(exam, key):
    """[{id, title, meta, issues}] — tests that can fill this section."""
    if exam == 'ielts':
        from ielts.models import IELTSTest, SpeakingTask, WritingTask
        if key == 'listening':
            out = []
            for t in IELTSTest.objects.filter(listening_sections__isnull=False).distinct().prefetch_related(
                    'listening_sections').order_by('-created_at'):
                secs = list(t.listening_sections.all())
                issues = _listening_issues(t, secs)
                if len(secs) != 4:
                    issues.append(f'{len(secs)} parts (a real test has 4)')
                out.append({'id': t.id, 'title': t.title, 'meta': f'{len(secs)} parts', 'issues': issues})
            return out
        if key == 'reading':
            rows = (IELTSTest.objects.filter(passages__isnull=False)
                    .annotate(n=Count('passages', distinct=True)).order_by('-created_at'))
            return [{'id': t.id, 'title': t.title, 'meta': f'{t.n} passages',
                     'issues': [] if t.n == 3 else [f'{t.n} passages (a real test has 3)']} for t in rows]
        if key in ('writing1', 'writing2'):
            n = 1 if key == 'writing1' else 2
            out = []
            for t in WritingTask.objects.filter(task_type=n).order_by('-created_at'):
                issues = []
                if n == 1 and t.test_type == 'ACADEMIC' and not t.image:
                    issues.append('No chart image')
                out.append({'id': t.id, 'title': t.title, 'meta': f'Task {n} · {t.get_test_type_display()}',
                            'issues': issues})
            return out
        if key == 'speaking':
            return [{'id': t.id, 'title': t.title,
                     'meta': 'Parts 1, 2 & 3' if t.test_type == 'MOCK' else f'Part {t.part} only',
                     'issues': [] if t.test_type == 'MOCK' else ['Only one part (a real test has 3)']}
                    for t in SpeakingTask.objects.filter(source='IELTS').order_by('test_type', '-created_at')]
    if exam == 'cefr':
        from cefr.models import CEFRSpeakingTest, CEFRTest, CEFRWritingTest
        if key == 'listening':
            out = []
            for t in CEFRTest.objects.filter(test_type='LISTENING', listening_sections__isnull=False).distinct() \
                    .prefetch_related('listening_sections').order_by('-created_at'):
                secs = list(t.listening_sections.all())
                out.append({'id': t.id, 'title': t.title, 'meta': f'{len(secs)} parts · {t.level}',
                            'issues': _listening_issues(t, secs)})
            return out
        if key == 'reading':
            rows = (CEFRTest.objects.filter(test_type='READING', reading_passages__isnull=False)
                    .annotate(n=Count('reading_passages', distinct=True)).order_by('-created_at'))
            return [{'id': t.id, 'title': t.title, 'meta': f'{t.n} parts · {t.level}', 'issues': []} for t in rows]
        if key == 'writing':
            return [{'id': t.id, 'title': t.title, 'meta': t.get_kind_display(),
                     'issues': [] if t.kind == 'FULL' else ['Not the full paper (1.1 + 1.2 + Part 2)']}
                    for t in CEFRWritingTest.objects.filter(is_active=True).order_by('-created_at')]
        if key == 'speaking':
            from api.cefr_speaking import is_ready, parts_of
            out = []
            for t in CEFRSpeakingTest.objects.filter(is_active=True).order_by('-created_at'):
                parts = parts_of(t)
                issues = [] if is_ready(t) else ['Part 1.2 pictures missing']
                if len(parts) < 4:
                    issues.append('Parts ' + ', '.join(parts) + ' only (a real test has 4)')
                out.append({'id': t.id, 'title': t.title, 'meta': 'Full test' if len(parts) >= 4 else 'Part test',
                            'issues': issues})
            return out
    return []


def blocking_issues(exam, key, ref_id):
    """Problems that make a picked test unusable in an exam (audio, pictures)."""
    item = next((o for o in options(exam, key) if o['id'] == ref_id), None)
    if item is None:
        return ['Test not found']
    return [i for i in item['issues'] if i.startswith(('Audio missing', 'Part 1.2 pictures'))]


# ── linking a section attempt to a rehearsal ─────────────────────────────────

def valid_link(mock, step, user, ref_id):
    """Is `ref_id` this user's attempt (or response) for this step's test?"""
    ref = getattr(mock, f"{step['field']}_id")
    if step['type'] in ('ielts_listening', 'ielts_reading'):
        from ielts.models import IELTSAttempt
        return IELTSAttempt.objects.filter(id=ref_id, user=user, test_id=ref).exists()
    if step['type'] in ('ielts_writing', 'ielts_speaking'):
        from ielts.models import IELTSAttempt
        return IELTSAttempt.objects.filter(id=ref_id, user=user, test__isnull=True).exists()
    if step['type'] in ('cefr_listening', 'cefr_reading'):
        from cefr.models import CEFRAttempt
        return CEFRAttempt.objects.filter(id=ref_id, user=user, test_id=ref).exists()
    if step['type'] == 'cefr_writing':
        from cefr.models import CEFRWritingResponse
        return CEFRWritingResponse.objects.filter(id=ref_id, user=user, test_id=ref).exists()
    if step['type'] == 'cefr_speaking':
        from cefr.models import CEFRSpeakingResponse
        return CEFRSpeakingResponse.objects.filter(id=ref_id, user=user, test_id=ref).exists()
    return False


# ── state and score of one step ──────────────────────────────────────────────

def _state(done=False, score=None, detail='', scored=True, review=None, response_id=None):
    return {'state': 'done' if done else 'in_progress', 'score': score, 'detail': detail,
            'scored': bool(done and scored), 'review_url': review, 'response_id': response_id}


def step_state(mock, step, user, ref_id, since=None):
    """Where the student is in one section. None when it was never started.

    `since` is when the full mock began: an IELTS writing / speaking start can
    hand back an older practice attempt that already has an answer, and that
    old answer must not count for this mock.
    """
    fresh = {'created_at__gte': since} if since else {}
    if not ref_id:
        return None
    ref = getattr(mock, f"{step['field']}_id")
    t = step['type']

    if t in ('ielts_listening', 'ielts_reading'):
        from ielts.models import IELTSAttempt
        a = IELTSAttempt.objects.filter(id=ref_id, user=user).first()
        if not a:
            return None
        if t == 'ielts_listening':
            from ielts.models import ListeningSection
            need = ListeningSection.objects.filter(test_id=ref).count()
            have = a.listening_answers.values('question__section').distinct().count()
            band = a.listening_band
        else:
            from ielts.models import ReadingPassage
            need = ReadingPassage.objects.filter(test_id=ref).count()
            have = a.reading_answers.values('question__passage').distinct().count()
            band = a.reading_band
        done = a.status == 'COMPLETED' and band is not None and have >= need
        return _state(done, float(band) if done else None, f'Band {float(band)}' if done else '')

    if t == 'ielts_writing':
        from ielts.models import WritingResponse
        r = (WritingResponse.objects.filter(attempt_id=ref_id, attempt__user=user, task_id=ref, **fresh)
             .order_by('-id').first())
        if not r:
            return _state(False)
        scored = r.ai_band is not None
        return _state(True, float(r.ai_band) if scored else None, f'Band {float(r.ai_band)}' if scored else '',
                      scored, f'/exam/ielts/writing/result/{r.id}', r.id)

    if t == 'ielts_speaking':
        from ielts.models import SpeakingResponse
        r = (SpeakingResponse.objects.filter(attempt_id=ref_id, attempt__user=user, task_id=ref, **fresh)
             .order_by('-id').first())
        if not r:
            return _state(False)
        scored = r.ai_band is not None
        return _state(True, float(r.ai_band) if scored else None, f'Band {float(r.ai_band)}' if scored else '',
                      scored, f'/exam/ielts/speaking/result/{r.id}', r.id)

    if t in ('cefr_listening', 'cefr_reading'):
        from api.cefr_views import LISTENING_SCORE_MAP, READING_SCORE_MAP, _calc_cefr_score
        from cefr.models import CEFRAttempt
        a = CEFRAttempt.objects.filter(id=ref_id, user=user).first()
        if not a:
            return None
        if a.status != 'COMPLETED':
            return _state(False)
        score = _calc_cefr_score(a.correct_count, LISTENING_SCORE_MAP if t == 'cefr_listening' else READING_SCORE_MAP)
        return _state(True, score, f'{score}/75 · {a.correct_count}/{a.total_count} correct')

    if t in ('cefr_writing', 'cefr_speaking'):
        from cefr.models import CEFRSpeakingResponse, CEFRWritingResponse
        model = CEFRWritingResponse if t == 'cefr_writing' else CEFRSpeakingResponse
        r = model.objects.filter(id=ref_id, user=user).first()
        if not r:
            return None
        if r.status == 'IN_PROGRESS':
            return _state(False)
        scored = r.status == 'READY' and r.score is not None
        review = (f'/exam/cefr/writing/result/{r.id}' if t == 'cefr_writing'
                  else f'/exam/cefr/speaking/test/{r.id}/result')
        return _state(True, r.score if scored else None, f'{r.score}/75' if scored else '', scored, review, r.id)
    return None


# ── the final result ─────────────────────────────────────────────────────────

def ielts_round(x):
    """IELTS overall rounding: to the nearest half band, .25 and .75 go up."""
    return math.floor(x * 2 + 0.5) / 2


def overall(exam, states):
    """Section scores and the overall result; `complete` once everything is scored.

    states: {step_key: step_state or None}
    """
    def score(k):
        st = states.get(k)
        return st['score'] if st and st['scored'] else None

    if exam == 'ielts':
        w1, w2 = score('writing1'), score('writing2')
        sections = {
            'listening': score('listening'),
            'reading': score('reading'),
            # Task 2 counts twice as much as Task 1
            'writing': ielts_round((w1 + 2 * w2) / 3) if w1 is not None and w2 is not None else None,
            'speaking': score('speaking'),
        }
        done = all(v is not None for v in sections.values())
        total = ielts_round(sum(sections.values()) / 4) if done else None
        return {'sections': sections, 'overall': total, 'label': f'Band {total}' if done else None,
                'complete': done}

    from api.cefr_writing import level_for
    sections = {k: score(k) for k in ('listening', 'reading', 'writing', 'speaking')}
    done = all(v is not None for v in sections.values())
    total = round(sum(sections.values()) / 4) if done else None
    level = level_for(total) if done else None
    return {'sections': sections, 'levels': {k: level_for(v) if v is not None else None for k, v in sections.items()},
            'overall': total, 'label': (f'{total}/75 · {level}' if done else None), 'level': level,
            'complete': done}


def missing_sections(mock):
    """Keys of sections that have no test picked yet."""
    return [s['key'] for s in steps_for(mock.exam) if not getattr(mock, f"{s['field']}_id")]

