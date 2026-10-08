"""
CEFR multilevel Speaking — the exam script, recorded answers, AI scoring and admin import.

The paper runs like the real computer-based exam: the examiner voice reads
each question, a preparation countdown runs, recording starts and stops by
itself.
  Part 1.1  questions about yourself        5 s to prepare · 30 s per answer
  Part 1.2  two pictures and questions      5 s to prepare · 30 s per answer
  Part 2    a picture / topic and questions 60 s to prepare · 2 min long turn
  Part 3    a statement, for and against    60 s to prepare · 2 min long turn
Score 0–75: 1.1 → 12 points, 1.2 → 15, Part 2 → 24, Part 3 → 24. Each part is
marked on four criteria (0–5) with the same strict caps and level bands as
CEFR writing (api.cefr_writing).

The examiner lines are built here (`script`) and the page plays exactly these
texts in one fixed voice, so every line is synthesized once, kept on disk and
shared by all students; importing a test warms that cache in Celery.

Student API
  GET  /api/cefr/speaking/                           ready tests + the user's latest result
  POST /api/cefr/speaking/<test_id>/start/           → {response_id}
  GET  /api/cefr/speaking/responses/<id>/            script, answers (audio URLs), status, result
  POST /api/cefr/speaking/responses/<id>/submit/     multipart: answers JSON + audio_<i> files
  POST /api/cefr/speaking/responses/<id>/retry/
start and submit answer 429 {code: "speaking_daily_limit", ...} while the hidden daily speaking
limit (api/speaking_limit.py, shared with IELTS speaking) has locked the user.
Admin API
  POST   /api/import/cefr/speaking/
  GET    /api/admin/cefr/speaking/
  DELETE /api/admin/cefr/speaking/<id>/
  POST/DELETE /api/admin/cefr/speaking/<id>/image/?slot=p12a|p12b|p2
"""
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from api import speaking_limit
from api.cefr_writing import LEVEL_MAX, _clamp, _clean_errors, _clean_list, _norm_quote, level_for
from cefr.models import CEFRSpeakingResponse, CEFRSpeakingTest

log = logging.getLogger(__name__)

MODEL = 'gpt-4.1'
VOICE, SPEED = 'nova', 0.95            # one voice for everyone → every line is cached once
EVAL_LOCK_TTL = 240
FALLBACK_AFTER = 90
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TRANSCRIPT = 4000

PARTS = {
    '1.1': {'label': 'Part 1.1', 'name': 'About you', 'prep': 5, 'speak': 30, 'points': 12, 'min_words': 40},
    '1.2': {'label': 'Part 1.2', 'name': 'Two pictures', 'prep': 5, 'speak': 30, 'points': 15, 'min_words': 40},
    '2':   {'label': 'Part 2', 'name': 'Long turn', 'prep': 60, 'speak': 120, 'points': 24, 'min_words': 150},
    '3':   {'label': 'Part 3', 'name': 'For and against', 'prep': 60, 'speak': 120, 'points': 24, 'min_words': 150},
}
PART_ORDER = ('1.1', '1.2', '2', '3')
CRITERIA = (
    ('task', 'Task & relevance'),
    ('fluency', 'Fluency & coherence'),
    ('vocabulary', 'Vocabulary'),
    ('grammar', 'Grammar'),
)
ERROR_CAPS = {'grammar': (4.0, 3.5, 3.0), 'vocabulary': (4.0, 3.5, 3.0), 'task': (4.5, 4.0, 3.5), 'fluency': (4.5, 4.0, 3.5)}
DEDUP_ORDER = ('grammar', 'vocabulary', 'task', 'fluency')
IMAGE_SLOTS = {'p12a': 'part12_image1', 'p12b': 'part12_image2', 'p2': 'part2_image'}
NUMBER = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}
OUTRO = 'That is the end of the speaking test. Thank you.'


# ── the paper ───────────────────────────────────────────────────────────────

