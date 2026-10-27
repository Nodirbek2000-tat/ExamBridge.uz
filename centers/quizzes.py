"""
A center's own A/B/C/D tests ("quizzes").

Staff (director, admin, teacher) build a question bank — by JSON import or
one question at a time — and choose how attempts are drawn:

  pick_count    how many questions an attempt gets (never more than the bank)
  same_for_all  True:  everyone gets the same questions, in the same order
                False: each student gets their own draw; questions that were
                       drawn least so far go first, so the whole bank is used
                       evenly across students
  shuffle_options  each student sees the A/B/C/D options in their own order

Active quizzes appear in the students' Tests section. A quiz is also a task
type ('center_quiz'), so it can be homework in a lesson or an olympiad, and
its results reach monitoring like any other test (centers/progress.py).
"""
import random
from collections import Counter

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    AssignmentSubmission, CenterMembership, LearningCenter, OlympiadParticipant, Quiz, QuizAttempt, QuizQuestion,
)
from .views import center_role

STAFF = ('director', 'admin', 'teacher')
LETTERS = 'ABCD'
MAX_QUESTIONS = 500


def _name(u):
    return f'{u.first_name} {u.last_name}'.strip() or u.email


# ── drawing questions ─────────────────────────────────────────────────────────

def draw(quiz, student):
    """(question ids in order, {qid: option order}) for a new attempt."""
    questions = list(quiz.questions.values_list('id', 'options'))
    ids = [q[0] for q in questions]
    n = min(quiz.pick_count or len(ids), len(ids))
    if quiz.same_for_all:
        # one fixed draw, until the bank changes
        rng = random.Random(f'{quiz.id}:{n}:{",".join(map(str, ids))}')
        picked = rng.sample(ids, n)
    else:
        rng = random.Random()
        used = Counter()
        for qids in quiz.attempts.values_list('question_ids', flat=True):
            used.update(qids)
        picked = sorted(ids, key=lambda i: (used[i], rng.random()))[:n]
        rng.shuffle(picked)
    orders = {}
    option_counts = dict(questions)
    for qid in picked:
        order = list(range(len(option_counts[qid])))
        if quiz.shuffle_options:
            rng.shuffle(order)
        orders[str(qid)] = order
    return picked, orders


def grade(attempt):
    correct_of = dict(QuizQuestion.objects.filter(id__in=attempt.question_ids).values_list('id', 'correct'))
    attempt.correct = sum(1 for qid in attempt.question_ids
                          if str(qid) in attempt.answers and attempt.answers[str(qid)] == correct_of.get(qid))
    attempt.total = len(attempt.question_ids)


def finish(attempt, at=None):
    if attempt.finished_at:
        return
    grade(attempt)
    attempt.finished_at = min(at or timezone.now(), attempt.deadline)
    attempt.save(update_fields=['correct', 'total', 'finished_at', 'answers'])


# ── parsing questions ─────────────────────────────────────────────────────────

def parse_question(item, n):
    """A question dict from JSON / the form, or raise ValueError('Question n: …')."""
    if not isinstance(item, dict):
        raise ValueError(f'Question {n}: must be an object')
    text = str(item.get('question') or item.get('text') or '').strip()
    options = item.get('options') or item.get('variants') or []
    if isinstance(options, dict):          # {"A": "...", "B": "..."}
        options = [options[k] for k in sorted(options)]
    options = [str(o).strip() for o in options if str(o).strip()]
    if not text:
        raise ValueError(f'Question {n}: the question text is empty')
    if not 2 <= len(options) <= 4:
        raise ValueError(f'Question {n}: needs 2 to 4 options (A–D)')
    answer = item.get('answer', item.get('correct'))
    if isinstance(answer, str) and answer.strip().upper() in LETTERS[:len(options)] and len(answer.strip()) == 1:
        correct = LETTERS.index(answer.strip().upper())
    elif isinstance(answer, int) and 0 <= answer < len(options):
        correct = answer
    elif isinstance(answer, str) and answer.strip() in options:
        correct = options.index(answer.strip())
    else:
        raise ValueError(f'Question {n}: "answer" must be a letter A–{LETTERS[len(options) - 1]}')
    return {'text': text, 'options': options, 'correct': correct,
            'explanation': str(item.get('explanation') or '').strip()}


def question_dict(q):
    return {'id': q.id, 'text': q.text, 'options': q.options, 'correct': q.correct,
            'answer': LETTERS[q.correct], 'explanation': q.explanation}


