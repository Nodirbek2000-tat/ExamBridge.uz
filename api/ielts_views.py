import json
import urllib.request
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.http import HttpResponse
from ielts.models import (
    IELTSTest, ReadingPassage, ReadingQuestion, ReadingChoice,
    ListeningSection, ListeningQuestion,
    SpeakingTask, WritingTask,
    IELTSAttempt, ReadingAnswer, ListeningAnswer,
    SpeakingResponse, WritingResponse,
    BookmarkedQuestion,
)
from cefr.models import CEFRReadingQuestion, CEFRListeningQuestion, CEFRQuestion


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _academic_reading_band(correct: int) -> float:
    """Official Academic Reading band table (40 questions)"""
    if correct >= 39: return 9.0
    if correct >= 37: return 8.5
    if correct >= 35: return 8.0
    if correct >= 33: return 7.5
    if correct >= 30: return 7.0
    if correct >= 27: return 6.5
    if correct >= 23: return 6.0
    if correct >= 19: return 5.5
    if correct >= 15: return 5.0
    if correct >= 13: return 4.5
    if correct >= 10: return 4.0
    if correct >= 7:  return 3.5
    if correct >= 4:  return 3.0
    return 2.5

def _general_reading_band(correct: int) -> float:
    """Official General Training Reading band table (40 questions)"""
    if correct >= 40: return 9.0
    if correct >= 39: return 8.5
    if correct >= 37: return 8.0
    if correct >= 36: return 7.5
    if correct >= 34: return 7.0
    if correct >= 32: return 6.5
    if correct >= 30: return 6.0
    if correct >= 27: return 5.5
    if correct >= 23: return 5.0
    if correct >= 19: return 4.5
    if correct >= 15: return 4.0
    if correct >= 10: return 3.5
    if correct >= 5:  return 3.0
    return 2.5

def _listening_band(correct: int) -> float:
    """Official Listening band table (40 questions)"""
    if correct >= 39: return 9.0
    if correct >= 37: return 8.5
    if correct >= 35: return 8.0
    if correct >= 32: return 7.5
    if correct >= 30: return 7.0
    if correct >= 26: return 6.5
    if correct >= 23: return 6.0
    if correct >= 18: return 5.5
    if correct >= 16: return 5.0
    if correct >= 13: return 4.5
    if correct >= 10: return 4.0
    if correct >= 6:  return 3.5
    if correct >= 3:  return 3.0
    return 2.5

def _pct_to_band(pct):
    """Legacy fallback — use specific band functions above instead"""
    if pct >= 97: return 9.0
    if pct >= 92: return 8.5
    if pct >= 87: return 8.0
    if pct >= 82: return 7.5
    if pct >= 75: return 7.0
    if pct >= 67: return 6.5
    if pct >= 57: return 6.0
    if pct >= 47: return 5.5
    if pct >= 37: return 5.0
    if pct >= 32: return 4.5
    if pct >= 25: return 4.0
    if pct >= 20: return 3.5
    if pct >= 15: return 3.0
    return 2.5


def _check_answer(question, user_answer_raw):
    """
    Returns True if user_answer matches correct_answer.
    Handles MULTI_SELECT (pipe-separated), case-insensitive.
    """
    correct_list = question.correct_answers_list()

    if question.question_type == 'MULTI':
        user_list = sorted([a.strip().upper() for a in user_answer_raw.split('|') if a.strip()])
        return user_list == sorted(correct_list)
    else:
        return user_answer_raw.strip().upper() == (correct_list[0] if correct_list else '')


def _serialize_question(q, bookmarked_ids=None):
    return {
        'id': q.id,
        'number': q.number,
        'question_type': q.question_type,
        'question_type_display': q.get_question_type_display(),
        'content': q.content,
        'image': q.image.url if q.image else None,
        'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        'group_instruction': q.group_instruction,
        'max_selections': q.max_selections,
        'word_bank': q.word_bank,
        'answer_review': q.answer_review,
        'is_bookmarked': (q.id in bookmarked_ids) if bookmarked_ids is not None else False,
    }


# ── TESTS ────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ielts_test_list(request):
    tests = IELTSTest.objects.filter(is_active=True)
    return Response([{
        'id': t.id,
        'title': t.title,
        'test_type': t.test_type,
        'test_type_display': t.get_test_type_display(),
        'is_premium': t.is_premium,
    } for t in tests])


