"""
CEFR multilevel Writing — tests, attempts, AI scoring and admin import.

The paper: Part 1 = Task 1.1 (informal letter, about 50 words) and Task 1.2
(formal letter, 120–150 words) on one situation; Part 2 = essay (180–200 words).

Scoring (0–75, the multilevel scale):
  Task 1.1 → 15 points, Task 1.2 → 25, Part 2 → 35.
  Each task is marked on four criteria, 0–5 each; task points =
  criteria total / 20 × task points. A part test is scaled to 75 so the
  level bands are the same everywhere:
  65–75 C1 · 51–64 B2 · 38–50 B1 · below 38 = below B1.
The examiner prompt is deliberately strict, and the caps below are applied
here as well, so a lenient model reply cannot inflate the score:
  - a criterion with quoted errors cannot get 5 (1 error <= 4, 2 <= 3.5, 3 <= 3);
  - one mistake is counted under one criterion only (duplicates are dropped);
  - too-short answers cap "task";
  - the total never goes above the level the examiner judged the paper to be.

Student API
  GET  /api/cefr/writing/                          tests + the user's latest result for each
  POST /api/cefr/writing/<test_id>/start/          resume or create a response → {response_id}
  GET  /api/cefr/writing/responses/<id>/           task texts, answers, status, score, result
  POST /api/cefr/writing/responses/<id>/submit/    {answers} → scoring in Celery
Admin API
  POST   /api/import/cefr/writing/                 JSON (one test, a list, or {"tests": [...]})
  GET    /api/admin/cefr/writing/
  DELETE /api/admin/cefr/writing/<id>/
"""
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from cefr.models import CEFRWritingResponse, CEFRWritingTest

log = logging.getLogger(__name__)

MODEL = 'gpt-4.1'
MAX_ANSWER_CHARS = 6000
EVAL_LOCK_TTL = 240          # longer than the OpenAI timeout below
FALLBACK_AFTER = 90          # seconds before the result view scores it itself

TASKS = {
    '1.1': {'field': 'task11', 'label': 'Task 1.1', 'genre': 'Informal letter', 'target': 'about 50 words',
            'min': 50, 'points': 15, 'register': 'informal, friendly'},
    '1.2': {'field': 'task12', 'label': 'Task 1.2', 'genre': 'Formal letter', 'target': '120–150 words',
            'min': 120, 'points': 25, 'register': 'formal or neutral'},
    '2':   {'field': 'task2', 'label': 'Part 2', 'genre': 'Essay', 'target': '180–200 words',
            'min': 180, 'points': 35, 'register': 'formal, academic'},
}
TASK_ORDER = ('1.1', '1.2', '2')

# kind decides how a mark is coloured on the result page (error = red, weak = amber)
CRITERIA = (
    ('task', 'Task fulfilment'),
    ('organisation', 'Organisation & cohesion'),
    ('vocabulary', 'Vocabulary'),
    ('grammar', 'Grammar'),
)
DEFAULT_TIME = {'FULL': 60, 'PART1': 25, 'PART2': 35}


def level_for(score):
    if score is None:
        return ''
    if score >= 65:
        return 'C1'
    if score >= 51:
        return 'B2'
    if score >= 38:
        return 'B1'
    return 'BELOW'


def task_keys(test):
    return [k for k in TASK_ORDER if (getattr(test, TASKS[k]['field']) or '').strip()]


def words_in(text):
    return len(str(text or '').split())


def _task_meta(test, key):
    t = TASKS[key]
    return {'key': key, 'label': t['label'], 'genre': t['genre'], 'target': t['target'],
            'min_words': t['min'], 'points': t['points'], 'prompt': getattr(test, t['field'])}


def _test_summary(test):
    keys = task_keys(test)
    return {
        'id': test.id, 'title': test.title, 'kind': test.kind, 'time_limit': test.time_limit,
        'is_premium': test.is_premium, 'tasks': keys,
        'max_points': sum(TASKS[k]['points'] for k in keys),
    }


def _response_brief(r):
    if r is None:
        return None
    return {'response_id': r.id, 'status': r.status, 'score': r.score, 'level': r.level,
            'date': (r.submitted_at or r.started_at).isoformat()}


# ── scoring ─────────────────────────────────────────────────────────────────

def _clamp(v, lo=0.0, hi=5.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, round(v * 2) / 2))


def _clean_list(items, limit, size=300):
    out = []
    for x in items or []:
        if isinstance(x, str) and x.strip():
            out.append(x.strip()[:size])
        if len(out) >= limit:
            break
    return out