# ── staff ─────────────────────────────────────────────────────────────────────

def _staff(request, center_id):
    center = LearningCenter.objects.filter(id=center_id).first()
    if not center:
        return None, Response({'error': 'Not found'}, status=404)
    if center_role(request.user, center) not in STAFF and not getattr(request.user, 'can_write_admin', False):
        return None, Response({'error': 'Forbidden'}, status=403)
    return center, None


def quiz_summary(quiz):
    finished = quiz.attempts.filter(finished_at__isnull=False)
    agg = finished.aggregate(n=Count('id'), students=Count('student', distinct=True))
    avg = None
    if agg['n']:
        rows = list(finished.values_list('correct', 'total'))
        avg = round(sum(c / t * 100 for c, t in rows if t) / len(rows))
    bank = quiz.questions.count()
    return {
        'id': quiz.id, 'title': quiz.title, 'description': quiz.description,
        'time_minutes': quiz.time_minutes, 'pick_count': quiz.pick_count, 'questions': bank,
        'per_attempt': min(quiz.pick_count or bank, bank),
        'same_for_all': quiz.same_for_all, 'shuffle_options': quiz.shuffle_options,
        'show_answers': quiz.show_answers, 'is_active': quiz.is_active,
        'attempts': agg['n'], 'students': agg['students'], 'average': avg,
        'created_by': _name(quiz.created_by) if quiz.created_by else '',
        'updated_at': quiz.updated_at.isoformat(),
    }


def _apply_settings(quiz, d):
    if 'title' in d:
        if not str(d['title']).strip():
            raise ValueError('Give the test a title')
        quiz.title = str(d['title']).strip()[:200]
    if 'description' in d:
        quiz.description = str(d.get('description') or '').strip()
    if 'time_minutes' in d:
        t = int(d['time_minutes'] or 0)
        if not 1 <= t <= 300:
            raise ValueError('Time must be 1–300 minutes')
        quiz.time_minutes = t
    if 'pick_count' in d:
        quiz.pick_count = int(d['pick_count']) if d['pick_count'] not in (None, '', 0, '0') else None
        if quiz.pick_count is not None and quiz.pick_count < 1:
            raise ValueError('Questions per attempt must be at least 1')
    for flag in ('same_for_all', 'shuffle_options', 'show_answers', 'is_active'):
        if flag in d:
            setattr(quiz, flag, bool(d[flag]))


def _add_questions(quiz, items, replace=False):
    parsed = [parse_question(item, i) for i, item in enumerate(items, 1)]
    if not parsed:
        raise ValueError('No questions given')
    with transaction.atomic():
        if replace:
            quiz.questions.all().delete()
        start = (quiz.questions.order_by('-order').values_list('order', flat=True).first() or 0) + 1
        if quiz.questions.count() + len(parsed) > MAX_QUESTIONS:
            raise ValueError(f'A test holds at most {MAX_QUESTIONS} questions')
        QuizQuestion.objects.bulk_create([QuizQuestion(quiz=quiz, order=start + i, **p) for i, p in enumerate(parsed)])
        quiz.save(update_fields=['updated_at'])
    return len(parsed)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def quizzes(request, center_id):
    center, err = _staff(request, center_id)
    if err:
        return err
    if request.method == 'GET':
        return Response([quiz_summary(q) for q in center.quizzes.select_related('created_by')])
    quiz = Quiz(center=center, created_by=request.user, title='')
    try:
        _apply_settings(quiz, {'title': '', **request.data})
        quiz.save()
        if request.data.get('questions'):
            _add_questions(quiz, request.data['questions'])
    except (ValueError, TypeError) as e:
        if quiz.pk:
            quiz.delete()
        return Response({'error': str(e)}, status=400)
    return Response(quiz_summary(quiz), status=201)


