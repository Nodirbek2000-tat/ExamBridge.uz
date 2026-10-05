from django.db import models
from django.conf import settings
from django.utils import timezone

# ── the shared word bank (Word Battle + Toby Run) ────────────────────────────
# One bank, one import (POST /api/games/words/admin/import/), one review table.
# Only status='published' rows are served to learners. Rows are never hard-deleted:
# set status back to 'draft' instead.

LEVEL_CHOICES = [('A1', 'A1'), ('A2', 'A2'), ('B1', 'B1'), ('B2', 'B2'), ('C1', 'C1')]
POS_CHOICES = [
    ('noun', 'noun'), ('verb', 'verb'), ('adj', 'adjective'), ('adv', 'adverb'),
    ('phrase', 'phrase'), ('other', 'other'),
]
STATUS_DRAFT = 'draft'
STATUS_REVIEWED = 'reviewed'
STATUS_PUBLISHED = 'published'
STATUS_CHOICES = [
    (STATUS_DRAFT, 'Qoralama'),
    (STATUS_REVIEWED, 'Tekshirilgan'),
    (STATUS_PUBLISHED, 'Chop etilgan'),
]
PHRASE_KIND_CHOICES = [('echo', 'echo'), ('answer', 'answer'), ('fill', 'fill'), ('twister', 'twister')]
# the character voices of games/tts_views.VOICES (kept literal so the migration stays stable)
VOICE_CHOICES = [(v, v) for v in ('toby', 'girl', 'boy', 'mum', 'teacher', 'man', 'grandma', 'driver', 'coach', 'narrator')]


class Word(models.Model):
    class Difficulty(models.TextChoices):
        EASY = 'EASY', 'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD = 'HARD', 'Hard'

    word = models.CharField(max_length=100, unique=True)
    definition = models.TextField(blank=True)
    example = models.TextField(blank=True)
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    category = models.CharField(max_length=100, blank=True, help_text='e.g. Academic, Science, Literature')
    created_at = models.DateTimeField(auto_now_add=True)

    # bank fields (vocabulary 0002_bank)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, blank=True, db_index=True)
    uz = models.CharField(max_length=120, blank=True, help_text='Main Uzbek meaning')
    pos = models.CharField(max_length=10, choices=POS_CHOICES, blank=True)
    topic = models.CharField(max_length=30, blank=True, db_index=True, help_text='Slug from vocabulary.bank.TOPICS')
    picture = models.CharField(max_length=40, blank=True, help_text='ItemArt key (Toby’s Day pictures)')
    say_also = models.JSONField(default=list, blank=True, help_text='Accepted spoken forms (≤ 6)')
    synonyms = models.JSONField(default=list, blank=True)
    antonyms = models.JSONField(default=list, blank=True)
    distractors = models.JSONField(default=list, blank=True, help_text='Word Battle wrong options (≤ 6)')
    tags = models.JSONField(default=list, blank=True, help_text='e.g. ["sat"], ["kids"]')
    speak = models.BooleanField(default=True, help_text='May be a single-word speaking target')
    speak_risk = models.BooleanField(default=False, help_text='Set by the speakability check')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PUBLISHED, db_index=True)
    source = models.CharField(max_length=40, blank=True, help_text='Import label')
    say_seen = models.PositiveIntegerField(default=0)
    say_ok = models.PositiveIntegerField(default=0)
    mean_seen = models.PositiveIntegerField(default=0)
    mean_ok = models.PositiveIntegerField(default=0)
    say_heard = models.JSONField(default=list, blank=True, help_text='Top mis-hears (nightly)')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['word']
        verbose_name = 'Word'
        verbose_name_plural = 'Words'
        indexes = [
            models.Index(fields=['status', 'level', 'topic'], name='vocab_word_status_lvl_topic'),
        ]

    def __str__(self):
        return self.word


