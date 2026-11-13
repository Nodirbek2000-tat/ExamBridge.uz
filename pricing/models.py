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
    stripe_subscription_id = models.CharField(max_length=120, blank=True, default='')
    amount_paid = models.PositiveIntegerField(default=0, help_text='Amount in USD cents')
    plan_label = models.CharField(max_length=20, blank=True, default='', help_text='1month or 3month')

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


# ─── Card payments through the Telegram bot ───────────────────────────────────
# Students transfer the price to a card, send the receipt to the bot, and an
# admin approves it. Prices are in so'm.

CARD_PLANS = {
    '1month': {'months': 1, 'days': 30, 'price_field': 'price_1month'},
    '3month': {'months': 3, 'days': 90, 'price_field': 'price_3month'},
}


class PaymentSettings(models.Model):
    """The one row of payment settings (admin panel → Payments)."""
    card_number = models.CharField(max_length=32, blank=True, default='')
    card_holder = models.CharField(max_length=100, blank=True, default='')
    card_label = models.CharField(max_length=60, blank=True, default='', help_text='e.g. Uzcard, Humo, bank name')
    price_1month = models.PositiveIntegerField(default=49000)
    price_3month = models.PositiveIntegerField(default=119000)
    accepting = models.BooleanField(default=True, help_text='Off: the bot stops taking new payments')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment settings'
        verbose_name_plural = 'Payment settings'

    def __str__(self):
        return 'Payment settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def price(self, plan):
        return getattr(self, CARD_PLANS[plan]['price_field'])


def _receipt_path(instance, filename):
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower()[:6]
    return f'payments/receipts/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}'


class CardPayment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='card_payments')
    plan = models.CharField(max_length=10, choices=[(k, k) for k in CARD_PLANS])
    amount = models.PositiveIntegerField(help_text="so'm")
    days = models.PositiveSmallIntegerField()
    receipt = models.FileField(upload_to=_receipt_path)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    telegram_id = models.BigIntegerField(null=True, blank=True)
    # The receipt post in the payments channel, so a decision can update it.
    channel_chat_id = models.BigIntegerField(null=True, blank=True)
    channel_message_id = models.BigIntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=120, blank=True, default='')
    reject_reason = models.CharField(max_length=200, blank=True, default='')
    premium_until = models.DateTimeField(null=True, blank=True, help_text='Premium end date after approval')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'decided_at'])]

    def __str__(self):
        return f'#{self.pk} {self.user_id} {self.plan} {self.status}'

    @property
    def months(self):
        return CARD_PLANS[self.plan]['months']
