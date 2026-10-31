"""
AI lessons admin API: lessons and the sources they are built from.

Lives under /api/admin/ai-lessons/, the AI area in accounts/admin_access.py.
The status cannot be edited like a field: a lesson only reaches students through
the publish action, which first checks it is fit to be seen.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .board import BoardError, clean_step
from .models import AILesson, LessonJob, LessonQuestion, LessonSource, LessonStep
from .pipeline import JobBusy, expire_stale_jobs, latest_job, start_job
from .providers import FakeProvider, LessonAIError
from .questions import QuestionError, clean_question

MAX_TITLE = 200
MAX_TEXT_CHARS = 200_000          # one pasted source; a whole book does not fit in a prompt
MAX_URL = LessonSource._meta.get_field('url').max_length
MAX_FILE_BYTES = 20 * 1024 * 1024
FILE_EXTENSIONS = {'.pdf', '.docx', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp'}

BUSY = (AILesson.Status.GENERATING, AILesson.Status.VOICING)

is_http_url = URLValidator(schemes=['http', 'https'])


def _source_data(s, request):
    return {
        'id': s.id,
        'kind': s.kind,
        'title': s.title,
        'text': s.text,
        'url': s.url,
        'file': request.build_absolute_uri(s.file.url) if s.file else None,
        'created_at': s.created_at,
    }


def _job_data(lesson):
    job = latest_job(lesson)
    if job is None:
        return None
    return {
        'kind': job.kind,
        'status': job.status,
        'stage': job.stage,
        'done': job.done,
        'total': job.total,
        'provider': job.provider,
        'error': job.error,
        'review': (job.data or {}).get('review'),
        'skipped': (job.data or {}).get('skipped', []),
        'updated_at': job.updated_at,
    }


def _question_data(q):
    return {
        'id': q.id,
        'kind': q.kind,
        'step': q.step_id,
        'is_homework': q.is_homework,
        'prompt': q.prompt,
        'options': q.options,
        'correct': q.correct,
        'rubric': q.rubric,
        'explanation': q.explanation,
        'origin': q.origin,
        'ai_verdict': q.ai_verdict,
        'ai_note': q.ai_note,
        'answers': q.answers_count,
        'right_answers': q.right_count,
    }


def _questions_with_counts(lesson):
    return lesson.questions.annotate(
        answers_count=Count('answers'),
        right_count=Count('answers', filter=Q(answers__is_correct=True)),
    )


def _lesson_data(lesson, request=None, full=False):
    data = {
        'id': lesson.id,
        'title': lesson.title,
        'subject': lesson.subject,
        'section': lesson.section,
        'language': lesson.language,
        'duration_minutes': lesson.duration_minutes,
        'level': lesson.level,
        'voice': lesson.voice,
        'brief': lesson.brief,
        'status': lesson.status,
        'ai_provider': lesson.ai_provider,
        'created_at': lesson.created_at,
        'updated_at': lesson.updated_at,
    }
    if full:
        steps = list(lesson.steps.all())
        data['sources'] = [_source_data(s, request) for s in lesson.sources.all()]
        data['steps'] = [
            {'id': s.id, 'order': s.order, 'say': s.say, 'board': s.board, 'voiced': bool(s.audio)}
            for s in steps
        ]
        data['steps_count'] = len(steps)
        data['job'] = _job_data(lesson)
        data['ai_mode'] = settings.AI_LESSON_PROVIDER
        data['questions'] = [_question_data(q) for q in _questions_with_counts(lesson)]
    else:
        data['sources_count'] = lesson.sources_count
        data['students'] = lesson.students
        data['finished'] = lesson.finished
    return data


def _clean_lesson(data, lesson=None):
    """(fields to save, error). A missing key keeps the lesson's current value."""
    def value(key, default):
        return data.get(key, getattr(lesson, key) if lesson else default)

    title = str(value('title', '')).strip()
    if not title:
        return None, 'Title is required.'
    if len(title) > MAX_TITLE:
        return None, f'Title can be at most {MAX_TITLE} characters.'

    subject = str(value('subject', AILesson.Subject.SAT)).upper()
    if subject not in AILesson.Subject.values:
        return None, 'Unknown subject.'
    section = str(value('section', AILesson.Section.GENERAL))
    if section not in AILesson.Section.values:
        return None, 'Unknown section.'
    language = str(value('language', AILesson.Language.UZBEK))
    if language not in AILesson.Language.values:
        return None, 'Unknown language.'
    try:
        duration = int(value('duration_minutes', AILesson.DURATIONS[0]))
    except (TypeError, ValueError):
        duration = None
    if duration not in AILesson.DURATIONS:
        return None, 'Duration must be 10, 15 or 20 minutes.'

    voice = str(value('voice', AILesson.Voice.NOVA))
    if voice not in AILesson.Voice.values:
        return None, 'Unknown voice.'

    return {
        'title': title,
        'subject': subject,
        'section': section,
        'language': language,
        'duration_minutes': duration,
        'level': str(value('level', '')).strip()[:30],
        'voice': voice,
        'brief': str(value('brief', '')).strip(),
    }, None


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def admin_lessons(request):
    if request.method == 'POST':
        fields, error = _clean_lesson(request.data)
        if error:
            return Response({'error': error}, status=400)
        lesson = AILesson.objects.create(created_by=request.user, **fields)
        return Response(_lesson_data(lesson, request, full=True), status=201)

    lessons = AILesson.objects.annotate(
        sources_count=Count('sources', distinct=True),
        students=Count('progress', distinct=True),
        finished=Count('progress', filter=Q(progress__completed_at__isnull=False), distinct=True),
    )
    subject = request.query_params.get('subject', '').upper()
    if subject:
        lessons = lessons.filter(subject=subject)
    status = request.query_params.get('status', '').upper()
    if status:
        lessons = lessons.filter(status=status)
    q = request.query_params.get('q', '').strip()
    if q:
        lessons = lessons.filter(Q(title__icontains=q) | Q(brief__icontains=q))
    return Response([_lesson_data(lesson) for lesson in lessons])


