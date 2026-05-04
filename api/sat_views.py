from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Count, Sum, Case, When, IntegerField
from tests_app.models import Test, TestAttempt, TestResult, Answer, Question, Module, BankQuestion, SavedBankQuestion, QuestionReport


def test_list_data(test, user=None):
    # Count M1 questions only (STANDARD) — always present regardless of test_mode
    total_questions = sum(
        m.questions.count()
        for s in test.sections.all()
        for m in s.modules.filter(module_number=1)
    )
    data = {
        'id': test.id,
        'display_name': test.display_name,
        'test_type': test.test_type,
        'test_mode': test.test_mode,
        'year': test.year,
        'month': test.get_month_display(),
        'month_num': test.month,
        'form': test.form,
        'is_international': test.is_international,
        'is_premium': test.is_premium,
        'has_questions': total_questions > 0,
        'question_count': total_questions,
        'best_score': None,
        'total_score': None,
        'math_score': None,
        'english_score': None,
        'attempts_count': 0,
    }
    if user:
        result = (
            TestResult.objects.filter(user=user, attempt__test=test)
            .order_by('-total_score')
            .first()
        )
        if result:
            data['best_score'] = result.total_score
            data['total_score'] = result.total_score
            data['math_score'] = result.math_score
            data['english_score'] = result.english_score
        data['attempts_count'] = TestAttempt.objects.filter(
            user=user, test=test, status='COMPLETED', is_individual=False
        ).count()
    return data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_test_list(request):
    tests = Test.objects.filter(is_active=True, test_type='SAT')
    return Response([test_list_data(t, request.user) for t in tests])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_start_test(request, test_id):
    test = get_object_or_404(Test, id=test_id, is_active=True)

    # Check premium
    if test.is_premium and not request.user.is_premium:
        return Response({'detail': 'Premium required.'}, status=403)

    # Check existing in-progress attempt (skip if force_new=true)
    force_new = request.data.get('force_new', False)
    if not force_new:
        existing = TestAttempt.objects.filter(
            user=request.user, test=test, status='IN_PROGRESS'
        ).first()
        if existing:
            return Response({'attempt_id': existing.id, 'resumed': True})

    first_module = Module.objects.filter(
        section__test=test, module_number=1,
        section__section_type='ENGLISH',
        difficulty_variant='STANDARD',
    ).first() or Module.objects.filter(
        section__test=test, module_number=1,
    ).first()

    if not first_module:
        return Response({'detail': 'This test has no questions yet.'}, status=400)

    attempt = TestAttempt.objects.create(
        user=request.user,
        test=test,
        current_module=first_module,
    )
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_attempt_detail(request, attempt_id):
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)
    module = attempt.current_module
    if not module:
        return Response({'detail': 'No current module.'}, status=400)

    questions = module.questions.prefetch_related('choices').all()
    answered = {a.question_id: a for a in attempt.answers.all()}

    questions_data = []
    for q in questions:
        ans = answered.get(q.id)
        questions_data.append({
            'id': q.id,
            'number': q.number,
            'question_type': q.question_type,
            'content': q.content,
            'math_equation': q.math_equation or '',
            'passage': q.passage,
            'table_data': q.table_data,
            'image': q.image.url if q.image else None,
            'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
            'answered': ans.selected_choice.option if ans and ans.selected_choice else (ans.text_answer if ans else None),
            'is_bookmarked': ans.is_bookmarked if ans else False,
        })

    return Response({
        'attempt_id': attempt.id,
        'test': attempt.test.display_name,
        'status': attempt.status,
        'is_individual': attempt.is_individual,
        'module': {
            'id': module.id,
            'number': module.module_number,
            'section': module.section.section_type,
            'difficulty_variant': module.difficulty_variant,
            'time_limit': module.time_limit,
        },
        'questions': questions_data,
        'current_question': attempt.current_question_number,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_submit_answer(request, attempt_id):
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)
    if attempt.status not in ('IN_PROGRESS', 'COMPLETED'):
        return Response({'detail': 'Attempt is not active.'}, status=400)
    question_id = request.data.get('question_id')
    answer_value = request.data.get('answer')
    time_spent = request.data.get('time_spent', 0)
    is_bookmarked = request.data.get('is_bookmarked', False)

    question = get_object_or_404(Question, id=question_id)

    answer_obj, _ = Answer.objects.get_or_create(attempt=attempt, question=question)
    answer_obj.time_spent = time_spent
    answer_obj.is_bookmarked = is_bookmarked

    if question.question_type == 'MCQ':
        from tests_app.models import Choice
        choice = get_object_or_404(Choice, question=question, option=answer_value)
        answer_obj.selected_choice = choice
        answer_obj.text_answer = ''
    else:
        answer_obj.text_answer = answer_value or ''
        answer_obj.selected_choice = None

    answer_obj.save()
    return Response({'saved': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_submit_module(request, attempt_id):
    """
    Submit current module and advance to next.
    Returns: { next: 'english_m2'|'break'|'math_m1'|'math_m2'|'finished', result_id, attempt_id }
    """
    from django.utils import timezone
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, status='IN_PROGRESS')
    current = attempt.current_module
    if not current:
        return Response({'detail': 'No current module.'}, status=400)

    test = attempt.test
    section_type = current.section.section_type   # ENGLISH / MATH
    module_num = current.module_number             # 1 / 2
    total_q = current.questions.count()

    # Count correct answers for current module
    correct = attempt.answers.filter(
        question__module=current, is_correct=True
    ).count()
    pct = (correct / total_q * 100) if total_q > 0 else 0

    # ── Routing logic ────────────────────────────────────────────────────────
    next_module = None
    next_key = None

    # ── Individual module mode — just return stats, no routing ─────────────────
    if attempt.is_individual:
        attempt.complete()
        return Response({
            'next': 'module_done',
            'attempt_id': attempt.id,
            'correct': correct,
            'total': total_q,
            'pct': round(pct, 1),
            'section': section_type,
            'module_num': module_num,
        })

    # ── 3-way adaptive routing ───────────────────────────────────────────────
    def _pick_variant(pct_val):
        if pct_val < 60:
            return 'EASY'
        elif pct_val <= 85:
            return 'MEDIUM'
        else:
            return 'HARD'

    def _get_m2(section, preferred_variant):
        """Get M2 module, falling back to other variants if preferred not found."""
        fallback_order = [preferred_variant, 'HARD', 'EASY', 'MEDIUM', 'STANDARD']
        for v in fallback_order:
            m = Module.objects.filter(
                section__test=test, section__section_type=section,
                module_number=2, difficulty_variant=v
            ).first()
            if m:
                return m
        return None

    if section_type == 'ENGLISH' and module_num == 1:
        variant = _pick_variant(pct)
        next_module = _get_m2('ENGLISH', variant)
        next_key = 'english_m2'

    elif section_type == 'ENGLISH' and module_num == 2:
        # → 10-minute break, then Math M1
        next_module = Module.objects.filter(
            section__test=test, section__section_type='MATH',
            module_number=1, difficulty_variant='STANDARD'
        ).first() or Module.objects.filter(
            section__test=test, section__section_type='MATH',
            module_number=1
        ).first()
        if next_module:
            attempt.current_module = next_module
            attempt.save(update_fields=['current_module'])
        return Response({'next': 'break', 'attempt_id': attempt.id})

    elif section_type == 'MATH' and module_num == 1:
        variant = _pick_variant(pct)
        next_module = _get_m2('MATH', variant)
        next_key = 'math_m2'

    elif section_type == 'MATH' and module_num == 2:
        # → All done! Calculate final result
        attempt.complete()
        from tests_app.scoring import calculate_sat_score
        result = calculate_sat_score(attempt)
        return Response({'next': 'finished', 'result_id': result.id, 'attempt_id': attempt.id})

    # Advance to next module
    if next_module:
        attempt.current_module = next_module
        attempt.current_question_number = 1
        attempt.save(update_fields=['current_module', 'current_question_number'])
        return Response({'next': next_key, 'attempt_id': attempt.id})
    else:
        # No next module found → finish anyway
        attempt.complete()
        from tests_app.scoring import calculate_sat_score
        result = calculate_sat_score(attempt)
        return Response({'next': 'finished', 'result_id': result.id, 'attempt_id': attempt.id})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_security_event(request, attempt_id):
    """Log tab switch, fullscreen exit, etc."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)
    event_type = request.data.get('event_type')
    events = attempt.answers.model  # just to have a reference — store on attempt
    # Store in a simple way using a dedicated field if needed
    return Response({'logged': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_stats(request):
    from tests_app.models import TestResult
    from accounts.models import UserStats
    results = TestResult.objects.filter(user=request.user)
    if not results.exists():
        # Still return exam_date / streak from stats if exists
        try:
            us = request.user.stats
        except UserStats.DoesNotExist:
            us = None
        return Response({
            'best_score': None,
            'best_english': None,
            'best_math': None,
            'rw_avg': None,
            'math_avg': None,
            'tests_taken': 0,
            'avg_score': None,
            'study_streak': us.current_streak if us else 0,
            'exam_date': us.sat_exam_date.isoformat() if us and us.sat_exam_date else None,
        })

    best_total = results.order_by('-total_score').first()
    best_eng = results.order_by('-english_score').first()
    best_math = results.order_by('-math_score').first()
    count = results.count()
    rw_avg = round(sum(r.english_score for r in results) / count)
    math_avg = round(sum(r.math_score for r in results) / count)

    try:
        us = request.user.stats
    except UserStats.DoesNotExist:
        us = None

    return Response({
        'best_score': best_total.total_score if best_total else None,
        'best_english': best_eng.english_score if best_eng else None,
        'best_math': best_math.math_score if best_math else None,
        'rw_avg': rw_avg,
        'math_avg': math_avg,
        'tests_taken': count,
        'avg_score': round((best_total.total_score + 0) / 1) if best_total else None,
        'study_streak': us.current_streak if us else 0,
        'exam_date': us.sat_exam_date.isoformat() if us and us.sat_exam_date else None,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sat_exam_date(request):
    """GET or set the user's SAT exam date."""
    from accounts.models import UserStats
    stats, _ = UserStats.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        date_str = request.data.get('exam_date')
        if not date_str:
            return Response({'detail': 'exam_date required.'}, status=400)
        from datetime import date
        try:
            # Accept YYYY-MM-DD
            parts = date_str.split('-')
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return Response({'detail': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
        stats.sat_exam_date = d
        stats.save(update_fields=['sat_exam_date'])
        return Response({'exam_date': d.isoformat()})
    return Response({'exam_date': stats.sat_exam_date.isoformat() if stats.sat_exam_date else None})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_ranking(request):
    """Return top 50 SAT users by XP and the current user's rank."""
    from accounts.models import UserStats
    # XP = total_tests_taken * 30
    all_stats = list(UserStats.objects.select_related('user').order_by('-total_tests_taken', '-best_total_score'))

    leaderboard = []
    user_rank = None
    user_xp = 0
    for idx, us in enumerate(all_stats):
        xp = us.total_tests_taken * 30
        if us.user_id == request.user.id:
            user_rank = idx + 1
            user_xp = xp
        if idx < 50:
            name = us.user.first_name or us.user.username or us.user.email.split('@')[0]
            leaderboard.append({
                'rank': idx + 1,
                'name': name,
                'initial': (name[0] if name else 'U').upper(),
                'xp': xp,
                'is_you': us.user_id == request.user.id,
            })

    if user_rank is None:
        user_rank = len(all_stats) + 1

    return Response({
        'top3': leaderboard[:3],
        'top50': leaderboard,
        'your_rank': user_rank,
        'your_xp': user_xp,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_result_detail(request, result_id):
    result = get_object_or_404(TestResult, id=result_id, user=request.user)
    attempt = result.attempt

    # Map question_id → Answer (only for questions the user submitted an answer for)
    answered_map = {
        ans.question_id: ans
        for ans in attempt.answers.select_related('selected_choice').all()
    }

    # Find which modules were actually attempted (from the answer records)
    attempted_module_ids = set(
        attempt.answers.values_list('question__module_id', flat=True).distinct()
    )

    # Always include the last active module (covers Math M2 when user answered 0 questions there)
    if attempt.current_module_id:
        attempted_module_ids.add(attempt.current_module_id)

    # For a full test, also find English M1, English M2, Math M1 STANDARD modules
    # so they always appear even if no answers were saved
    test = attempt.test
    standard_modules = Module.objects.filter(
        section__test=test,
        module_number=1,
        difficulty_variant='STANDARD',
    ).values_list('id', flat=True)
    attempted_module_ids.update(standard_modules)

    # Fetch ALL questions from those modules (including unanswered/omitted ones)
    all_questions = (
        Question.objects
        .filter(module_id__in=attempted_module_ids)
        .select_related('module__section')
        .prefetch_related('choices')
        .order_by('module__section__section_type', 'module__module_number', 'number')
    )

    answers_data = []

    def _q_data(q, ans, omitted_module=False):
        user_answer = None
        if ans:
            user_answer = ans.selected_choice.option if ans.selected_choice else (ans.text_answer or None)
        return {
            'question_id': q.id,
            'number': q.number,
            'section': q.module.section.section_type,
            'module': q.module.module_number,
            'module_variant': q.module.difficulty_variant,
            'question_type': q.question_type,
            'content': q.content,
            'math_equation': q.math_equation or '',
            'passage': q.passage,
            'table_data': q.table_data,
            'image': q.image.url if q.image else None,
            'difficulty': q.difficulty,
            'category': q.category,
            'topic': q.topic,
            'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
            'correct_answer': q.correct_answer,
            'explanation': q.explanation,
            'user_answer': user_answer,
            'is_correct': ans.is_correct if ans else False,
            'is_bookmarked': ans.is_bookmarked if ans else False,
            'time_spent': ans.time_spent if ans else 0,
            'omitted_module': omitted_module,
        }

    for q in all_questions:
        ans = answered_map.get(q.id)
        answers_data.append(_q_data(q, ans, omitted_module=False))

    # ── Include predicted M2 as "Omitted" when not taken ───────────────────────
    def _pick_variant(pct_val):
        if pct_val < 60:
            return 'EASY'
        elif pct_val <= 85:
            return 'MEDIUM'
        else:
            return 'HARD'

    seen_sections_m2 = set(
        q.module.section.section_type
        for q in all_questions
        if q.module.module_number == 2
    )

    for section_type in ['ENGLISH', 'MATH']:
        if section_type in seen_sections_m2:
            continue  # M2 already included

        # Check if M1 was done for this section
        m1_answers = attempt.answers.filter(
            question__module__section__section_type=section_type,
            question__module__module_number=1,
        )
        m1_total = m1_answers.count()
        if m1_total == 0:
            continue  # M1 not done either — skip

        m1_correct = m1_answers.filter(is_correct=True).count()
        pct = (m1_correct / m1_total * 100) if m1_total > 0 else 0
        predicted_variant = _pick_variant(pct)

        # Find the predicted M2 module (with fallback)
        m2_module = None
        for v in [predicted_variant, 'HARD', 'EASY', 'MEDIUM', 'STANDARD']:
            m2_module = Module.objects.filter(
                section__test=test,
                section__section_type=section_type,
                module_number=2,
                difficulty_variant=v,
            ).first()
            if m2_module:
                break

        if not m2_module:
            continue

        m2_questions = (
            Question.objects
            .filter(module=m2_module)
            .select_related('module__section')
            .prefetch_related('choices')
            .order_by('number')
        )
        for q in m2_questions:
            answers_data.append(_q_data(q, None, omitted_module=True))

    return Response({
        'id': result.id,
        'total_score': result.total_score,
        'math_score': result.math_score,
        'english_score': result.english_score,
        'math_raw': result.math_raw,
        'english_raw': result.english_raw,
        'math_m1_correct': result.math_m1_correct,
        'math_m2_correct': result.math_m2_correct,
        'english_m1_correct': result.english_m1_correct,
        'english_m2_correct': result.english_m2_correct,
        'test': result.attempt.test.display_name,
        'completed_at': result.attempt.finished_at,
        'answers': answers_data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_force_finish(request, attempt_id):
    """
    Force-finish an in-progress attempt (used when user clicks Submit & Exit).
    Skips remaining modules, marks attempt complete, calculates score, returns result_id.
    """
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, status='IN_PROGRESS')
    attempt.complete()
    from tests_app.scoring import calculate_sat_score
    result = calculate_sat_score(attempt)
    return Response({'result_id': result.id})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def sat_result_delete(request, result_id):
    result = get_object_or_404(TestResult, id=result_id, user=request.user)
    attempt = result.attempt
    attempt.delete()   # cascades to TestResult + Answers
    return Response({'deleted': True})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def sat_individual_attempt_delete(request, attempt_id):
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, is_individual=True)
    attempt.delete()
    return Response({'deleted': True})


def _bank_q_data(q, saved_map=None):
    return {
        'id': q.id,
        'subject': q.subject,
        'category': q.category,
        'question_type': q.question_type,
        'topic': q.topic,
        'content': q.content,
        'math_equation': q.math_equation or '',
        'passage': q.passage or '',
        'table_data': q.table_data,
        'image': q.image.url if q.image else None,
        'difficulty': q.difficulty,
        'choice_a': q.choice_a,
        'choice_b': q.choice_b,
        'choice_c': q.choice_c,
        'choice_d': q.choice_d,
        'choice_a_image': q.choice_a_image.url if q.choice_a_image else None,
        'choice_b_image': q.choice_b_image.url if q.choice_b_image else None,
        'choice_c_image': q.choice_c_image.url if q.choice_c_image else None,
        'choice_d_image': q.choice_d_image.url if q.choice_d_image else None,
        'correct_answer': q.correct_answer,
        'explanation': q.explanation,
        'year': q.year,
        'source': q.source,
        'is_saved': (
            saved_map is not None
            and q.id in saved_map
            and bool(saved_map.get(q.id, {}).get('is_bookmarked'))
        ),
        'user_answer': saved_map.get(q.id, {}).get('user_answer', '') if saved_map else '',
        'is_correct': saved_map.get(q.id, {}).get('is_correct', None) if saved_map else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_practice_list(request):
    """Return bank questions filtered by subject/category/difficulty."""
    subject = request.GET.get('subject', '')
    category = request.GET.get('category', '')
    difficulty = request.GET.get('difficulty', '')
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 20))

    saved_only = request.GET.get('saved_only', '')
    answer_filter = request.GET.get('answer_filter', '')

    qs = BankQuestion.objects.all()
    if subject:
        qs = qs.filter(subject=subject)
    if category:
        qs = qs.filter(category=category)
    topic = request.GET.get('topic', '')
    if topic:
        qs = qs.filter(topic=topic)
    if difficulty:
        qs = qs.filter(difficulty=difficulty)

    # Saved filter (Mark for review only, not mere progress)
    if saved_only == 'true':
        saved_ids = list(
            SavedBankQuestion.objects.filter(user=request.user, is_bookmarked=True).values_list(
                'question_id', flat=True
            )
        )
        qs = qs.filter(id__in=saved_ids)
    elif saved_only == 'false':
        saved_ids = list(
            SavedBankQuestion.objects.filter(user=request.user, is_bookmarked=True).values_list(
                'question_id', flat=True
            )
        )
        qs = qs.exclude(id__in=saved_ids)

    # Answer status filter
    if answer_filter == 'correct':
        ids = list(SavedBankQuestion.objects.filter(user=request.user, is_correct=True).values_list('question_id', flat=True))
        qs = qs.filter(id__in=ids)
    elif answer_filter == 'incorrect':
        ids = list(SavedBankQuestion.objects.filter(user=request.user, is_correct=False).values_list('question_id', flat=True))
        qs = qs.filter(id__in=ids)
    elif answer_filter == 'unanswered':
        answered_ids = list(SavedBankQuestion.objects.filter(user=request.user).values_list('question_id', flat=True))
        qs = qs.exclude(id__in=answered_ids)

    total = qs.count()
    start = (page - 1) * page_size
    qs = qs[start:start + page_size]

    # Build saved map
    saved = SavedBankQuestion.objects.filter(
        user=request.user, question_id__in=[q.id for q in qs]
    ).values('question_id', 'user_answer', 'is_correct', 'is_bookmarked')
    saved_map = {s['question_id']: s for s in saved}

    return Response({
        'total': total,
        'page': page,
        'page_size': page_size,
        'results': [_bank_q_data(q, saved_map) for q in qs],
    })


MATH_DOMAIN_ORDER = ['algebra', 'advanced_math', 'problem_data', 'geometry']
ENGLISH_DOMAIN_ORDER = ['craft_structure', 'expression_ideas', 'info_ideas', 'standard_english']


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_practice_bank_overview(request):
    """Per-domain / per-topic question counts and user's correct/wrong totals."""
    subject = request.GET.get('subject', '')
    if not subject:
        return Response({'detail': 'subject is required'}, status=400)

    difficulty    = request.GET.get('difficulty', '')
    saved_only    = request.GET.get('saved_only', '')
    answer_filter = request.GET.get('answer_filter', '')

    base = BankQuestion.objects.filter(subject=subject)

    # Apply same filters as sat_practice_list
    if difficulty:
        base = base.filter(difficulty=difficulty)
    if saved_only == 'true':
        ids = list(
            SavedBankQuestion.objects.filter(user=request.user, is_bookmarked=True).values_list(
                'question_id', flat=True
            )
        )
        base = base.filter(id__in=ids)
    elif saved_only == 'false':
        ids = list(
            SavedBankQuestion.objects.filter(user=request.user, is_bookmarked=True).values_list(
                'question_id', flat=True
            )
        )
        base = base.exclude(id__in=ids)
    if answer_filter == 'correct':
        ids = list(SavedBankQuestion.objects.filter(user=request.user, is_correct=True).values_list('question_id', flat=True))
        base = base.filter(id__in=ids)
    elif answer_filter == 'incorrect':
        ids = list(SavedBankQuestion.objects.filter(user=request.user, is_correct=False).values_list('question_id', flat=True))
        base = base.filter(id__in=ids)
    elif answer_filter == 'unanswered':
        ids = list(SavedBankQuestion.objects.filter(user=request.user).values_list('question_id', flat=True))
        base = base.exclude(id__in=ids)

    grand_total = base.count()

    topic_rows = list(
        base.values('category', 'topic').annotate(total=Count('id')).order_by('category', 'topic')
    )

    saved_rows = SavedBankQuestion.objects.filter(
        user=request.user,
        question__subject=subject,
    ).values('question__category', 'question__topic').annotate(
        correct=Sum(Case(When(is_correct=True, then=1), default=0, output_field=IntegerField())),
        wrong=Sum(Case(When(is_correct=False, then=1), default=0, output_field=IntegerField())),
    )

    stats_lookup = {
        (r['question__category'], r['question__topic']): {
            'correct': r['correct'] or 0,
            'wrong': r['wrong'] or 0,
        }
        for r in saved_rows
    }

    order = MATH_DOMAIN_ORDER if subject == 'Matematika' else ENGLISH_DOMAIN_ORDER

    domains_out = []
    for cat in order:
        topics_for_cat = [r for r in topic_rows if r['category'] == cat]
        cat_total = sum(r['total'] for r in topics_for_cat)
        topics_out = []
        for r in topics_for_cat:
            key = (r['category'], r['topic'])
            st = stats_lookup.get(key, {'correct': 0, 'wrong': 0})
            label = (r['topic'] or '').strip() or '—'
            topics_out.append({
                'topic': r['topic'],
                'topic_label': label,
                'total': r['total'],
                'correct': st['correct'],
                'wrong': st['wrong'],
            })
        domains_out.append({
            'category': cat,
            'total': cat_total,
            'topics': topics_out,
        })

    ut = SavedBankQuestion.objects.filter(user=request.user, question__subject=subject)
    correct_total = ut.filter(is_correct=True).count()
    wrong_total = ut.filter(is_correct=False).count()

    return Response({
        'grand_total': grand_total,
        'answered_total': correct_total + wrong_total,
        'correct_total': correct_total,
        'wrong_total': wrong_total,
        'domains': domains_out,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_practice_save(request, question_id):
    """Bookmark (save/unsave) or record attempt without bookmarking (record)."""
    q = get_object_or_404(BankQuestion, id=question_id)
    action = request.data.get('action', 'save')
    user_answer = request.data.get('user_answer', '')
    is_correct = bool(request.data.get('is_correct', False))

    if action == 'unsave':
        obj = SavedBankQuestion.objects.filter(user=request.user, question=q).first()
        if obj:
            obj.is_bookmarked = False
            obj.save(update_fields=['is_bookmarked'])
        return Response({'saved': False})

    if action == 'record':
        obj, created = SavedBankQuestion.objects.get_or_create(
            user=request.user,
            question=q,
            defaults={
                'user_answer': user_answer,
                'is_correct': is_correct,
                'is_bookmarked': False,
            },
        )
        if not created:
            obj.user_answer = user_answer
            obj.is_correct = is_correct
            obj.save(update_fields=['user_answer', 'is_correct'])
        return Response({'saved': obj.is_bookmarked, 'recorded': True})

    if action == 'save':
        obj, created = SavedBankQuestion.objects.update_or_create(
            user=request.user,
            question=q,
            defaults={
                'user_answer': user_answer,
                'is_correct': is_correct,
                'is_bookmarked': True,
            },
        )
        return Response({'saved': True, 'created': created})

    return Response({'detail': 'Invalid action'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_practice_detail(request, question_id):
    """Return a single bank question by ID (used when loading by ?qid=)."""
    q = get_object_or_404(BankQuestion, id=question_id)
    saved = SavedBankQuestion.objects.filter(user=request.user, question=q).first()
    saved_map = {}
    if saved:
        saved_map[q.id] = {
            'user_answer': saved.user_answer,
            'is_correct': saved.is_correct,
            'is_bookmarked': saved.is_bookmarked,
        }
    return Response(_bank_q_data(q, saved_map))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_saved_questions(request):
    """List user's saved bank questions, optionally filtered."""
    filter_correct = request.GET.get('correct', '')  # 'true', 'false', ''
    subject = request.GET.get('subject', '')
    category = request.GET.get('category', '')

    qs = SavedBankQuestion.objects.filter(user=request.user, is_bookmarked=True).select_related('question')
    if filter_correct == 'true':
        qs = qs.filter(is_correct=True)
    elif filter_correct == 'false':
        qs = qs.filter(is_correct=False)
    if subject:
        qs = qs.filter(question__subject=subject)
    if category:
        qs = qs.filter(question__category=category)

    results = []
    for s in qs:
        q = s.question
        data = _bank_q_data(q)
        data['user_answer'] = s.user_answer
        data['is_correct'] = s.is_correct
        data['saved_at'] = s.saved_at
        results.append(data)

    return Response({'results': results, 'total': len(results)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_test_bookmarks(request):
    """Return questions bookmarked (For Review) during full mock/individual tests."""
    from tests_app.models import Answer, TestAttempt
    answers = (
        Answer.objects
        .filter(attempt__user=request.user, is_bookmarked=True)
        .select_related('question', 'question__module', 'question__module__section',
                        'attempt', 'attempt__test', 'selected_choice')
        .prefetch_related('question__choices')
        .order_by('-attempt__started_at')
    )

    results = []
    for ans in answers:
        q = ans.question
        results.append({
            'question_id': q.id,
            'number': q.number,
            'section': q.module.section.section_type,
            'module': q.module.module_number,
            'question_type': q.question_type,
            'content': q.content,
            'math_equation': q.math_equation or '',
            'passage': q.passage,
            'table_data': q.table_data,
            'image': q.image.url if q.image else None,
            'difficulty': q.difficulty,
            'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
            'correct_answer': q.correct_answer,
            'explanation': q.explanation,
            'user_answer': ans.selected_choice.option if ans.selected_choice else ans.text_answer,
            'is_correct': ans.is_correct,
            'test_name': ans.attempt.test.display_name if ans.attempt.test else '',
            'attempt_id': ans.attempt.id,
        })

    return Response({'results': results, 'total': len(results)})


# ── TEST RESULTS HISTORY ───────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_test_results_history(request, test_id):
    """All completed full-test attempts for a specific test by current user."""
    test = get_object_or_404(Test, id=test_id, is_active=True)
    results = (
        TestResult.objects
        .filter(user=request.user, attempt__test=test, attempt__is_individual=False)
        .select_related('attempt')
        .order_by('-calculated_at')
    )

    data = []
    for r in results:
        # Module detail: how many correct in each module
        answers = r.attempt.answers.select_related(
            'question__module__section'
        ).all()
        module_stats = {}
        for ans in answers:
            mod = ans.question.module
            key = f"{mod.section.section_type}_M{mod.module_number}_{mod.difficulty_variant}"
            if key not in module_stats:
                module_stats[key] = {
                    'section': mod.section.section_type,
                    'module_number': mod.module_number,
                    'difficulty_variant': mod.difficulty_variant,
                    'module_id': mod.id,
                    'correct': 0,
                    'total': 0,
                }
            if ans.is_correct:
                module_stats[key]['correct'] += 1

        # Set total = actual question count in each module (not just answered ones)
        for key, stat in module_stats.items():
            stat['total'] = Question.objects.filter(module_id=stat.pop('module_id')).count()

        data.append({
            'result_id': r.id,
            'attempt_id': r.attempt.id,
            'total_score': r.total_score,
            'math_score': r.math_score,
            'english_score': r.english_score,
            'math_raw': r.math_raw,
            'english_raw': r.english_raw,
            'math_m1_correct': r.math_m1_correct,
            'math_m2_correct': r.math_m2_correct,
            'english_m1_correct': r.english_m1_correct,
            'english_m2_correct': r.english_m2_correct,
            'completed_at': r.attempt.finished_at,
            'module_stats': list(module_stats.values()),
        })

    best = max((r['total_score'] for r in data), default=None)
    return Response({
        'test': test.display_name,
        'test_id': test.id,
        'attempts_count': len(data),
        'best_score': best,
        'results': data,
    })


# ── INDIVIDUAL MODULE API ──────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_test_modules(request, test_id):
    """List all available modules for a test, with user's best stats per module."""
    test = get_object_or_404(Test, id=test_id, is_active=True)
    modules = Module.objects.filter(
        section__test=test
    ).select_related('section').prefetch_related('questions')

    result = []
    for m in modules.order_by('section__section_type', 'module_number', 'difficulty_variant'):
        q_count = m.questions.count()
        if q_count == 0:
            continue
        # All completed attempts for this module
        completed_attempts = TestAttempt.objects.filter(
            user=request.user, current_module=m, is_individual=True, status='COMPLETED'
        ).order_by('-finished_at')

        best_correct = None
        latest_attempt_id = None
        last_attempt_at = None
        best_pct = None

        if completed_attempts.exists():
            latest = completed_attempts.first()
            latest_attempt_id = latest.id
            last_attempt_at = latest.finished_at

            best_score = 0
            for att in completed_attempts:
                correct = att.answers.filter(question__module=m, is_correct=True).count()
                if correct > best_score:
                    best_score = correct
            best_correct = best_score
            best_pct = round(best_score / q_count * 100) if q_count > 0 else 0

        result.append({
            'id': m.id,
            'section': m.section.section_type,
            'module_number': m.module_number,
            'difficulty_variant': m.difficulty_variant,
            'time_limit': m.time_limit,
            'question_count': q_count,
            'best_correct': best_correct,
            'best_pct': best_pct,
            'attempts_count': completed_attempts.count(),
            'latest_attempt_id': latest_attempt_id,
            'last_attempt_at': last_attempt_at,
        })

    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_individual_stats(request, test_id):
    """All individual module attempts for a test — for score history and answer review circles."""
    test = get_object_or_404(Test, id=test_id, is_active=True)
    attempts = TestAttempt.objects.filter(
        user=request.user, test=test, is_individual=True, status='COMPLETED'
    ).select_related('current_module__section').order_by('-finished_at')

    data = []
    for att in attempts:
        mod = att.current_module
        correct = att.answers.filter(is_correct=True).count()
        total = att.answers.count()
        data.append({
            'attempt_id': att.id,
            'module_id': mod.id if mod else None,
            'section': mod.section.section_type if mod else '',
            'module_number': mod.module_number if mod else 0,
            'difficulty_variant': mod.difficulty_variant if mod else '',
            'correct': correct,
            'total': total,
            'pct': round(correct / total * 100) if total > 0 else 0,
            'finished_at': att.finished_at,
        })

    return Response({
        'test': test.display_name,
        'test_id': test.id,
        'total_attempts': len(data),
        'attempts': data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sat_start_individual_module(request):
    """Start an individual module attempt. POST: {module_id}"""
    module_id = request.data.get('module_id')
    if not module_id:
        return Response({'error': 'module_id required'}, status=400)

    module = get_object_or_404(Module, id=module_id)
    test = module.section.test

    # Check for existing in-progress individual attempt for this module
    existing = TestAttempt.objects.filter(
        user=request.user, test=test, is_individual=True,
        status='IN_PROGRESS', current_module=module
    ).first()
    if existing:
        return Response({'attempt_id': existing.id, 'resumed': True})

    attempt = TestAttempt.objects.create(
        user=request.user,
        test=test,
        current_module=module,
        is_individual=True,
    )
    return Response({'attempt_id': attempt.id, 'resumed': False}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_individual_result(request, attempt_id):
    """Get result stats for a completed individual module attempt."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, is_individual=True)
    module = attempt.current_module

    # Map question_id → Answer for fast lookup
    answered_map = {
        ans.question_id: ans
        for ans in attempt.answers.select_related('selected_choice').all()
    }

    # Fetch ALL questions from the module (including unanswered/omitted)
    all_questions = (
        Question.objects
        .filter(module=module)
        .prefetch_related('choices')
        .order_by('number')
    )

    correct = sum(1 for a in answered_map.values() if a.is_correct)
    total = all_questions.count()

    answers_data = []
    for q in all_questions:
        ans = answered_map.get(q.id)
        user_answer = None
        if ans:
            user_answer = ans.selected_choice.option if ans.selected_choice else (ans.text_answer or None)
        answers_data.append({
            'question_id': q.id,
            'number': q.number,
            'question_type': q.question_type,
            'content': q.content,
            'math_equation': q.math_equation or '',
            'passage': q.passage,
            'table_data': q.table_data,
            'image': q.image.url if q.image else None,
            'difficulty': q.difficulty,
            'category': q.category,
            'topic': q.topic,
            'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
            'correct_answer': q.correct_answer,
            'explanation': q.explanation,
            'user_answer': user_answer,
            'is_correct': ans.is_correct if ans else False,
            'time_spent': ans.time_spent if ans else 0,
        })

    return Response({
        'attempt_id': attempt.id,
        'status': attempt.status,
        'module': {
            'section': module.section.section_type if module else '',
            'module_number': module.module_number if module else 0,
            'difficulty_variant': module.difficulty_variant if module else '',
        },
        'test': attempt.test.display_name,
        'correct': correct,
        'total': total,
        'pct': round(correct / total * 100, 1) if total > 0 else 0,
        'started_at': attempt.started_at,
        'finished_at': attempt.finished_at,
        'answers': answers_data,
    })


# ── QUESTION REPORTS ──────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_question_report(request, question_id):
    """User submits a report about a practice bank question."""
    question = get_object_or_404(BankQuestion, id=question_id)
    reason  = request.data.get('reason', 'other')
    details = request.data.get('details', '').strip()
    QuestionReport.objects.create(
        user=request.user,
        question=question,
        reason=reason,
        details=details,
    )
    return Response({'success': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_reports_list(request):
    """Admin: list all question reports."""
    if not request.user.is_staff:
        return Response({'detail': 'Forbidden'}, status=403)
    status_filter = request.query_params.get('status', '')
    qs = QuestionReport.objects.select_related('user', 'question').order_by('-created_at')
    if status_filter:
        qs = qs.filter(status=status_filter)
    data = []
    for r in qs:
        data.append({
            'id': r.id,
            'reason': r.reason,
            'reason_display': r.get_reason_display(),
            'details': r.details,
            'status': r.status,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
            'user_email': r.user.email if r.user else '—',
            'question_id': r.question.id if r.question else None,
            'question_content': (r.question.content or '')[:120] if r.question else '—',
        })
    return Response({'results': data, 'total': len(data)})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def admin_report_update(request, report_id):
    """Admin: update report status."""
    if not request.user.is_staff:
        return Response({'detail': 'Forbidden'}, status=403)
    report = get_object_or_404(QuestionReport, id=report_id)
    new_status = request.data.get('status')
    if new_status in [s[0] for s in QuestionReport.Status.choices]:
        report.status = new_status
        report.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'status': report.status})