def parts_of(test):
    out = []
    if test.part11:
        out.append('1.1')
    if test.part12:
        out.append('1.2')
    if (test.part2 or {}).get('prompt'):
        out.append('2')
    if (test.part3 or {}).get('topic'):
        out.append('3')
    return out


def is_ready(test):
    """A test with Part 1.2 needs both pictures before students can take it."""
    return '1.2' not in parts_of(test) or bool(test.part12_image1 and test.part12_image2)


def _url(f, request):
    if not f:
        return None
    return request.build_absolute_uri(f.url) if request else f.url


def _intro(key, n):
    count = NUMBER.get(n, str(n))
    many = 'questions' if n != 1 else 'question'
    if key == '1.1':
        return (f'Part one point one. I am going to ask you {count} short {many} about yourself. '
                f'You have thirty seconds to answer each question.')
    if key == '1.2':
        return (f'Part one point two. Look at the two pictures. I am going to ask you {count} {many} about them. '
                f'You have thirty seconds to answer each question.')
    if key == '2':
        return ('Part two. Look at the task and read the questions. You have one minute to prepare. '
                'Then you will speak for two minutes.')
    return ('Part three. Read the statement and the arguments for and against it. You have one minute to prepare. '
            'Then you will speak for two minutes. Talk about both sides and give your own opinion.')


def script(test, request=None):
    """Everything the exam page shows and says, in order."""
    parts = []
    for key in parts_of(test):
        p = PARTS[key]
        part = {'key': key, 'label': p['label'], 'name': p['name'], 'points': p['points'],
                'prep': p['prep'], 'speak': p['speak']}
        if key in ('1.1', '1.2'):
            qs = test.part11 if key == '1.1' else test.part12
            part['steps'] = [{'q': i, 'question': q, 'say': q} for i, q in enumerate(qs)]
            if key == '1.2':
                part['images'] = [_url(test.part12_image1, request), _url(test.part12_image2, request)]
        elif key == '2':
            prompt, questions = test.part2.get('prompt', ''), list(test.part2.get('questions') or [])
            part['prompt'], part['bullets'] = prompt, questions
            part['image'] = _url(test.part2_image, request)
            part['steps'] = [{'q': 0, 'question': prompt + ('\n' + '\n'.join(f'• {q}' for q in questions) if questions else ''),
                              'say': ' '.join([prompt, *questions])}]
        else:
            topic = test.part3.get('topic', '')
            part['topic'], part['for'], part['against'] = topic, list(test.part3.get('for') or []), list(test.part3.get('against') or [])
            part['steps'] = [{'q': 0, 'question': topic, 'say': f'The statement is: {topic}'}]
        part['intro'] = _intro(key, len(part['steps']))
        parts.append(part)
    return {'voice': VOICE, 'speed': SPEED, 'parts': parts, 'outro': OUTRO}


def tts_lines(test):
    s = script(test)
    return [x for p in s['parts'] for x in [p['intro'], *(st['say'] for st in p['steps'])]] + [s['outro']]


def warm_tts(test):
    """Synthesize every examiner line of a test once (Celery, after import)."""
    from api.ielts_views import tts_audio
    made = 0
    for line in tts_lines(test):
        try:
            _, status = tts_audio(line, VOICE, SPEED)
            made += status == 'MISS'
        except Exception:
            log.warning('TTS warm-up failed for test %s: %.60s', test.id, line, exc_info=True)
    return made


# ── scoring ─────────────────────────────────────────────────────────────────

def _spoken(text):
    t = str(text or '').strip()
    return '' if t.lower() == '(no transcript)' else t


def _words(text):
    return len(_spoken(text).split())


def _empty_part(key, n):
    note = 'Nothing was recorded for this part.'
    return {**{c: {'score': 0, 'label': label, 'feedback': note, 'strengths': [], 'errors': []} for c, label in CRITERIA},
            'good_phrases': [], 'better': [''] * n, 'points': 0, 'max_points': PARTS[key]['points'], 'words': 0}