def _lesson_for_change(pk):
    """The lesson, after releasing it from a job whose worker died (the panel polls this)."""
    lesson = get_object_or_404(AILesson, pk=pk)
    if lesson.status in BUSY:
        expire_stale_jobs(lesson)
        lesson.refresh_from_db()
    return lesson


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_lesson_detail(request, pk):
    lesson = _lesson_for_change(pk)
    if request.method == 'DELETE':
        if lesson.status in BUSY:
            return Response({'error': 'Wait for the running job to finish.'}, status=409)
        files = [s.file for s in lesson.sources.exclude(file='')]
        files += [s.audio for s in lesson.steps.exclude(audio='')]
        lesson.delete()
        for f in files:
            f.delete(save=False)
        return Response({'deleted': True})

    if request.method == 'PATCH':
        fields, error = _clean_lesson(request.data, lesson)
        if error:
            return Response({'error': error}, status=400)
        for key, value in fields.items():
            setattr(lesson, key, value)
        # Only these fields: a worker may be saving the status at this very moment.
        lesson.save(update_fields=[*fields, 'updated_at'])
    return Response(_lesson_data(lesson, request, full=True))


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_lesson_publish(request, pk):
    """{"publish": true} shows the lesson to students, false takes it back to a draft."""
    lesson = _lesson_for_change(pk)
    if lesson.status in BUSY:
        return Response({'error': 'Wait for the running job to finish.'}, status=409)
    publish = request.data.get('publish') is True
    if publish and not lesson.steps.exists():
        return Response({'error': 'A lesson needs steps before it can be published.'}, status=400)
    if publish and lesson.ai_provider == FakeProvider.name:
        return Response({'error': 'This lesson was written by the fake test AI and cannot be published.'}, status=400)
    lesson.status = AILesson.Status.PUBLISHED if publish else AILesson.Status.DRAFT
    lesson.save(update_fields=['status', 'updated_at'])
    return Response(_lesson_data(lesson, request, full=True))


def _start(request, lesson, kind, restart=False):
    try:
        start_job(lesson, kind, restart=restart)
    except JobBusy:
        return Response({'error': 'A job is already running for this lesson.'}, status=409)
    except LessonAIError as error:
        return Response({'error': str(error)}, status=400)
    lesson.refresh_from_db()
    return Response(_lesson_data(lesson, request, full=True), status=202)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_lesson_generate(request, pk):
    """Let the AI write the lesson. A failed job continues unless {"restart": true}."""
    lesson = get_object_or_404(AILesson, pk=pk)
    if lesson.status == AILesson.Status.PUBLISHED:
        return Response({'error': 'Unpublish the lesson before writing it again.'}, status=409)
    if not lesson.brief.strip() and not lesson.sources.exists():
        return Response({'error': 'Say what the lesson must cover, or add a source.'}, status=400)
    return _start(request, lesson, LessonJob.Kind.GENERATE, restart=request.data.get('restart') is True)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_lesson_approve(request, pk):
    """The admin has read the lesson: record the voice for every step that has none."""
    lesson = get_object_or_404(AILesson, pk=pk)
    if lesson.status == AILesson.Status.PUBLISHED:
        return Response({'error': 'Unpublish the lesson before recording it.'}, status=409)
    if not lesson.steps.exists():
        return Response({'error': 'A lesson needs steps before it can be recorded.'}, status=400)
    return _start(request, lesson, LessonJob.Kind.VOICE)


