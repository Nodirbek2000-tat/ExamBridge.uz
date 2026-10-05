"""
What the games report to the platform. Every game calls these; the Games
admin section reads the totals.

    count_open(user, slug)   — the learner opened the game (deduplicated per minute)
    count_play(user, slug)   — the learner finished a round / run / attempt

Both are cheap and never raise: a stats hiccup must never break a game.

The hub list (which games are on, their order, players this week) is read by
every learner who opens /games, so it is cached for five minutes and dropped
the moment an admin changes a game.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import Game, GameDay

log = logging.getLogger(__name__)

OPEN_DEDUPE_SECONDS = 60
# Below this many players a week the hub shows no number at all — never tiny or fake counts.
MIN_PUBLIC_PLAYERS = 30
HUB_CACHE_KEY = 'gamestats:hub:v1'
SLUGS_CACHE_KEY = 'gamestats:slugs:v1'
HUB_CACHE_SECONDS = 5 * 60


def _valid(user, slug):
    return (
        user is not None and getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None)
        and isinstance(slug, str) and 0 < len(slug) <= 40
    )


def _bump(user, slug, field):
    """+1 on today's row for (user, slug) — one UPDATE, or an INSERT the first time today."""
    today = timezone.localdate()
    rows = GameDay.objects.filter(user=user, slug=slug, date=today)
    if rows.update(**{field: F(field) + 1}):
        return
    try:
        with transaction.atomic():           # a savepoint: a lost race never breaks the caller's transaction
            GameDay.objects.create(user=user, slug=slug, date=today, **{field: 1})
    except IntegrityError:                   # another request created today's row first
        rows.update(**{field: F(field) + 1})


def count_open(user, slug):
    """Record that `user` opened game `slug` today. Repeats within a minute are ignored. → True if counted."""
    try:
        if not _valid(user, slug):
            return False
        try:
            first = cache.add(f'gamestats:open:{user.pk}:{slug}', 1, OPEN_DEDUPE_SECONDS)
        except Exception:                    # cache down: count it rather than lose it
            first = True
        if not first:
            return False
        _bump(user, slug, 'opens')
        return True
    except Exception:
        log.warning('count_open failed (%s)', slug, exc_info=True)
        return False


def count_play(user, slug):
    """Record that `user` finished one play of game `slug` today. → True if counted."""
    try:
        if not _valid(user, slug):
            return False
        _bump(user, slug, 'plays')
        return True
    except Exception:
        log.warning('count_play failed (%s)', slug, exc_info=True)
        return False


# ── hub ──────────────────────────────────────────────────────────────────────

def known_slugs():
    """Every game the platform knows (hidden ones included)."""
    slugs = cache.get(SLUGS_CACHE_KEY)
    if slugs is None:
        slugs = list(Game.objects.values_list('slug', flat=True))
        cache.set(SLUGS_CACHE_KEY, slugs, HUB_CACHE_SECONDS)
    return set(slugs)


def _players_week(slug):
    """Distinct learners who opened or played `slug` in the last 7 days.

    Voice games also count their finished runs, so the number is right from the
    first day this table exists (runs older than it were never in GameDay).
    """
    from games.models import VOICE_GAME_SLUGS, VoiceGameRun

    since = timezone.localdate() - timedelta(days=6)
    # one GameDay row per learner per day — a learner who came back on 3 days is still 1 player
    users = GameDay.objects.filter(slug=slug, date__gte=since).values('user_id')
    if slug in VOICE_GAME_SLUGS:
        runs = VoiceGameRun.objects.filter(slug=slug, created_at__gte=timezone.now() - timedelta(days=7))
        return users.union(runs.values('user_id')).count()      # UNION drops the repeats
    return users.distinct().count()


def public_count(n):
    return n if n >= MIN_PUBLIC_PLAYERS else None


def hub_games():
    """[{slug, title, status, order, players_week}] for every game that is not hidden, in hub order."""
    data = cache.get(HUB_CACHE_KEY)
    if data is not None:
        return data
    data = [{
        'slug': g.slug,
        'title': g.title,
        'status': g.status,
        'order': g.order,
        'players_week': public_count(_players_week(g.slug)),
    } for g in Game.objects.exclude(status=Game.HIDDEN).order_by('order', 'id')]
    cache.set(HUB_CACHE_KEY, data, HUB_CACHE_SECONDS)
    return data


def bust_hub_cache():
    cache.delete_many([HUB_CACHE_KEY, SLUGS_CACHE_KEY])
