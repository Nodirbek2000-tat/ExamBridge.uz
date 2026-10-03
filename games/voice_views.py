"""
Speak & Play — progress, runs, weekly leaderboard and the games hub numbers.

Speech is recognised in the learner's browser, so the server only stores what
a game wants to keep (free-form `data`), each finished run, and totals.

    GET/PUT  /api/games/voice/<slug>/progress/
    POST     /api/games/voice/<slug>/runs/
    GET      /api/games/voice/<slug>/leaderboard/
    GET      /api/games/hub/
"""
import json
import math
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Max, Min, Value
from django.db.models.functions import Greatest
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .models import ShadowingAttempt, VoiceGameProgress, VoiceGameRun, VOICE_GAME_SLUGS

WEEK = timedelta(days=7)
MAX_DATA_BYTES = 20 * 1024
LEADERBOARD_SIZE = 10
# Below this many players a week we show no number at all — never tiny or fake counts.
MIN_PUBLIC_PLAYERS = 30
HUB_CACHE_KEY = 'games:hub:players_week:v1'
HUB_CACHE_SECONDS = 10 * 60


class VoiceGameWriteThrottle(UserRateThrottle):
    """Caps saves / finished runs per user. Reads only count toward the global limit."""

    scope = 'voice_games'
    rate = '900/hour'   # a save after every line of fast play stays far below this

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


VOICE_THROTTLES = [UserRateThrottle, VoiceGameWriteThrottle]


# ── helpers ──────────────────────────────────────────────────────────────────

def _check_slug(slug):
    if slug not in VOICE_GAME_SLUGS:
        raise Http404('Unknown game')


def _progress_payload(slug, progress):
    if progress is None:
        return {'slug': slug, 'data': {}, 'best_score': 0, 'plays': 0, 'stars_total': 0}
    return {
        'slug': slug,
        'data': progress.data if isinstance(progress.data, dict) else {},
        'best_score': progress.best_score,
        'plays': progress.plays,
        'stars_total': progress.stars_total,
    }


def _clamp_int(value, lo, hi):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(n):
        return lo
    return max(lo, min(hi, int(round(n))))


def _clamp_accuracy(value):
    try:
        a = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(a):
        return 0.0
    # A percentage slipped through — read 85 as 0.85. Starts at 2, not 1, so a
    # float-rounding 1.0000000000000002 stays 1.0 instead of becoming 0.01.
    if 2 <= a <= 100:
        a = a / 100
    return round(max(0.0, min(1.0, a)), 4)


def _public_name(first, last):
    """'Nodirbek S.' — first name + last initial. Never an email or username."""
    parts = (first or '').split()
    # an email or a phone number typed into the name field stays private
    if not parts or '@' in parts[0] or sum(ch.isdigit() for ch in parts[0]) >= 5:
        return 'Player'
    given = parts[0][:20]
    surname = (last or '').strip() or (parts[1] if len(parts) > 1 else '')
    if surname and '@' not in surname:
        return f'{given} {surname[0].upper()}.'
    return given


def _public_count(n):
    return n if n >= MIN_PUBLIC_PLAYERS else None


# ── endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
@throttle_classes(VOICE_THROTTLES)
def voice_progress(request, slug):
    """GET → saved state + totals (defaults if never played). PUT {data: {...}} replaces the saved state."""
    _check_slug(slug)

    if request.method == 'GET':
        progress = VoiceGameProgress.objects.filter(user=request.user, slug=slug).first()
        return Response(_progress_payload(slug, progress))

    body = request.data if isinstance(request.data, dict) else {}
    data = body.get('data')
    if not isinstance(data, dict):
        return Response({'error': '`data` must be a JSON object'}, status=400)
    try:
        serialized = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError):
        return Response({'error': '`data` is not valid JSON'}, status=400)
    if '\\u0000' in serialized:      # json.dumps writes NUL as the escape \u0000
        # PostgreSQL jsonb cannot store U+0000 — refuse it instead of a 500
        return Response({'error': '`data` must not contain NUL characters'}, status=400)
    size = len(serialized.encode('utf-8'))
    if size > MAX_DATA_BYTES:
        return Response({'error': f'`data` is too large ({size} bytes, max {MAX_DATA_BYTES})'}, status=400)

    progress, _ = VoiceGameProgress.objects.get_or_create(user=request.user, slug=slug)
    progress.data = data
    progress.save(update_fields=['data', 'updated_at'])
    return Response(_progress_payload(slug, progress))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes(VOICE_THROTTLES)
