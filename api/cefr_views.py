from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from cefr.models import (
    CEFRTest, CEFRQuestion, CEFRChoice,
    CEFRReadingPassage, CEFRReadingQuestion, CEFRReadingChoice,
    CEFRListeningSection, CEFRListeningQuestion, CEFRListeningChoice,
    CEFRAttempt, CEFRAnswer, CEFRReadingAnswer, CEFRListeningAnswer,
)
from ielts.models import BookmarkedQuestion


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _check_answer_cefr(question, user_answer_raw):
    if question.question_type == 'MULTI':
        correct_list = sorted([a.strip().upper() for a in question.correct_answer.split('|') if a.strip()])
        user_list = sorted([a.strip().upper() for a in user_answer_raw.split('|') if a.strip()])
        return user_list == correct_list
    return user_answer_raw.strip().upper() == question.correct_answer.strip().upper()


def _serialize_cefr_question(q, bookmarked_ids=None):
    return {
        'id': q.id,
        'number': q.number,
        'question_type': q.question_type,
        'question_type_display': q.get_question_type_display(),
        'content': q.content,
        'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        'group_instruction': getattr(q, 'group_instruction', ''),
        'max_selections': getattr(q, 'max_selections', 1),
        'image': q.image.url if getattr(q, 'image', None) else None,
        'word_bank': getattr(q, 'word_bank', []) or [],
        'answer_review': getattr(q, 'answer_review', ''),
        'is_bookmarked': (q.id in bookmarked_ids) if bookmarked_ids is not None else False,
    }


