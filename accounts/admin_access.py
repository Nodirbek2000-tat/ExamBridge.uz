"""Admin panel tiered access control.

One place decides what each admin tier may reach, so nothing depends on
remembering to put the right permission class on ~115 separate views.

Tiers (see User.admin_tier):
    'super'  — the owner (is_superuser). Everything, including Django admin.
    1        — everything in the React admin panel, but NOT Django admin.
    2        — read-only; most sections, no AI Structures, no imports.
    3        — read-only; only Users / Learning Centers / Reports.

Levels 2 and 3 can never create, edit or delete: every unsafe HTTP method on
an admin path is refused. The matching menu items are hidden in the UI too
(sat_front/src/pages/admin/adminAccess.js keeps the same table), but the
server is what actually enforces it.
"""

from django.http import JsonResponse

# ── Areas ─────────────────────────────────────────────────────────────────────

USERS = 'users'
LEADERBOARD = 'leaderboard'
TESTMAKON = 'testmakon'
SAT = 'sat'
IELTS = 'ielts'
CEFR = 'cefr'
STUDY = 'study'
GAMES = 'games'
CENTERS = 'centers'
REPORTS = 'reports'
SYSTEM = 'system'
AI = 'ai'
IMPORT = 'import'

# Path prefix → area. Checked in order, so longer/more specific prefixes come
# first. Anything not matched here is not an admin path and is left alone.
PREFIX_AREAS = (
    ('/api/admin/users/', USERS),
    ('/api/admin/testmakon/', TESTMAKON),
    ('/api/admin/leaderboard/', LEADERBOARD),
    ('/api/admin/export/ielts/', IELTS),
    ('/api/admin/export/cefr/', CEFR),
    ('/api/admin/export/', SAT),
    ('/api/admin/sat/', SAT),
    ('/api/admin/ielts/', IELTS),
    ('/api/admin/cefr/', CEFR),
    ('/api/admin/study/', STUDY),
    ('/api/admin/games/', GAMES),
    ('/api/admin/ai/', AI),
    ('/api/admin/reports/', REPORTS),
    ('/api/admin/security/', SYSTEM),
    ('/api/system/', SYSTEM),
    ('/api/import/', IMPORT),
    ('/api/centers/admin/', CENTERS),
    ('/api/games/stats/admin/', GAMES),
    ('/api/games/speaking/admin/', GAMES),
    ('/api/games/words/admin/', GAMES),
    ('/api/games/runner/admin/', GAMES),
    ('/api/games/word-battle/admin/', GAMES),
)

# Which areas each tier may READ. Tiers 'super' and 1 get everything.
READABLE_BY_LEVEL = {
    2: {USERS, LEADERBOARD, TESTMAKON, SAT, IELTS, CEFR, STUDY, GAMES,
        CENTERS, REPORTS, SYSTEM},
    3: {USERS, CENTERS, REPORTS},
}

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def area_for_path(path):
    """Return the admin area a request path belongs to, or None."""
    for prefix, area in PREFIX_AREAS:
        if path.startswith(prefix):
            return area
    return None


def may_read(user, area):
    tier = user.admin_tier
    if tier in ('super', 1):
        return True
    allowed = READABLE_BY_LEVEL.get(tier)
    return bool(allowed and area in allowed)


def may_write(user, area):
    # Only the owner and level 1 may change anything.
    return user.admin_tier in ('super', 1) and may_read(user, area)


def check(user, path, method):
    """Return None if allowed, or an error string explaining the refusal."""
    area = area_for_path(path)
    if area is None:
        return None                      # not an admin path
    if not getattr(user, 'is_authenticated', False):
        return None                      # let the view's own auth answer
    if not may_read(user, area):
        return 'This section is not available for your admin level.'
    if method not in SAFE_METHODS and not may_write(user, area):
        return 'Your admin level is view-only.'
    return None


# ── Middleware ────────────────────────────────────────────────────────────────

def _resolve_user(request):
    """Get the acting user.

    The admin panel authenticates with a JWT bearer token, which Django's
    session middleware knows nothing about — request.user is AnonymousUser at
    this point. So we decode the token here as well. Admin traffic is low, so
    the extra decode costs nothing worth optimising.
    """
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        result = JWTAuthentication().authenticate(request)
    except Exception:
        return None                      # invalid/expired token — view will 401
    return result[0] if result else None


class AdminLevelMiddleware:
    """Blocks admin API paths the acting admin's level may not use."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        area = area_for_path(request.path)
        if area is not None:
            user = _resolve_user(request)
            if user is not None:
                error = check(user, request.path, request.method)
                if error:
                    return JsonResponse({'detail': error}, status=403)
        return self.get_response(request)
