"""
Speaking game: the learner reads a short text aloud and every word of the text is
scored — by Azure Pronunciation Assessment (per word and per sound, speaking/azure.py)
or, when Azure is off or fails, by what Whisper heard (speaking/scoring.py).

    SpeakingLesson   — a text to read (imported by staff from JSON)
    SpeakingAttempt  — one recording of one lesson and its word-by-word result
"""
import os
import uuid

from django.conf import settings
from django.db import models

LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}


AUDIO_EXTS = ('.webm', '.ogg', '.m4a', '.mp4', '.mp3', '.wav')


def attempt_audio_path(instance, filename):
    # a random name: media files are served without a login, so the path must not be guessable;
    # only audio extensions — /media/ is served from the site's own origin, so an uploaded
    # "x.html" or "x.svg" must never keep its extension
    ext = os.path.splitext(filename or '')[1].lower()
    if ext not in AUDIO_EXTS:
        ext = '.webm'
    return f'games/speaking/{uuid.uuid4().hex}{ext}'


class SpeakingLesson(models.Model):
    LEVEL_CHOICES = [(lv, lv) for lv in LEVELS]

    title = models.CharField(max_length=200)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, default='A1', db_index=True)
    topic = models.CharField(max_length=100, blank=True)
    text = models.TextField(help_text='The passage the learner reads aloud (one sentence per line is fine).')
    order = models.PositiveIntegerField(default=0, help_text='Position inside its level (smaller first).')
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level', 'order', 'id']

    def __str__(self):
        return f'{self.title} ({self.level})'


class SpeakingAttempt(models.Model):
    PROCESSING = 'PROCESSING'
    READY = 'READY'
    FAILED = 'FAILED'
    STATUS_CHOICES = [(PROCESSING, 'Processing'), (READY, 'Ready'), (FAILED, 'Failed')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='speaking_game_attempts')
    lesson = models.ForeignKey(SpeakingLesson, on_delete=models.CASCADE, related_name='attempts')
    audio = models.FileField(upload_to=attempt_audio_path, blank=True)
    mime = models.CharField(max_length=60, blank=True)
    duration_sec = models.FloatField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PROCESSING, db_index=True)
    error = models.CharField(max_length=40, blank=True, help_text="Why it failed: 'no-speech', 'service', 'bad-audio'")

    AZURE = 'azure'
    WHISPER = 'whisper'
    ENGINE_CHOICES = [(AZURE, 'Azure pronunciation assessment'), (WHISPER, 'Whisper transcription')]

    transcript = models.TextField(blank=True)
    # [{i, word, say, score 0–100, status, heard, start, end}]
    #   status: 'ok' (>= 80) | 'fix' (60–79) | 'bad' (said, < 60; Azure only) | 'skip' (not said)
    # Azure adds: phonemes [{p: IPA phoneme, score 0–100}], error ('Mispronunciation' | 'Omission' |
    #   'Insertion' | 'UnexpectedBreak' | 'MissingBreak' | 'Monotone' | ''), tip (the weakest
    #   phoneme when it is below 60, else '')
    words = models.JSONField(default=list, blank=True)
    # mean word score 0–100 (a word not said counts 0); with Azure: its word AccuracyScores
    accuracy = models.PositiveSmallIntegerField(default=0)
    fluency_wpm = models.PositiveSmallIntegerField(default=0)
    ok_count = models.PositiveSmallIntegerField(default=0)
    fix_count = models.PositiveSmallIntegerField(default=0)       # 'fix' + 'bad'
    skip_count = models.PositiveSmallIntegerField(default=0)
    # who scored it; rows from before Azure are 'whisper'
    engine = models.CharField(max_length=10, choices=ENGINE_CHOICES, default=WHISPER)
    # Azure Pronunciation Assessment, 0–100; null when unknown (Whisper)
    pron_score = models.PositiveSmallIntegerField(null=True, blank=True)
    fluency_score = models.PositiveSmallIntegerField(null=True, blank=True)
    completeness_score = models.PositiveSmallIntegerField(null=True, blank=True)
    prosody_score = models.PositiveSmallIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'lesson'], name='speaking_att_user_lesson')]

    def __str__(self):
        return f'{self.user_id} · {self.lesson_id} · {self.status} · {self.accuracy}%'
