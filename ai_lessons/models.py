"""
AI lessons: a topic and its sources become a voiced lesson on a teaching board.

The path of one lesson:
    admin gives the topic and sources → AI writes steps and questions → an admin
    approves → the voice is recorded once → the lesson is published.

Everything is saved once and replayed, so a student opening a lesson costs no
tokens. Nothing here reaches students until the status is PUBLISHED.
"""
import os
import uuid

from django.conf import settings
from django.db import models


def source_file_path(instance, filename):
    # A random name: /media/ is public, so the path must not be guessable.
    ext = os.path.splitext(filename or '')[1].lower()
    return f'ai_lessons/sources/{uuid.uuid4().hex}{ext}'


def step_audio_path(instance, filename):
    return f'ai_lessons/audio/{instance.lesson_id}/{uuid.uuid4().hex}.mp3'


class AILesson(models.Model):
    class Subject(models.TextChoices):
        SAT = 'SAT', 'SAT'
        IELTS = 'IELTS', 'IELTS'
        CEFR = 'CEFR', 'CEFR'
        GENERAL = 'GENERAL', 'General'

    class Section(models.TextChoices):
        GENERAL = 'general', 'General'
        MATH = 'math', 'Math'
        READING_WRITING = 'reading_writing', 'Reading & Writing'
        READING = 'reading', 'Reading'
        LISTENING = 'listening', 'Listening'
        WRITING = 'writing', 'Writing'
        SPEAKING = 'speaking', 'Speaking'
        GRAMMAR = 'grammar', 'Grammar'

    class Language(models.TextChoices):
        UZBEK = 'uz', "O'zbekcha"
        ENGLISH = 'en', 'English'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        GENERATING = 'GENERATING', 'Generating'
        REVIEW = 'REVIEW', 'In review'
        APPROVED = 'APPROVED', 'Approved'
        VOICING = 'VOICING', 'Recording voice'
        PUBLISHED = 'PUBLISHED', 'Published'
        FAILED = 'FAILED', 'Failed'

    class Voice(models.TextChoices):
        NOVA = 'nova', 'Nova (ayol)'
        SHIMMER = 'shimmer', 'Shimmer (ayol)'
        ONYX = 'onyx', 'Onyx (erkak)'
        ECHO = 'echo', 'Echo (erkak)'

    DURATIONS = (10, 15, 20)

    title = models.CharField(max_length=200)
    subject = models.CharField(max_length=10, choices=Subject.choices, default=Subject.SAT)
    section = models.CharField(max_length=20, choices=Section.choices, default=Section.GENERAL)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.UZBEK)
    duration_minutes = models.PositiveSmallIntegerField(default=10)
    level = models.CharField(max_length=30, blank=True, help_text='Who it is for, e.g. B2 or Band 6+')
    voice = models.CharField(max_length=20, choices=Voice.choices, default=Voice.NOVA)
    brief = models.TextField(blank=True, help_text='What the lesson must cover and how to teach it')

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    ai_provider = models.CharField(max_length=20, blank=True, help_text='Who wrote the steps; empty when written by hand')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.subject} · {self.title}'


class LessonSource(models.Model):
    """Material the AI must build the lesson from: pasted text, a file or a link."""

    class Kind(models.TextChoices):
        TEXT = 'TEXT', 'Text'
        FILE = 'FILE', 'File'
        LINK = 'LINK', 'Link'

    lesson = models.ForeignKey(AILesson, on_delete=models.CASCADE, related_name='sources')
    kind = models.CharField(max_length=4, choices=Kind.choices)
    title = models.CharField(max_length=200)
    text = models.TextField(blank=True)
    file = models.FileField(upload_to=source_file_path, blank=True)
    url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.kind} · {self.title}'


class LessonStep(models.Model):
    """One beat of the lesson: what the teacher says and what appears on the board."""

    lesson = models.ForeignKey(AILesson, on_delete=models.CASCADE, related_name='steps')
    order = models.PositiveSmallIntegerField(default=0)
    say = models.TextField(help_text='Spoken text')
    board = models.JSONField(default=list, blank=True, help_text='What is drawn on the board, in order')
    audio = models.FileField(upload_to=step_audio_path, blank=True)
    audio_seconds = models.FloatField(default=0)

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['lesson', 'order'], name='uniq_lessonstep_lesson_order'),
        ]

    def __str__(self):
        return f'{self.lesson_id} · step {self.order}'


