from django.db import models
from django.conf import settings
from django.utils import timezone


class Plan(models.Model):
    class PlanType(models.TextChoices):
        FREE = 'FREE', 'Free'
        PRO = 'PRO', 'Pro'
        PREMIUM = 'PREMIUM', 'Premium'

    name = models.CharField(max_length=50)
    plan_type = models.CharField(max_length=10, choices=PlanType.choices, unique=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    duration_days = models.PositiveSmallIntegerField(default=30)
    features = models.JSONField(default=list, help_text='List of feature strings')
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'

    def __str__(self):
        return f"{self.name} (${self.price})"


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    payment_ref = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'status']),
        ]
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'

    def __str__(self):
        return f"{self.user.email} - {self.plan.name} ({self.status})"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE and timezone.now() < self.expires_at

    def save(self, *args, **kwargs):
        # Auto-expire
        if self.status == self.Status.ACTIVE and timezone.now() >= self.expires_at:
            self.status = self.Status.EXPIRED
        super().save(*args, **kwargs)
        # Update user premium flag
        self.user.is_premium = self.is_active
        if self.is_active:
            self.user.premium_until = self.expires_at
        self.user.save(update_fields=['is_premium', 'premium_until'])
