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


# ── Speak & Play (voice games) ──────────────────────────────────────────────
# Games where the learner says a line and the character / car does it.
# Speech is recognised in the browser; the server only keeps progress + runs.

VOICE_GAME_CHOICES = [
    ('tobys-day', "Toby's Day"),
    ('voice-drive', 'Voice Drive'),
    ('runner', 'Toby Run'),
]
VOICE_GAME_SLUGS = frozenset(slug for slug, _ in VOICE_GAME_CHOICES)


class VoiceGameProgress(models.Model):
    """One row per (user, game): the game's own saved state + running totals."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='voice_game_progress')
    slug = models.CharField(max_length=40, choices=VOICE_GAME_CHOICES)
    data = models.JSONField(default=dict, blank=True, help_text='Free-form state the game stores (≤ 20 KB)')
    best_score = models.PositiveIntegerField(default=0)
    plays = models.PositiveIntegerField(default=0)
    stars_total = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'slug'], name='uniq_voice_progress_user_slug'),
        ]
        verbose_name = 'Voice game progress'
        verbose_name_plural = 'Voice game progress'

    def __str__(self):
        return f'{self.user} · {self.slug} · best {self.best_score}'


class VoiceGameRun(models.Model):
    """One finished play of a voice game — feeds the weekly leaderboard."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='voice_game_runs')
    slug = models.CharField(max_length=40, choices=VOICE_GAME_CHOICES)
    score = models.PositiveIntegerField(default=0)
    stars = models.PositiveSmallIntegerField(default=0)
    accuracy = models.FloatField(default=0)          # 0–1
    level = models.CharField(max_length=40, blank=True, default='')
    duration_sec = models.PositiveIntegerField(default=0)
    lines_said = models.PositiveIntegerField(default=0)
    # idempotency key from the client (Runner finish); '' = none. Unique per user when set.
    ref = models.CharField(max_length=32, blank=True, default='')
    # False = kept for the learner's history but left out of the leaderboard (listen / card mode, flags)
    ranked = models.BooleanField(default=True)
    meta = models.JSONField(default=dict, blank=True, help_text='Per-game details (mode, stt, flags…)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug', 'created_at'], name='voice_run_slug_created_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['user', 'ref'], condition=~models.Q(ref=''), name='uniq_voice_run_user_ref'),
        ]

    def __str__(self):
        return f'{self.user} · {self.slug} · {self.score}'
