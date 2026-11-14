"""
API for the ExamBridge Telegram bot (/api/bot/...).

Only the bot may call it: every request must carry the shared secret in the
X-Bot-Secret header (TELEGRAM_BOT_API_SECRET in both .env files). The bot
runs next to the site, so these paths skip the browser-only security checks
(accounts/middleware.py) — the secret is what protects them.
"""
import hmac
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from accounts.models import User, UserDailyVisit
from accounts.presence import online_since
from pricing import card
from pricing.models import PaymentSettings

from .models import BotChannel, TelegramAccount, TelegramLogin, TelegramToken, bot_email, is_bot_email


class IsBot(BasePermission):
    def has_permission(self, request, view):
        secret = settings.TELEGRAM_BOT_API_SECRET
        given = request.headers.get('X-Bot-Secret', '')
        return bool(secret) and hmac.compare_digest(given.encode(), secret.encode())


def bot_api(methods):
    """@api_view for bot endpoints: secret header only, no JWT, no throttling."""
    def wrap(fn):
        fn = throttle_classes([])(fn)
        fn = permission_classes([IsBot])(fn)
        fn = authentication_classes([])(fn)
        return api_view(methods)(fn)
    return wrap


def _error(code, detail, status=400):
    return Response({'code': code, 'detail': detail}, status=status)


def _telegram_id(value):
    try:
        tid = int(value)
    except (TypeError, ValueError):
        return None
    return tid if tid > 0 else None


def _clean(value, limit):
    return str(value or '').strip()[:limit]


def _profile(data):
    return {
        'username': _clean(data.get('username'), 64).lstrip('@'),
        'first_name': _clean(data.get('first_name'), 128),
        'last_name': _clean(data.get('last_name'), 128),
    }


def _account_payload(account):
    user = account.user
    user.check_premium()
    return {
        'telegram_id': account.telegram_id,
        'user_id': user.id,
        'full_name': user.full_name if not is_bot_email(user.email) else account.display_name,
        'email': None if is_bot_email(user.email) else user.email,
        'phone': account.phone,
        'is_premium': user.is_premium,
        'premium_until': timezone.localtime(user.premium_until).isoformat() if user.premium_until else None,
        'registered_via_bot': account.registered_via_bot,
    }


def _account(telegram_id):
    return TelegramAccount.objects.select_related('user').filter(telegram_id=telegram_id).first()


# ── Users ─────────────────────────────────────────────────────────────────────

@bot_api(['POST'])
def user_sync(request):
    """Is this Telegram user registered? Keeps their name and @username fresh."""
    tid = _telegram_id(request.data.get('telegram_id'))
    if not tid:
        return _error('bad_request', 'telegram_id is required.')
    account = _account(tid)
    if account is None:
        return Response({'registered': False})
    profile = _profile(request.data)
    changed = [k for k, v in profile.items() if v and getattr(account, k) != v]
    if changed:
        for k in changed:
            setattr(account, k, profile[k])
        account.save(update_fields=changed + ['updated_at'])
    return Response({'registered': True, 'account': _account_payload(account)})


PHONE_RE = re.compile(r'\D')


@bot_api(['POST'])
def user_register(request):
    """Create a site account for a Telegram user who shared their phone number."""
    tid = _telegram_id(request.data.get('telegram_id'))
    digits = PHONE_RE.sub('', str(request.data.get('phone') or ''))
    if not tid or not 7 <= len(digits) <= 15:
        return _error('bad_request', 'telegram_id and a phone number are required.')

    existing = _account(tid)
    if existing:
        return Response({'registered': True, 'created': False, 'account': _account_payload(existing)})

    profile = _profile(request.data)
    email = bot_email(tid)
    try:
        with transaction.atomic():
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User(
                    email=email, username=email,
                    first_name=profile['first_name'] or 'Telegram',
                    last_name=profile['last_name'],
                    phone=f'+{digits}',
                )
                user.set_unusable_password()
                user.save()
            account = TelegramAccount.objects.create(
                user=user, telegram_id=tid, phone=f'+{digits}', registered_via_bot=True, **profile,
            )
    except IntegrityError:            # two taps at once: the other request won
        account = _account(tid)
        if account is None:
            raise
        return Response({'registered': True, 'created': False, 'account': _account_payload(account)})
    return Response({'registered': True, 'created': True, 'account': _account_payload(account)}, status=201)