def voice_run_create(request, slug):
    """Body {score, stars, accuracy, level, duration_sec, lines_said} → 201 {id, best_score}."""
    _check_slug(slug)
    body = request.data if isinstance(request.data, dict) else {}

    score = _clamp_int(body.get('score'), 0, 100_000)
    stars = _clamp_int(body.get('stars'), 0, 999)
    accuracy = _clamp_accuracy(body.get('accuracy'))
    duration = _clamp_int(body.get('duration_sec'), 0, 3600)
    lines = _clamp_int(body.get('lines_said'), 0, 1000)
    level = str(body.get('level') or '').replace('\x00', '').strip()[:40]

    with transaction.atomic():
        run = VoiceGameRun.objects.create(
            user=request.user, slug=slug, score=score, stars=stars, accuracy=accuracy,
            level=level, duration_sec=duration, lines_said=lines,
        )
        progress, _ = VoiceGameProgress.objects.get_or_create(user=request.user, slug=slug)
        # One UPDATE with F() — two runs finishing at once never lose a play or stars.
        VoiceGameProgress.objects.filter(pk=progress.pk).update(
            plays=F('plays') + 1,
            stars_total=F('stars_total') + stars,
            best_score=Greatest(F('best_score'), Value(score)),
            updated_at=timezone.now(),
        )
        best = VoiceGameProgress.objects.filter(pk=progress.pk).values_list('best_score', flat=True).first()

    return Response({'id': run.id, 'best_score': best or 0}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def voice_leaderboard(request, slug):
    """This week's (last 7 days) top 10 — one row per user, by their best run."""
    _check_slug(slug)
    since = timezone.now() - WEEK

    week_runs = VoiceGameRun.objects.filter(slug=slug, created_at__gte=since)

    # GROUP BY user in the database; ties go to whoever started playing first.
    top_rows = (
        week_runs
        .values('user', 'user__first_name', 'user__last_name')
        .annotate(best=Max('score'), first_at=Min('created_at'))
        .order_by('-best', 'first_at', 'user')[:LEADERBOARD_SIZE]
    )
    top = [{
        'name': _public_name(r['user__first_name'], r['user__last_name']),
        'score': r['best'],
        'is_me': r['user'] == request.user.id,
    } for r in top_rows]

    my_best = week_runs.filter(user=request.user).aggregate(best=Max('score'))['best']
    rank = None
    if my_best is not None:
        # competition ranking: players with a strictly higher best + 1 (HAVING … COUNT in SQL)
        rank = (
            week_runs.values('user').annotate(best=Max('score')).filter(best__gt=my_best).count() + 1
        )

    all_time = VoiceGameProgress.objects.filter(user=request.user, slug=slug).values_list('best_score', flat=True).first()
    return Response({
        'top': top,
        'me': {'best_score': my_best or 0, 'rank': rank, 'best_all_time': all_time or 0},
    })


def _players_week():
    """Distinct players per game in the last 7 days, hidden (None) below MIN_PUBLIC_PLAYERS. Cached 10 min."""
    cached = cache.get(HUB_CACHE_KEY)
    if cached is not None:
        return cached
    since = timezone.now() - WEEK
    counts = dict(
        VoiceGameRun.objects
        .filter(created_at__gte=since)
        .values('slug')
        .annotate(n=Count('user', distinct=True))
        .values_list('slug', 'n')
    )
    counts['shadowing'] = (
        ShadowingAttempt.objects.filter(created_at__gte=since)
        .aggregate(n=Count('user', distinct=True))['n'] or 0
    )
    result = {
        slug: {'players_week': _public_count(counts.get(slug, 0))}
        for slug in ('tobys-day', 'voice-drive', 'shadowing')
    }
    cache.set(HUB_CACHE_KEY, result, HUB_CACHE_SECONDS)
    return result


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def games_hub(request):
    """{ games: { slug: { players_week } }, me: { plays_week } } — real numbers only."""
    since = timezone.now() - WEEK
    plays_week = (
        VoiceGameRun.objects.filter(user=request.user, created_at__gte=since).count()
        + ShadowingAttempt.objects.filter(user=request.user, created_at__gte=since).count()
    )
    return Response({'games': _players_week(), 'me': {'plays_week': plays_week}})