def _shape_part(key, raw, answers):
    p = PARTS[key]
    raw = raw if isinstance(raw, dict) else {}
    words = sum(_words(a['transcript']) for a in answers)
    better = raw.get('better') if isinstance(raw.get('better'), list) else []
    out = {
        'good_phrases': _clean_list(raw.get('good_phrases'), 4, 140), 'words': words, 'max_points': p['points'],
        # a stronger version per answer — never for an answer that was not given
        'better': [str(better[i] if i < len(better) and isinstance(better[i], str) else '').strip()[:1200]
                   if _words(a['transcript']) else '' for i, a in enumerate(answers)],
    }
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
    expected = p['min_words'] * max(len(answers), 1)
    total = 0.0
    for c, label in CRITERIA:
        score = _clamp(crit[c].get('score'))
        if errors[c]:
            score = min(score, ERROR_CAPS[c][min(len(errors[c]), 3) - 1])
        if c in ('task', 'fluency'):
            # too little speech for the time given — independent of the model
            if words < expected * 0.4:
                score = min(score, 1.0 if c == 'task' else 1.5)
            elif words < expected * 0.7:
                score = min(score, 2.5)
        total += score
        out[c] = {'score': score, 'label': label, 'feedback': str(crit[c].get('feedback') or '').strip()[:500],
                  'strengths': _clean_list(crit[c].get('strengths'), 2), 'errors': errors[c]}
    out['points'] = round(total / 20 * p['points'], 1)
    return out


SYSTEM = """You are a strict, experienced examiner for the Uzbekistan national CEFR multilevel Speaking exam.
You mark exactly what the candidate said and never give the benefit of the doubt: when you hesitate between two scores, give the lower one.
The answers are automatic speech-recognition transcripts: they have no punctuation or capitals — never mark punctuation or capitalisation.
Return only valid JSON."""

INSTRUCTIONS = """Mark every part below on four criteria. Each criterion is scored 0–5 (0.5 steps allowed):
5 = strong C1: would impress a C1 examiner — precise, less common vocabulary, a wide range of complex structures, fully developed answers, no errors. This is rare.
4 = B2: clear, relevant and mostly accurate; good but ordinary range. Error-free yet ordinary speech is 4, not 5.
3 = B1: answers the question simply, noticeable errors, limited range, little development.
2 = A2: very short or partly irrelevant answers, frequent errors, very simple language.
1 = barely answers or is mostly hard to follow.
0 = nothing said or completely off-topic.
Most learners score 2.5–4; a typical intermediate candidate is 3. When you hesitate, choose the lower score.

Criteria:
- task: answers each question directly and fully and uses the time given (about {wpm} words per 30 seconds is a fully used answer); Part 1.2 describes and compares the pictures; Part 2 covers every question; Part 3 gives both sides and a clear opinion.
- fluency: develops and links ideas, logical order, little repetition or false starts.
- vocabulary: range, precision, collocations, topic words.
- grammar: range of structures and accuracy.

Rules:
- Errors: copy each "quote" WORD FOR WORD from the transcript (2–10 words, no "..."), it is highlighted in the candidate's text. At most 3 errors per criterion, most important first, only real errors you can quote.
- Something MISSING (an unanswered question, too short an answer) is not a quote: leave "quote" empty and explain it in "issue".
- Each mistake goes under ONE criterion only: grammar (verb forms, agreement, articles, word order), vocabulary (word choice, collocation), task (relevance, missing content, length), fluency (development, linking, repetition). Never repeat a quote under a second criterion.
- strengths: at most 2 short sentences (4–12 words) per criterion, only if real.
- feedback: 1–2 sentences per criterion, in English.
- good_phrases: up to 4 short phrases (2–8 words, copied word for word) the candidate used well in that part; [] if none.
- better: one entry per answer of the part, in the same order — the same ideas said one level higher (B1 → B2, B2 → C1) in natural spoken English, at most 90 words for Part 1 answers and 160 words for Parts 2 and 3. "" for an answer that is empty.
- overall_level: your holistic judgement of the whole performance — exactly one of "C1", "B2", "B1", "BELOW". Be strict: C1 only if every part shows C1 control.
- summary: 2 sentences — the overall level and the single most useful next step."""