class Phrase(models.Model):
    """A line to say: echo (repeat it), answer (reply to a question), fill (the missing chunk), twister."""

    kind = models.CharField(max_length=10, choices=PHRASE_KIND_CHOICES)
    text = models.CharField(max_length=200, blank=True,
                            help_text='echo / twister: the line · fill: the full sentence · answer: the model answer')
    prompt = models.CharField(max_length=200, blank=True, help_text='answer: the question · fill: the sentence with ___')
    answer = models.CharField(max_length=80, blank=True, help_text='fill: the missing chunk')
    accept = models.JSONField(default=list, blank=True,
                              help_text='answer: keyword groups [[..], [..]] · fill: extra accepted chunks')
    min_words = models.PositiveSmallIntegerField(default=0)
    uz = models.CharField(max_length=240, blank=True)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, blank=True, db_index=True)
    topic = models.CharField(max_length=30, blank=True, db_index=True)
    picture = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PUBLISHED, db_index=True)
    source = models.CharField(max_length=40, blank=True)
    voice = models.CharField(max_length=12, choices=VOICE_CHOICES, blank=True)
    key = models.CharField(max_length=240, unique=True, help_text='kind|level|normalized prompt-or-text (upserts)')
    say_seen = models.PositiveIntegerField(default=0)
    say_ok = models.PositiveIntegerField(default=0)
    say_heard = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['level', 'kind', 'id']
        indexes = [
            models.Index(fields=['status', 'level', 'topic', 'kind'], name='vocab_phrase_stat_lvl_tp_kind'),
        ]

    def __str__(self):
        return f'{self.kind} · {self.level} · {(self.prompt or self.text)[:60]}'


class Review(models.Model):
    """Spaced-repetition state of one item for one learner and one skill (vocabulary/srs.py).

    kind: 'w' word · 'p' phrase. item_id is a plain integer (no FK) so bulk upserts work;
    orphans are purged by the nightly roll-up. skill: 'say' (Runner) · 'mean' (Word Battle, Listen mode).
    """

    KIND_CHOICES = [('w', 'word'), ('p', 'phrase')]
    SKILL_CHOICES = [('say', 'say'), ('mean', 'mean')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bank_reviews')
    kind = models.CharField(max_length=1, choices=KIND_CHOICES)
    item_id = models.PositiveIntegerField()
    skill = models.CharField(max_length=4, choices=SKILL_CHOICES)
    box = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(default=timezone.now)
    seen = models.PositiveIntegerField(default=0)
    ok = models.PositiveIntegerField(default=0)
    miss = models.PositiveIntegerField(default=0)
    first_ok = models.PositiveIntegerField(default=0)
    best_ms = models.PositiveIntegerField(null=True, blank=True)
    last_heard = models.CharField(max_length=80, blank=True)
    last_verdict = models.CharField(max_length=5, blank=True)
    promoted_on = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'kind', 'item_id', 'skill'], name='uniq_review_user_item_skill'),
        ]
        indexes = [
            models.Index(fields=['user', 'skill', 'due_at'], name='review_user_skill_due_idx'),
        ]

    def __str__(self):
        return f'{self.user_id} · {self.kind}{self.item_id} · {self.skill} · box {self.box}'


class UserWord(models.Model):
    """User's vocabulary learning progress (spaced repetition). Legacy — unused by the games."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_words')
    word = models.ForeignKey(Word, on_delete=models.CASCADE, related_name='user_words')
    learned = models.BooleanField(default=False)
    review_count = models.PositiveSmallIntegerField(default=0)
    next_review = models.DateTimeField(default=timezone.now)
    last_reviewed = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['user', 'word']
        verbose_name = 'User Word'
        verbose_name_plural = 'User Words'

    def __str__(self):
        return f"{self.user.email} - {self.word.word}"

    def mark_reviewed(self, correct: bool):
        """Spaced repetition interval calculation."""
        self.review_count += 1
        self.last_reviewed = timezone.now()
        if correct:
            intervals = [1, 3, 7, 14, 30, 60]
            idx = min(self.review_count - 1, len(intervals) - 1)
            self.next_review = timezone.now() + timezone.timedelta(days=intervals[idx])
            if self.review_count >= 5:
                self.learned = True
        else:
            self.next_review = timezone.now() + timezone.timedelta(days=1)
        self.save(update_fields=['review_count', 'last_reviewed', 'next_review', 'learned'])