# ── READING ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reading_passages(request):
    standalones = ReadingPassage.objects.filter(is_standalone=True).order_by('passage_number')
    practice_completed_ids = set(
        IELTSAttempt.objects.filter(
            user=request.user, status='COMPLETED', reading_passage__isnull=False
        ).values_list('reading_passage_id', flat=True)
    )
    practices = [{
        'id': p.id,
        'title': p.title,
        'passage_number': p.passage_number,
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'is_premium': p.is_premium,
        'question_count': p.questions.count(),
        'attempted': p.id in practice_completed_ids,
    } for p in standalones]

    mock_tests = IELTSTest.objects.filter(
        is_active=True,
        passages__isnull=False
    ).prefetch_related('passages').distinct()

    completed_ids = set(
        IELTSAttempt.objects.filter(user=request.user, status='COMPLETED', test__isnull=False)
        .values_list('test_id', flat=True)
    )

    mocks = []
    for test in mock_tests:
        passages = test.passages.order_by('passage_number')
        if not passages.exists():
            continue
        total_questions = sum(p.questions.count() for p in passages)
        mocks.append({
            'id': test.id,
            'title': test.title,
            'test_type': test.test_type,
            'difficulty': passages.first().difficulty,
            'is_premium': test.is_premium,
            'part_count': passages.count(),
            'time_limit': passages.count() * 20,
            'total_questions': total_questions,
            'attempted': test.id in completed_ids,
            'parts': [{
                'id': p.id,
                'passage_number': p.passage_number,
                'title': p.title,
                'question_count': p.questions.count(),
            } for p in passages],
        })

    return Response({'practices': practices, 'mocks': mocks})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reading_passage_detail(request, passage_id):
    p = get_object_or_404(ReadingPassage, id=passage_id)

    bookmarked_ids = set(
        BookmarkedQuestion.objects.filter(
            user=request.user,
            source_type='IELTS_READING',
            question_id__in=p.questions.values_list('id', flat=True)
        ).values_list('question_id', flat=True)
    )

    questions = []
    for q in p.questions.prefetch_related('choices').all():
        questions.append(_serialize_question(q, bookmarked_ids))

    return Response({
        'id': p.id,
        'title': p.title,
        'content': p.content,
        'image': p.image.url if p.image else None,
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'questions': questions,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reading_start(request, passage_id):
    passage = get_object_or_404(ReadingPassage, id=passage_id)
    existing = IELTSAttempt.objects.filter(
        user=request.user, reading_passage=passage, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = IELTSAttempt.objects.create(user=request.user, reading_passage=passage)
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reading_mock_start(request, test_id):
    test = get_object_or_404(IELTSTest, id=test_id, is_active=True)
    existing = IELTSAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = IELTSAttempt.objects.create(user=request.user, test=test)
    passages = test.passages.order_by('passage_number')
    return Response({
        'attempt_id': attempt.id,
        'resumed': False,
        'passages': [{'id': p.id, 'passage_number': p.passage_number, 'title': p.title} for p in passages],
    }, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reading_submit(request, passage_id):
    attempt_id = request.data.get('attempt_id')
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user)
    answers = request.data.get('answers', {})

    passage = get_object_or_404(ReadingPassage, id=passage_id)
    questions = list(passage.questions.prefetch_related('choices').all())

    correct = 0
    total = len(questions)
    results = []

    for q in questions:
        user_ans_raw = str(answers.get(str(q.id), ''))
        is_correct = _check_answer(q, user_ans_raw)
        if is_correct:
            correct += 1
        ReadingAnswer.objects.update_or_create(
            attempt=attempt, question=q,
            defaults={'answer': user_ans_raw, 'is_correct': is_correct}
        )
        results.append({
            'question_id': q.id,
            'number': q.number,
            'question_type': q.question_type,
            'content': q.content,
            'user_answer': user_ans_raw,
            'correct_answer': q.correct_answer,
            'correct_answers_list': q.correct_answers_list(),
            'is_correct': is_correct,
            'explanation': q.explanation,
        })

    pct = (correct / total * 100) if total > 0 else 0
    difficulty = getattr(passage, 'difficulty', 'MEDIUM') or 'MEDIUM'
    if difficulty == 'HARD':
        band = _general_reading_band(correct)
    else:
        band = _academic_reading_band(correct)

    # For mock tests (attempt has test_id), accumulate band from ALL answers so far
    if attempt.test_id:
        all_correct = attempt.reading_answers.filter(is_correct=True).count()
        attempt.reading_band = _general_reading_band(all_correct) if difficulty == 'HARD' else _academic_reading_band(all_correct)
    else:
        attempt.reading_band = band
    attempt.save(update_fields=['reading_band'])
    attempt.complete()

    return Response({
        'correct': correct,
        'total': total,
        'score_percent': round(pct, 1),
        'band': float(band),
        'difficulty': difficulty,
        'results': results,
    })


# ── LISTENING ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listening_sections(request):
    standalones = ListeningSection.objects.filter(is_standalone=True).order_by('section_number')
    practice_completed_ids = set(
        IELTSAttempt.objects.filter(
            user=request.user, status='COMPLETED', listening_section__isnull=False
        ).values_list('listening_section_id', flat=True)
    )
    practices = [{
        'id': s.id,
        'title': s.title,
        'section_number': s.section_number,
        'has_audio': bool(s.audio_file or s.audio_url),
        'audio_url': s.audio_url or (s.audio_file.url if s.audio_file else None),
        'difficulty': s.difficulty,
        'is_premium': s.is_premium,
        'question_count': s.questions.count(),
        'attempted': s.id in practice_completed_ids,
    } for s in standalones]

    mock_tests = IELTSTest.objects.filter(
        is_active=True,
        listening_sections__isnull=False
    ).prefetch_related('listening_sections').distinct()

    completed_ids = set(
        IELTSAttempt.objects.filter(user=request.user, status='COMPLETED', test__isnull=False)
        .values_list('test_id', flat=True)
    )

    mocks = []
    for test in mock_tests:
        sections = test.listening_sections.order_by('section_number')
        if not sections.exists():
            continue
        total_questions = sum(s.questions.count() for s in sections)
        mocks.append({
            'id': test.id,
            'title': test.title,
            'test_type': test.test_type,
            'is_premium': test.is_premium,
            'section_count': sections.count(),
            'time_limit': 40,
            'total_questions': total_questions,
            'attempted': test.id in completed_ids,
            'parts': [{
                'id': s.id,
                'section_number': s.section_number,
                'title': s.title,
                'question_count': s.questions.count(),
            } for s in sections],
        })

    return Response({'practices': practices, 'mocks': mocks})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listening_section_detail(request, section_id):
    s = get_object_or_404(ListeningSection, id=section_id)

    bookmarked_ids = set(
        BookmarkedQuestion.objects.filter(
            user=request.user,
            source_type='IELTS_LISTENING',
            question_id__in=s.questions.values_list('id', flat=True)
        ).values_list('question_id', flat=True)
    )

    questions = []
    for q in s.questions.prefetch_related('choices').all():
        questions.append(_serialize_question(q, bookmarked_ids))

    # Unified audio: if the section belongs to a test with a test-level audio
    test_audio = None
    if s.test_id:
        t = s.test
        test_audio = t.audio_url or (t.audio_file.url if t.audio_file else None)

    return Response({
        'id': s.id,
        'title': s.title,
        'section_number': s.section_number,
        'part_label': f'Part {s.section_number}',
        'audio_url': s.audio_url or (s.audio_file.url if s.audio_file else None),
        'test_audio_url': test_audio,
        'transcript': s.transcript,
        'difficulty': s.difficulty,
        'questions': questions,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def listening_start(request, section_id):
    section = get_object_or_404(ListeningSection, id=section_id)
    existing = IELTSAttempt.objects.filter(
        user=request.user, listening_section=section, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = IELTSAttempt.objects.create(user=request.user, listening_section=section)
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def listening_mock_start(request, test_id):
    test = get_object_or_404(IELTSTest, id=test_id, is_active=True)
    existing = IELTSAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = IELTSAttempt.objects.create(user=request.user, test=test)
    sections = test.listening_sections.order_by('section_number')
    return Response({
        'attempt_id': attempt.id,
        'resumed': False,
        'sections': [{'id': s.id, 'section_number': s.section_number, 'title': s.title} for s in sections],
    }, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def listening_submit(request, section_id):
    attempt_id = request.data.get('attempt_id')
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user)
    answers = request.data.get('answers', {})

    section = get_object_or_404(ListeningSection, id=section_id)
    questions = list(section.questions.prefetch_related('choices').all())

    correct = 0
    total = len(questions)
    results = []

    for q in questions:
        user_ans_raw = str(answers.get(str(q.id), ''))
        is_correct = _check_answer(q, user_ans_raw)
        if is_correct:
            correct += 1
        ListeningAnswer.objects.update_or_create(
            attempt=attempt, question=q,
            defaults={'answer': user_ans_raw, 'is_correct': is_correct}
        )
        results.append({
            'question_id': q.id,
            'number': q.number,
            'question_type': q.question_type,
            'content': q.content,
            'user_answer': user_ans_raw,
            'correct_answer': q.correct_answer,
            'correct_answers_list': q.correct_answers_list(),
            'is_correct': is_correct,
            'explanation': q.explanation,
        })

    pct = (correct / total * 100) if total > 0 else 0
    band = _listening_band(correct)
    if attempt.test_id:
        all_correct = attempt.listening_answers.filter(is_correct=True).count()
        attempt.listening_band = _listening_band(all_correct)
    else:
        attempt.listening_band = band
    attempt.save(update_fields=['listening_band'])
    attempt.complete()

    return Response({
        'correct': correct,
        'total': total,
        'score_percent': round(pct, 1),
        'band': float(band),
        'results': results,
    })


# ── SPEAKING ─────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaking_tasks(request):
    """List all speaking tasks."""
    tasks = SpeakingTask.objects.all()
    part_filter = request.query_params.get('part')
    if part_filter:
        tasks = tasks.filter(part=int(part_filter))
    completed_ids = set(
        SpeakingResponse.objects.filter(attempt__user=request.user)
        .values_list('task_id', flat=True)
    )
    result = []
    for t in tasks:
        result.append({
            'id': t.id,
            'title': t.title,
            'test_type': t.test_type,
            'part': t.part,
            'topic': t.topic,
            'prompt': t.prompt,
            'questions': t.questions,
            'bullet_points': t.bullet_points,
            'follow_up': t.follow_up,
            'parts_data': t.parts_data,
            'is_premium': t.is_premium,
            'created_at': str(t.created_at)[:10],
            'attempted': t.id in completed_ids,
        })
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_tts(request):
    """Generate TTS audio via OpenAI for examiner voices."""
    text = request.data.get('text', '').strip()
    voice = request.data.get('voice', 'nova')  # alloy, echo, fable, onyx, nova, shimmer
    speed = float(request.data.get('speed', 0.92))

    if not text:
        return Response({'error': 'text required'}, status=400)

    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key or api_key == 'your-openai-api-key-here':
        return Response({'error': 'OpenAI API key not configured'}, status=503)

    payload = json.dumps({
        'model': 'tts-1',
        'input': text[:4096],
        'voice': voice,
        'speed': max(0.25, min(4.0, speed)),
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.openai.com/v1/audio/speech',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio_data = resp.read()
        http_resp = HttpResponse(audio_data, content_type='audio/mpeg')
        http_resp['Cache-Control'] = 'private, max-age=3600'
        http_resp['Content-Length'] = str(len(audio_data))
        return http_resp
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            msg = json.loads(body).get('error', {}).get('message', str(e))
        except Exception:
            msg = str(e)
        return Response({'error': msg}, status=502)
    except Exception as e:
        return Response({'error': str(e)}, status=502)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_submit(request, attempt_id):
    """Save speaking transcripts + per-question audio recordings."""
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user)
    task_id = request.data.get('task_id')

    # transcripts arrives as JSON string (multipart) or list (JSON body)
    transcripts_raw = request.data.get('transcripts', '[]')
    if isinstance(transcripts_raw, str):
        try:
            transcripts = json.loads(transcripts_raw)
        except Exception:
            transcripts = []
    else:
        transcripts = list(transcripts_raw)

    task = get_object_or_404(SpeakingTask, id=task_id)

    # Save per-question audio files
    from django.core.files.storage import default_storage
    for i, item in enumerate(transcripts):
        audio_key = f'audio_{i}'
        if audio_key in request.FILES:
            audio_file = request.FILES[audio_key]
            path = f'ielts/speaking/attempt_{attempt_id}_q{i}.webm'
            if default_storage.exists(path):
                default_storage.delete(path)
            saved_path = default_storage.save(path, audio_file)
            item['audio_url'] = request.build_absolute_uri(default_storage.url(saved_path))

    response_obj, _ = SpeakingResponse.objects.update_or_create(
        attempt=attempt,
        task=task,
        defaults={'transcripts': transcripts}
    )

    attempt.status = 'COMPLETED'
    attempt.save(update_fields=['status'])

    return Response({'id': response_obj.id, 'status': 'saved', 'transcripts': transcripts}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def speaking_ai_analyze(request):
    """Score IELTS speaking transcripts using GPT-4.1."""
    transcripts = request.data.get('transcripts', [])  # [{question, transcript}, ...]
    task_type = request.data.get('test_type', 'PART')
    parts_info = request.data.get('parts_info', '')

    if not transcripts:
        return Response({'error': 'transcripts required'}, status=400)

    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key or api_key == 'your-openai-api-key-here':
        return Response({'error': 'OpenAI API key is not configured'}, status=503)

    # Format transcript for the prompt
    transcript_text = ''
    for i, item in enumerate(transcripts, 1):
        q = item.get('question', f'Question {i}')
        a = item.get('transcript', '').strip()
        transcript_text += f'Q{i}: {q}\nA{i}: {a if a else "(no answer recorded)"}\n\n'

    system_msg = """You are an experienced, fair-minded IELTS Speaking examiner. You are having a good day and you score exactly what you hear — no more, no less.

CRITICAL SCORING RULES:
- Use 0.5 increments: 0, 0.5, 1.0 ... 9.0
- overall_band = average of 4 criterion bands, rounded to nearest 0.5
- YOU MUST GIVE 9.0 when the speaking meets Band 9 descriptors. Band 9 is NOT rare or unreachable — award it confidently when earned.
- Do NOT cap scores at 8 out of habit. If you cannot find meaningful errors in a criterion, that criterion is 8.5 or 9.
- Only penalise for errors that actually appear in the transcript

BAND ANCHORS:
Band 9.0: Speaks with complete flexibility, sophisticated vocabulary, no errors, fully coherent and fluent
Band 8.5: Near-perfect; only 1-2 trivial slips across the entire response
Band 8.0: Very fluent, wide range, only very infrequent minor errors
Band 7.5: Generally fluent with occasional hesitation, good range, errors rare in complex language
Band 7.0: Effective communication, some hesitation, some imprecision in less common vocabulary/grammar
Band 6.0: Noticeable errors and hesitation, generally gets meaning across
Band 5.0: Limited range, frequent errors, sometimes difficult to understand

NOTE: You are evaluating TEXT transcripts — judge fluency from response length, discourse markers, coherence; acknowledge pronunciation limitation in that section.

DECISION RULE: If you find yourself awarding 8 but cannot quote actual errors — raise to 9. Do not penalise for what you cannot see.

Return ONLY valid JSON, no markdown, no preamble"""

    user_msg = f"""IELTS Speaking Test Transcripts ({parts_info or task_type}):

{transcript_text}

Evaluate the candidate's spoken English based on these transcripts.

INSTRUCTIONS:
1. Read every answer. If they are fluent, varied, and error-free → give 8.5 or 9 per criterion, not 8
2. Only list errors you can actually QUOTE from the text. If you cannot quote an error, it does not exist — do not invent problems
3. Fewer quoted errors = higher band. No quoted errors = Band 8.5 or 9
4. Pronunciation: evaluated from text; focus on what is visible; give general tips
5. For answer_corrections: one entry per answer. Rewrite as fluent, natural English. If already excellent, still provide a polished version. Keep originals short (max 2 sentences).

Return this exact JSON:
{{
  "overall_band": <0-9 in 0.5 steps>,
  "fluency_coherence": {{
    "band": <0-9>,
    "label": "Fluency & Coherence",
    "feedback": "<3 sentences on response length, connected speech, coherence, fillers>",
    "strengths": ["<strength 1>", "<strength 2>"],
    "errors": [
      {{"quote": "<exact text from transcript>", "issue": "<problem>", "suggestion": "<how to improve>"}}
    ]
  }},
  "lexical_resource": {{
    "band": <0-9>,
    "label": "Lexical Resource",
    "feedback": "<3 sentences on vocabulary range, topic-specific words, collocations>",
    "strengths": ["<strength>"],
    "errors": [{{"quote": "<exact text>", "issue": "<problem>", "suggestion": "<better word/phrase>"}}]
  }},
  "grammatical_range": {{
    "band": <0-9>,
    "label": "Grammatical Range & Accuracy",
    "feedback": "<3 sentences on sentence structures, tenses, accuracy>",
    "strengths": ["<strength>"],
    "errors": [{{"quote": "<exact text>", "issue": "<grammar error>", "suggestion": "<correction>"}}]
  }},
  "pronunciation": {{
    "band": <0-9>,
    "label": "Pronunciation",
    "feedback": "<2-3 sentences — note that this is assessed from text transcript, so focus on sentence rhythm patterns visible in text, and give general pronunciation tips based on L1 interference patterns common for this level>",
    "strengths": ["<general strength>"],
    "errors": [{{"quote": "<text feature suggesting pronunciation issue>", "issue": "<likely issue>", "suggestion": "<tip>"}}]
  }},
  "answer_corrections": [
    {{
      "q_index": <1-based index matching the Q number>,
      "original": "<exact text the candidate said — keep it short, max 2 sentences>",
      "corrected": "<improved/corrected version of the same answer — natural, fluent English>",
      "note": "<1 short sentence explaining the key improvement: grammar, vocabulary, or fluency>"
    }}
  ]
}}"""

    payload = json.dumps({
        'model': 'gpt-4.1',
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
        ],
        'temperature': 0.1,
        'max_tokens': 3500,
        'response_format': {'type': 'json_object'},
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        content = data['choices'][0]['message']['content']
        result = json.loads(content)

        # Save analysis to DB if response_id provided
        response_id = request.data.get('response_id')
        if response_id:
            try:
                sr = SpeakingResponse.objects.get(id=response_id, attempt__user=request.user)
                sr.ai_band = result.get('overall_band')
                criteria_keys = ['fluency_coherence', 'lexical_resource', 'grammatical_range', 'pronunciation']
                sr.ai_criteria = {k: result[k] for k in criteria_keys if k in result}
                sr.ai_feedback = result.get('fluency_coherence', {}).get('feedback', '')
                sr.save(update_fields=['ai_band', 'ai_criteria', 'ai_feedback'])
            except SpeakingResponse.DoesNotExist:
                pass

        return Response(result)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            msg = json.loads(body).get('error', {}).get('message', str(e))
        except Exception:
            msg = str(e)
        return Response({'error': msg}, status=502)
    except Exception as e:
        return Response({'error': str(e)}, status=502)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaking_history(request):
    """User's speaking history."""
    responses = SpeakingResponse.objects.filter(
        attempt__user=request.user
    ).select_related('task', 'attempt').order_by('-created_at')[:30]

    result = []
    for r in responses:
        result.append({
            'id': r.id,
            'task_id': r.task_id,
            'task_title': r.task.title,
            'task_part': r.task.part,
            'task_test_type': r.task.test_type,
            'ai_band': str(r.ai_band) if r.ai_band else None,
            'ai_criteria': r.ai_criteria,
            'transcripts': r.transcripts or [],
            'created_at': str(r.created_at),
        })
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def speaking_review(request, response_id):
    """Get full speaking review with transcripts, audio URLs, and AI analysis."""
    sr = get_object_or_404(SpeakingResponse, id=response_id, attempt__user=request.user)
    return Response({
        'id': sr.id,
        'task_id': sr.task_id,
        'task_title': sr.task.title,
        'task_part': sr.task.part,
        'task_test_type': sr.task.test_type,
        'transcripts': sr.transcripts,
        'ai_feedback': sr.ai_feedback,
        'ai_band': str(sr.ai_band) if sr.ai_band else None,
        'ai_criteria': sr.ai_criteria,
        'created_at': str(sr.created_at),
    })


# ── WRITING ─────────────────────────────────────────────────────────────────

def _serialize_writing_task(t, request=None):
    image_url = None
    if t.image:
        image_url = request.build_absolute_uri(t.image.url) if request else t.image.url
    return {
        'id': t.id,
        'title': t.title,
        'task_type': t.task_type,
        'test_type': t.test_type,
        'difficulty': t.difficulty,
        'prompt': t.prompt,
        'image': image_url,
        'recommendations': t.recommendations or [],
        'min_words': t.min_words,
        'time_limit': t.time_limit,
        'is_premium': t.is_premium,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_tasks(request):
    task_type  = request.query_params.get('task_type')
    difficulty = request.query_params.get('difficulty')
    tasks = WritingTask.objects.all()
    if task_type:
        tasks = tasks.filter(task_type=task_type)
    if difficulty:
        tasks = tasks.filter(difficulty=difficulty.upper())
    completed_ids = set(
        WritingResponse.objects.filter(attempt__user=request.user)
        .values_list('task_id', flat=True)
    )
    result = []
    for t in tasks:
        d = _serialize_writing_task(t, request)
        d['attempted'] = t.id in completed_ids
        result.append(d)
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_start(request, task_id):
    """Start (or resume) a writing attempt for a given task."""
    task = get_object_or_404(WritingTask, id=task_id)
    existing = WritingResponse.objects.filter(
        attempt__user=request.user,
        task=task,
        attempt__status='IN_PROGRESS',
    ).select_related('attempt').first()
    if existing:
        return Response({
            'attempt_id': existing.attempt.id,
            'response_id': existing.id,
            'task': _serialize_writing_task(task, request),
            'resumed': True,
        })
    attempt = IELTSAttempt.objects.create(user=request.user)
    return Response({
        'attempt_id': attempt.id,
        'task': _serialize_writing_task(task, request),
        'resumed': False,
    }, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_submit(request, attempt_id):
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user)
    task_id = request.data.get('task_id')
    text = request.data.get('text', '')
    task = get_object_or_404(WritingTask, id=task_id)

    response = WritingResponse.objects.create(
        attempt=attempt,
        task=task,
        response_text=text,
    )
    from api.tasks import evaluate_writing
    evaluate_writing.delay(response.id)
    return Response({'id': response.id, 'word_count': response.word_count, 'status': 'processing'}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_result(request, response_id):
    response = get_object_or_404(WritingResponse, id=response_id, attempt__user=request.user)
    return Response({
        'id': response.id,
        'task_id': response.task_id,
        'task_title': response.task.title,
        'task_type': response.task.task_type,
        'task_prompt': response.task.prompt,
        'response_text': response.response_text,
        'word_count': response.word_count,
        'ai_feedback': response.ai_feedback,
        'ai_band': str(response.ai_band) if response.ai_band else None,
        'ai_criteria': response.ai_criteria,
        'status': 'ready' if response.ai_feedback else 'processing',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_history(request):
    """GET /api/ielts/writing/history/"""
    responses = WritingResponse.objects.filter(
        attempt__user=request.user
    ).select_related('task', 'attempt').order_by('-created_at')[:50]
    result = []
    for r in responses:
        result.append({
            'id': r.id,
            'task_id': r.task_id,
            'task_title': r.task.title,
            'task_type': r.task.task_type,
            'test_type': r.task.test_type,
            'ai_band': str(r.ai_band) if r.ai_band else None,
            'ai_criteria': r.ai_criteria,
            'word_count': r.word_count,
            'created_at': str(r.created_at),
        })
    return Response(result)


# ── WRITING AI ANALYSIS ───────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def writing_ai_analyze(request):
    """Call OpenAI to score an IELTS writing response on 4 criteria."""
    text       = request.data.get('text', '').strip()
    task_type  = request.data.get('task_type', 2)
    prompt_txt = request.data.get('prompt', '')
    word_count = request.data.get('word_count', len(text.split()))
    own_title  = request.data.get('own_title', '')
    min_words  = 150 if task_type == 1 else 250

    if not text:
        return Response({'error': 'text is required'}, status=400)

    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key or api_key == 'your-openai-api-key-here':
        return Response({'error': 'OpenAI API key is not configured'}, status=503)

    task_label = 'Task 1 (Academic – Graph/Chart Description)' if task_type == 1 else 'Task 2 (Essay)'
    ta_label   = 'Task Achievement' if task_type == 1 else 'Task Response'
    effective_prompt = prompt_txt or own_title or '(not provided)'

    word_penalty = ''
    if word_count < min_words:
        deficit = min_words - word_count
        word_penalty = (
            f'\nCRITICAL: The response is {deficit} words SHORT of the minimum {min_words}. '
            f'This MUST significantly lower the Task {"Achievement" if task_type == 1 else "Response"} band score.'
        )

    system_msg = """You are an experienced, fair-minded IELTS Writing examiner. You are having a good day and you score exactly what you see — no more, no less.

CRITICAL SCORING RULES:
- Use 0.5 increments: 0, 0.5, 1.0 … 9.0
- overall_band = average of 4 criterion bands, rounded to nearest 0.5
- YOU MUST GIVE 9.0 when the writing meets Band 9 descriptors. Band 9 is NOT rare — it simply means the task is fully and skillfully completed with no significant errors. Do NOT treat 9 as unreachable.
- YOU MUST GIVE 8.5 when 3 criteria are 9 and 1 is 8, or similar combinations averaging 8.5
- Do NOT cap scores at 8 out of habit. If you find yourself giving 8 when you cannot identify meaningful errors, the correct score is 9.
- Only deduct for errors that actually appear in the text and genuinely affect quality

BAND ANCHORS — apply these literally:
Band 9.0: Task fully and precisely addressed, ideas well developed, virtually no errors of any kind, wide sophisticated vocabulary, full range of accurate complex structures
Band 8.5: Meets Band 9 in most ways; only 1-2 very minor imperfections across the whole response
Band 8.0: All task requirements met skillfully, very infrequent minor errors, good range of structures and vocabulary
Band 7.5: Addresses task well, mostly fluent, occasional errors in complex language but meaning always clear
Band 7.0: Addresses all parts adequately, clear progression, some imprecision in less common vocabulary/grammar
Band 6.0: Generally addresses task, relevant but not always developed, noticeable errors but meaning clear
Band 5.0: Partially addresses task, limited range, errors sometimes obscure meaning

DECISION RULE: After scoring, ask yourself — "Are there actual errors in this text that prevent a higher band?" If the answer is no, raise the band. Do not penalise for perfection.

Return ONLY valid JSON — no markdown, no code fences, no preamble."""

    user_msg = f"""IELTS Writing {task_label}

--- TASK PROMPT ---
{effective_prompt}
--- END PROMPT ---

--- STUDENT RESPONSE ({word_count} words) ---
{text}
--- END RESPONSE ---

Minimum words: {min_words} | Actual words: {word_count}{word_penalty}

INSTRUCTIONS:
1. Read the response carefully. Score each criterion against the band anchors — not against an imaginary "perfect" standard
2. If the response fully addresses the task with clear ideas and very few/no real errors → give 8.5 or 9, not 8
3. For EACH criterion: note genuine strengths, then list only REAL errors you actually found in the text with exact quotes
4. If you cannot find meaningful errors for a criterion, that criterion is Band 8.5 or 9
5. Never say "there are some errors" if you cannot quote them — honesty means awarding the score the text deserves

Return this exact JSON (no extra fields):
{{
  "overall_band": <0–9 in 0.5 steps, computed as avg of 4 bands>,
  "task_achievement": {{
    "band": <0–9 in 0.5 steps>,
    "label": "{ta_label}",
    "feedback": "<3 sentences: (1) how well task is addressed, (2) key strengths, (3) main weakness>",
    "strengths": ["<concrete strength from the text, max 2 items>"],
    "errors": [
      {{"quote": "<exact phrase from essay>", "issue": "<precise problem>", "suggestion": "<corrected version or advice>"}},
      {{"quote": "<exact phrase>", "issue": "<precise problem>", "suggestion": "<fix>"}}
    ]
  }},
  "coherence_cohesion": {{
    "band": <0–9 in 0.5 steps>,
    "label": "Coherence & Cohesion",
    "feedback": "<3 sentences on paragraphing, logical flow, cohesive devices>",
    "strengths": ["<max 2>"],
    "errors": [
      {{"quote": "<exact phrase or describe the structural issue>", "issue": "<why it hurts cohesion>", "suggestion": "<how to fix>"}},
      {{"quote": "<exact phrase>", "issue": "<problem>", "suggestion": "<fix>"}}
    ]
  }},
  "lexical_resource": {{
    "band": <0–9 in 0.5 steps>,
    "label": "Lexical Resource",
    "feedback": "<3 sentences on vocab range, precision, collocations, spelling>",
    "strengths": ["<max 2>"],
    "errors": [
      {{"quote": "<exact wrong word/phrase from text>", "issue": "<why it is wrong: e.g., wrong collocation, spelling, overused>", "suggestion": "<correct alternative>"}},
      {{"quote": "<exact phrase>", "issue": "<problem>", "suggestion": "<fix>"}}
    ]
  }},
  "grammatical_range": {{
    "band": <0–9 in 0.5 steps>,
    "label": "Grammatical Range & Accuracy",
    "feedback": "<3 sentences on sentence variety, tenses, articles, prepositions, error rate>",
    "strengths": ["<max 2>"],
    "errors": [
      {{"quote": "<exact grammatically wrong sentence or phrase>", "issue": "<grammar rule violated>", "suggestion": "<corrected version>"}},
      {{"quote": "<exact phrase>", "issue": "<problem>", "suggestion": "<corrected version>"}}
    ]
  }}
}}"""

    payload = json.dumps({
        'model': 'gpt-4.1',
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user',   'content': user_msg},
        ],
        'temperature': 0.1,
        'max_tokens': 3000,
        'response_format': {'type': 'json_object'},
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        content = data['choices'][0]['message']['content']
        result  = json.loads(content)
        return Response(result)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            msg = json.loads(body).get('error', {}).get('message', str(e))
        except Exception:
            msg = str(e)
        return Response({'error': msg}, status=502)
    except Exception as e:
        return Response({'error': str(e)}, status=502)


# ── ATTEMPT ──────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ielts_start_attempt(request):
    test_id = request.data.get('test_id')
    test = get_object_or_404(IELTSTest, id=test_id) if test_id else None

    existing = IELTSAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})

    attempt = IELTSAttempt.objects.create(user=request.user, test=test)
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ielts_security_event(request, attempt_id):
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user)
    event = {
        'type': request.data.get('event_type'),
        'timestamp': request.data.get('timestamp'),
    }
    if event['type'] == 'TAB_SWITCH':
        attempt.tab_switches += 1
    elif event['type'] == 'FULLSCREEN_EXIT':
        attempt.fullscreen_exits += 1
    attempt.security_events.append(event)
    attempt.save(update_fields=['tab_switches', 'fullscreen_exits', 'security_events'])
    return Response({'logged': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ielts_stats(request):
    attempts = IELTSAttempt.objects.filter(user=request.user, status='COMPLETED')
    if not attempts.exists():
        return Response({'attempts': 0, 'best_band': None})

    bands = [float(a.overall_band) for a in attempts if a.overall_band]
    reading_bands = [float(a.reading_band) for a in attempts if a.reading_band]
    listening_bands = [float(a.listening_band) for a in attempts if a.listening_band]

    return Response({
        'attempts': attempts.count(),
        'best_band': max(bands) if bands else None,
        'avg_band': round(sum(bands) / len(bands), 1) if bands else None,
        'best_reading': max(reading_bands) if reading_bands else None,
        'best_listening': max(listening_bands) if listening_bands else None,
    })


# ── HISTORY ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ielts_history(request):
    """
    GET /api/ielts/history/?type=reading|listening|all
    Returns all completed attempts for the user.
    """
    section_type = request.query_params.get('type', 'all')
    attempts = IELTSAttempt.objects.filter(
        user=request.user, status='COMPLETED'
    ).select_related('test', 'reading_passage', 'listening_section').order_by('-finished_at')

    def _time_spent(a):
        if a.finished_at and a.started_at:
            return int((a.finished_at - a.started_at).total_seconds())
        return None

    result = []
    for a in attempts:
        if a.reading_passage_id and section_type in ('all', 'reading'):
            result.append({
                'id': a.id,
                'type': 'reading',
                'label': 'Practice Reading',
                'title': a.reading_passage.title if a.reading_passage else 'Reading',
                'passage_id': a.reading_passage_id,
                'band': float(a.reading_band) if a.reading_band else None,
                'correct': a.reading_answers.filter(is_correct=True).count(),
                'total': a.reading_answers.count(),
                'finished_at': a.finished_at,
                'time_spent': _time_spent(a),
            })
        elif a.listening_section_id and section_type in ('all', 'listening'):
            result.append({
                'id': a.id,
                'type': 'listening',
                'label': 'Practice Listening',
                'title': a.listening_section.title if a.listening_section else 'Listening',
                'section_id': a.listening_section_id,
                'section_number': a.listening_section.section_number if a.listening_section else None,
                'band': float(a.listening_band) if a.listening_band else None,
                'correct': a.listening_answers.filter(is_correct=True).count(),
                'total': a.listening_answers.count(),
                'finished_at': a.finished_at,
                'time_spent': _time_spent(a),
            })
        elif a.test_id and section_type in ('all', 'reading', 'listening'):
            reading_correct = a.reading_answers.filter(is_correct=True).count()
            reading_total = a.reading_answers.count()
            listening_correct = a.listening_answers.filter(is_correct=True).count()
            listening_total = a.listening_answers.count()
            result.append({
                'id': a.id,
                'type': 'mock',
                'label': 'Mock Test',
                'title': a.test.title if a.test else 'Mock Test',
                'test_id': a.test_id,
                'reading_band': float(a.reading_band) if a.reading_band else None,
                'listening_band': float(a.listening_band) if a.listening_band else None,
                'overall_band': float(a.overall_band) if a.overall_band else None,
                'reading_correct': reading_correct,
                'reading_total': reading_total,
                'listening_correct': listening_correct,
                'listening_total': listening_total,
                'finished_at': a.finished_at,
                'time_spent': _time_spent(a),
            })

    return Response(result)


# ── ATTEMPT REVIEW ───────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reading_attempt_review(request, attempt_id):
    """
    GET /api/ielts/attempt/<id>/reading-review/
    Full review: passage content + each question with correct/wrong answers.
    """
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user, status='COMPLETED')

    if attempt.reading_passage_id:
        passages = [attempt.reading_passage]
    elif attempt.test_id:
        passages = list(attempt.test.passages.order_by('passage_number'))
    else:
        passages = []

    user_answers = {ra.question_id: ra for ra in attempt.reading_answers.all()}
    passages_data = []

    for passage in passages:
        questions_data = []
        for q in passage.questions.prefetch_related('choices').order_by('number'):
            ua = user_answers.get(q.id)
            questions_data.append({
                'id': q.id,
                'number': q.number,
                'question_type': q.question_type,
                'question_type_display': q.get_question_type_display(),
                'content': q.content,
                'group_instruction': q.group_instruction,
                'max_selections': q.max_selections,
                'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                'user_answer': ua.answer if ua else '',
                'correct_answer': q.correct_answer,
                'correct_answers_list': q.correct_answers_list(),
                'is_correct': ua.is_correct if ua else False,
                'explanation': q.explanation,
            })
        passages_data.append({
            'id': passage.id,
            'passage_number': passage.passage_number,
            'title': passage.title,
            'content': passage.content,
            'image': passage.image.url if passage.image else None,
            'questions': questions_data,
        })

    correct = sum(1 for ua in user_answers.values() if ua.is_correct)
    total = len(user_answers)

    flat_results = []
    for p in passages_data:
        for q in p['questions']:
            flat_results.append({
                'question_id': q['id'],
                'number': q['number'],
                'passage_id': p['id'],
                'user_answer': q['user_answer'],
                'correct_answer': q['correct_answer'],
                'is_correct': q['is_correct'],
            })

    return Response({
        'attempt_id': attempt.id,
        'finished_at': attempt.finished_at,
        'band': float(attempt.reading_band) if attempt.reading_band else None,
        'correct': correct,
        'total': total,
        'score_percent': round(correct / total * 100, 1) if total else 0,
        'results': flat_results,
        'passages': passages_data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listening_attempt_review(request, attempt_id):
    """
    GET /api/ielts/attempt/<id>/listening-review/
    Full review: transcript with [N] markers + each question with answers.
    """
    attempt = get_object_or_404(IELTSAttempt, id=attempt_id, user=request.user, status='COMPLETED')

    if attempt.listening_section_id:
        sections = [attempt.listening_section]
    elif attempt.test_id:
        sections = list(attempt.test.listening_sections.order_by('section_number'))
    else:
        sections = []

    user_answers = {la.question_id: la for la in attempt.listening_answers.all()}
    sections_data = []

    for section in sections:
        questions_data = []
        for q in section.questions.prefetch_related('choices').order_by('number'):
            ua = user_answers.get(q.id)
            questions_data.append({
                'id': q.id,
                'number': q.number,
                'question_type': q.question_type,
                'question_type_display': q.get_question_type_display(),
                'content': q.content,
                'group_instruction': q.group_instruction,
                'max_selections': q.max_selections,
                'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                'user_answer': ua.answer if ua else '',
                'correct_answer': q.correct_answer,
                'correct_answers_list': q.correct_answers_list(),
                'is_correct': ua.is_correct if ua else False,
                'explanation': q.explanation,
            })
        sections_data.append({
            'id': section.id,
            'section_number': section.section_number,
            'title': section.title,
            'audio_url': section.audio_url or (section.audio_file.url if section.audio_file else None),
            'transcript': section.transcript,
            'questions': questions_data,
        })

    correct = sum(1 for ua in user_answers.values() if ua.is_correct)
    total = len(user_answers)

    flat_results = []
    for s in sections_data:
        for q in s['questions']:
            flat_results.append({
                'question_id': q['id'],
                'number': q['number'],
                'user_answer': q['user_answer'],
                'correct_answer': q['correct_answer'],
                'is_correct': q['is_correct'],
            })

    return Response({
        'attempt_id': attempt.id,
        'finished_at': attempt.finished_at,
        'band': float(attempt.listening_band) if attempt.listening_band else None,
        'correct': correct,
        'total': total,
        'score_percent': round(correct / total * 100, 1) if total else 0,
        'results': flat_results,
        'sections': sections_data,
    })


# ── BOOKMARKS ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bookmark_list(request):
    """GET /api/ielts/bookmarks/ — All bookmarked questions with full data."""
    bookmarks = BookmarkedQuestion.objects.filter(user=request.user).order_by('-created_at')

    result = []
    for bm in bookmarks:
        item = {
            'id': bm.id,
            'source_type': bm.source_type,
            'question_id': bm.question_id,
            'created_at': bm.created_at,
        }
        if bm.source_type == 'IELTS_READING':
            try:
                q = ReadingQuestion.objects.prefetch_related('choices').get(id=bm.question_id)
                item['question'] = {
                    'id': q.id, 'number': q.number,
                    'question_type': q.question_type,
                    'question_type_display': q.get_question_type_display(),
                    'content': q.content,
                    'correct_answer': q.correct_answer,
                    'explanation': q.explanation,
                    'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                    'passage_title': q.passage.title,
                    'passage_id': q.passage_id,
                }
            except ReadingQuestion.DoesNotExist:
                item['question'] = None
        elif bm.source_type == 'IELTS_LISTENING':
            try:
                q = ListeningQuestion.objects.prefetch_related('choices').get(id=bm.question_id)
                item['question'] = {
                    'id': q.id, 'number': q.number,
                    'question_type': q.question_type,
                    'question_type_display': q.get_question_type_display(),
                    'content': q.content,
                    'correct_answer': q.correct_answer,
                    'explanation': q.explanation,
                    'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                    'section_title': q.section.title,
                    'section_id': q.section_id,
                }
            except ListeningQuestion.DoesNotExist:
                item['question'] = None
        elif bm.source_type == 'CEFR':
            # Try all CEFR question models in order
            q = None
            source_label = ''
            try:
                q = CEFRReadingQuestion.objects.prefetch_related('choices').get(id=bm.question_id)
                source_label = q.passage.title
                item['question'] = {
                    'id': q.id, 'number': q.number,
                    'question_type': q.question_type,
                    'question_type_display': q.get_question_type_display(),
                    'content': q.content,
                    'correct_answer': q.correct_answer,
                    'explanation': q.explanation,
                    'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                    'source_label': source_label,
                    'cefr_model': 'reading',
                }
            except CEFRReadingQuestion.DoesNotExist:
                pass
            if q is None:
                try:
                    q = CEFRListeningQuestion.objects.prefetch_related('choices').get(id=bm.question_id)
                    source_label = q.section.title
                    item['question'] = {
                        'id': q.id, 'number': q.number,
                        'question_type': q.question_type,
                        'question_type_display': q.get_question_type_display(),
                        'content': q.content,
                        'correct_answer': q.correct_answer,
                        'explanation': q.explanation,
                        'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                        'source_label': source_label,
                        'cefr_model': 'listening',
                    }
                except CEFRListeningQuestion.DoesNotExist:
                    pass
            if q is None:
                try:
                    q = CEFRQuestion.objects.prefetch_related('choices').get(id=bm.question_id)
                    source_label = q.test.title if hasattr(q, 'test') else 'CEFR Grammar'
                    item['question'] = {
                        'id': q.id, 'number': q.number,
                        'question_type': q.question_type,
                        'question_type_display': q.get_question_type_display(),
                        'content': q.content,
                        'correct_answer': q.correct_answer,
                        'explanation': q.explanation,
                        'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                        'source_label': source_label,
                        'cefr_model': 'grammar',
                    }
                except CEFRQuestion.DoesNotExist:
                    item['question'] = None
        result.append(item)

    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bookmark_toggle(request):
    """
    POST /api/ielts/bookmarks/toggle/
    Body: {"source_type": "IELTS_READING", "question_id": 5}
    Toggles bookmark. Returns {"bookmarked": true/false}
    """
    source_type = request.data.get('source_type')
    question_id = request.data.get('question_id')

    if source_type not in ('IELTS_READING', 'IELTS_LISTENING', 'CEFR'):
        return Response({'error': 'Invalid source_type'}, status=400)
    if not question_id:
        return Response({'error': 'question_id required'}, status=400)

    bm, created = BookmarkedQuestion.objects.get_or_create(
        user=request.user,
        source_type=source_type,
        question_id=question_id,
    )
    if not created:
        bm.delete()
        return Response({'bookmarked': False})
    return Response({'bookmarked': True}, status=201)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def bookmark_delete(request, bookmark_id):
    bm = get_object_or_404(BookmarkedQuestion, id=bookmark_id, user=request.user)
    bm.delete()
    return Response({'deleted': True})


# ── PERFORMANCE ANALYSIS ─────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ielts_analysis(request):
    """
    GET /api/ielts/analysis/
    Performance breakdown by question type + band score chart.
    """
    reading_answers = ReadingAnswer.objects.filter(
        attempt__user=request.user,
        attempt__status='COMPLETED'
    ).select_related('question')

    reading_by_type = {}
    for ra in reading_answers:
        qt = ra.question.question_type
        if qt not in reading_by_type:
            reading_by_type[qt] = {
                'type': qt,
                'display': ra.question.get_question_type_display(),
                'correct': 0, 'total': 0
            }
        reading_by_type[qt]['total'] += 1
        if ra.is_correct:
            reading_by_type[qt]['correct'] += 1

    listening_answers = ListeningAnswer.objects.filter(
        attempt__user=request.user,
        attempt__status='COMPLETED'
    ).select_related('question')

    listening_by_type = {}
    for la in listening_answers:
        qt = la.question.question_type
        if qt not in listening_by_type:
            listening_by_type[qt] = {
                'type': qt,
                'display': la.question.get_question_type_display(),
                'correct': 0, 'total': 0
            }
        listening_by_type[qt]['total'] += 1
        if la.is_correct:
            listening_by_type[qt]['correct'] += 1

    def enrich(d):
        for v in d.values():
            v['accuracy'] = round(v['correct'] / v['total'] * 100, 1) if v['total'] else 0
        return sorted(d.values(), key=lambda x: x['accuracy'])

    attempts_qs = IELTSAttempt.objects.filter(
        user=request.user, status='COMPLETED'
    ).order_by('finished_at').values(
        'id', 'finished_at', 'reading_band', 'listening_band', 'overall_band'
    )

    return Response({
        'reading': {
            'by_type': enrich(reading_by_type),
            'total_correct': sum(v['correct'] for v in reading_by_type.values()),
            'total_questions': sum(v['total'] for v in reading_by_type.values()),
        },
        'listening': {
            'by_type': enrich(listening_by_type),
            'total_correct': sum(v['correct'] for v in listening_by_type.values()),
            'total_questions': sum(v['total'] for v in listening_by_type.values()),
        },
        'attempts_chart': [{
            'id': a['id'],
            'finished_at': a['finished_at'],
            'reading_band': float(a['reading_band']) if a['reading_band'] else None,
            'listening_band': float(a['listening_band']) if a['listening_band'] else None,
            'overall_band': float(a['overall_band']) if a['overall_band'] else None,
        } for a in attempts_qs],
    })