def _build_prompt(test, keys, answers_by_part):
    s = {p['key']: p for p in script(test)['parts']}
    blocks = []
    for k in keys:
        p, part = PARTS[k], s[k]
        head = f'=== {k}: {p["label"]} — {p["name"]}, {p["prep"]} s to prepare, {p["speak"]} s to speak per answer ==='
        extra = ''
        if k == '1.2':
            extra = '(The candidate sees two pictures; you cannot see them — judge whether they describe and compare them.)\n'
        if k == '3':
            extra = (f'Arguments FOR: {"; ".join(part.get("for", []))}\n'
                     f'Arguments AGAINST: {"; ".join(part.get("against", []))}\n')
        lines = [head, extra.rstrip()] if extra else [head]
        for i, a in enumerate(answers_by_part[k], 1):
            t = _spoken(a['transcript'])
            lines.append(f'Q{i}: {a["question"]}\nA{i} ({_words(t)} words): {t or "(nothing said)"}')
        blocks.append('\n'.join(lines))
    shape = ', '.join(f'"{k}": {{"task": {{...}}, "fluency": {{...}}, "vocabulary": {{...}}, "grammar": {{...}}, '
                      f'"good_phrases": [...], "better": [...]}}' for k in keys)
    criterion = '{"score": <0-5>, "feedback": "...", "strengths": ["..."], "errors": [{"quote": "...", "issue": "...", "suggestion": "..."}]}'
    return (INSTRUCTIONS.replace('{wpm}', '55') + '\n\n' + '\n\n'.join(blocks) +
            f'\n\nReturn JSON exactly like this (only these part keys):\n'
            f'{{"parts": {{{shape}}}, "overall_level": "C1|B2|B1|BELOW", "summary": "..."}}\nwhere every criterion is {criterion}')


def _call_ai(test, keys, answers_by_part):
    from openai import OpenAI
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise RuntimeError('OpenAI API key is not configured')
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=120)
    reply = client.chat.completions.create(
        model=MODEL, temperature=0.1, max_tokens=5000, response_format={'type': 'json_object'},
        messages=[{'role': 'system', 'content': SYSTEM},
                  {'role': 'user', 'content': _build_prompt(test, keys, answers_by_part)}],
    )
    return json.loads(reply.choices[0].message.content or '{}')


def _group(test, answers):
    """Answers by part, in script order, with the official question text (missing ones are empty)."""
    given = {(a.get('part'), a.get('q')): a for a in answers or [] if isinstance(a, dict)}
    out = {}
    for part in script(test)['parts']:
        out[part['key']] = [{'question': st['question'],
                             'transcript': _spoken((given.get((part['key'], st['q'])) or {}).get('transcript'))}
                            for st in part['steps']]
    return out