@api_view(['PUT'])
@permission_classes([IsAdminUser])
def admin_step_detail(request, pk):
    """Correct one step by hand. Changing the words drops the voice that said the old ones."""
    step = get_object_or_404(LessonStep, pk=pk)
    lesson = _lesson_for_change(step.lesson_id)
    if lesson.status in BUSY:
        return Response({'error': 'Wait for the running job to finish.'}, status=409)
    try:
        fields = clean_step(request.data)
    except BoardError as error:
        return Response({'error': str(error)}, status=400)

    if fields['say'] != step.say and step.audio:
        step.audio.delete(save=False)
        step.audio_seconds = 0
    step.say = fields['say']
    step.board = fields['board']
    step.save()
    return Response(_lesson_data(lesson, request, full=True))


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_lesson_questions(request, pk):
    lesson = _lesson_for_change(pk)
    if lesson.status in BUSY:
        return Response({'error': 'Wait for the running job to finish.'}, status=409)
    try:
        fields = clean_question(request.data, lesson)
    except QuestionError as error:
        return Response({'error': str(error)}, status=400)
    fields['order'] = lesson.questions.count()
    LessonQuestion.objects.create(lesson=lesson, **fields)
    return Response(_lesson_data(lesson, request, full=True), status=201)


@api_view(['PUT', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_question_detail(request, pk):
    question = get_object_or_404(LessonQuestion.objects.select_related('lesson'), pk=pk)
    lesson = _lesson_for_change(question.lesson_id)
    if lesson.status in BUSY:
        return Response({'error': 'Wait for the running job to finish.'}, status=409)
    if request.method == 'DELETE':
        question.delete()
        return Response(_lesson_data(lesson, request, full=True))

    try:
        fields = clean_question(request.data, lesson)
    except QuestionError as error:
        return Response({'error': str(error)}, status=400)
    for key, value in fields.items():
        setattr(question, key, value)
    question.save()
    return Response(_lesson_data(lesson, request, full=True))


def lesson_play_data(lesson, request):
    """Everything the board player needs for one lesson."""
    return {
        'id': lesson.id,
        'title': lesson.title,
        'subject': lesson.subject,
        'section': lesson.section,
        'language': lesson.language,
        'level': lesson.level,
        'steps': [
            {
                'id': s.id,
                'say': s.say,
                'board': s.board,
                'audio': request.build_absolute_uri(s.audio.url) if s.audio else None,
                'audio_seconds': s.audio_seconds,
            }
            for s in lesson.steps.all()
        ],
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_lesson_play(request, pk):
    """An admin previews a lesson in the player, whatever its status."""
    return Response(lesson_play_data(get_object_or_404(AILesson, pk=pk), request))


def _clean_source(request):
    """(LessonSource fields, error) for one new source."""
    kind = str(request.data.get('kind', '')).upper()
    title = str(request.data.get('title', '')).strip()[:MAX_TITLE]

    if kind == LessonSource.Kind.TEXT:
        text = str(request.data.get('text', '')).strip()
        if not text:
            return None, 'Text is required.'
        if len(text) > MAX_TEXT_CHARS:
            return None, f'Text can be at most {MAX_TEXT_CHARS:,} characters.'
        return {'kind': kind, 'title': title or 'Text', 'text': text}, None

    if kind == LessonSource.Kind.LINK:
        url = str(request.data.get('url', '')).strip()
        if len(url) > MAX_URL:
            return None, f'A link can be at most {MAX_URL} characters.'
        try:
            is_http_url(url)
        except ValidationError:
            return None, 'Enter a full http or https link.'
        return {'kind': kind, 'title': title or url[:MAX_TITLE], 'url': url}, None

    if kind == LessonSource.Kind.FILE:
        upload = request.FILES.get('file')
        if not upload:
            return None, 'File is required.'
        if upload.size > MAX_FILE_BYTES:
            return None, 'File can be at most 20 MB.'
        if os.path.splitext(upload.name)[1].lower() not in FILE_EXTENSIONS:
            return None, 'Allowed files: PDF, DOCX, TXT, MD, PNG, JPG, WEBP.'
        return {'kind': kind, 'title': title or upload.name[:MAX_TITLE], 'file': upload}, None

    return None, 'Unknown source kind.'


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, JSONParser])
def admin_lesson_sources(request, pk):
    lesson = get_object_or_404(AILesson, pk=pk)
    fields, error = _clean_source(request)
    if error:
        return Response({'error': error}, status=400)
    source = LessonSource.objects.create(lesson=lesson, **fields)
    lesson.save(update_fields=['updated_at'])
    return Response(_source_data(source, request), status=201)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_source_detail(request, pk):
    source = get_object_or_404(LessonSource, pk=pk)
    lesson = source.lesson
    stored = source.file
    source.delete()
    if stored:
        stored.delete(save=False)
    lesson.save(update_fields=['updated_at'])
    return Response({'deleted': True})
