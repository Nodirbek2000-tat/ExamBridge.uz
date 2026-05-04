from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Extended user model for SAT Platform."""

    email = models.EmailField(_('email address'), unique=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)

    # Premium system
    is_premium = models.BooleanField(default=False)
    premium_until = models.DateTimeField(null=True, blank=True)

    # Profile completion flag (shown after Google OAuth)
    profile_completed = models.BooleanField(default=False)

    # Security
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['is_premium']),
        ]

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email.split('@')[0]

    def check_premium(self):
        """Check and update premium status."""
        if self.is_premium and self.premium_until:
            if timezone.now() > self.premium_until:
                self.is_premium = False
                self.premium_until = None
                self.save(update_fields=['is_premium', 'premium_until'])
        return self.is_premium

    def is_locked(self):
        if self.locked_until and timezone.now() < self.locked_until:
            return True
        return False

    def record_failed_login(self):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.locked_until = timezone.now() + timezone.timedelta(minutes=30)
        self.save(update_fields=['failed_login_attempts', 'locked_until'])

    def reset_login_attempts(self):
        if self.failed_login_attempts > 0:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=['failed_login_attempts', 'locked_until'])


class UserStats(models.Model):
    """Track user performance statistics."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='stats')

    # Test statistics
    total_tests_taken = models.PositiveIntegerField(default=0)
    best_total_score = models.PositiveSmallIntegerField(default=0)
    best_math_score = models.PositiveSmallIntegerField(default=0)
    best_english_score = models.PositiveSmallIntegerField(default=0)
    avg_total_score = models.FloatField(default=0.0)

    # Streak
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True)

    # Vocabulary
    words_learned = models.PositiveIntegerField(default=0)

    # SAT exam date (user-set)
    sat_exam_date = models.DateField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Stats'
        verbose_name_plural = 'User Stats'

    def __str__(self):
        return f"{self.user.email} - Stats"

    def update_streak(self):
        from django.utils.timezone import now
        today = now().date()
        if self.last_activity_date:
            diff = (today - self.last_activity_date).days
            if diff == 1:
                self.current_streak += 1
                self.longest_streak = max(self.current_streak, self.longest_streak)
            elif diff > 1:
                self.current_streak = 1
        else:
            self.current_streak = 1
        self.last_activity_date = today
        self.save(update_fields=['current_streak', 'longest_streak', 'last_activity_date'])