def _clean_errors(items):
    out = []
    for e in items or []:
        if not isinstance(e, dict):
            continue
        quote = str(e.get('quote') or '').strip()[:200]
        issue = str(e.get('issue') or '').strip()[:300]
        if not (quote or issue):
            continue
        out.append({'quote': quote, 'issue': issue, 'suggestion': str(e.get('suggestion') or '').strip()[:300]})
        if len(out) >= 3:
            break
    return out


# a quoted error caps its criterion — nobody gets 5 for a text with mistakes in it
ERROR_CAPS = {
    'grammar': (4.0, 3.5, 3.0),
    'vocabulary': (4.0, 3.5, 3.0),
    'task': (4.5, 4.0, 3.5),
    'organisation': (4.5, 4.0, 3.5),
}
# the most specific criterion keeps a mistake the model listed twice
DEDUP_ORDER = ('grammar', 'vocabulary', 'task', 'organisation')
LEVEL_MAX = {'C1': 75, 'B2': 64, 'B1': 50, 'BELOW': 37}


def _norm_quote(q):
    return ' '.join(str(q).lower().split()).strip(' .,!?;:"\'')


def _empty_task(key, note='No answer was written for this task.'):
    return {
        **{c: {'score': 0, 'label': label, 'feedback': note, 'strengths': [], 'errors': []} for c, label in CRITERIA},
        'good_phrases': [], 'points': 0, 'max_points': TASKS[key]['points'], 'words': 0,
    }


def _shape_task(key, raw, words):
    """Clean one task from the AI reply, apply the length caps and compute points."""
    t = TASKS[key]
    raw = raw if isinstance(raw, dict) else {}
    out = {'good_phrases': _clean_list(raw.get('good_phrases'), 4, 140), 'words': words, 'max_points': t['points']}
    crit = {c: raw.get(c) if isinstance(raw.get(c), dict) else {} for c, _ in CRITERIA}
    errors, seen = {}, set()
    for c in DEDUP_ORDER:
        kept = []
        for e in _clean_errors(crit[c].get('errors')):
            q = _norm_quote(e['quote'])
            if q and q in seen:
                continue
            seen.add(q)
            kept.append(e)
        errors[c] = kept
    total = 0.0
    for c, label in CRITERIA:
        score = _clamp(crit[c].get('score'))
        if errors[c]:
            score = min(score, ERROR_CAPS[c][min(len(errors[c]), 3) - 1])
        if c == 'task':
            # strict length rule, independent of what the model decided
            if words < t['min'] * 0.5:
                score = min(score, 1.0)
            elif words < t['min'] * 0.8:
                score = min(score, 2.5)
        total += score
        out[c] = {'score': score, 'label': label, 'feedback': str(crit[c].get('feedback') or '').strip()[:500],
                  'strengths': _clean_list(crit[c].get('strengths'), 2), 'errors': errors[c]}
    out['points'] = round(total / 20 * t['points'], 1)
    return out


def _finish(keys, tasks, summary='', judged=''):
    raw = round(sum(tasks[k]['points'] for k in keys), 1)
    max_raw = sum(TASKS[k]['points'] for k in keys)
    score = round(raw / max_raw * 75) if max_raw else 0
    judged = str(judged or '').strip().upper().replace(' ', '_')
    judged = {'BELOW_B1': 'BELOW', 'A2': 'BELOW', 'A1': 'BELOW'}.get(judged, judged)
    capped = judged in LEVEL_MAX and score > LEVEL_MAX[judged]
    if capped:
        score = LEVEL_MAX[judged]          # the examiner's overall judgement is the ceiling
    return score, {'tasks': tasks, 'order': keys, 'raw': raw, 'max_raw': max_raw,
                   'summary': str(summary or '').strip()[:600],
                   'judged_level': judged if judged in LEVEL_MAX else '', 'capped': capped}


SYSTEM = """You are a strict, experienced examiner for the Uzbekistan national CEFR multilevel Writing exam.
You mark exactly what is on the page and never give the benefit of the doubt: when you hesitate between two scores, give the lower one.
Return only valid JSON."""

