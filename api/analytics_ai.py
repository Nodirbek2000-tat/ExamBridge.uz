"""
AI analysis for the SAT / IELTS / CEFR analytics pages — streamed as Markdown.

The model gets exactly the numbers the page shows (plus, for IELTS/CEFR, the
student's real recent mistakes) and a fixed list of links it may use, so every
claim can be checked against the charts and every link opens a real page.

POST /api/analytics/ai/  {exam: sat|ielts|cefr, range, lang: uz|en, refresh?, cached_only?}
  cached_only → JSON {text|null}: an earlier analysis of the same numbers, never a new call
  otherwise   → text/event-stream: {type: meta|delta|done|error}
"""
import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

log = logging.getLogger(__name__)

MIN_QUESTIONS = 10
DAILY_LIMIT = 6              # new generations per user, per exam, per day
CACHE_SECONDS = 7 * 24 * 3600
MODEL = 'gpt-4o'


def _sat(user, range_key):
    from api.sat_analytics import build_analytics, _ai_facts
    data = build_analytics(user, range_key)
    links = [{'label': 'Full-length SAT tests', 'url': '/app/sat/tests'},
             {'label': 'SAT practice (question bank)', 'url': '/app/sat/practice'},
             {'label': 'Saved questions (review mistakes)', 'url': '/app/sat/saved'}]
    for s in data['lowest_skills']:
        subject = 'math' if s['section'] == 'MATH' else 'english'
        links.append({'label': f"Practise {s['topic']}", 'url': f"/app/sat/practice?subject={subject}&topic={s['topic']}"})
    hints = ('Digital SAT. Example techniques: Words in Context -> cover the options and predict your own word first; '
             'Punctuation/boundaries -> check whether each side of the mark is a complete sentence; '
             'Linear equations -> write the equation from the words, isolate the variable, plug the answer back in.')
    return _ai_facts(data), data['totals']['attempted'], links, hints


def _skill_exam(user, exam, range_key):
    from api.exam_analytics import build_exam_analytics, ai_facts
    data = build_exam_analytics(user, exam.upper(), range_key)
    base = f'/app/{exam}/skills?tab='
    links = [{'label': f'{exam.upper()} {s.title()} tests', 'url': base + s} for s in ('reading', 'listening', 'writing', 'speaking')]
    links.append({'label': 'Test history', 'url': f'/app/{exam}/history'})
    if exam == 'ielts':
        links.append({'label': 'Saved / bookmarked questions', 'url': '/app/bookmarks'})
        hints = ('IELTS Academic/General. Example techniques: TFNG -> FALSE contradicts the text, NOT GIVEN is simply absent — '
                 'underline the claim and look for a direct contradiction; Gap/note completion -> check the word limit and '
                 'spelling, predict the word class before listening; Matching headings -> read the paragraph first, name its '
                 'main idea, then match; Writing -> plan 5 minutes, one clear position, linking words; Speaking -> extend answers '
                 'with reason + example.')
    else:
        hints = ('Uzbekistan national CEFR multilevel exam (Reading 5 parts, Listening 6 parts, scores mapped to B1/B2/C1). '
                 'Example techniques: Part 1 gaps -> the missing word appears elsewhere in the text, check grammar around the gap; '
                 'text/speaker matching -> underline the key idea of each text, eliminate used options; T/F/NG -> look for a direct '
                 'contradiction; note completion -> predict the word class and respect the word limit.')
    return ai_facts(data), data['totals']['answered'] + data['totals']['writing_count'] + data['totals']['speaking_count'], links, hints


PROMPT = """You are an experienced {exam} coach. You are given one student's exact practice statistics as JSON{mistakes_note}.
Write a specific, honest analysis in Markdown.

Accuracy rules (the student sees the charts next to your text):
- Use ONLY numbers that appear in the JSON. Never invent, estimate, add up or recalculate anything.
- Whenever you state a result, copy its "result" string exactly as given (e.g. "0/22 (0%)") — never build the fraction yourself.
- Items marked "too_few_attempts_to_judge" are neither strengths nor weaknesses — at most say there is not enough data yet.
- A strength needs 5+ attempts and 70%+ accuracy (or, for writing/speaking, an average of band 6.5+ or 51+/75 from 2+ responses). If there is none, say so in one line — no invented praise.
- "left_blank" means the question was left empty (usually time pressure) — treat it separately from wrong answers.
  If most answers are blank, the first fix is answering every question (a guess costs nothing) and managing time, before any technique.
- If something is not in the data, do not mention it.

Advice rules:
- Name the exact question type / skill / criterion and give a concrete technique. Context: {hints}
- {mistake_rule}
- The plan must be doable on this platform. Every plan step ends with ONE markdown link chosen from ALLOWED_LINKS (copy the url exactly), e.g. [Practise reading](/app/ielts/skills?tab=reading). Never use any other url.
- Order by impact: many attempts + low accuracy first. Calm, constructive tone; never call the results "bad" or "very low".

Format — exactly these sections, nothing before or after:
## Summary
2-4 sentences: overall result, then every skill that has data (reading, listening, and writing / speaking bands if present).
## Strengths
Bullets, or one line (in the answer language) saying there is not enough evidence yet.
## What to fix
Up to 4 bullets: **name** — its "result" string copied exactly — the technique.
{mistake_section}## 7-day plan
A numbered list of 3-5 steps, most important first, each ending with a link.

Write in {language}; keep exam terms, question-type and skill names in English exactly as given."""