def score_answers(test, answers, ai=None):
    """Pure scoring step (the AI call is injectable for tests). Returns (score, result)."""
    by_part = _group(test, answers)
    keys = list(by_part)
    parts, spoken = {}, []
    for k in keys:
        if any(_words(a['transcript']) for a in by_part[k]):
            spoken.append(k)
        else:
            parts[k] = _empty_part(k, len(by_part[k]))
    summary, judged = ('Nothing was recorded, so the test could not be assessed.' if not spoken else ''), ''
    if spoken:
        reply = (ai or _call_ai)(test, spoken, {k: by_part[k] for k in spoken})
        raw_parts = reply.get('parts') if isinstance(reply.get('parts'), dict) else {}
        for k in spoken:
            parts[k] = _shape_part(k, raw_parts.get(k), by_part[k])
        summary, judged = reply.get('summary', ''), reply.get('overall_level', '')
    raw = round(sum(parts[k]['points'] for k in keys), 1)
    max_raw = sum(PARTS[k]['points'] for k in keys)
    score = round(raw / max_raw * 75) if max_raw else 0
    judged = str(judged or '').strip().upper().replace(' ', '_')
    judged = {'BELOW_B1': 'BELOW', 'A2': 'BELOW', 'A1': 'BELOW'}.get(judged, judged)
    capped = judged in LEVEL_MAX and score > LEVEL_MAX[judged]
    if capped:
        score = LEVEL_MAX[judged]
    return score, {'parts': parts, 'order': keys, 'raw': raw, 'max_raw': max_raw,
                   'summary': str(summary or '').strip()[:600],
                   'judged_level': judged if judged in LEVEL_MAX else '', 'capped': capped}


def fill_missing_transcripts(response):
    """
    Answers the browser could not turn into text (Safari, Firefox, Yandex Browser,
    a blocked speech service) but that were recorded: transcribe them on the
    server with Whisper once, so the student is scored on what they really said.
    """
    from api.stt import whisper_transcribe
    answers, changed = list(response.answers or []), False
    for a in answers:
        if not isinstance(a, dict) or _spoken(a.get('transcript')) or not a.get('audio') or a.get('stt') == 'whisper':
            continue
        try:
            with default_storage.open(a['audio'], 'rb') as fh:
                data = fh.read()
            if not data or len(data) > MAX_AUDIO_BYTES:
                continue
            a['transcript'] = whisper_transcribe(data, a.get('mime') or 'audio/webm', timeout=60)[:MAX_TRANSCRIPT]
            a['stt'] = 'whisper'
            changed = True
        except Exception:
            log.warning('Whisper fallback failed for CEFR speaking %s (%s)', response.id, a.get('audio'), exc_info=True)
    if changed:
        response.answers = answers
        response.save(update_fields=['answers'])
    return changed


def evaluate_response(response):
    """Score a submitted response once (Celery and the fallback share the lock)."""
    if response.status != CEFRSpeakingResponse.Status.SCORING:
        return False
    lock = f'cefr_speaking_eval_lock:{response.id}'
    if not cache.add(lock, '1', timeout=EVAL_LOCK_TTL):
        return False
    try:
        fill_missing_transcripts(response)
        score, result = score_answers(response.test, response.answers or [])
        response.score, response.level, response.result = score, level_for(score), result
        response.status = CEFRSpeakingResponse.Status.READY
        response.scored_at = timezone.now()
        response.save(update_fields=['score', 'level', 'result', 'status', 'scored_at'])
        return True
    except Exception:
        cache.delete(lock)
        raise


# ── student views ───────────────────────────────────────────────────────────

def _minutes(test):
    secs = 0
    for k in parts_of(test):
        n = len(test.part11) if k == '1.1' else len(test.part12) if k == '1.2' else 1
        secs += 15 + n * (PARTS[k]['prep'] + PARTS[k]['speak'] + 8)      # intro + reading each question
    return max(1, round(secs / 60))