def _quiz(center, quiz_id):
    return center.quizzes.select_related('created_by').filter(id=quiz_id).first()


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def quiz_detail(request, center_id, quiz_id):
    center, err = _staff(request, center_id)
    if err:
        return err
    quiz = _quiz(center, quiz_id)
    if not quiz:
        return Response({'error': 'Not found'}, status=404)
    if request.method == 'DELETE':
        quiz.delete()
        return Response({'deleted': True})
    if request.method == 'PATCH':
        try:
            _apply_settings(quiz, request.data)
        except (ValueError, TypeError) as e:
            return Response({'error': str(e)}, status=400)
        if quiz.is_active and not quiz.questions.exists():
            return Response({'error': 'Add questions before making the test active'}, status=400)
        quiz.save()
    return Response({**quiz_summary(quiz), 'question_list': [question_dict(q) for q in quiz.questions.all()]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quiz_import(request, center_id, quiz_id):
    """JSON: {"questions": [{"question", "options": [...], "answer": "B", "explanation"}]} or a bare list."""
    center, err = _staff(request, center_id)
    if err:
        return err
    quiz = _quiz(center, quiz_id)
    if not quiz:
        return Response({'error': 'Not found'}, status=404)
    data = request.data
    items = data if isinstance(data, list) else data.get('questions')
    if not isinstance(items, list):
        return Response({'error': 'Send {"questions": [ ... ]}'}, status=400)
    try:
        added = _add_questions(quiz, items, replace=bool(not isinstance(data, list) and data.get('replace')))
    except ValueError as e:
        return Response({'error': str(e)}, status=400)
    return Response({'added': added, 'questions': quiz.questions.count()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quiz_add_question(request, center_id, quiz_id):
    center, err = _staff(request, center_id)
    if err:
        return err
    quiz = _quiz(center, quiz_id)
    if not quiz:
        return Response({'error': 'Not found'}, status=404)
    try:
        _add_questions(quiz, [request.data])
    except ValueError as e:
        return Response({'error': str(e).replace('Question 1: ', '')}, status=400)
    return Response(question_dict(quiz.questions.order_by('-order', '-id').first()), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def quiz_question(request, center_id, quiz_id, question_id):
    center, err = _staff(request, center_id)
    if err:
        return err
    q = QuizQuestion.objects.filter(id=question_id, quiz_id=quiz_id, quiz__center=center).select_related('quiz').first()
    if not q:
        return Response({'error': 'Not found'}, status=404)
    if request.method == 'DELETE':
        q.delete()
        q.quiz.save(update_fields=['updated_at'])
        return Response({'deleted': True})
    try:
        p = parse_question({**question_dict(q), 'question': q.text, 'answer': q.correct, **request.data}, 1)
    except ValueError as e:
        return Response({'error': str(e).replace('Question 1: ', '')}, status=400)
    for k, v in p.items():
        setattr(q, k, v)
    q.save()
    q.quiz.save(update_fields=['updated_at'])
    return Response(question_dict(q))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quiz_results(request, center_id, quiz_id):
    center, err = _staff(request, center_id)
    if err:
        return err
    quiz = _quiz(center, quiz_id)
    if not quiz:
        return Response({'error': 'Not found'}, status=404)
    classes = {m.user_id: m.school_class for m in CenterMembership.objects.filter(center=center, role='student')}
    rows = []
    for a in quiz.attempts.select_related('student').order_by('-started_at')[:500]:
        if not a.finished_at and timezone.now() > a.deadline:
            finish(a, a.deadline)
        rows.append({
            'id': a.id, 'student': _name(a.student), 'student_id': a.student_id,
            'school_class': classes.get(a.student_id, ''),
            'finished': bool(a.finished_at), 'correct': a.correct, 'total': a.total or len(a.question_ids),
            'percent': a.percent if a.finished_at else None,
            'seconds': int((a.finished_at - a.started_at).total_seconds()) if a.finished_at else None,
            'started_at': a.started_at.isoformat(),
        })
    return Response({**quiz_summary(quiz), 'rows': rows})


# ── students ──────────────────────────────────────────────────────────────────

def _may_take(user, quiz):
    """Active quizzes for the center's students; any quiz assigned to them; staff may try their own."""
    role = center_role(user, quiz.center)
    if role in STAFF:
        return True
    if role != 'student':
        return False
    if quiz.is_active:
        return True
    return (AssignmentSubmission.objects.filter(student=user, assignment__task_type='center_quiz',
                                                assignment__task_ref_id=quiz.id).exists()
            or OlympiadParticipant.objects.filter(student=user, olympiad__task_type='center_quiz',
                                                  olympiad__task_ref_id=quiz.id).exists())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_quizzes(request):
    centers = CenterMembership.objects.filter(user=request.user, role='student', is_active=True).values_list('center_id', flat=True)
    out = []
    mine = QuizAttempt.objects.filter(student=request.user, finished_at__isnull=False)
    for quiz in (Quiz.objects.filter(center_id__in=centers, is_active=True).select_related('center')
                 .annotate(n=Count('questions')).filter(n__gt=0)):
        attempts = list(mine.filter(quiz=quiz))
        best = max(attempts, key=lambda a: a.percent, default=None)
        out.append({
            'id': quiz.id, 'title': quiz.title, 'description': quiz.description, 'time_minutes': quiz.time_minutes,
            'questions': min(quiz.pick_count or quiz.n, quiz.n), 'attempts': len(attempts),
            'best': {'percent': best.percent, 'correct': best.correct, 'total': best.total} if best else None,
            'center': quiz.center.name,
        })
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quiz_start(request, quiz_id):
    quiz = Quiz.objects.select_related('center').filter(id=quiz_id).first()
    if not quiz or not _may_take(request.user, quiz):
        return Response({'error': 'Not found'}, status=404)
    if not quiz.questions.exists():
        return Response({'error': 'This test has no questions yet'}, status=400)
    now = timezone.now()
    for a in QuizAttempt.objects.filter(quiz=quiz, student=request.user, finished_at__isnull=True):
        if now < a.deadline:
            return Response({'attempt_id': a.id, 'resumed': True})
        finish(a, a.deadline)
    ids, orders = draw(quiz, request.user)
    a = QuizAttempt.objects.create(quiz=quiz, student=request.user, question_ids=ids, option_orders=orders,
                                   total=len(ids))
    return Response({'attempt_id': a.id, 'resumed': False}, status=201)


def _attempt_payload(a):
    quiz = a.quiz
    by_id = {q.id: q for q in QuizQuestion.objects.filter(id__in=a.question_ids)}
    done = bool(a.finished_at)
    reveal = done and quiz.show_answers
    questions = []
    for qid in a.question_ids:
        q = by_id.get(qid)
        if not q:
            continue
        order = a.option_orders.get(str(qid)) or list(range(len(q.options)))
        chosen = a.answers.get(str(qid))
        item = {'id': qid, 'text': q.text, 'options': [q.options[i] for i in order],
                'chosen': order.index(chosen) if chosen is not None and chosen in order else None}
        if reveal:
            item['correct'] = order.index(q.correct) if q.correct in order else None
            item['explanation'] = q.explanation
        questions.append(item)
    return {
        'id': a.id, 'quiz': {'id': quiz.id, 'title': quiz.title, 'description': quiz.description,
                             'time_minutes': quiz.time_minutes, 'show_answers': quiz.show_answers},
        'started_at': a.started_at.isoformat(), 'deadline': a.deadline.isoformat(),
        'server_now': timezone.now().isoformat(), 'finished': done,
        'result': {'correct': a.correct, 'total': a.total, 'percent': a.percent,
                   'seconds': int((a.finished_at - a.started_at).total_seconds())} if done else None,
        'questions': questions,
    }


def _own_attempt(request, attempt_id):
    a = QuizAttempt.objects.select_related('quiz').filter(id=attempt_id, student=request.user).first()
    if a and not a.finished_at and timezone.now() > a.deadline:
        finish(a, a.deadline)              # time is up: hand in what was answered
    return a


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quiz_attempt(request, attempt_id):
    a = _own_attempt(request, attempt_id)
    if not a:
        return Response({'error': 'Not found'}, status=404)
    return Response(_attempt_payload(a))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quiz_answer(request, attempt_id):
    """Save one answer: {question_id, option} — option is the position the student saw (0 = A)."""
    a = _own_attempt(request, attempt_id)
    if not a:
        return Response({'error': 'Not found'}, status=404)
    if a.finished_at:
        return Response({'error': 'finished'}, status=400)
    qid = str(request.data.get('question_id'))
    order = a.option_orders.get(qid)
    try:
        shown = int(request.data.get('option'))
        original = order[shown]
    except (TypeError, ValueError, IndexError):
        return Response({'error': 'Bad answer'}, status=400)
    a.answers = {**a.answers, qid: original}
    a.save(update_fields=['answers'])
    return Response({'saved': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quiz_submit(request, attempt_id):
    a = _own_attempt(request, attempt_id)
    if not a:
        return Response({'error': 'Not found'}, status=404)
    finish(a)
    # the task / olympiad this belongs to picks the result up right away
    try:
        from .views import auto_complete_assignments
        auto_complete_assignments(request.user, 'center_quiz')
    except Exception:
        pass
    return Response(_attempt_payload(a))

