import json
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count, Avg, Q
from django.contrib import messages
from django.core.paginator import Paginator
from datetime import timedelta

from accounts.models import User, UserStats
from tests_app.models import (
    Test, TestSection, Module, Question, Choice,
    TestAttempt, TestResult, BankQuestion
)


def staff_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/auth/login/?next=' + request.path)
        if not (request.user.is_staff or request.user.is_superuser):
            return render(request, 'panel/forbidden.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def panel_context():
    """Common stats for sidebar."""
    return {
        'total_users': User.objects.count(),
        'total_tests': Test.objects.filter(is_active=True).count(),
        'total_attempts': TestAttempt.objects.count(),
        'total_bank_questions': BankQuestion.objects.count(),
    }


@staff_required
def dashboard_view(request):
    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # User stats
    users_today = User.objects.filter(created_at__date=today).count()
    users_week = User.objects.filter(created_at__gte=week_ago).count()
    users_month = User.objects.filter(created_at__gte=month_ago).count()
    total_users = User.objects.count()
    premium_users = User.objects.filter(is_premium=True).count()

    # Test stats
    total_tests = Test.objects.filter(is_active=True).count()
    attempts_today = TestAttempt.objects.filter(started_at__date=today).count()
    completed_today = TestAttempt.objects.filter(
        started_at__date=today, status='COMPLETED'
    ).count()
    abandoned_count = TestAttempt.objects.filter(
        started_at__gte=month_ago, status='ABANDONED'
    ).count()

    # Score stats
    avg_score = TestResult.objects.filter(
        calculated_at__gte=month_ago
    ).aggregate(avg=Avg('total_score'))['avg']

    # Recent users
    recent_users = User.objects.order_by('-created_at')[:10]

    # Recent attempts
    recent_attempts = TestAttempt.objects.select_related(
        'user', 'test'
    ).order_by('-started_at')[:10]

    # Problem tests (most abandoned)
    problem_tests = (
        TestAttempt.objects
        .filter(status='ABANDONED', started_at__gte=month_ago)
        .values('test__id', 'test__year', 'test__month', 'test__form', 'test__test_type')
        .annotate(abandon_count=Count('id'))
        .order_by('-abandon_count')[:5]
    )

    # Daily signups for chart (last 14 days)
    chart_data = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        count = User.objects.filter(created_at__date=day).count()
        chart_data.append({'date': day.strftime('%b %d'), 'count': count})

    ctx = {
        'users_today': users_today,
        'users_week': users_week,
        'users_month': users_month,
        'total_users': total_users,
        'premium_users': premium_users,
        'total_tests': total_tests,
        'attempts_today': attempts_today,
        'completed_today': completed_today,
        'abandoned_count': abandoned_count,
        'avg_score': round(avg_score) if avg_score else 0,
        'recent_users': recent_users,
        'recent_attempts': recent_attempts,
        'problem_tests': problem_tests,
        'chart_data_json': json.dumps(chart_data),
        'bank_question_count': BankQuestion.objects.count(),
        **panel_context(),
    }
    return render(request, 'panel/dashboard.html', ctx)


@staff_required
def users_view(request):
    q = request.GET.get('q', '')
    filter_premium = request.GET.get('premium', '')
    filter_country = request.GET.get('country', '')

    users = User.objects.select_related('stats').order_by('-created_at')

    if q:
        users = users.filter(
            Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
    if filter_premium == '1':
        users = users.filter(is_premium=True)
    elif filter_premium == '0':
        users = users.filter(is_premium=False)
    if filter_country:
        users = users.filter(country__icontains=filter_country)

    paginator = Paginator(users, 30)
    page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'panel/users.html', {
        'page_obj': page,
        'q': q,
        'filter_premium': filter_premium,
        'filter_country': filter_country,
        **panel_context(),
    })


@staff_required
def toggle_premium_view(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    user = get_object_or_404(User, id=user_id)
    user.is_premium = not user.is_premium
    if user.is_premium:
        user.premium_until = timezone.now() + timedelta(days=365)
    else:
        user.premium_until = None
    user.save(update_fields=['is_premium', 'premium_until'])
    return JsonResponse({'is_premium': user.is_premium})


@staff_required
def tests_view(request):
    tests = Test.objects.annotate(
        attempt_count=Count('attempts'),
        completed_count=Count('attempts', filter=Q(attempts__status='COMPLETED')),
    ).order_by('-year', '-month')

    return render(request, 'panel/tests.html', {
        'tests': tests,
        **panel_context(),
    })


@staff_required
def toggle_test_active_view(request, test_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    test = get_object_or_404(Test, id=test_id)
    test.is_active = not test.is_active
    test.save(update_fields=['is_active'])
    return JsonResponse({'is_active': test.is_active})


@staff_required
def import_test_json_view(request):
    """Import full SAT test from JSON (existing format)."""
    if request.method == 'GET':
        return render(request, 'panel/import_test.html', panel_context())

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Noto\'g\'ri JSON format'}, status=400)

    try:
        from tests_app.models import Test, TestSection, Module, Question, Choice
        test, created = Test.objects.get_or_create(
            test_type=data.get('test_type', 'SAT'),
            year=data['year'],
            month=data['month'],
            form=data.get('form', 'A'),
            is_international=data.get('is_international', False),
        )
        stats = {'test_id': test.id, 'created': created, 'questions': 0}

        for sec_data in data.get('sections', []):
            section, _ = TestSection.objects.get_or_create(
                test=test, section_type=sec_data['section_type']
            )
            for mod_data in sec_data.get('modules', []):
                module, _ = Module.objects.get_or_create(
                    section=section,
                    module_number=mod_data['module_number'],
                    defaults={'time_limit': mod_data.get('time_limit', 35)}
                )
                for q_data in mod_data.get('questions', []):
                    question, q_created = Question.objects.update_or_create(
                        module=module,
                        number=q_data['number'],
                        defaults={
                            'question_type': q_data.get('question_type', 'MCQ'),
                            'content': q_data.get('content') or '',
                            'passage': q_data.get('passage') or '',
                            'table_data': q_data.get('table_data'),
                            'correct_answer': q_data['correct_answer'],
                            'explanation': q_data.get('explanation') or '',
                            'difficulty': q_data.get('difficulty', 'MEDIUM'),
                        }
                    )
                    if q_created or q_data.get('update_choices'):
                        question.choices.all().delete()
                        for c in q_data.get('choices', []):
                            Choice.objects.create(
                                question=question, option=c['option'], text=c['text']
                            )
                    stats['questions'] += 1

        return JsonResponse({'success': True, **stats})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@staff_required
def questions_view(request):
    subject = request.GET.get('subject', '')
    topic = request.GET.get('topic', '')
    difficulty = request.GET.get('difficulty', '')
    year = request.GET.get('year', '')
    q_search = request.GET.get('q', '')

    questions = BankQuestion.objects.all()

    if subject:
        questions = questions.filter(subject=subject)
    if topic:
        questions = questions.filter(topic__icontains=topic)
    if difficulty:
        questions = questions.filter(difficulty=difficulty)
    if year:
        questions = questions.filter(year=year)
    if q_search:
        questions = questions.filter(content__icontains=q_search)

    # Unique topics for filter
    topics = BankQuestion.objects.values_list('topic', flat=True).distinct().order_by('topic')
    years = BankQuestion.objects.exclude(year=None).values_list('year', flat=True).distinct().order_by('-year')

    paginator = Paginator(questions, 20)
    page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'panel/questions.html', {
        'page_obj': page,
        'subject': subject,
        'topic': topic,
        'difficulty': difficulty,
        'year_filter': year,
        'q_search': q_search,
        'topics': list(topics),
        'years': list(years),
        'subjects': BankQuestion.Subject.choices,
        'difficulties': BankQuestion.Difficulty.choices,
        **panel_context(),
    })