def _summary(test):
    keys = parts_of(test)
    return {'id': test.id, 'title': test.title, 'is_premium': test.is_premium, 'parts': keys,
            'max_points': sum(PARTS[k]['points'] for k in keys), 'minutes': _minutes(test)}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaking_tests(request):
    tests = [t for t in CEFRSpeakingTest.objects.filter(is_active=True) if is_ready(t) and parts_of(t)]
    latest = {}
    for r in (CEFRSpeakingResponse.objects.filter(user=request.user, test__in=tests)
              .only('id', 'test_id', 'status', 'score', 'level', 'started_at', 'submitted_at')):
        latest.setdefault(r.test_id, r)
    out = []
    for t in tests:
        r = latest.get(t.id)
        out.append({**_summary(t), 'last': None if r is None else {
            'response_id': r.id, 'status': r.status, 'score': r.score, 'level': r.level,
            'date': (r.submitted_at or r.started_at).isoformat()}})
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_start(request, test_id):
    test = get_object_or_404(CEFRSpeakingTest, id=test_id, is_active=True)
    if not is_ready(test):
        return Response({'detail': 'This test is not ready yet.'}, status=409)
    if test.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'detail': 'Premium required.'}, status=403)
    # hidden daily limit shared with IELTS speaking: a locked user does not record anything
    refused = speaking_limit.refuse_if_locked(request.user)
    if refused is not None:
        return refused
    # a speaking test cannot be resumed half-way (the recordings live in the page), so every start is fresh
    CEFRSpeakingResponse.objects.filter(user=request.user, test=test, status='IN_PROGRESS').delete()
    r = CEFRSpeakingResponse.objects.create(user=request.user, test=test)
    return Response({'response_id': r.id}, status=201)


def _answers_out(answers, request):
    out = []
    for a in answers or []:
        a = dict(a)
        path = a.pop('audio', '')
        a.pop('mime', None)
        a['audio_url'] = request.build_absolute_uri(default_storage.url(path)) if path else None
        out.append(a)
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaking_response(request, response_id):
    r = get_object_or_404(CEFRSpeakingResponse.objects.select_related('test'), id=response_id, user=request.user)
    if r.status == CEFRSpeakingResponse.Status.SCORING and r.submitted_at:
        wait_key = f'cefr_speaking_fallback_wait:{r.id}'
        if (timezone.now() - r.submitted_at).total_seconds() >= FALLBACK_AFTER and not cache.get(wait_key):
            try:
                if evaluate_response(r):
                    log.warning('Fallback scored CEFR speaking %s (Celery did not)', r.id)
            except Exception:
                cache.set(wait_key, 1, 120)
                log.exception('Fallback CEFR speaking scoring failed for %s', r.id)
    return Response({
        'id': r.id, 'status': r.status, 'score': r.score, 'level': r.level, 'result': r.result,
        'answers': _answers_out(r.answers, request), 'started_at': r.started_at, 'submitted_at': r.submitted_at,
        'test': _summary(r.test), 'script': script(r.test, request),
    })