# ── GRAMMAR / VOCABULARY TESTS ───────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_test_list(request):
    level = request.query_params.get('level')
    test_type = request.query_params.get('test_type')
    tests = CEFRTest.objects.filter(is_active=True)
    if level:
        tests = tests.filter(level=level)
    if test_type:
        tests = tests.filter(test_type=test_type)

    completed_ids = set(
        CEFRAttempt.objects.filter(user=request.user, status='COMPLETED')
        .values_list('test_id', flat=True)
    )

    return Response([{
        'id': t.id,
        'title': t.title,
        'level': t.level,
        'test_type': t.test_type,
        'test_type_display': t.get_test_type_display(),
        'time_limit': t.time_limit,
        'is_premium': t.is_premium,
        'question_count': t.questions.count(),
        'attempted': t.id in completed_ids,
    } for t in tests])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_test_detail(request, test_id):
    test = get_object_or_404(CEFRTest, id=test_id, is_active=True)

    bookmarked_ids = set(
        BookmarkedQuestion.objects.filter(
            user=request.user,
            source_type='CEFR',
            question_id__in=test.questions.values_list('id', flat=True)
        ).values_list('question_id', flat=True)
    )

    questions = []
    for q in test.questions.prefetch_related('choices').all():
        qdata = _serialize_cefr_question(q, bookmarked_ids)
        qdata['passage'] = q.passage
        qdata['image'] = q.image.url if q.image else None
        questions.append(qdata)

    return Response({
        'id': test.id,
        'title': test.title,
        'level': test.level,
        'test_type': test.test_type,
        'time_limit': test.time_limit,
        'questions': questions,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_start_attempt(request, test_id):
    test = get_object_or_404(CEFRTest, id=test_id, is_active=True)
    if test.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'detail': 'Premium required.'}, status=403)

    existing = CEFRAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})

    attempt = CEFRAttempt.objects.create(
        user=request.user, test=test, attempt_type='GRAMMAR'
    )
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_submit_attempt(request, attempt_id):
    attempt = get_object_or_404(CEFRAttempt, id=attempt_id, user=request.user, status='IN_PROGRESS')
    answers_data = request.data.get('answers', [])

    correct = 0
    total = 0
    for item in answers_data:
        q = get_object_or_404(CEFRQuestion, id=item['question_id'])
        is_correct = _check_answer_cefr(q, item.get('answer', ''))
        CEFRAnswer.objects.update_or_create(
            attempt=attempt, question=q,
            defaults={'answer': item.get('answer', ''), 'is_correct': is_correct},
        )
        correct += int(is_correct)
        total += 1

    score_percent = (correct / total * 100) if total else 0
    attempt.score_percent = score_percent
    attempt.correct_count = correct
    attempt.total_count = total
    attempt.complete()

    return Response({
        'score_percent': round(score_percent, 1),
        'correct': correct,
        'total': total,
        'level': attempt.test.level if attempt.test else '',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_attempt_review(request, attempt_id):
    """GET /api/cefr/attempt/<id>/review/ — Full review with answers."""
    attempt = get_object_or_404(CEFRAttempt, id=attempt_id, user=request.user, status='COMPLETED')

    if attempt.attempt_type == 'GRAMMAR' and attempt.test_id:
        user_answers = {a.question_id: a for a in attempt.answers.all()}
        questions_data = []
        for q in attempt.test.questions.prefetch_related('choices').order_by('number'):
            ua = user_answers.get(q.id)
            questions_data.append({
                'id': q.id, 'number': q.number,
                'question_type': q.question_type,
                'question_type_display': q.get_question_type_display(),
                'content': q.content,
                'passage': q.passage,
                'group_instruction': q.group_instruction,
                'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
                'user_answer': ua.answer if ua else '',
                'correct_answer': q.correct_answer,
                'is_correct': ua.is_correct if ua else False,
                'explanation': q.explanation,
            })
        return Response({
            'attempt_id': attempt.id,
            'attempt_type': 'GRAMMAR',
            'finished_at': attempt.finished_at,
            'score_percent': attempt.score_percent,
            'correct': attempt.correct_count,
            'total': attempt.total_count,
            'level': attempt.test.level if attempt.test else '',
            'questions': questions_data,
        })

    elif attempt.attempt_type == 'READING':
        if attempt.reading_passage_id:
            passages = [attempt.reading_passage]
        elif attempt.test_id:
            passages = list(attempt.test.reading_passages.order_by('passage_number'))
        else:
            passages = []

        user_answers = {a.question_id: a for a in attempt.reading_answers.all()}
        passages_data = []
        for passage in passages:
            questions_data = []
            for q in passage.questions.prefetch_related('choices').order_by('number'):
                ua = user_answers.get(q.id)
                questions_data.append({
                    'id': q.id, 'number': q.number,
                    'question_type': q.question_type,
                    'question_type_display': q.get_question_type_display(),
                    'content': q.content,
                    'group_instruction': q.group_instruction,
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
        return Response({
            'attempt_id': attempt.id,
            'attempt_type': 'READING',
            'finished_at': attempt.finished_at,
            'score_percent': round(correct / total * 100, 1) if total else 0,
            'correct': correct,
            'total': total,
            'passages': passages_data,
        })

    elif attempt.attempt_type == 'LISTENING':
        if attempt.listening_section_id:
            sections = [attempt.listening_section]
        elif attempt.test_id:
            sections = list(attempt.test.listening_sections.order_by('section_number'))
        else:
            sections = []

        user_answers = {a.question_id: a for a in attempt.listening_answers.all()}
        sections_data = []
        for section in sections:
            questions_data = []
            for q in section.questions.prefetch_related('choices').order_by('number'):
                ua = user_answers.get(q.id)
                questions_data.append({
                    'id': q.id, 'number': q.number,
                    'question_type': q.question_type,
                    'question_type_display': q.get_question_type_display(),
                    'content': q.content,
                    'group_instruction': q.group_instruction,
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
        return Response({
            'attempt_id': attempt.id,
            'attempt_type': 'LISTENING',
            'finished_at': attempt.finished_at,
            'score_percent': round(correct / total * 100, 1) if total else 0,
            'correct': correct,
            'total': total,
            'sections': sections_data,
        })

    return Response({'error': 'Unknown attempt type'}, status=400)


# ── READING ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_reading_list(request):
    level = request.query_params.get('level')
    qs = CEFRReadingPassage.objects.all()
    if level:
        qs = qs.filter(level=level)

    return Response([{
        'id': p.id,
        'title': p.title,
        'level': p.level,
        'passage_number': p.passage_number,
        'question_count': p.questions.count(),
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'is_premium': p.is_premium,
        'is_mock': p.is_mock,
        'is_standalone': p.is_standalone,
    } for p in qs])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_reading_detail(request, passage_id):
    p = get_object_or_404(CEFRReadingPassage, id=passage_id)

    bookmarked_ids = set(
        BookmarkedQuestion.objects.filter(
            user=request.user, source_type='CEFR',
            question_id__in=p.questions.values_list('id', flat=True)
        ).values_list('question_id', flat=True)
    )

    questions = [_serialize_cefr_question(q, bookmarked_ids)
                 for q in p.questions.prefetch_related('choices').all()]

    return Response({
        'id': p.id, 'title': p.title, 'content': p.content,
        'level': p.level, 'time_limit': p.time_limit,
        'image': p.image.url if p.image else None,
        'questions': questions,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_reading_start(request, passage_id):
    passage = get_object_or_404(CEFRReadingPassage, id=passage_id)
    existing = CEFRAttempt.objects.filter(
        user=request.user, reading_passage=passage, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = CEFRAttempt.objects.create(
        user=request.user, reading_passage=passage, attempt_type='READING'
    )
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_reading_submit(request, passage_id):
    attempt_id = request.data.get('attempt_id')
    attempt = get_object_or_404(CEFRAttempt, id=attempt_id, user=request.user)
    answers = request.data.get('answers', {})

    passage = get_object_or_404(CEFRReadingPassage, id=passage_id)
    questions = list(passage.questions.prefetch_related('choices').all())

    correct = 0
    total = len(questions)
    results = []

    for q in questions:
        user_ans = str(answers.get(str(q.id), ''))
        is_correct = _check_answer_cefr(q, user_ans)
        if is_correct:
            correct += 1
        CEFRReadingAnswer.objects.update_or_create(
            attempt=attempt, question=q,
            defaults={'answer': user_ans, 'is_correct': is_correct}
        )
        results.append({
            'question_id': q.id, 'number': q.number,
            'user_answer': user_ans,
            'correct_answer': q.correct_answer,
            'correct_answers_list': q.correct_answers_list(),
            'is_correct': is_correct,
            'explanation': q.explanation,
        })

    score_percent = (correct / total * 100) if total else 0
    attempt.score_percent = score_percent
    attempt.correct_count = correct
    attempt.total_count = total
    attempt.complete()

    return Response({
        'correct': correct, 'total': total,
        'score_percent': round(score_percent, 1),
        'results': results,
    })


# ── LISTENING ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_listening_list(request):
    level = request.query_params.get('level')
    qs = CEFRListeningSection.objects.all()
    if level:
        qs = qs.filter(level=level)

    return Response([{
        'id': s.id,
        'title': s.title,
        'level': s.level,
        'section_number': s.section_number,
        'question_count': s.questions.count(),
        'time_limit': s.time_limit,
        'has_audio': bool(s.audio_file or s.audio_url),
        'is_premium': s.is_premium,
        'is_mock': s.is_mock,
        'is_standalone': s.is_standalone,
    } for s in qs])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_listening_detail(request, section_id):
    s = get_object_or_404(CEFRListeningSection, id=section_id)

    bookmarked_ids = set(
        BookmarkedQuestion.objects.filter(
            user=request.user, source_type='CEFR',
            question_id__in=s.questions.values_list('id', flat=True)
        ).values_list('question_id', flat=True)
    )

    questions = [_serialize_cefr_question(q, bookmarked_ids)
                 for q in s.questions.prefetch_related('choices').all()]

    return Response({
        'id': s.id, 'title': s.title,
        'level': s.level, 'time_limit': s.time_limit,
        'audio_url': s.audio_url or (s.audio_file.url if s.audio_file else None),
        'transcript': s.transcript,
        'questions': questions,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_listening_start(request, section_id):
    section = get_object_or_404(CEFRListeningSection, id=section_id)
    existing = CEFRAttempt.objects.filter(
        user=request.user, listening_section=section, status='IN_PROGRESS'
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})
    attempt = CEFRAttempt.objects.create(
        user=request.user, listening_section=section, attempt_type='LISTENING'
    )
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_listening_submit(request, section_id):
    attempt_id = request.data.get('attempt_id')
    attempt = get_object_or_404(CEFRAttempt, id=attempt_id, user=request.user)
    answers = request.data.get('answers', {})

    section = get_object_or_404(CEFRListeningSection, id=section_id)
    questions = list(section.questions.prefetch_related('choices').all())

    correct = 0
    total = len(questions)
    results = []

    for q in questions:
        user_ans = str(answers.get(str(q.id), ''))
        is_correct = _check_answer_cefr(q, user_ans)
        if is_correct:
            correct += 1
        CEFRListeningAnswer.objects.update_or_create(
            attempt=attempt, question=q,
            defaults={'answer': user_ans, 'is_correct': is_correct}
        )
        results.append({
            'question_id': q.id, 'number': q.number,
            'user_answer': user_ans,
            'correct_answer': q.correct_answer,
            'correct_answers_list': q.correct_answers_list(),
            'is_correct': is_correct,
            'explanation': q.explanation,
        })

    score_percent = (correct / total * 100) if total else 0
    attempt.score_percent = score_percent
    attempt.correct_count = correct
    attempt.total_count = total
    attempt.complete()

    return Response({
        'correct': correct, 'total': total,
        'score_percent': round(score_percent, 1),
        'results': results,
    })


# ── HISTORY ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_history(request):
    """GET /api/cefr/history/"""
    attempts = CEFRAttempt.objects.filter(
        user=request.user, status='COMPLETED'
    ).select_related('test', 'reading_passage', 'listening_section').order_by('-finished_at')

    result = []
    for a in attempts:
        item = {
            'id': a.id,
            'attempt_type': a.attempt_type,
            'score_percent': round(a.score_percent, 1) if a.score_percent else None,
            'correct': a.correct_count,
            'total': a.total_count,
            'finished_at': a.finished_at,
            'level': a.test.level if a.test else '',
        }
        if a.attempt_type == 'GRAMMAR' and a.test:
            item['title'] = a.test.title
            item['label'] = f"Grammar [{a.test.level}]"
        elif a.attempt_type == 'READING' and a.reading_passage:
            item['title'] = a.reading_passage.title
            item['label'] = 'Reading Practice'
        elif a.attempt_type == 'LISTENING' and a.listening_section:
            item['title'] = a.listening_section.title
            item['label'] = 'Listening Practice'
        else:
            item['title'] = a.test.title if a.test else 'CEFR Test'
            item['label'] = a.attempt_type
        result.append(item)

    return Response(result)


# ── PERFORMANCE ANALYSIS ─────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cefr_analysis(request):
    """GET /api/cefr/analysis/"""
    grammar_answers = CEFRAnswer.objects.filter(
        attempt__user=request.user, attempt__status='COMPLETED'
    ).select_related('question')

    grammar_by_type = {}
    for a in grammar_answers:
        qt = a.question.question_type
        if qt not in grammar_by_type:
            grammar_by_type[qt] = {
                'type': qt, 'display': a.question.get_question_type_display(),
                'correct': 0, 'total': 0
            }
        grammar_by_type[qt]['total'] += 1
        if a.is_correct:
            grammar_by_type[qt]['correct'] += 1

    reading_answers = CEFRReadingAnswer.objects.filter(
        attempt__user=request.user, attempt__status='COMPLETED'
    ).select_related('question')

    reading_by_type = {}
    for a in reading_answers:
        qt = a.question.question_type
        if qt not in reading_by_type:
            reading_by_type[qt] = {
                'type': qt, 'display': a.question.get_question_type_display(),
                'correct': 0, 'total': 0
            }
        reading_by_type[qt]['total'] += 1
        if a.is_correct:
            reading_by_type[qt]['correct'] += 1

    listening_answers = CEFRListeningAnswer.objects.filter(
        attempt__user=request.user, attempt__status='COMPLETED'
    ).select_related('question')

    listening_by_type = {}
    for a in listening_answers:
        qt = a.question.question_type
        if qt not in listening_by_type:
            listening_by_type[qt] = {
                'type': qt, 'display': a.question.get_question_type_display(),
                'correct': 0, 'total': 0
            }
        listening_by_type[qt]['total'] += 1
        if a.is_correct:
            listening_by_type[qt]['correct'] += 1

    def enrich(d):
        for v in d.values():
            v['accuracy'] = round(v['correct'] / v['total'] * 100, 1) if v['total'] else 0
        return sorted(d.values(), key=lambda x: x['accuracy'])

    attempts_qs = CEFRAttempt.objects.filter(
        user=request.user, status='COMPLETED'
    ).order_by('finished_at').values('id', 'finished_at', 'score_percent', 'attempt_type')

    return Response({
        'grammar': {
            'by_type': enrich(grammar_by_type),
            'total_correct': sum(v['correct'] for v in grammar_by_type.values()),
            'total_questions': sum(v['total'] for v in grammar_by_type.values()),
        },
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
            'score_percent': a['score_percent'],
            'attempt_type': a['attempt_type'],
        } for a in attempts_qs],
    })


# ── SECURITY ─────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cefr_security_event(request, attempt_id):
    attempt = get_object_or_404(CEFRAttempt, id=attempt_id, user=request.user)
    event = {'type': request.data.get('event_type'), 'timestamp': request.data.get('timestamp')}
    if event['type'] == 'TAB_SWITCH':
        attempt.tab_switches += 1
    attempt.security_events.append(event)
    attempt.save(update_fields=['tab_switches', 'security_events'])
    return Response({'logged': True})