MISTAKES_SECTION = "## Mistake patterns\n2-4 bullets about patterns in recent_mistakes, quoting the student's answer vs the correct answer.\n"


def _facts(user, exam, range_key):
    if exam == 'sat':
        return _sat(user, range_key)
    return _skill_exam(user, exam, range_key)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analytics_ai(request):
    exam = str(request.data.get('exam', '')).lower()
    if exam not in ('sat', 'ielts', 'cefr'):
        return Response({'error': 'Unknown exam.'}, status=400)
    range_key = request.data.get('range', '30d')
    lang = 'en' if request.data.get('lang') == 'en' else 'uz'
    user = request.user

    facts, volume, links, hints = _facts(user, exam, range_key)
    if volume < MIN_QUESTIONS:
        return Response({'insufficient': True, 'needed': MIN_QUESTIONS, 'have': volume})

    payload = {'stats': facts, 'ALLOWED_LINKS': links}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    cache_key = f'analytics_ai:{exam}:{user.id}:{lang}:{digest}'
    cached = cache.get(cache_key)

    if request.data.get('cached_only'):
        return Response({'text': cached})

    def sse(obj):
        return f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'

    def replay(text):
        yield sse({'type': 'meta', 'cached': True})
        yield sse({'type': 'delta', 'text': text})
        yield sse({'type': 'done'})

    def stream_response(gen):
        resp = StreamingHttpResponse(gen, content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        return resp

    if cached and not request.data.get('refresh'):
        return stream_response(replay(cached))
    if not settings.OPENAI_API_KEY:
        return Response({'error': 'AI analysis is not configured.'}, status=503)

    day_key = f'analytics_ai_count:{exam}:{user.id}:{timezone.localdate().isoformat()}'
    used = cache.get(day_key, 0)
    if used >= DAILY_LIMIT:
        if cached:
            return stream_response(replay(cached))
        return Response({'error': f"Bugungi AI tahlil limiti tugadi ({DAILY_LIMIT} ta). Ertaga qayta urinib ko'ring."}, status=429)

    has_mistakes = bool(facts.get('recent_mistakes'))
    system = PROMPT.format(
        exam={'sat': 'Digital SAT', 'ielts': 'IELTS', 'cefr': 'CEFR multilevel'}[exam],
        mistakes_note=' plus the student\'s most recent wrong answers' if has_mistakes else '',
        hints=hints,
        mistake_rule=('Look at recent_mistakes and name the pattern (e.g. spelling, word limit, NOT GIVEN chosen as FALSE, '
                      'wrong part of speech) with a real example from the list.') if has_mistakes else
                     'There is no list of individual mistakes — work from the counts.',
        mistake_section=MISTAKES_SECTION if has_mistakes else '',
        language='Uzbek (Latin script)' if lang == 'uz' else 'English',
    )

    def generate():
        from openai import OpenAI
        yield sse({'type': 'meta', 'cached': False})
        text = ''
        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60)
            stream = client.chat.completions.create(
                model=MODEL, temperature=0.2, max_tokens=1300, stream=True,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, default=str)}],
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    text += delta
                    yield sse({'type': 'delta', 'text': delta})
        except Exception:
            log.exception('analytics AI failed (%s)', exam)
            yield sse({'type': 'error', 'message': "AI tahlilni hozir olib bo'lmadi. Birozdan keyin qayta urinib ko'ring."})
            return
        if text.strip():
            cache.set(cache_key, text, CACHE_SECONDS)
            cache.set(day_key, cache.get(day_key, 0) + 1, 36 * 3600)
        yield sse({'type': 'done'})

    return stream_response(generate())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def exam_analytics(request, exam):
    """GET /api/analytics/<ielts|cefr>/?range=7d|30d|90d|all"""
    from api.exam_analytics import build_exam_analytics
    exam = exam.lower()
    if exam not in ('ielts', 'cefr'):
        return Response({'error': 'Unknown exam.'}, status=404)
    return Response(build_exam_analytics(request.user, exam.upper(), request.query_params.get('range', '30d')))