def _seconds(v):
    try:
        return max(0, min(600, round(float(v))))
    except (TypeError, ValueError):
        return 0


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_submit(request, response_id):
    r = get_object_or_404(CEFRSpeakingResponse.objects.select_related('test'), id=response_id, user=request.user)
    if r.status != CEFRSpeakingResponse.Status.IN_PROGRESS:
        return Response({'id': r.id, 'status': r.status})
    try:
        raw = json.loads(request.data.get('answers') or '[]')
    except (TypeError, ValueError):
        return Response({'error': 'answers must be a JSON list'}, status=400)
    if not isinstance(raw, list):
        return Response({'error': 'answers must be a JSON list'}, status=400)

    # the first submission counts towards the hidden daily speaking limit (shared with IELTS);
    # a response is submitted once, so a repeated submit is free forever
    refused, use_id = speaking_limit.claim(request.user, speaking_limit.CEFR, r.id, r.started_at, free_for=None)
    if refused is not None:
        return refused
    try:
        valid = {(p['key'], st['q']): st['question'] for p in script(r.test)['parts'] for st in p['steps']}
        answers, seen = [], set()
        for i, a in enumerate(raw):
            if not isinstance(a, dict):
                continue
            key = (str(a.get('part')), a.get('q'))
            if key not in valid or key in seen:
                continue
            seen.add(key)
            item = {'part': key[0], 'q': key[1], 'question': valid[key],
                    'transcript': _spoken(a.get('transcript'))[:MAX_TRANSCRIPT],
                    'seconds': _seconds(a.get('seconds')),
                    'audio': ''}
            f = request.FILES.get(f'audio_{i}')
            if f and f.size <= MAX_AUDIO_BYTES and (f.content_type or '').startswith(('audio/', 'video/webm', 'application/octet-stream')):
                mime = (f.content_type or 'audio/webm').split(';')[0].strip().lower()
                ext = 'm4a' if mime in ('audio/mp4', 'audio/x-m4a', 'audio/aac') else 'ogg' if mime == 'audio/ogg' else 'webm'
                path = f'cefr/speaking/answers/r{r.id}_{key[0].replace(".", "")}_{key[1]}.{ext}'
                if default_storage.exists(path):
                    default_storage.delete(path)
                item['audio'] = default_storage.save(path, f)
                item['mime'] = mime
            answers.append(item)

        with transaction.atomic():
            r = CEFRSpeakingResponse.objects.select_for_update().get(id=r.id)
            if r.status != CEFRSpeakingResponse.Status.IN_PROGRESS:
                return Response({'id': r.id, 'status': r.status})   # a parallel submit of it won; it is counted
            r.answers, r.submitted_at = answers, timezone.now()
            nothing = not any(_words(a['transcript']) or a['audio'] for a in answers)
            if nothing:
                r.score, r.result = score_answers(r.test, answers)
                r.level, r.status, r.scored_at = level_for(r.score), CEFRSpeakingResponse.Status.READY, timezone.now()
            else:
                r.status = CEFRSpeakingResponse.Status.SCORING
            r.save()
    except Exception:
        speaking_limit.release(use_id)          # a failed submission does not use up the allowance
        raise
    if not nothing:
        from api.tasks import evaluate_cefr_speaking
        transaction.on_commit(lambda: evaluate_cefr_speaking.delay(r.id))
    return Response({'id': r.id, 'status': r.status})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_retry(request, response_id):
    updated = CEFRSpeakingResponse.objects.filter(
        id=response_id, user=request.user, status=CEFRSpeakingResponse.Status.FAILED,
    ).update(status=CEFRSpeakingResponse.Status.SCORING, submitted_at=timezone.now())
    if updated:
        cache.delete(f'cefr_speaking_fallback_wait:{response_id}')
        from api.tasks import evaluate_cefr_speaking
        evaluate_cefr_speaking.delay(response_id)
    r = get_object_or_404(CEFRSpeakingResponse, id=response_id, user=request.user)
    return Response({'id': r.id, 'status': r.status})


# ── admin ───────────────────────────────────────────────────────────────────

def _str_list(v, where, field, lo=1, hi=6):
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ValueError(f'{where}: "{field}" matnlar ro\'yxati bo\'lishi kerak, masalan ["...", "..."].')
    if not lo <= len(v) <= hi:
        raise ValueError(f'{where}: "{field}" ichida {lo}–{hi} ta element bo\'lsin (hozir {len(v)}).')
    return [x.strip() for x in v]


