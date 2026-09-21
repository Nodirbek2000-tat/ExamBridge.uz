from django.conf import settings
from django.db import models


class Article(models.Model):
    """A reading article: cover image + short teaser on the card, full PDF inside."""

    LEVEL_CHOICES = [
        ('A2', 'A2 — Elementary'),
        ('B1', 'B1 — Intermediate'),
        ('B2', 'B2 — Upper-Intermediate'),
        ('C1', 'C1 — Advanced'),
    ]

    title = models.CharField(max_length=250)
    excerpt = models.TextField(blank=True, help_text='Short teaser shown under the title on the card')
    topic = models.CharField(max_length=100, blank=True, help_text='e.g. Health, Technology, Psychology')
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, blank=True)

    cover = models.ImageField(upload_to='study/articles/covers/', blank=True, null=True)
    pdf = models.FileField(upload_to='study/articles/pdf/', blank=True, null=True)

    views_count = models.PositiveIntegerField(default=0)
    is_premium = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0, help_text='Lower shows first')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title


class ArticleView(models.Model):
    """One open of an article by a user — powers the eye/read counter."""

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='article_views')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.article}'


class WritingSample(models.Model):
    """A model IELTS essay: the prompt on the card, the full essay inside."""

    TASK_CHOICES = [(1, 'Task 1'), (2, 'Task 2')]

    task_type = models.PositiveSmallIntegerField(choices=TASK_CHOICES, default=2)
    prompt = models.TextField(help_text='The exam question shown on the card')
    instruction = models.TextField(
        blank=True,
        help_text='Line under the prompt, e.g. "Give reasons for your answer and include any relevant examples…"',
    )
    essay = models.TextField(help_text='The full model answer')
    band = models.CharField(max_length=10, default='8.0+', help_text='e.g. 8.0+, 7.5, 9.0')

    is_premium = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0, help_text='Lower shows first')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return f'Task {self.task_type} — {self.prompt[:60]}'

    @property
    def word_count(self):
        return len(self.essay.split())


class Podcast(models.Model):
    """
    An audio track (podcast episode or song) played with a karaoke-style
    transcript. `words` holds Whisper's word-level timings, produced once at
    upload time: [{"word": "hello", "start": 0.12, "end": 0.44}, ...]
    """

    class Section(models.TextChoices):
        SHADOWING = 'shadowing', 'Shadowing'   # audio or video
        PODCAST = 'podcast', 'Podcasts'        # video only

    title = models.CharField(max_length=250)
    author = models.CharField(max_length=150, blank=True, help_text='Speaker or artist')
    # Which Study Tools shelf this belongs to — chosen by the admin on upload,
    # independent of whether the media itself is audio or video.
    section = models.CharField(max_length=20, choices=Section.choices, default=Section.SHADOWING)
    # Exactly one of these carries the media.
    audio = models.FileField(upload_to='study/podcasts/audio/', blank=True, null=True)
    video = models.FileField(upload_to='study/podcasts/video/', blank=True, null=True)
    cover = models.ImageField(upload_to='study/podcasts/covers/', blank=True, null=True)

    duration_sec = models.FloatField(default=0)
    transcript = models.TextField(blank=True)
    words = models.JSONField(default=list, blank=True, help_text='Whisper word timings')

    is_premium = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0, help_text='Lower shows first')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title

    @property
    def duration_label(self):
        total = int(self.duration_sec or 0)
        return f'{total // 60}:{total % 60:02d}'

    @property
    def media_kind(self):
        return 'video' if self.video else 'audio'


class PodcastListen(models.Model):
    """Per-user state for a podcast — drives the 'Seen' badge and resume point."""

    podcast = models.ForeignKey(Podcast, on_delete=models.CASCADE, related_name='listens')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='podcast_listens')
    position_sec = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['podcast', 'user']
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user} → {self.podcast_id}'


class WritingSampleRead(models.Model):
    """
    Per-user state for one sample: whether they opened it ("Analyzed" badge)
    and their private note. One row per user+sample.
    """

    sample = models.ForeignKey(WritingSample, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='writing_sample_reads')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['sample', 'user']
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user} → {self.sample_id}'
