"""
Telegram bot: which Telegram user is which site account, one-time links
between the bot and the site, and the channels a user must join.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

# Accounts created in the bot have no e-mail of their own. The model needs a
# unique one, so they get this address, which is never shown to anyone.
BOT_EMAIL_DOMAIN = 'tg.exambridge.uz'


def bot_email(telegram_id):
    return f'tg{telegram_id}@{BOT_EMAIL_DOMAIN}'


def is_bot_email(email):
    return (email or '').lower().endswith('@' + BOT_EMAIL_DOMAIN)


class TelegramAccount(models.Model):
    """One Telegram user, linked to one site account."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='telegram')
    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True, default='')
    first_name = models.CharField(max_length=128, blank=True, default='')
    last_name = models.CharField(max_length=128, blank=True, default='')
    phone = models.CharField(max_length=32, blank=True, default='')
    registered_via_bot = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Telegram account'

    def __str__(self):
        return f'{self.telegram_id} → {self.user_id}'

    @property
    def display_name(self):
        name = f'{self.first_name} {self.last_name}'.strip()
        return name or (f'@{self.username}' if self.username else str(self.telegram_id))


class TelegramToken(models.Model):
    """A one-time link between the bot and the site.

    login — the bot gives it to a user; opening it on the site signs them in.
    link  — the site gives it to a signed-in user; opening the bot with it
            attaches their Telegram to that account.
    """
    LOGIN = 'login'
    LINK = 'link'
    PURPOSES = [(LOGIN, 'Sign in on the site'), (LINK, 'Link Telegram to an account')]
    LIFETIME = {LOGIN: timedelta(minutes=10), LINK: timedelta(minutes=30)}

    token = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=8, choices=PURPOSES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='telegram_tokens')
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'purpose'])]

    def __str__(self):
        return f'{self.purpose} for {self.user_id}'

    @classmethod
    def issue(cls, user, purpose):
        """A fresh token; any older unused one of the same kind stops working."""
        cls.objects.filter(user=user, purpose=purpose, used_at__isnull=True).delete()
        # 24 random bytes → 32 URL-safe characters, which fits Telegram's
        # 64-character /start payload together with its prefix.
        return cls.objects.create(
            token=secrets.token_urlsafe(24),
            purpose=purpose,
            user=user,
            expires_at=timezone.now() + cls.LIFETIME[purpose],
        )

    @classmethod
    def redeem(cls, token, purpose):
        """The user the token belongs to, or None. A token works once."""
        if not token or len(token) > 64:
            return None
        with transaction.atomic():
            row = (
                cls.objects.select_for_update()
                .select_related('user')
                .filter(token=token, purpose=purpose, used_at__isnull=True, expires_at__gt=timezone.now())
                .first()
            )
            if row is None:
                return None
            row.used_at = timezone.now()
            row.save(update_fields=['used_at'])
        return row.user if row.user.is_active else None


class BotChannel(models.Model):
    """A channel every bot user must join before using the bot."""
    chat_id = models.BigIntegerField(unique=True)
    title = models.CharField(max_length=255)
    username = models.CharField(max_length=64, blank=True, default='')
    invite_link = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.title

    @property
    def url(self):
        return f'https://t.me/{self.username}' if self.username else self.invite_link


class TelegramLogin(models.Model):
    """"Continue with Telegram" on the site.

    The browser starts a request and keeps asking about it; the student opens
    the bot with the request's token and confirms; the next question from the
    browser gets the JWT pair. The bot asks for an explicit tap, so a link
    someone else sent you cannot sign them into your account.
    """
    LIFETIME = timedelta(minutes=5)
    # After the bot confirms, the waiting browser has this long to pick it up.
    PICKUP = timedelta(minutes=2)

    token = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The bot says so when the student created their account during this sign-in.
    signed_up = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'login request {self.pk}'

    @classmethod
    def start(cls):
        now = timezone.now()
        cls.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()   # old requests
        return cls.objects.create(token=secrets.token_urlsafe(24), expires_at=now + cls.LIFETIME)