@bot_api(['POST'])
def user_link(request):
    """Attach this Telegram to the site account that made the link (/start link_<token>)."""
    tid = _telegram_id(request.data.get('telegram_id'))
    if not tid:
        return _error('bad_request', 'telegram_id is required.')
    user = TelegramToken.redeem(_clean(request.data.get('token'), 64), TelegramToken.LINK)
    if user is None:
        return _error('bad_token', 'This link has expired or was already used.')

    profile = _profile(request.data)
    with transaction.atomic():
        taken = TelegramAccount.objects.select_for_update().filter(telegram_id=tid).first()
        if taken and taken.user_id != user.id:
            return _error('telegram_taken', 'This Telegram is already linked to another account.', 409)
        account = taken or TelegramAccount.objects.select_for_update().filter(user=user).first()
        if account is None:
            account = TelegramAccount.objects.create(user=user, telegram_id=tid, **profile)
        else:
            # The account was linked to another Telegram before: move it here.
            account.telegram_id = tid
            for k, v in profile.items():
                if v:
                    setattr(account, k, v)
            account.save()
    return Response({'linked': True, 'account': _account_payload(account)})


@bot_api(['POST'])
def user_login_confirm(request):
    """The student tapped "✅ Ha, kiraman" for a "Continue with Telegram" request."""
    account = _account(_telegram_id(request.data.get('telegram_id')))
    if account is None:
        return _error('not_registered', 'Register in the bot first.', 404)
    if not account.user.is_active:
        return _error('inactive', 'This account is disabled.', 403)
    now = timezone.now()
    with transaction.atomic():
        row = (
            TelegramLogin.objects.select_for_update()
            .filter(token=_clean(request.data.get('token'), 64), user__isnull=True,
                    used_at__isnull=True, expires_at__gt=now)
            .first()
        )
        if row is None:
            return _error('bad_token', 'This sign-in request has expired.')
        row.user = account.user
        row.confirmed_at = now
        row.signed_up = bool(request.data.get('signed_up'))
        row.save(update_fields=['user', 'confirmed_at', 'signed_up'])
    return Response({'confirmed': True})


@bot_api(['POST'])
def user_login_link(request):
    """A one-time link that signs this Telegram user in on the site."""
    account = _account(_telegram_id(request.data.get('telegram_id')))
    if account is None:
        return _error('not_registered', 'Register in the bot first.', 404)
    if not account.user.is_active:
        return _error('inactive', 'This account is disabled.', 403)
    return Response({
        'url': card.site_login_url(account.user),
        'expires_in': int(TelegramToken.LIFETIME[TelegramToken.LOGIN].total_seconds()),
    })


# ── Payments ──────────────────────────────────────────────────────────────────

@bot_api(['GET'])
def plans(request):
    config = PaymentSettings.load()
    return Response({
        'accepting': bool(config.accepting and config.card_number),
        'card': {'number': config.card_number, 'holder': config.card_holder, 'label': config.card_label},
        'plans': card.plans_payload(config),
    })


@bot_api(['POST'])
def payment_create(request):
    account = _account(_telegram_id(request.data.get('telegram_id')))
    if account is None:
        return _error('not_registered', 'Register in the bot first.', 404)
    try:
        payment = card.create_payment(
            account.user, request.data.get('plan'), request.FILES.get('receipt'),
            telegram_id=account.telegram_id,
        )
    except card.CardPaymentError as e:
        return _error(e.code, e.message, e.status)
    return Response({
        'id': payment.id, 'plan': payment.plan, 'months': payment.months,
        'amount': payment.amount, 'status': payment.status,
        # False: the payments channel did not get it — the bot sends it to the admins itself.
        'posted': bool(payment.channel_message_id),
    }, status=201)