@staff_required
def import_questions_view(request):
    """Import BankQuestions from JSON (Uzbek format)."""
    if request.method == 'GET':
        return render(request, 'panel/import_questions.html', panel_context())

    try:
        body = request.body.decode('utf-8')
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Noto\'g\'ri JSON format'}, status=400)

    # Support both single object and list
    if isinstance(data, dict):
        data = [data]

    created_count = 0
    errors = []

    DIFFICULTY_MAP = {
        'easy': 'easy', 'oson': 'easy',
        'medium': 'medium', "o'rta": 'medium', "orta": 'medium',
        'hard': 'hard', 'qiyin': 'hard',
    }
    SUBJECT_MAP = {
        'matematika': 'Matematika', 'math': 'Matematika',
        'ingliz tili': 'Ingliz tili', 'english': 'Ingliz tili',
        'reading': 'Reading', 'reading & writing': 'Reading',
    }

    for i, item in enumerate(data):
        try:
            diff_raw = str(item.get('Qiyinlik', 'medium')).lower().strip()
            difficulty = DIFFICULTY_MAP.get(diff_raw, 'medium')

            subject_raw = str(item.get('Fan', 'Matematika')).lower().strip()
            subject = SUBJECT_MAP.get(subject_raw, item.get('Fan', 'Matematika'))

            correct = str(item.get('Togri_javob', '')).strip().upper()

            BankQuestion.objects.create(
                subject=subject,
                topic=item.get('Mavzu', ''),
                content=item.get('Savol', ''),
                difficulty=difficulty,
                choice_a=item.get('A', ''),
                choice_b=item.get('B', ''),
                choice_c=item.get('C', ''),
                choice_d=item.get('D', ''),
                correct_answer=correct,
                explanation=item.get('Izoh', ''),
                year=item.get('Yil') or item.get('year') or None,
                source=item.get('Manba', ''),
            )
            created_count += 1
        except Exception as e:
            errors.append(f"#{i+1}: {str(e)}")

    return JsonResponse({
        'success': True,
        'created': created_count,
        'errors': errors,
        'total': len(data),
    })


