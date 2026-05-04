from django.db import models
from django.conf import settings
from django.utils import timezone


class Word(models.Model):
    class Difficulty(models.TextChoices):
        EASY = 'EASY', 'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD = 'HARD', 'Hard'

    word = models.CharField(max_length=100, unique=True)
    definition = models.TextField()
    example = models.TextField(blank=True)
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    category = models.CharField(max_length=100, blank=True, help_text='e.g. Academic, Science, Literature')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['word']
        verbose_name = 'Word'
        verbose_name_plural = 'Words'

    def __str__(self):
        return self.word


class UserWord(models.Model):
    """User's vocabulary learning progress (spaced repetition)."""

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
