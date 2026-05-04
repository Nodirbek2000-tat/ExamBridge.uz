import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_POST
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.conf import settings

from .models import (
    Test, TestSection, Module, Question, Choice,
    TestAttempt, Answer, TestResult, SavedQuestion, AIAnalysis
)
from .scoring import calculate_sat_score


@login_required
def test_list_view(request):
    """Tests organized by year and month."""
    tests = Test.objects.filter(is_active=True).order_by('-year', '-month')

    # Group by year → month
    grouped = {}
    for test in tests:
        year = test.year
        month = test.get_month_display()
        if year not in grouped:
            grouped[year] = {}
        if month not in grouped[year]:
            grouped[year][month] = []
        grouped[year][month].append(test)

    return render(request, 'tests/test_list.html', {'grouped': grouped})


@login_required
def test_detail_view(request, test_id):
    """Test detail page before starting."""
    test = get_object_or_404(Test, id=test_id, is_active=True)

    if test.is_premium and not request.user.is_premium:
        return render(request, 'tests/premium_required.html', {'test': test})

    # Check if user has in-progress attempt
    existing = TestAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).first()

    return render(request, 'tests/test_detail.html', {
        'test': test,
        'existing_attempt': existing,
    })


@login_required
@require_POST
def start_test_view(request, test_id):
    """Create a new attempt and redirect to test."""
    test = get_object_or_404(Test, id=test_id, is_active=True)

    if test.is_premium and not request.user.is_premium:
        return JsonResponse({'error': 'Premium required'}, status=403)

    # Abandon any existing in-progress attempt
    TestAttempt.objects.filter(
        user=request.user, test=test, status='IN_PROGRESS'
    ).update(status='ABANDONED')

    # Get first module (Math Module 1)
    first_module = Module.objects.filter(
        section__test=test,
        section__section_type='MATH',
        module_number=1
    ).first()

    attempt = TestAttempt.objects.create(
        user=request.user,
        test=test,
        current_module=first_module,
        current_question_number=1,
    )

    return redirect('test_attempt', attempt_id=attempt.id)


@login_required
def test_attempt_view(request, attempt_id):
    """Main test-taking view — shows one module at a time."""
    attempt = get_object_or_404(
        TestAttempt,
        id=attempt_id,
        user=request.user,
        status='IN_PROGRESS'
    )

    module = attempt.current_module
    if not module:
        return redirect('test_result', attempt_id=attempt.id)

    questions = module.questions.prefetch_related('choices').order_by('number')
    existing_answers = {
        a.question_id: a for a in attempt.answers.filter(
            question__module=module
        ).select_related('selected_choice')
    }

    # Build answer map for JS
    answer_map = {}
    for q_id, ans in existing_answers.items():
        if ans.selected_choice:
            answer_map[str(q_id)] = ans.selected_choice.option
        elif ans.text_answer:
            answer_map[str(q_id)] = ans.text_answer

    return render(request, 'tests/test_attempt.html', {
        'attempt': attempt,
        'module': module,
        'questions': questions,
        'answer_map_json': json.dumps(answer_map),
        'bookmarked_ids': list(
            existing_answers[qid].question_id
            for qid in existing_answers if existing_answers[qid].is_bookmarked
        ),
        'time_limit_seconds': module.time_limit * 60,
    })


@login_required
@require_POST
def save_answer_view(request, attempt_id):
    """AJAX: Save a single answer."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, status='IN_PROGRESS')

    try:
        data = json.loads(request.body)
        question_id = data.get('question_id')
        selected_option = data.get('selected_option', '')
        text_answer = data.get('text_answer', '')
        is_bookmarked = data.get('is_bookmarked', False)
        time_spent = data.get('time_spent', 0)
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid data'}, status=400)

    question = get_object_or_404(Question, id=question_id, module=attempt.current_module)

    selected_choice = None
    if selected_option and question.question_type == 'MCQ':
        selected_choice = question.choices.filter(option=selected_option).first()

    Answer.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={
            'selected_choice': selected_choice,
            'text_answer': text_answer,
            'is_bookmarked': is_bookmarked,
            'time_spent': time_spent,
            'is_skipped': not selected_option and not text_answer,
        }
    )

    return JsonResponse({'saved': True})


@login_required
@require_POST
def submit_module_view(request, attempt_id):
    """Submit current module and move to next or finish."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user, status='IN_PROGRESS')

    current_module = attempt.current_module
    section_type = current_module.section.section_type
    module_number = current_module.module_number

    # Determine next module
    next_module = None
    if module_number == 1:
        # Move to module 2 of same section
        next_module = Module.objects.filter(
            section=current_module.section,
            module_number=2
        ).first()
    else:
        # Module 2 done — move to next section
        if section_type == 'MATH':
            next_module = Module.objects.filter(
                section__test=attempt.test,
                section__section_type='ENGLISH',
                module_number=1
            ).first()
        # If ENGLISH module 2 → done

    if next_module:
        attempt.current_module = next_module
        attempt.current_question_number = 1
        attempt.save(update_fields=['current_module', 'current_question_number'])
        return JsonResponse({'next': 'module', 'module_id': next_module.id,
                             'module_number': next_module.module_number,
                             'section': next_module.section.section_type})
    else:
        # All modules done — calculate result
        attempt.complete()
        result = calculate_sat_score(attempt)
        return JsonResponse({'next': 'result', 'result_id': result.id})