@staff_required
def delete_question_view(request, question_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    question = get_object_or_404(BankQuestion, id=question_id)
    question.delete()
    return JsonResponse({'deleted': True})


@staff_required
def analytics_view(request):
    now = timezone.now()
    month_ago = now - timedelta(days=30)

    # Attempts by status
    status_stats = TestAttempt.objects.values('status').annotate(count=Count('id'))

    # Score distribution
    score_ranges = [
        ('400-600', 400, 600),
        ('600-800', 600, 800),
        ('800-1000', 800, 1000),
        ('1000-1200', 1000, 1200),
        ('1200-1400', 1200, 1400),
        ('1400-1600', 1400, 1601),
    ]
    score_dist = []
    for label, low, high in score_ranges:
        count = TestResult.objects.filter(total_score__gte=low, total_score__lt=high).count()
        score_dist.append({'range': label, 'count': count})

    # Most attempted tests
    top_tests = (
        Test.objects
        .annotate(attempt_count=Count('attempts'))
        .order_by('-attempt_count')[:10]
    )

    # Question bank stats
    bank_by_subject = BankQuestion.objects.values('subject').annotate(count=Count('id'))
    bank_by_difficulty = BankQuestion.objects.values('difficulty').annotate(count=Count('id'))

    # Daily attempts (14 days)
    today = now.date()
    daily_attempts = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        count = TestAttempt.objects.filter(started_at__date=day).count()
        completed = TestAttempt.objects.filter(started_at__date=day, status='COMPLETED').count()
        daily_attempts.append({
            'date': day.strftime('%b %d'),
            'total': count,
            'completed': completed,
        })

    return render(request, 'panel/analytics.html', {
        'status_stats': list(status_stats),
        'score_dist': score_dist,
        'top_tests': top_tests,
        'bank_by_subject': list(bank_by_subject),
        'bank_by_difficulty': list(bank_by_difficulty),
        'daily_attempts_json': json.dumps(daily_attempts),
        'score_dist_json': json.dumps(score_dist),
        **panel_context(),
    })