INSTRUCTIONS = """Mark every task below on four criteria. Each criterion is scored 0–5 (0.5 steps allowed):
5 = strong C1: would impress a C1 examiner — sophisticated, precise vocabulary (less common words, idiomatic collocations), a wide range of complex structures, no errors at all. This is rare.
4 = B2: clear, appropriate and mostly accurate; good but ordinary range. Error-free yet ordinary writing is 4, not 5.
3 = B1: adequate but simple, noticeable errors, limited range.
2 = A2: task only partly done, frequent errors, very simple language.
1 = barely attempts the task or is mostly hard to understand.
0 = no answer, off-topic, or the task text copied.
Most learner texts score 2.5–4; a typical intermediate text is 3. When you hesitate, choose the lower score.

Criteria:
- task: covers every point the task asks for, reaches the word target, uses the right register ({registers}).
- organisation: paragraphing, logical order, linking words, letter conventions (greeting and closing) where a letter is asked for.
- vocabulary: range, precision, collocations, spelling.
- grammar: range of structures and accuracy.

Rules:
- Errors: copy each "quote" WORD FOR WORD from the student's answer (2–10 words, no "..."), it is highlighted in their text. At most 3 errors per criterion, most important first. Read every sentence and list every real error you can quote (agreement, verb patterns, articles, prepositions, two sentences joined with only a comma, wrong word, spelling).
- Something MISSING (a content point, too few words) is not a quote: leave "quote" empty and explain it in "issue".
- Each mistake goes under ONE criterion only: grammar (verb forms, agreement, articles, word order, sentence boundaries), vocabulary (word choice, collocation, spelling), task (missing content, wrong register, length), organisation (paragraphing, linking, letter conventions). Never repeat a quote under a second criterion.
- strengths: at most 2 short sentences (4–12 words) per criterion, only if real.
- feedback: 1–2 sentences per criterion, in English.
- good_phrases: up to 4 short phrases (2–8 words, copied word for word) the student used well in that task; [] if none.
- A word count below the target must lower "task"; the wrong register must lower "task" too.
- overall_level: your holistic judgement of the whole paper — exactly one of "C1", "B2", "B1", "BELOW". Be strict: C1 only if every task shows C1 control.
- summary: 2 sentences — the overall level and the single most useful next step."""


def _build_prompt(test, keys, answers):
    parts = []
    if test.situation.strip() and any(k in keys for k in ('1.1', '1.2')):
        parts.append(f'SITUATION (shared by Task 1.1 and 1.2):\n{test.situation.strip()}')
    for k in keys:
        t = TASKS[k]
        text = answers.get(k, '')
        parts.append(
            f'=== {k}: {t["label"]} — {t["genre"]}, {t["target"]}, register: {t["register"]} ===\n'
            f'TASK:\n{getattr(test, t["field"]).strip()}\n'
            f'STUDENT ANSWER ({words_in(text)} words):\n{text}'
        )
    shape = ', '.join(f'"{k}": {{"task": {{...}}, "organisation": {{...}}, "vocabulary": {{...}}, "grammar": {{...}}, '
                      f'"good_phrases": [...]}}' for k in keys)
    criterion = '{"score": <0-5>, "feedback": "...", "strengths": ["..."], "errors": [{"quote": "...", "issue": "...", "suggestion": "..."}]}'
    registers = '; '.join(f'{TASKS[k]["label"]} {TASKS[k]["register"]}' for k in keys)
    return (INSTRUCTIONS.replace('{registers}', registers) + '\n\n' + '\n\n'.join(parts) +
            f'\n\nReturn JSON exactly like this (only these task keys):\n'
            f'{{"tasks": {{{shape}}}, "overall_level": "C1|B2|B1|BELOW", "summary": "..."}}\nwhere every criterion is {criterion}')


def _call_ai(test, keys, answers):
    from openai import OpenAI
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise RuntimeError('OpenAI API key is not configured')
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=120)
    reply = client.chat.completions.create(
        model=MODEL, temperature=0.1, max_tokens=4000, response_format={'type': 'json_object'},
        messages=[{'role': 'system', 'content': SYSTEM},
                  {'role': 'user', 'content': _build_prompt(test, keys, answers)}],
    )
    return json.loads(reply.choices[0].message.content or '{}')


def score_answers(test, answers, ai=None):
    """Pure scoring step (the AI call is injectable for tests). Returns (score, result)."""
    keys = task_keys(test)
    tasks, written = {}, []
    for k in keys:
        if words_in(answers.get(k)) == 0:
            tasks[k] = _empty_task(k)
        else:
            written.append(k)
    summary = 'Nothing was written, so the test could not be assessed.' if not written else ''
    if written:
        reply = (ai or _call_ai)(test, written, answers)
        raw_tasks = reply.get('tasks') if isinstance(reply.get('tasks'), dict) else {}
        for k in written:
            tasks[k] = _shape_task(k, raw_tasks.get(k), words_in(answers.get(k)))
        summary = reply.get('summary', '')
        return _finish(keys, tasks, summary, reply.get('overall_level'))
    return _finish(keys, tasks, summary)