@login_required
def test_result_view(request, attempt_id):
    """Result page after test completion."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)

    try:
        result = attempt.result
    except TestResult.DoesNotExist:
        if attempt.status == 'COMPLETED':
            result = calculate_sat_score(attempt)
        else:
            return redirect('test_list')

    return render(request, 'tests/test_result.html', {
        'attempt': attempt,
        'result': result,
        'test': attempt.test,
    })


@login_required
def test_review_view(request, attempt_id):
    """Review all questions with correct/wrong indicators."""
    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)

    modules_data = []
    for section in attempt.test.sections.all():
        for module in section.modules.all():
            questions_data = []
            for question in module.questions.prefetch_related('choices').order_by('number'):
                try:
                    answer = attempt.answers.get(question=question)
                except Answer.DoesNotExist:
                    answer = None
                questions_data.append({
                    'question': question,
                    'answer': answer,
                    'is_correct': answer.is_correct if answer else False,
                    'is_skipped': answer.is_skipped if answer else True,
                })
            modules_data.append({
                'module': module,
                'section': section,
                'questions': questions_data,
                'correct_count': sum(1 for q in questions_data if q['is_correct']),
                'total': len(questions_data),
            })

    return render(request, 'tests/test_review.html', {
        'attempt': attempt,
        'result': attempt.result,
        'modules_data': modules_data,
    })


@login_required
@require_POST
def toggle_save_question_view(request, question_id):
    """Toggle save/unsave a question."""
    question = get_object_or_404(Question, id=question_id)
    saved, created = SavedQuestion.objects.get_or_create(
        user=request.user,
        question=question
    )
    if not created:
        saved.delete()
        return JsonResponse({'saved': False})
    return JsonResponse({'saved': True})


@login_required
def saved_count_view(request):
    count = SavedQuestion.objects.filter(user=request.user).count()
    return JsonResponse({'count': count})


@login_required
def saved_questions_view(request):
    """User's saved/bookmarked questions."""
    saved = SavedQuestion.objects.filter(
        user=request.user
    ).select_related(
        'question__module__section__test'
    ).order_by('-saved_at')

    return render(request, 'tests/saved_questions.html', {'saved_questions': saved})


@login_required
@require_POST
def ai_analysis_view(request, attempt_id):
    """Generate AI analysis for a test result. Pro feature."""
    if not request.user.is_premium:
        return JsonResponse({'error': 'Premium required'}, status=403)

    attempt = get_object_or_404(TestAttempt, id=attempt_id, user=request.user)
    result = get_object_or_404(TestResult, attempt=attempt)

    # Return cached analysis if exists
    if hasattr(result, 'ai_analysis'):
        ana = result.ai_analysis
        return JsonResponse({
            'weak_areas': ana.weak_areas,
            'recommendations': ana.recommendations,
            'university_suggestions': ana.university_suggestions,
        })

    # Build prompt from answers
    wrong_answers = Answer.objects.filter(
        attempt=attempt, is_correct=False
    ).select_related('question').order_by('question__module__section__section_type')

    math_wrong = [a for a in wrong_answers if a.question.module.section.section_type == 'MATH']
    eng_wrong = [a for a in wrong_answers if a.question.module.section.section_type == 'ENGLISH']

    prompt = f"""A student took a Digital SAT practice test and scored {result.total_score}/1600 (Math: {result.math_score}/800, English: {result.english_score}/800).

Math wrong answers: {len(math_wrong)} out of 44
English wrong answers: {len(eng_wrong)} out of 54

Math question difficulties wrong: {', '.join(set(a.question.difficulty or 'unknown' for a in math_wrong)) or 'none'}
English question difficulties wrong: {', '.join(set(a.question.difficulty or 'unknown' for a in eng_wrong)) or 'none'}

Based on this performance, provide:
1. weak_areas: A JSON list of 3-5 specific weak skill areas (e.g. "Linear equations", "Vocabulary in context")
2. recommendations: 2-3 sentences of personalized study advice
3. university_suggestions: A JSON list of 3-5 universities with name and country that match this score range

Respond in this exact JSON format:
{{
  "weak_areas": ["area1", "area2", "area3"],
  "recommendations": "Your study advice here...",
  "university_suggestions": [
    {{"name": "University Name", "country": "Country", "chance": "match"}}
  ]
}}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=getattr(settings, 'ANTHROPIC_API_KEY', ''))
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = message.content[0].text.strip()
        # Extract JSON
        if '```json' in raw:
            raw = raw.split('```json')[1].split('```')[0].strip()
        elif '```' in raw:
            raw = raw.split('```')[1].split('```')[0].strip()
        data = json.loads(raw)
    except Exception as e:
        # Fallback analysis without API
        data = {
            'weak_areas': ['Review incorrect answers', 'Focus on timed practice'],
            'recommendations': f'You scored {result.total_score}/1600. Focus on reviewing your wrong answers and practice under timed conditions.',
            'university_suggestions': []
        }

    AIAnalysis.objects.update_or_create(
        result=result,
        defaults={
            'weak_areas': data.get('weak_areas', []),
            'recommendations': data.get('recommendations', ''),
            'university_suggestions': data.get('university_suggestions', []),
        }
    )

    return JsonResponse({
        'weak_areas': data.get('weak_areas', []),
        'recommendations': data.get('recommendations', ''),
        'university_suggestions': data.get('university_suggestions', []),
    })
