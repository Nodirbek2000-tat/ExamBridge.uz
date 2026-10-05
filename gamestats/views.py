"""
Games platform API — mounted at /api/games/stats/

    POST  open/                         {slug}  → the learner opened a game
    GET   hub/                          → [{slug, title, status, order, players_week}] (not hidden, hub order)

    staff only:
    GET   admin/overview/?days=1|7|30|90
    GET   admin/games/<slug>/           → {slug, title, status, order, config, defaults, effective}
    PATCH admin/games/<slug>/           {status?, order?, config?}  (config: validated by gamestats/configs.py
                                        and merged into the overrides; a null value resets one key)
    POST  admin/warm-voices/            {lines: [{text, voice}]}  → 202 job status
    GET   admin/warm-voices/status/
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .configs import bust_config_cache, defaults_for, effective, merge, schema_for
from .models import Game, GameDay
from .services import bust_hub_cache, count_open, hub_games, known_slugs
from .tasks import WARM_MAX_LINES, start_warm, warm_running, warm_status

RANGES = (1, 7, 30, 90)
MAX_ORDER = 32_000
MAX_RAW_LINES = 20_000


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


# ── learners ─────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def open_game(request):
    slug = _body(request).get('slug')
    if not isinstance(slug, str) or slug not in known_slugs():
        return Response({'error': 'unknown game'}, status=400)
    return Response({'ok': True, 'counted': count_open(request.user, slug)})


@api_view(['GET'])
@permission_classes([AllowAny])
def hub(request):
    return Response(hub_games())


# ── admin ────────────────────────────────────────────────────────────────────

def _game_payload(g):
    return {'slug': g.slug, 'title': g.title, 'status': g.status, 'order': g.order}


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_overview(request):
    try:
        days = int(request.query_params.get('days', 7))
    except (TypeError, ValueError):
        days = 7
    if days not in RANGES:
        days = 7
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]

    games = list(Game.objects.order_by('order', 'id'))
    rows = GameDay.objects.filter(slug__in=[g.slug for g in games], date__gte=start, date__lte=today)

    # one row per (slug, day): a GameDay row is one learner, so COUNT(*) = players that day
    daily = defaultdict(dict)
    for r in rows.values('slug', 'date').annotate(o=Sum('opens'), p=Sum('plays'), n=Count('id')):
        daily[r['slug']][r['date']] = r
    players = dict(rows.values('slug').annotate(n=Count('user', distinct=True)).values_list('slug', 'n'))
    total_players = rows.aggregate(n=Count('user', distinct=True))['n'] or 0

    zero = {'o': 0, 'p': 0, 'n': 0}
    out = []
    for g in games:
        d = daily.get(g.slug, {})
        series = [{'date': dt.isoformat(), 'opens': d.get(dt, zero)['o'], 'players': d.get(dt, zero)['n'],
                   'plays': d.get(dt, zero)['p']} for dt in dates]
        t = d.get(today, zero)
        out.append({
            **_game_payload(g),
            'opens': sum(s['opens'] for s in series),
            'players': players.get(g.slug, 0),
            'plays': sum(s['plays'] for s in series),
            'today': {'opens': t['o'], 'players': t['n'], 'plays': t['p']},
            'series': series,
        })

    return Response({
        'days': days,
        'start': start.isoformat(),
        'end': today.isoformat(),
        'totals': {
            'opens': sum(g['opens'] for g in out),
            'players': total_players,
            'plays': sum(g['plays'] for g in out),
        },
        'games': out,
    })


def _config_payload(g):
    return {**_game_payload(g), 'config': g.config or {}, 'defaults': defaults_for(g.slug),
            'effective': effective(g.slug, g.config)}


@api_view(['GET', 'PATCH'])
@permission_classes([IsAdminUser])
def admin_game_update(request, slug):
    game = get_object_or_404(Game, slug=slug)
    if request.method == 'GET':
        return Response(_config_payload(game))
    body = _body(request)
    fields = []
    if 'status' in body:
        # a list / dict here is unhashable — check the type before the lookup (400, never a 500)
        if not isinstance(body['status'], str) or body['status'] not in dict(Game.STATUS_CHOICES):
            return Response({'error': 'status must be live, soon or hidden'}, status=400)
        game.status = body['status']
        fields.append('status')
    if 'order' in body:
        order = body['order']
        if isinstance(order, bool) or not isinstance(order, (int, str)):
            return Response({'error': 'order must be a number'}, status=400)
        try:
            order = int(order)
        except ValueError:
            return Response({'error': 'order must be a number'}, status=400)
        if not 0 <= order <= MAX_ORDER:
            return Response({'error': f'order must be 0–{MAX_ORDER}'}, status=400)
        game.order = order
        fields.append('order')
    if 'config' in body:
        validate = schema_for(slug)
        if validate is None:
            return Response({'error': 'bu o‘yinda sozlamalar yo‘q'}, status=400)
        try:
            clean = validate(body['config'])
        except ValueError as e:               # ConfigError, or a plain ValueError from a game's own validator
            return Response({'error': str(e) or 'config noto‘g‘ri'}, status=400)
        game.config = merge(game.config, clean)
        fields.append('config')
    if not fields:
        return Response({'error': 'nothing to change (status, order, config)'}, status=400)
    game.save(update_fields=fields + ['updated_at'])
    bust_hub_cache()
    if 'config' in fields:
        bust_config_cache(slug)
        return Response(_config_payload(game))
    return Response(_game_payload(game))


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def admin_warm_voices(request):
    """POST {lines: [{text, voice}]} → 202 job status. GET → the same as status/."""
    if request.method == 'GET':
        return Response(warm_status())

    from games.tts_views import MAX_CHARS, VOICES

    raw = _body(request).get('lines')
    if not isinstance(raw, list) or not raw:
        return Response({'error': '`lines` must be a non-empty list of {text, voice}'}, status=400)

    lines, seen = [], set()
    unknown = capped = 0
    for item in raw[:MAX_RAW_LINES]:
        if isinstance(item, str):
            item = {'text': item}
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            continue
        voice = item.get('voice') or ''
        if voice and (not isinstance(voice, str) or voice not in VOICES):   # a list would be unhashable
            unknown += 1
            continue
        if voice:   # the same clean-up as POST /api/games/voice/tts/, so the cache key matches
            text = ' '.join(item['text'].split())[:MAX_CHARS]
        else:       # a plain sayLine(text) → the examiner voice endpoint
            text = item['text'].strip()[:4096]
        if not text or '\x00' in text or (text, voice) in seen:
            continue
        if len(lines) >= WARM_MAX_LINES:
            capped += 1
            continue
        seen.add((text, voice))
        lines.append([text, voice])

    if not lines:
        return Response({'error': 'no valid lines', 'unknown_voices': unknown}, status=400)
    if warm_running():
        return Response({'error': 'already running', **warm_status()}, status=409)
    st = start_warm(lines)
    return Response({**st, 'unknown_voices': unknown, 'capped': capped}, status=202)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_warm_status(request):
    return Response(warm_status())
