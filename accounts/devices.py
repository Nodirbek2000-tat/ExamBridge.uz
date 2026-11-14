"""
A student account works on at most MAX_DEVICES devices at a time.

Signing in starts a UserSession and puts its key into the JWT pair (the `sid`
claim). A device over the limit signs out the one used longest ago; the same
device signing in again only replaces its own session. Every request checks its
session is still live — one cache read — so a signed-out device stops at once.
Students see their devices in the profile and can sign any of them out.

Admins (is_staff) are not limited. TestMakon students get their tokens from
TestMakon's server rather than from a device, so they have no sessions at all.
Tokens issued before sessions existed carry no `sid`: they keep working until
their next refresh, which starts a session for them.
"""
import hashlib
import ipaddress
import re
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User, UserSession

CACHE_SECONDS = 600            # how long "live or not" is remembered between requests
SEEN_EVERY_SECONDS = 300       # last_seen_at is written at most this often
DEVICE_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')

SIGNED_OUT = {
    UserSession.Reason.LIMIT: 'You were signed out: this account was opened on another device.',
    UserSession.Reason.USER: 'You were signed out from another device.',
}
SIGNED_OUT_DEFAULT = 'You were signed out. Please sign in again.'

# (text in the User-Agent, name shown) — the first match wins, so the order matters.
BROWSERS = [
    ('telegram', 'Telegram'), ('edg', 'Edge'), ('opr/', 'Opera'), ('yabrowser', 'Yandex Browser'),
    ('samsungbrowser', 'Samsung Internet'), ('fxios', 'Firefox'), ('firefox', 'Firefox'),
    ('crios', 'Chrome'), ('chrome', 'Chrome'), ('safari', 'Safari'),
]
SYSTEMS = [
    ('iphone', 'iPhone'), ('ipad', 'iPad'), ('android', 'Android'), ('windows', 'Windows'),
    ('macintosh', 'Mac'), ('mac os x', 'Mac'), ('cros', 'Chromebook'), ('linux', 'Linux'),
]
MOBILE = ('iphone', 'android', 'mobile')


def max_devices():
    return getattr(settings, 'MAX_DEVICES', 2)


def has_sessions(user):
    return user.platform != User.PLATFORM_TESTMAKON


def is_limited(user):
    return has_sessions(user) and not user.is_staff


# ── Telling devices apart ─────────────────────────────────────────────────────

def device_id(request):
    """The browser's own id (X-Device-Id, kept in its storage), else a hash of its User-Agent."""
    if request is None:
        return 'unknown'
    given = request.META.get('HTTP_X_DEVICE_ID', '').strip()
    if DEVICE_ID.match(given):
        return given
    agent = request.META.get('HTTP_USER_AGENT', '')
    return 'ua-' + hashlib.sha256(agent.encode()).hexdigest()[:40]


def device_name(agent):
    """'Chrome on Windows', 'Safari on iPhone' — enough for a student to know the device."""
    text = (agent or '').lower()
    if not text:
        return 'Unknown device'
    if 'okhttp' in text or 'expo' in text or 'exambridgeapp' in text:
        return 'ExamBridge app'
    browser = next((name for mark, name in BROWSERS if mark in text), 'Browser')
    system = next((name for mark, name in SYSTEMS if mark in text), '')
    return f'{browser} on {system}' if system else browser


def is_mobile(agent):
    text = (agent or '').lower()
    return any(mark in text for mark in MOBILE)


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    value = (forwarded.split(',')[0] if forwarded else request.META.get('REMOTE_ADDR', '')).strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


# ── Sessions ──────────────────────────────────────────────────────────────────

def _cache_key(key):
    return f'device-session:{key}'


def live_sessions(user):
    since = timezone.now() - settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME']
    return user.device_sessions.filter(revoked_at__isnull=True, last_seen_at__gte=since)


def revoke(keys, reason):
    keys = list(keys)
    if not keys:
        return
    UserSession.objects.filter(key__in=keys, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_reason=reason,
    )
    cache.set_many({_cache_key(k): False for k in keys}, CACHE_SECONDS)


def start(user, request=None):
    """A session for the device signing in. Returns its key, or None for an account without sessions."""
    if not has_sessions(user):
        return None
    agent = request.META.get('HTTP_USER_AGENT', '')[:300] if request is not None else ''
    device = device_id(request)
    with transaction.atomic():
        # One sign-in at a time per account, so two at once cannot both slip under the limit.
        User.objects.select_for_update().filter(pk=user.pk).exists()
        revoke(live_sessions(user).filter(device_id=device).values_list('key', flat=True),
               UserSession.Reason.REPLACED)
        session = UserSession.objects.create(
            user=user, key=secrets.token_hex(16), device_id=device,
            device=device_name(agent), user_agent=agent, ip=client_ip(request),
        )
        if is_limited(user):
            others = live_sessions(user).exclude(pk=session.pk).order_by('-last_seen_at', '-id')
            revoke(others.values_list('key', flat=True)[max_devices() - 1:], UserSession.Reason.LIMIT)
    cache.set(_cache_key(session.key), True, CACHE_SECONDS)
    return session.key


def tokens_for(user, request=None):
    """The JWT pair for a sign-in, tied to a new device session."""
    refresh = RefreshToken.for_user(user)
    key = start(user, request)
    if key:
        refresh['sid'] = key            # copied into every access token made from it
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def is_live(key):
    live = cache.get(_cache_key(key))
    if live is None:
        live = UserSession.objects.filter(key=key, revoked_at__isnull=True).exists()
        cache.set(_cache_key(key), live, CACHE_SECONDS)
    return live


def seen(key):
    """Note that a session is in use, at most once every few minutes."""
    if cache.add(f'device-seen:{key}', 1, SEEN_EVERY_SECONDS):
        UserSession.objects.filter(key=key).update(last_seen_at=timezone.now())


def signed_out_error(key):
    reason = UserSession.objects.filter(key=key).values_list('revoked_reason', flat=True).first()
    return {'detail': SIGNED_OUT.get(reason, SIGNED_OUT_DEFAULT), 'code': 'session_revoked', 'reason': reason or ''}


class DeviceJWTAuthentication(JWTAuthentication):
    """The usual JWT check, plus: the token's device session must still be live."""

    def get_user(self, validated_token):
        key = validated_token.get('sid')
        if key:
            if not is_live(key):
                raise AuthenticationFailed(signed_out_error(key))
            seen(key)
        return super().get_user(validated_token)
