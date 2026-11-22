"""
Site side of the Telegram bot: open the bot linked to your account, sign in with
the one-time link the bot gives you, or be signed in at once when the site is
opened from a bot button inside Telegram (a Mini App).
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts import devices
from api.auth_views import LoginRateThrottle, get_tokens, user_data

from .models import TelegramAccount, TelegramLogin, TelegramToken


def bot_url(start=None):
    name = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
    if not name:
        return None
    return f'https://t.me/{name}' + (f'?start={start}' if start else '')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def telegram_open(request):
    """
    The bot link for paying for this account. The bot first shows which account
    it is and asks to confirm, so the Premium never lands on another account —
    say the student's Telegram already has an account of its own from the bot.
    """
    if not bot_url():
        return Response({'detail': 'Telegram payments are not configured yet.'}, status=503)
    linked = TelegramAccount.objects.filter(user=request.user).exists()
    token = TelegramToken.issue(request.user, TelegramToken.PAY)
    return Response({'url': bot_url(f'pay_{token.token}'), 'linked': linked})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def telegram_login(request):
    """Exchange the bot's one-time sign-in link for the usual JWT pair."""
    user = TelegramToken.redeem(str(request.data.get('token') or '').strip(), TelegramToken.LOGIN)
    if user is None:
        return Response(
            {'detail': 'This sign-in link has expired or was already used. Open the bot and tap “Saytga kirish” again.'},
            status=400,
        )
    return Response({'user': user_data(user), **get_tokens(user, request)})


# ── Opened from a bot button inside Telegram (Mini App) ───────────────────────

INIT_DATA_MAX_AGE = 24 * 3600     # Telegram makes a new one each time the Mini App opens


def telegram_user(init_data, token, now=None):
    """
    The Telegram user a Mini App's initData speaks for, or None. Telegram signs it with the
    bot's token (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app),
    so only Telegram can make one for our bot.
    """
    if not init_data or not token or len(init_data) > 4096:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    given = fields.pop('hash', '')
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), given):
        return None
    try:
        age = (now or time.time()) - int(fields.get('auth_date', 0))
        user = json.loads(fields.get('user', ''))
        telegram_id = int(user['id'])
    except (TypeError, ValueError, KeyError):
        return None
    if not -300 <= age <= INIT_DATA_MAX_AGE:
        return None
    return {**user, 'id': telegram_id}


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def telegram_webapp_login(request):
    """
    The site opened inside Telegram from a bot button signs in as that Telegram's account.
    This session takes no device place and signs no other device out (accounts/devices.py).
    """
    tg = telegram_user(str(request.data.get('init_data') or ''), settings.TELEGRAM_BOT_TOKEN)
    if tg is None:
        return Response({'detail': 'Telegram could not confirm who you are.', 'code': 'bad_init_data'}, status=400)
    account = (TelegramAccount.objects.select_related('user')
               .filter(telegram_id=tg['id'], user__is_active=True).first())
    if account is None:
        return Response({'detail': 'This Telegram is not linked to an ExamBridge account yet. '
                                   'Open the bot and press /start.', 'code': 'not_linked'}, status=404)
    user = account.user
    return Response({'user': user_data(user), **devices.tokens_for(user, request, telegram=True)})


# ── "Continue with Telegram" (login and sign-up pages) ────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def telegram_login_start(request):
    """Start a sign-in through the bot. The browser then asks telegram_login_poll."""
    if not bot_url():
        return Response({'detail': 'Telegram sign-in is not available right now.'}, status=503)
    row = TelegramLogin.start()
    return Response({
        'token': row.token,
        'url': bot_url(f'login_{row.token}'),
        'expires_in': int(TelegramLogin.LIFETIME.total_seconds()),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([])          # asked every few seconds; the 32-character token is the guard
def telegram_login_poll(request):
    """{'status': 'pending' | 'expired' | 'ok'}; 'ok' carries the JWT pair, once."""
    token = str(request.data.get('token') or '').strip()[:64]
    now = timezone.now()
    with transaction.atomic():
        row = TelegramLogin.objects.select_for_update().filter(token=token).first()
        if row is None or row.used_at:
            return Response({'status': 'expired'})
        if row.user_id is None:
            return Response({'status': 'pending' if row.expires_at > now else 'expired'})
        if now > row.confirmed_at + TelegramLogin.PICKUP or not row.user.is_active:
            return Response({'status': 'expired'})
        row.used_at = now
        row.save(update_fields=['used_at'])

    user = row.user
    # Signed up in the bot during this very sign-in: the site sends them to onboarding.
    return Response({'status': 'ok', 'user': user_data(user), **get_tokens(user, request), 'created': row.signed_up})