def _decider(request):
    name = _clean(request.data.get('admin_name'), 80)
    tid = _telegram_id(request.data.get('admin_telegram_id'))
    label = name or (str(tid) if tid else 'admin')
    return f'{label} (Telegram)'


def _decision_response(payment):
    return Response({
        'id': payment.id, 'status': payment.status,
        'premium_until': timezone.localtime(payment.premium_until).isoformat() if payment.premium_until else None,
        'reject_reason': payment.reject_reason,
    })


@bot_api(['POST'])
def payment_approve(request, payment_id):
    try:
        return _decision_response(card.approve(payment_id, _decider(request)))
    except card.CardPaymentError as e:
        return _error(e.code, e.message, e.status)


@bot_api(['POST'])
def payment_reject(request, payment_id):
    try:
        return _decision_response(card.reject(payment_id, _decider(request), _clean(request.data.get('reason'), 200)))
    except card.CardPaymentError as e:
        return _error(e.code, e.message, e.status)


# ── Mandatory channels ────────────────────────────────────────────────────────

def _channel_payload(ch):
    return {'chat_id': ch.chat_id, 'title': ch.title, 'username': ch.username, 'url': ch.url}


@bot_api(['GET', 'POST'])
def channels(request):
    if request.method == 'GET':
        return Response({'channels': [_channel_payload(c) for c in BotChannel.objects.all()]})

    try:
        chat_id = int(request.data.get('chat_id'))
    except (TypeError, ValueError):
        return _error('bad_request', 'chat_id is required.')
    title = _clean(request.data.get('title'), 255)
    username = _clean(request.data.get('username'), 64).lstrip('@')
    invite_link = _clean(request.data.get('invite_link'), 255)
    if not title or not (username or invite_link):
        return _error('bad_request', 'title and a username or invite link are required.')
    channel, created = BotChannel.objects.update_or_create(
        chat_id=chat_id, defaults={'title': title, 'username': username, 'invite_link': invite_link},
    )
    return Response(_channel_payload(channel), status=201 if created else 200)


@bot_api(['DELETE'])
def channel_delete(request, chat_id):
    deleted, _ = BotChannel.objects.filter(chat_id=int(chat_id)).delete()
    if not deleted:
        return _error('not_found', 'Channel not found.', 404)
    return Response(status=204)


# ── Statistics for /admin ─────────────────────────────────────────────────────

@bot_api(['GET'])
def stats(request):
    from api.stats_views import SOURCES, _section_row

    now = timezone.now()
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    month_start = today_start - timedelta(days=29)
    users = User.objects.all()      # same count as the admin dashboard
    sections = [_section_row(s, today_start, week_start, month_start) for s in SOURCES]

    return Response({
        'users': {
            'total': users.count(),
            'today': users.filter(created_at__gte=today_start).count(),
            'week': users.filter(created_at__gte=week_start).count(),
            'online': users.filter(last_seen_at__gte=online_since()).count(),
            'active_today': UserDailyVisit.objects.filter(date=timezone.localdate()).count(),
            'premium': users.filter(is_premium=True).filter(
                Q(premium_until__isnull=True) | Q(premium_until__gt=now),
            ).count(),
        },
        'bot': {
            'total': TelegramAccount.objects.count(),
            'today': TelegramAccount.objects.filter(created_at__gte=today_start).count(),
            'registered_here': TelegramAccount.objects.filter(registered_via_bot=True).count(),
        },
        'mocks': {
            'started_today': sum(s['started_today'] for s in sections),
            'completed_today': sum(s['completed_today'] for s in sections),
            'completed_week': sum(s['completed_week'] for s in sections),
        },
        'payments': card.payment_totals(),
    })