def evaluate_response(response):
    """Score a submitted response once (Celery and the fallback share the lock)."""
    if response.status != CEFRWritingResponse.Status.SCORING:
        return False
    lock = f'cefr_writing_eval_lock:{response.id}'
    if not cache.add(lock, '1', timeout=EVAL_LOCK_TTL):
        return False
    try:
        score, result = score_answers(response.test, response.answers or {})
        response.score, response.level, response.result = score, level_for(score), result
        response.status = CEFRWritingResponse.Status.READY
        response.scored_at = timezone.now()
        response.save(update_fields=['score', 'level', 'result', 'status', 'scored_at'])
        return True
    except Exception:
        cache.delete(lock)
        raise


# ── student views ───────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_tests(request):
    tests = list(CEFRWritingTest.objects.filter(is_active=True))
    latest = {}
    for r in (CEFRWritingResponse.objects.filter(user=request.user, test__in=tests)
              .only('id', 'test_id', 'status', 'score', 'level', 'started_at', 'submitted_at')):
        latest.setdefault(r.test_id, r)          # ordered newest first
    return Response([{**_test_summary(t), 'last': _response_brief(latest.get(t.id))} for t in tests])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_start(request, test_id):
    test = get_object_or_404(CEFRWritingTest, id=test_id, is_active=True)
    if test.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'detail': 'Premium required.'}, status=403)
    existing = CEFRWritingResponse.objects.filter(
        user=request.user, test=test, status=CEFRWritingResponse.Status.IN_PROGRESS).first()
    if existing:
        return Response({'response_id': existing.id, 'resumed': True})
    r = CEFRWritingResponse.objects.create(user=request.user, test=test)
    return Response({'response_id': r.id, 'resumed': False}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_response(request, response_id):
    r = get_object_or_404(CEFRWritingResponse.objects.select_related('test'), id=response_id, user=request.user)
    if r.status == CEFRWritingResponse.Status.SCORING and r.submitted_at:
        wait_key = f'cefr_writing_fallback_wait:{r.id}'
        if (timezone.now() - r.submitted_at).total_seconds() >= FALLBACK_AFTER and not cache.get(wait_key):
            try:
                if evaluate_response(r):
                    log.warning('Fallback scored CEFR writing %s (Celery did not)', r.id)
            except Exception:
                # back off, so a failing AI is not called again on every poll
                cache.set(wait_key, 1, 120)
                log.exception('Fallback CEFR writing scoring failed for %s', r.id)
    test = r.test
    keys = task_keys(test)
    return Response({
        'id': r.id, 'status': r.status, 'score': r.score, 'level': r.level, 'result': r.result,
        'answers': r.answers or {}, 'started_at': r.started_at, 'submitted_at': r.submitted_at,
        'test': {**_test_summary(test), 'situation': test.situation},
        'tasks': [_task_meta(test, k) for k in keys],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_submit(request, response_id):
    with transaction.atomic():
        r = get_object_or_404(CEFRWritingResponse.objects.select_for_update().select_related('test'),
                              id=response_id, user=request.user)
        if r.status != CEFRWritingResponse.Status.IN_PROGRESS:
            return Response({'id': r.id, 'status': r.status})       # already submitted — idempotent
        raw = request.data.get('answers') if isinstance(request.data.get('answers'), dict) else {}
        keys = task_keys(r.test)
        r.answers = {k: str(raw.get(k) or '').strip()[:MAX_ANSWER_CHARS] for k in keys}
        r.submitted_at = timezone.now()
        nothing = not any(words_in(v) for v in r.answers.values())
        if nothing:
            # nothing to read — score 0 straight away, no AI call
            r.score, r.result = score_answers(r.test, r.answers)
            r.level, r.status, r.scored_at = level_for(r.score), CEFRWritingResponse.Status.READY, timezone.now()
        else:
            r.status = CEFRWritingResponse.Status.SCORING
        r.save()
    if not nothing:
        from api.tasks import evaluate_cefr_writing
        transaction.on_commit(lambda: evaluate_cefr_writing.delay(r.id))
    return Response({'id': r.id, 'status': r.status})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_retry(request, response_id):
    """Queue scoring again after it failed."""
    updated = CEFRWritingResponse.objects.filter(
        id=response_id, user=request.user, status=CEFRWritingResponse.Status.FAILED,
    ).update(status=CEFRWritingResponse.Status.SCORING, submitted_at=timezone.now())
    if updated:
        cache.delete(f'cefr_writing_fallback_wait:{response_id}')
        from api.tasks import evaluate_cefr_writing
        evaluate_cefr_writing.delay(response_id)
    r = get_object_or_404(CEFRWritingResponse, id=response_id, user=request.user)
    return Response({'id': r.id, 'status': r.status})


# ── admin ───────────────────────────────────────────────────────────────────

FIELD_ALIASES = {'task_1_1': 'task11', 'task11': 'task11', '1.1': 'task11',
                 'task_1_2': 'task12', 'task12': 'task12', '1.2': 'task12',
                 'part_2': 'task2', 'task_2': 'task2', 'task2': 'task2', '2': 'task2'}


def _parse_test(item, n):
    """Validate one imported test → model kwargs, or raise ValueError with a message for the admin."""
    where = f'Test #{n}'
    if not isinstance(item, dict):
        raise ValueError(f'{where}: obyekt bo\'lishi kerak ({{...}}).')
    title = str(item.get('title') or '').strip()
    if not title:
        raise ValueError(f'{where}: "title" yozilmagan.')
    where = f'"{title}"'
    fields = {'task11': '', 'task12': '', 'task2': ''}
    for k, v in item.items():
        if k in FIELD_ALIASES:
            if not isinstance(v, str):
                raise ValueError(f'{where}: "{k}" matn (string) bo\'lishi kerak.')
            fields[FIELD_ALIASES[k]] = v.strip()
    has1 = bool(fields['task11']) or bool(fields['task12'])
    if has1 and not (fields['task11'] and fields['task12']):
        raise ValueError(f'{where}: Part 1 uchun "task_1_1" va "task_1_2" ikkalasi ham kerak.')
    if not has1 and not fields['task2']:
        raise ValueError(f'{where}: hech bo\'lmasa Part 1 ("task_1_1" + "task_1_2") yoki "part_2" bo\'lishi kerak.')
    kind = 'FULL' if has1 and fields['task2'] else 'PART1' if has1 else 'PART2'
    situation = str(item.get('situation') or '').strip()
    if has1 and not situation:
        raise ValueError(f'{where}: Part 1 uchun "situation" (umumiy vaziyat matni) kerak.')
    try:
        time_limit = int(item.get('time_limit') or DEFAULT_TIME[kind])
    except (TypeError, ValueError):
        raise ValueError(f'{where}: "time_limit" daqiqada son bo\'lishi kerak.')
    if not 5 <= time_limit <= 180:
        raise ValueError(f'{where}: "time_limit" 5–180 daqiqa oralig\'ida bo\'lsin.')
    return {'title': title[:200], 'kind': kind, 'situation': situation if has1 else '', **fields,
            'time_limit': time_limit, 'is_premium': bool(item.get('is_premium', False))}


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_writing(request):
    data = request.data
    if hasattr(data, 'get') and request.FILES.get('file'):
        try:
            data = json.load(request.FILES['file'])
        except ValueError:
            return Response({'error': 'Fayldagi JSON noto\'g\'ri.'}, status=400)
    items = data.get('tests') if isinstance(data, dict) and 'tests' in data else data
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return Response({'error': 'JSON bitta test, testlar ro\'yxati yoki {"tests": [...]} bo\'lishi kerak.'}, status=400)
    try:
        parsed = [_parse_test(item, i + 1) for i, item in enumerate(items)]
    except ValueError as e:
        return Response({'error': str(e)}, status=400)
    with transaction.atomic():
        created = [CEFRWritingTest.objects.create(**kw) for kw in parsed]
    return Response({'created': len(created), 'items': [{'id': t.id, 'title': t.title, 'kind': t.kind} for t in created]},
                    status=201)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_writing_list(request):
    rows = CEFRWritingTest.objects.annotate(n=Count('responses')).order_by('-created_at')
    return Response([{**_test_summary(t), 'situation': t.situation, 'task11': t.task11, 'task12': t.task12,
                      'task2': t.task2, 'responses': t.n, 'is_active': t.is_active,
                      'created_at': t.created_at} for t in rows])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_writing_delete(request, pk):
    get_object_or_404(CEFRWritingTest, pk=pk).delete()
    return Response(status=204)
