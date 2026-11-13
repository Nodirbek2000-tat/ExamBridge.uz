import time
import re
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.conf import settings

# ── Bot User-Agent blacklist ──────────────────────────────────────────────────
_BOT_PATTERNS = re.compile(
    r'(curl|wget|python-requests|python-httpx|httpie|scrapy|selenium|playwright|'
    r'puppeteer|phantomjs|headless|go-http-client|java/|ruby|perl|libwww|'
    r'axios|node-fetch|got/|undici|okhttp|aiohttp|httpx|mechanize|'
    r'scrapy|nutch|heritrix|googlebot|bingbot|yandex|semrush|ahrefsbot|'
    r'mj12bot|dotbot|petalbot|bytespider|claudebot|gptbot|chatgpt|'
    r'facebookexternalhit|twitterbot|linkedinbot|whatsapp|telegrambot|'
    r'masscan|nikto|sqlmap|nmap|zgrab|dirbuster|gobuster|wfuzz|ffuf)',
    re.IGNORECASE,
)

# The ExamBridge mobile app sends "ExamBridgeApp/<version> (<os>; ...)". Such a UA is never
# matched against the patterns above: whatever follows the prefix (an Android device codename
# such as "ruby", a library token) could collide with the short tokens and ban the user's IP.
_APP_UA_PREFIX = 'ExamBridgeApp/'

_API_PREFIX = '/api/'
_ADMIN_PATHS = ('/admin/', '/api/admin/')

# Tashqi servislar (Stripe webhook va h.k.) — security tekshiruvlardan ozod.
# Bu yo'llar Accept/User-Agent header yubormasligi mumkin va imzo bilan himoyalangan.
# /api/bot/ — o'zimizning Telegram botimiz, X-Bot-Secret kaliti bilan himoyalangan.
_EXEMPT_PATHS = ('/api/payments/webhook/', '/api/bot/')


def _is_exempt(path):
    return any(path.startswith(p) for p in _EXEMPT_PATHS)

# Cache keys
BLOCKED_SET_KEY = 'sec:blocked_ips'
BLOCKED_LOG_PREFIX = 'sec:log:'
BLOCK_SECONDS = 3600        # 1 soat
LOGIN_BLOCK_PREFIX = 'login_block:'
LOGIN_PATH = '/api/auth/login/'
BRUTE_BLOCK_SECONDS = 3600
BOT_BLOCK_SECONDS = 86400   # botlar 24 soat
SET_TTL = 86400 * 7         # set 7 kun saqlanadi


def _get_client_ip(request):
    """
    The visitor's address. nginx is the only way in and writes X-Real-IP itself,
    so it cannot be faked. The first X-Forwarded-For entry can: the browser sends
    it, and trusting it let anyone get another network blocked.
    """
    real = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real:
        return real
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[-1].strip()      # the entry our own proxy added
    return request.META.get('REMOTE_ADDR', '')


def _is_json_request(request):
    return (
        request.META.get('HTTP_ACCEPT', '').startswith('application/json')
        or request.META.get('CONTENT_TYPE', '').startswith('application/json')
    )


def _block_response(request, reason='Forbidden', status=403):
    if request.path.startswith(_API_PREFIX) or _is_json_request(request):
        return JsonResponse({'detail': reason}, status=status)
    return HttpResponse(reason, status=status, content_type='text/plain')


def _register_block(ip, reason, ua='', path='', ttl=BLOCK_SECONDS):
    """Cache ga bloklangan IP ni sababi bilan yozamiz."""
    now = int(time.time())
    entry = {
        'ip': ip,
        'reason': reason,
        'ua': ua[:200],
        'path': path[:100],
        'blocked_at': now,
        'unblock_at': now + ttl,
    }
    cache.set(f'{BLOCKED_LOG_PREFIX}{ip}', entry, ttl)
    # Shared set of blocked IPs (for listing)
    blocked = cache.get(BLOCKED_SET_KEY) or set()
    blocked.add(ip)
    cache.set(BLOCKED_SET_KEY, blocked, SET_TTL)


def _unregister_block(ip):
    cache.delete(f'{BLOCKED_LOG_PREFIX}{ip}')
    cache.delete(f'{LOGIN_BLOCK_PREFIX}{ip}')
    blocked = cache.get(BLOCKED_SET_KEY) or set()
    blocked.discard(ip)
    cache.set(BLOCKED_SET_KEY, blocked, SET_TTL)


def get_all_blocked():
    """Barcha bloklangan IP lar ro'yxatini qaytaradi."""
    blocked = cache.get(BLOCKED_SET_KEY) or set()
    result = []
    for ip in list(blocked):
        entry = cache.get(f'{BLOCKED_LOG_PREFIX}{ip}')
        if entry:
            result.append(entry)
        else:
            # TTL o'tib ketgan — set dan ham olib tashlaymiz
            blocked.discard(ip)
    cache.set(BLOCKED_SET_KEY, blocked, SET_TTL)
    result.sort(key=lambda x: x.get('blocked_at', 0), reverse=True)
    return result


