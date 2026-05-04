from django.db import models
from django.conf import settings


class University(models.Model):
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100, default='USA')
    logo = models.ImageField(upload_to='universities/', null=True, blank=True)
    description = models.TextField(blank=True)
    website_url = models.URLField(blank=True)

    # Required SAT scores
    min_total_score = models.PositiveSmallIntegerField(default=0, help_text='Minimum total SAT score')
    min_math_score = models.PositiveSmallIntegerField(default=0)
    min_english_score = models.PositiveSmallIntegerField(default=0)

    # Info
    acceptance_rate = models.FloatField(default=0.0, help_text='Acceptance rate in percent (e.g. 5.4)')
    ranking = models.PositiveSmallIntegerField(null=True, blank=True, help_text='National ranking')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ranking', 'name']
        verbose_name = 'University'
        verbose_name_plural = 'Universities'
        indexes = [
            models.Index(fields=['min_total_score']),
        ]

    def __str__(self):
        return self.name

    def is_reachable(self, total_score: int, math_score: int = 0, english_score: int = 0) -> bool:
        return (
            total_score >= self.min_total_score and
            math_score >= self.min_math_score and
            english_score >= self.min_english_score
        )


class UniversityRecommendation(models.Model):
    """Store AI-generated university recommendations per user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='university_recs')
    university = models.ForeignKey(University, on_delete=models.CASCADE)
    total_score_used = models.PositiveSmallIntegerField()
    math_score_used = models.PositiveSmallIntegerField()
    english_score_used = models.PositiveSmallIntegerField()
    chance = models.CharField(max_length=20, blank=True, help_text='reach / match / safety')
    ai_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'University Recommendation'
        verbose_name_plural = 'University Recommendations'

    def __str__(self):
        return f"{self.user.email} → {self.university.name}"
