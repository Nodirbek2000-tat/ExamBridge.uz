from django.conf import settings
from django.db import models


class ShadowingText(models.Model):
    """A short passage the learner reads aloud, mirrored back with it for shadowing practice."""

    LEVEL_CHOICES = [
        ('A1', 'A1 — Beginner'),
        ('A2', 'A2 — Elementary'),
        ('B1', 'B1 — Intermediate'),
        ('B2', 'B2 — Upper-Intermediate'),
        ('C1', 'C1 — Advanced'),
    ]

    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=100, blank=True, help_text='e.g. Travel, Movies, Daily life')
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, default='B1')
    body = models.TextField(help_text='The passage the learner reads aloud')
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level', 'title']

    def __str__(self):
        return f'{self.title} ({self.level})'

    @property
    def word_count(self):
        return len((self.body or '').split())


class ShadowingAttempt(models.Model):
    """One recorded shadowing attempt + its AI-scored result."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shadowing_attempts')
    text = models.ForeignKey(ShadowingText, on_delete=models.CASCADE, related_name='attempts')
    audio_file = models.FileField(upload_to='games/shadowing/', blank=True, null=True)
    duration_sec = models.FloatField(default=0)

    transcript = models.TextField(blank=True, help_text='What Whisper heard')
    word_results = models.JSONField(default=list, blank=True, help_text='[{word, status, score}, ...]')

    overall_score = models.PositiveSmallIntegerField(default=0)   # /100
    accuracy_pct = models.PositiveSmallIntegerField(default=0)    # % of reference words matched
    fluency_wpm = models.PositiveSmallIntegerField(default=0)     # words per minute
    cefr_estimate = models.CharField(max_length=2, blank=True)

    correct_count = models.PositiveSmallIntegerField(default=0)
    flagged_count = models.PositiveSmallIntegerField(default=0)   # said, but low-confidence / mismatched
    skipped_count = models.PositiveSmallIntegerField(default=0)   # never said

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} · {self.text} · {self.overall_score}/100'