def _parse_test(item, n):
    where = f'Test #{n}'
    if not isinstance(item, dict):
        raise ValueError(f'{where}: obyekt bo\'lishi kerak ({{...}}).')
    title = str(item.get('title') or '').strip()
    if not title:
        raise ValueError(f'{where}: "title" yozilmagan.')
    where = f'"{title}"'
    out = {'title': title[:200], 'is_premium': bool(item.get('is_premium', False)),
           'part11': [], 'part12': [], 'part2': {}, 'part3': {}}
    if 'part_1_1' in item:
        out['part11'] = _str_list(item['part_1_1'], where, 'part_1_1', 1, 5)
    if 'part_1_2' in item:
        out['part12'] = _str_list(item['part_1_2'], where, 'part_1_2', 1, 5)
    if 'part_2' in item:
        p2 = item['part_2']
        if not isinstance(p2, dict) or not str(p2.get('prompt') or '').strip():
            raise ValueError(f'{where}: "part_2" {{"prompt": "...", "questions": [...]}} ko\'rinishida bo\'lsin — "prompt" majburiy.')
        out['part2'] = {'prompt': p2['prompt'].strip(),
                        'questions': _str_list(p2['questions'], where, 'part_2.questions', 1, 5) if p2.get('questions') else []}
    if 'part_3' in item:
        p3 = item['part_3']
        if not isinstance(p3, dict) or not str(p3.get('topic') or '').strip():
            raise ValueError(f'{where}: "part_3" {{"topic": "...", "for": [...], "against": [...]}} ko\'rinishida bo\'lsin — "topic" majburiy.')
        out['part3'] = {'topic': p3['topic'].strip(),
                        'for': _str_list(p3.get('for'), where, 'part_3.for'),
                        'against': _str_list(p3.get('against'), where, 'part_3.against')}
    if not (out['part11'] or out['part12'] or out['part2'] or out['part3']):
        raise ValueError(f'{where}: hech bo\'lmasa bitta qism kerak: "part_1_1", "part_1_2", "part_2" yoki "part_3".')
    return out


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_speaking(request):
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
        created = [CEFRSpeakingTest.objects.create(**kw) for kw in parsed]
        from api.tasks import warm_cefr_speaking_tts
        for t in created:
            transaction.on_commit(lambda tid=t.id: warm_cefr_speaking_tts.delay(tid))
    return Response({'created': len(created),
                     'items': [{'id': t.id, 'title': t.title, 'parts': parts_of(t), 'needs_images': not is_ready(t)}
                               for t in created]}, status=201)


def _admin_row(t, request):
    return {**_summary(t), 'responses': getattr(t, 'n', 0), 'is_active': t.is_active, 'created_at': t.created_at,
            'ready': is_ready(t), 'part11': t.part11, 'part12': t.part12, 'part2': t.part2, 'part3': t.part3,
            'images': {slot: _url(getattr(t, field), request) for slot, field in IMAGE_SLOTS.items()}}


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_speaking_list(request):
    rows = CEFRSpeakingTest.objects.annotate(n=Count('responses')).order_by('-created_at')
    return Response([_admin_row(t, request) for t in rows])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_speaking_delete(request, pk):
    t = get_object_or_404(CEFRSpeakingTest, pk=pk)
    for field in IMAGE_SLOTS.values():
        if getattr(t, field):
            getattr(t, field).delete(save=False)
    t.delete()
    return Response(status=204)


@api_view(['POST', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_speaking_image(request, pk):
    """Upload (replace) or remove one picture: slot p12a / p12b (Part 1.2) or p2 (Part 2)."""
    from PIL import Image
    t = get_object_or_404(CEFRSpeakingTest, pk=pk)
    field = IMAGE_SLOTS.get(request.query_params.get('slot') or request.data.get('slot') or '')
    if not field:
        return Response({'error': 'slot p12a, p12b yoki p2 bo\'lishi kerak.'}, status=400)
    current = getattr(t, field)
    if request.method == 'DELETE':
        if current:
            current.delete(save=False)
        setattr(t, field, None)
        t.save(update_fields=[field])
        return Response(_admin_row(t, request))
    image = request.FILES.get('image')
    if not image:
        return Response({'error': 'Rasm fayli yuborilmadi.'}, status=400)
    if image.size > 5 * 1024 * 1024:
        return Response({'error': "Rasm 5 MB dan katta bo'lmasin."}, status=400)
    try:
        Image.open(image).verify()
    except Exception:
        return Response({'error': "Fayl rasm emas (PNG, JPG yoki WEBP yuklang)."}, status=400)
    image.seek(0)
    if current:
        current.delete(save=False)
    setattr(t, field, image)
    t.save(update_fields=[field])
    return Response(_admin_row(t, request))