class LessonQuestion(models.Model):
    """A question inside the lesson (step set) or in its homework (is_homework)."""

    class Kind(models.TextChoices):
        CHOICE = 'CHOICE', 'Multiple choice'
        WRITTEN = 'WRITTEN', 'Written answer'

    class Origin(models.TextChoices):
        ADMIN = 'ADMIN', 'Added by admin'
        AI = 'AI', 'Written by AI'

    class Verdict(models.TextChoices):
        PENDING = 'PENDING', 'Not checked'
        GOOD = 'GOOD', 'Good'
        BAD = 'BAD', 'Needs work'

    lesson = models.ForeignKey(AILesson, on_delete=models.CASCADE, related_name='questions')
    step = models.ForeignKey(LessonStep, on_delete=models.SET_NULL, null=True, blank=True, related_name='questions')
    is_homework = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    kind = models.CharField(max_length=7, choices=Kind.choices, default=Kind.CHOICE)
    prompt = models.TextField()
    options = models.JSONField(default=list, blank=True)
    correct = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Index into options')
    rubric = models.TextField(blank=True, help_text='How a written answer is judged')
    explanation = models.TextField(blank=True)

    origin = models.CharField(max_length=5, choices=Origin.choices, default=Origin.ADMIN)
    ai_verdict = models.CharField(max_length=7, choices=Verdict.choices, default=Verdict.PENDING)
    ai_note = models.TextField(blank=True)

    class Meta:
        ordering = ['is_homework', 'order', 'id']

    def __str__(self):
        return f'{self.lesson_id} · {self.kind} · {self.prompt[:40]}'


class LessonJob(models.Model):
    """One run of the pipeline. `data` holds every finished stage, so it can be continued."""

    class Kind(models.TextChoices):
        GENERATE = 'GENERATE', 'Write the lesson'
        VOICE = 'VOICE', 'Record the voice'

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        RUNNING = 'RUNNING', 'Running'
        DONE = 'DONE', 'Done'
        FAILED = 'FAILED', 'Failed'

    class Stage(models.TextChoices):
        PLAN = 'PLAN', 'Planning'
        SCRIPT = 'SCRIPT', 'Writing the script'
        QUESTIONS = 'QUESTIONS', 'Writing questions'
        REVIEW = 'REVIEW', 'Checking the lesson'
        VOICE = 'VOICE', 'Recording the voice'

    ACTIVE = (Status.QUEUED, Status.RUNNING)

    lesson = models.ForeignKey(AILesson, on_delete=models.CASCADE, related_name='jobs')
    kind = models.CharField(max_length=8, choices=Kind.choices)
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.QUEUED)
    stage = models.CharField(max_length=9, choices=Stage.choices, blank=True)
    done = models.PositiveSmallIntegerField(default=0)
    total = models.PositiveSmallIntegerField(default=0)
    provider = models.CharField(max_length=20)
    data = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            # Two jobs on one lesson would overwrite each other's work.
            models.UniqueConstraint(
                fields=['lesson'], condition=models.Q(status__in=('QUEUED', 'RUNNING')),
                name='uniq_lessonjob_active_per_lesson',
            ),
        ]

    def __str__(self):
        return f'{self.lesson_id} · {self.kind} · {self.status}'


class LessonProgress(models.Model):
    """How far one student got in one lesson."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lesson_progress')
    lesson = models.ForeignKey(AILesson, on_delete=models.CASCADE, related_name='progress')
    last_step = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'lesson'], name='uniq_lessonprogress_user_lesson'),
        ]

    def __str__(self):
        return f'{self.user_id} · {self.lesson_id} · step {self.last_step}'


class LessonAnswer(models.Model):
    """A student's one answer to a question. Answers cannot be changed afterwards."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lesson_answers')
    question = models.ForeignKey(LessonQuestion, on_delete=models.CASCADE, related_name='answers')
    choice = models.PositiveSmallIntegerField(null=True, blank=True)
    text = models.TextField(blank=True)
    is_correct = models.BooleanField(null=True, blank=True, help_text='Empty until a written answer is marked')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'question'], name='uniq_lessonanswer_user_question'),
        ]

    def __str__(self):
        return f'{self.user_id} · {self.question_id} · {self.is_correct}'