# ── 1. Bot Blocker Middleware ─────────────────────────────────────────────────
class BotBlockerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ua = request.META.get('HTTP_USER_AGENT', '')
        path = request.path
        ip = _get_client_ip(request)

        # Webhook va boshqa tashqi servis yo'llari — tekshiruvsiz o'tkazamiz
        if _is_exempt(path):
            return self.get_response(request)

        # Block known bad bots (our own mobile app is never one)
        if ua and not ua.startswith(_APP_UA_PREFIX) and _BOT_PATTERNS.search(ua):
            _register_block(ip, f'bot_ua:{ua[:60]}', ua=ua, path=path, ttl=BOT_BLOCK_SECONDS)
            return _block_response(request, 'Access denied.', 403)

        # Block empty User-Agent on API and admin paths
        if not ua and (path.startswith(_API_PREFIX) or path.startswith('/admin/')):
            _register_block(ip, 'no_user_agent', ua='', path=path, ttl=BLOCK_SECONDS)
            return _block_response(request, 'Access denied.', 403)

        # Block missing Accept header on API
        if path.startswith(_API_PREFIX):
            accept = request.META.get('HTTP_ACCEPT', '')
            if not accept:
                _register_block(ip, 'no_accept_header', ua=ua, path=path, ttl=BLOCK_SECONDS)
                return _block_response(request, 'Access denied.', 403)

        return self.get_response(request)


# ── 2. Brute Force / Rate Limit Middleware ────────────────────────────────────
def _count(key, window):
    """Add one to a counter that lives `window` seconds from its first hit; return it."""
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:                 # expired between add and incr
        cache.set(key, 1, window)
        return 1


class BruteForceProtectionMiddleware:
    """
    A school or a learning centre puts a whole class behind one address, so the
    address is only ever stopped for traffic no classroom produces, and even then
    only the login is closed, never the site. A student who keeps typing a wrong
    password locks just their own account (api/auth_views.login_view).
    """
    MAX_FAILURES = 100                 # wrong passwords from one address in WINDOW_SECONDS
    WINDOW_SECONDS = 600
    BLOCK_SECONDS = 900                # the login closes for this long; everything else keeps working
    API_RATE_LIMIT = 300               # API requests a minute from one address, not signed in
    API_RATE_LIMIT_SIGNED_IN = 3000    # signed in: about a hundred students working at once
    API_RATE_WINDOW = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def _over_rate(self, ip, request):
        signed_in = request.META.get('HTTP_AUTHORIZATION', '').startswith('Bearer ')
        limit = self.API_RATE_LIMIT_SIGNED_IN if signed_in else self.API_RATE_LIMIT
        key = f'api_rate:{ip}:{"user" if signed_in else "anon"}'
        return _count(key, self.API_RATE_WINDOW) > limit

    def __call__(self, request):
        ip = _get_client_ip(request)
        path = request.path
        ua = request.META.get('HTTP_USER_AGENT', '')

        # Webhook — rate limit/blok tekshiruvidan ozod
        if _is_exempt(path):
            return self.get_response(request)

        if path == LOGIN_PATH and cache.get(f'{LOGIN_BLOCK_PREFIX}{ip}'):
            return _block_response(
                request, 'Too many failed sign-ins from this network. Try again in 15 minutes.', 429,
            )

        # Only this request is refused: the address is not blocked for anyone else.
        if path.startswith(_API_PREFIX) and self._over_rate(ip, request):
            return _block_response(request, 'Too many requests. Slow down.', 429)

        response = self.get_response(request)

        if path == LOGIN_PATH and response.status_code == 401:
            failures = _count(f'login_fail:{ip}', self.WINDOW_SECONDS)
            if failures >= self.MAX_FAILURES:
                cache.set(f'{LOGIN_BLOCK_PREFIX}{ip}', True, self.BLOCK_SECONDS)
                cache.delete(f'login_fail:{ip}')
                _register_block(
                    ip,
                    f'brute_force:{failures}_failed_logins',
                    ua=ua,
                    path=path,
                    ttl=self.BLOCK_SECONDS,
                )

        return response


# ── 3. Admin IP Whitelist Middleware ──────────────────────────────────────────
class AdminIPWhitelistMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._static_whitelist = getattr(settings, 'ADMIN_IP_WHITELIST', [])

    def _get_dynamic_whitelist(self):
        """DB cache dan dinamik whitelist (admin qilingan userlar IP si)."""
        return cache.get('sec:admin_whitelist') or []

    def __call__(self, request):
        path = request.path
        is_admin_path = any(path.startswith(p) for p in _ADMIN_PATHS)

        if is_admin_path:
            whitelist = self._static_whitelist + self._get_dynamic_whitelist()
            if whitelist:
                ip = _get_client_ip(request)
                if ip not in whitelist:
                    ua = request.META.get('HTTP_USER_AGENT', '')
                    _register_block(ip, 'admin_ip_not_whitelisted', ua=ua, path=path, ttl=600)
                    return _block_response(request, 'Admin access restricted.', 403)

        return self.get_response(request)


# ── Whitelist helpers (backend views dan chaqiriladi) ────────────────────────
def add_to_admin_whitelist(ip):
    wl = cache.get('sec:admin_whitelist') or []
    if ip not in wl:
        wl.append(ip)
        cache.set('sec:admin_whitelist', wl, SET_TTL)


def remove_from_admin_whitelist(ip):
    wl = cache.get('sec:admin_whitelist') or []
    wl = [x for x in wl if x != ip]
    cache.set('sec:admin_whitelist', wl, SET_TTL)


def get_admin_whitelist():
    static = getattr(settings, 'ADMIN_IP_WHITELIST', [])
    dynamic = cache.get('sec:admin_whitelist') or []
    return list(set(static + dynamic))


# ── 4. Premium Check Middleware ───────────────────────────────────────────────
class PremiumCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.is_premium:
            request.user.check_premium()
        return self.get_response(request)


# ── 5. Security Headers Middleware ────────────────────────────────────────────
class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        # Uploaded media (article PDFs, images) is embedded in our own pages via
        # <iframe>/<embed>, so it must allow same-origin framing. Everything else
        # stays DENY.
        response['X-Frame-Options'] = 'SAMEORIGIN' if request.path.startswith('/media/') else 'DENY'
        response['Server'] = 'SAT+'
        return response
