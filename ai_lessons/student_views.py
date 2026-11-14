"""
What students may do with lessons. Only PUBLISHED lessons exist here.

Correct answers and explanations leave the server only inside an answer the
student has already given, so they cannot be read from the page beforehand.
"""
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .marking import mark_written
from .models import AILesson, LessonAnswer, LessonProgress, LessonQuestion
from .play import answer_data, asked_in_lesson, lesson_play_data, question_data, visible_questions
from .questions import QuestionError, mark
from .recommend import recommendations


def _published():
    return AILesson.objects.filter(status=AILesson.Status.PUBLISHED)


def _progress_data(progress, steps_count):
    if progress is None:
        return {'last_step': 0, 'percent': 0, 'completed': False}
    completed = progress.completed_at is not None
    percent = 100 if completed else round(progress.last_step / steps_count * 100) if steps_count else 0
    return {'last_step': progress.last_step, 'percent': percent, 'completed': completed}


def _lesson_card(lesson, progress, steps_count):
    return {
        'id': lesson.id,
        'title': lesson.title,
        'subject': lesson.subject,
        'section': lesson.section,
        'level': lesson.level,
        'duration_minutes': lesson.duration_minutes,
        'progress': _progress_data(progress, steps_count),
    }


def _my_progress(user):
    return {
        p.lesson_id: p
        for p in LessonProgress.objects.filter(user=user, lesson__status=AILesson.Status.PUBLISHED)
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_list(request):
    lessons = _published().annotate(steps_count=Count('steps')).order_by('-created_at')
    subject = request.query_params.get('subject', '').upper()
    if subject:
        lessons = lessons.filter(subject__in=[subject, AILesson.Subject.GENERAL])
    mine = _my_progress(request.user)
    return Response([_lesson_card(lesson, mine.get(lesson.id), lesson.steps_count) for lesson in lessons])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_recommended(request):
    """
    ?exam=sat|ielts|cefr (or none for all): the student's weak spots and the
    lessons that teach them, each with the spots it covers.
    """
    exam = request.query_params.get('exam', '').lower() or None
    found = recommendations(request.user, exam)
    mine = _my_progress(request.user)
    counts = dict(
        _published().filter(pk__in=[lesson.id for lesson, _ in found['lessons']])
        .annotate(n=Count('steps')).values_list('id', 'n')
    )
    public = ('name', 'area', 'result')
    return Response({
        'spots': [{k: s[k] for k in public} for s in found['spots']],
        'lessons': [
            {**_lesson_card(lesson, mine.get(lesson.id), counts.get(lesson.id, 0)),
             'covers': [{k: s[k] for k in public} for s in covers]}
            for lesson, covers in found['lessons']
        ],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_detail(request, pk):
    lesson = get_object_or_404(_published(), pk=pk)
    progress = LessonProgress.objects.filter(user=request.user, lesson=lesson).first()
    completed = bool(progress and progress.completed_at)

    questions = list(visible_questions(lesson.questions))
    answers = {
        a.question_id: a
        for a in LessonAnswer.objects.filter(user=request.user, question__lesson=lesson)
    }
    data = lesson_play_data(lesson, request)
    last = data['steps'][-1]['id'] if data['steps'] else None
    homework = [q for q in questions if q.is_homework]

    return Response({
        **data,
        'progress': _progress_data(progress, len(data['steps'])),
        'questions': [question_data(q, answers.get(q.id), last) for q in asked_in_lesson(questions, last)],
        'homework_count': len(homework),
        # The homework opens once the lesson has been watched to the end.
        'homework': [question_data(q, answers.get(q.id)) for q in homework] if completed else None,
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
        visible_questions(LessonQuestion.objects).select_related('lesson'),
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
        return Response(answer_data(existing, question))

    try:
        choice, text, is_correct = mark(question, request.data.get('choice'), request.data.get('text'))
    except QuestionError as error:
        return Response({'error': str(error)}, status=400)

    answer, created = LessonAnswer.objects.get_or_create(
        user=request.user, question=question,
        defaults={'choice': choice, 'text': text, 'is_correct': is_correct},
    )
    if created and question.kind == LessonQuestion.Kind.WRITTEN:
        mark_written(answer)            # a few seconds; the answer is saved either way
    return Response(answer_data(answer, question), status=201 if created else 200)
