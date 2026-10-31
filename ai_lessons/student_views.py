"""
What students may do with lessons. Only PUBLISHED lessons exist here.

Correct answers and explanations leave the server only inside an answer the
student has already given, so they cannot be read from the page beforehand.
"""
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AILesson, LessonAnswer, LessonProgress, LessonQuestion
from .questions import QuestionError, mark
from .views import lesson_play_data


def _published():
    return AILesson.objects.filter(status=AILesson.Status.PUBLISHED)


def _progress_data(progress, steps_count):
    if progress is None:
        return {'last_step': 0, 'percent': 0, 'completed': False}
    completed = progress.completed_at is not None
    percent = 100 if completed else round(progress.last_step / steps_count * 100) if steps_count else 0
    return {'last_step': progress.last_step, 'percent': percent, 'completed': completed}


def _answer_data(answer, question):
    return {
        'choice': answer.choice,
        'text': answer.text,
        'is_correct': answer.is_correct,
        'correct': question.correct,
        'explanation': question.explanation,
    }


def _question_data(question, answers):
    answer = answers.get(question.id)
    return {
        'id': question.id,
        'step': question.step_id,
        'kind': question.kind,
        'prompt': question.prompt,
        'options': question.options,
        'answer': _answer_data(answer, question) if answer else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_list(request):
    lessons = _published().annotate(steps_count=Count('steps')).order_by('-created_at')
    subject = request.query_params.get('subject', '').upper()
    if subject:
        lessons = lessons.filter(subject__in=[subject, AILesson.Subject.GENERAL])
    mine = {
        p.lesson_id: p
        for p in LessonProgress.objects.filter(user=request.user, lesson__status=AILesson.Status.PUBLISHED)
    }
    return Response([
        {
            'id': lesson.id,
            'title': lesson.title,
            'subject': lesson.subject,
            'section': lesson.section,
            'level': lesson.level,
            'duration_minutes': lesson.duration_minutes,
            'progress': _progress_data(mine.get(lesson.id), lesson.steps_count),
        }
        for lesson in lessons
    ])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_detail(request, pk):
    lesson = get_object_or_404(_published(), pk=pk)
    progress = LessonProgress.objects.filter(user=request.user, lesson=lesson).first()
    completed = bool(progress and progress.completed_at)

    questions = list(lesson.questions.filter(Q(is_homework=True) | Q(step__isnull=False)))
    answers = {
        a.question_id: a
        for a in LessonAnswer.objects.filter(user=request.user, question__lesson=lesson)
    }
    in_lesson = [q for q in questions if not q.is_homework]
    homework = [q for q in questions if q.is_homework]

    data = lesson_play_data(lesson, request)
    return Response({
        **data,
        'progress': _progress_data(progress, len(data['steps'])),
        'questions': [_question_data(q, answers) for q in in_lesson],
        'homework_count': len(homework),
        # The homework opens once the lesson has been watched to the end.
        'homework': [_question_data(q, answers) for q in homework] if completed else None,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def lesson_progress(request, pk):
    lesson = get_object_or_404(_published(), pk=pk)
    steps_count = lesson.steps.count()
    try:
        step = int(request.data.get('step', 0))
    except (TypeError, ValueError):
        return Response({'error': 'Bad step.'}, status=400)
    step = min(max(step, 0), max(steps_count - 1, 0))

    progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    progress.last_step = step
    fields = ['last_step', 'updated_at']
    if request.data.get('completed') is True and progress.completed_at is None:
        progress.completed_at = timezone.now()
        fields.append('completed_at')
    progress.save(update_fields=fields)
    return Response(_progress_data(progress, steps_count))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def question_answer(request, pk):
    """One answer per question: sending it again returns the first one unchanged."""
    question = get_object_or_404(
        LessonQuestion.objects.select_related('lesson').filter(Q(is_homework=True) | Q(step__isnull=False)),
        pk=pk, lesson__status=AILesson.Status.PUBLISHED,
    )
    if question.is_homework:
        finished = LessonProgress.objects.filter(
            user=request.user, lesson=question.lesson, completed_at__isnull=False,
        ).exists()
        if not finished:
            return Response({'error': 'Finish the lesson first.'}, status=403)

    existing = LessonAnswer.objects.filter(user=request.user, question=question).first()
    if existing:
        return Response(_answer_data(existing, question))

    try:
        choice, text, is_correct = mark(question, request.data.get('choice'), request.data.get('text'))
    except QuestionError as error:
        return Response({'error': str(error)}, status=400)

    answer, created = LessonAnswer.objects.get_or_create(
        user=request.user, question=question,
        defaults={'choice': choice, 'text': text, 'is_correct': is_correct},
    )
    return Response(_answer_data(answer, question), status=201 if created else 200)
