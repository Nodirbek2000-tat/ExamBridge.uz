"""
TOBY RUN — learner endpoints (RUNNER_PLAN §B8.3). Mounted at /api/games/runner/.

    GET  deck/?level=A2&topic=metro   → {deck, seed, level, topic, server_stt, clips_left, config, items[≤40]}
    POST finish/                      → {score, best, new_best, ranked, flags, srs: {strengthened, new, weak}, …}
    POST practice/                    → {fixed}            (results-screen retries and the Tuzat drill)
    GET  me/?level=A2                 → {level, topics: [...], week: {...}}
    GET  board/?by=score|said         → {by, top: [{rank, name, value, is_me}], me: {value, rank}, players}
    GET  admin/stats/?days=1|7|30     (staff) → the Games → Runner admin page (cached 10 min)

The browser judges speech, so the deck carries the answers; the server recomputes the
score from the outcomes and clamps what is impossible (runner_logic.plausibility).
The run is saved by finish/ (never also POST /voice/runner/runs/). A repeated `ref`
answers with the stored run.
"""
import secrets
import statistics
from datetime import datetime, time, timedelta

from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, ExpressionWrapper, F, FloatField, Max, Min, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from gamestats.configs import game_config
from gamestats.models import GameDay
from gamestats.services import count_play
from vocabulary import srs
from vocabulary.bank import TOPICS
from vocabulary.models import Phrase, Review, Word

from . import runner_logic as rl
from .models import VoiceGameRun
from .runs import find_run, record_run
from .stt_views import clips_left, game_key
from .voice_views import _public_name

SLUG = 'runner'
DECK_SALT = 'runner.deck'
DECK_MAX_AGE = 3 * 3600
MAX_BODY = 24 * 1024
MAX_OUTCOMES = 120
MAX_PRACTICE = 20
CANDS_KEY = 'runner:cands:v1:{}'
CANDS_SECONDS = 10 * 60
ME_KEY = 'runner:me:v1:{}:{}'
ME_SECONDS = 5 * 60
DECK_CONFIG_KEYS = ('speed_scale', 'window_scale', 'balloon_gap_scale', 'station_every_m', 'clips_per_run',
                    'twister', 'revive_voice', 'levels', 'leaderboard')


class DeckThrottle(UserRateThrottle):
    scope = 'runner_deck'
    rate = '30/min'


class FinishThrottle(UserRateThrottle):
    scope = 'runner_finish'
    rate = '60/hour'


class PracticeThrottle(UserRateThrottle):
    scope = 'runner_practice'
    rate = '120/hour'


def _bad(msg, status=400):
    return Response({'error': msg}, status=status)


def _cache_get(key):
    try:
        return cache.get(key)
    except Exception:
        return None


def _cache_set(key, value, seconds):
    try:
        cache.set(key, value, seconds)
    except Exception:
        pass


def _cache_delete(key):
    try:
        cache.delete(key)
    except Exception:
        pass


def candidates(level):
    """Published speakable words + every published phrase at `level` and one level below (cached 10 min)."""
    key = CANDS_KEY.format(level)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    levels = [lv for lv in (level, rl.level_below(level)) if lv]
    words = list(Word.objects.filter(status='published', speak=True, level__in=levels).values(
        'id', 'word', 'uz', 'pos', 'level', 'topic', 'picture', 'say_also', 'synonyms', 'antonyms', 'definition',
        'distractors', 'speak_risk'))
    phrases = list(Phrase.objects.filter(status='published', level__in=levels).values(
        'id', 'kind', 'text', 'prompt', 'answer', 'accept', 'min_words', 'uz', 'level', 'topic', 'picture', 'voice'))
    out = {'w': words, 'p': phrases}
    _cache_set(key, out, CANDS_SECONDS)
    return out


def _level(request, cfg, default='A1'):
    level = str(request.GET.get('level') or default).upper()
    if level not in rl.LEVEL_ORDER:
        return None
    return level


# ── deck ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([DeckThrottle])
def deck(request):
    cfg = game_config(SLUG)
    level = _level(request, cfg)
    if not level or level not in (cfg.get('levels') or rl.LEVEL_ORDER):
        return _bad('level')
    topic = str(request.GET.get('topic') or 'all')
    if topic not in TOPICS:
        topic = 'all'
    user = request.user
    reviews = {}
    for kind, iid, skill, box, due_at in Review.objects.filter(user=user, skill__in=('say', 'mean')).values_list(
            'kind', 'item_id', 'skill', 'box', 'due_at'):
        reviews.setdefault((kind, iid), {})[skill] = (box, due_at)
    seed = secrets.randbelow(2_000_000_000) + 1
    items, tok = rl.build_deck(level, topic, seed, candidates(level), reviews, timezone.now(),
                               twister=bool(cfg.get('twister', True)))
    token = signing.dumps({'u': user.id, 'lv': level, 'tp': topic, 's': seed, 'n': secrets.token_hex(4), **tok},
                          salt=DECK_SALT, compress=True)
    server_stt = bool(cfg.get('server_stt', True))
    return Response({
        'deck': token, 'seed': seed, 'level': level, 'topic': topic,
        'server_stt': server_stt, 'clips_left': clips_left(user) if server_stt else 0,
        'config': {k: cfg.get(k) for k in DECK_CONFIG_KEYS},
        'items': items,
    })


def _load_deck(raw, user):
    if not isinstance(raw, str) or len(raw) > 8000:
        return None
    try:
        tok = signing.loads(raw, salt=DECK_SALT, max_age=DECK_MAX_AGE)
    except (signing.BadSignature, ValueError):          # SignatureExpired is a BadSignature
        return None
    if not isinstance(tok, dict) or tok.get('u') != user.id or tok.get('lv') not in rl.LEVEL_ORDER:
        return None
    return tok


def _num(value, lo, hi, default=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if value != value or value in (float('inf'), float('-inf')):
        return default
    return max(lo, min(hi, value))


# ── finish ───────────────────────────────────────────────────────────────────

def _body_too_big(request):
    try:
        if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BODY:
            return True
        return len(request.body) > MAX_BODY
    except Exception:
        return True


def _stored_payload(run):
    resp = (run.meta or {}).get('resp')
    if isinstance(resp, dict):
        return resp
    return {'score': run.score, 'best': run.score, 'new_best': False, 'ranked': run.ranked,
            'flags': (run.meta or {}).get('flags', []), 'srs': {'strengthened': 0, 'new': 0, 'weak': 0}, 'run': run.id}


def apply_reviews(user, outcomes, skill_of, now):
    """srs.apply for each outcome (in order) → (rows to save, counts). One SELECT."""
    keys = {(o['k'], o['id'], skill_of(o)) for o in outcomes}
    if not keys:
        return [], {'strengthened': 0, 'new': 0, 'weak': 0}
    ids_w = [i for k, i, _ in keys if k == 'w']
    ids_p = [i for k, i, _ in keys if k == 'p']
    existing = {}
    cond = Q()
    if ids_w:
        cond |= Q(kind='w', item_id__in=ids_w)
    if ids_p:
        cond |= Q(kind='p', item_id__in=ids_p)
    for r in Review.objects.filter(cond, user=user):
        existing[(r.kind, r.item_id, r.skill)] = r
    rows = {}
    effects = {}
    created = set()
    for o in outcomes:
        key = (o['k'], o['id'], skill_of(o))
        r = rows.get(key) or existing.get(key)
        if r is None:
            r = srs.new_review(user.id, o['k'], o['id'], key[2], now)
            created.add(key)
        eff = srs.apply(r, o['v'], now, tries=o['tries'], pt=o.get('pt') or '')
        if eff == 'skipped' and key not in rows:          # silence changes nothing (and creates no row)
            created.discard(key)
            continue
        if o['v'] in ('ok', 'close', 'miss'):
            if o.get('heard'):
                r.last_heard = o['heard'][:80]
            if o['v'] == 'ok' and isinstance(o.get('ms'), int) and o['ms'] > 0:
                r.best_ms = o['ms'] if not r.best_ms else min(r.best_ms, o['ms'])
        rows[key] = r
        effects.setdefault(key, []).append(eff)
    counts = {
        'strengthened': sum(1 for e in effects.values() if 'promoted' in e),
        'new': sum(1 for k in rows if k in created),
        'weak': sum(1 for k, r in rows.items() if r.last_verdict == 'miss'),
    }
    return list(rows.values()), counts


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([FinishThrottle])
def finish(request):
    if _body_too_big(request):
        return _bad('too-large')
    body = request.data if isinstance(request.data, dict) else {}
    user = request.user
    tok = _load_deck(body.get('deck'), user)
    if tok is None:
        return _bad('deck')
    duration = _num(body.get('duration_s'), -1, 10 ** 6)
    if duration is None or duration < 10 or duration > 3600:
        return _bad('duration')
    raw = body.get('outcomes') if isinstance(body.get('outcomes'), list) else []
    if len(raw) > MAX_OUTCOMES:
        return _bad('outcomes')
    ref = body.get('ref') if isinstance(body.get('ref'), str) else ''
    ref = ''.join(ch for ch in ref if ch.isalnum() or ch in '-_')[:32]
    mode = body.get('mode') if body.get('mode') in ('voice', 'listen', 'card') else 'voice'
    stt = body.get('stt') if body.get('stt') in ('browser', 'server') else 'browser'
    level = tok['lv']
    distance = int(_num(body.get('distance_m'), 0, 10 ** 6, 0))
    score_client = _num(body.get('score_client'), 0, 10 ** 9)
    cfg = game_config(SLUG)

    outcomes = rl.clean_outcomes(raw, rl.token_index(tok))
    sc = rl.score(outcomes, level=level, distance_m=distance, stt=stt, mode=mode,
                  window_scale=float(cfg.get('window_scale') or 1.0))
    flags = rl.plausibility(level=level, distance_m=distance, duration_s=duration, outcomes=outcomes,
                            score_client=score_client, server_score=sc['score'],
                            speed_scale=float(cfg.get('speed_scale') or 1.0))
    ranked = not flags and mode == 'voice' and bool(cfg.get('leaderboard', True))
    spoken = [o for o in outcomes if o['v'] != 'skip']
    ok = sum(1 for o in spoken if o['v'] == 'ok')
    close = sum(1 for o in spoken if o['v'] == 'close')
    first = sum(1 for o in spoken if o['v'] == 'ok' and o['tries'] <= 1)
    now = timezone.now()
    skill_of = (lambda o: 'mean') if mode == 'listen' else (lambda o: 'say')
    rows, counts = ([], {'strengthened': 0, 'new': 0, 'weak': 0}) if flags else apply_reviews(user, outcomes, skill_of, now)
    resp = {
        'score': sc['score'], 'points': sc['points'], 'passes': sc['passes'], 'mult': sc['mult'],
        'ranked': ranked, 'flags': flags, 'srs': counts,
    }
    meta = {
        'mode': mode, 'stt': stt, 'said': len(spoken), 'ok': ok, 'first': first, 'close': close,
        'clips': int(_num(body.get('clips'), 0, 1000, 0)), 'distance': distance,
        'revives': int(_num(body.get('revives'), 0, 10, 0)), 'stations': int(_num(body.get('stations'), 0, 100, 0)),
        'coins': int(_num(body.get('coins'), 0, 100000, 0)), 'topic': tok.get('tp') or 'all', 'flags': flags,
        'new': counts['new'], 'by_kind': _by_kind(spoken), 'ms_med': _ms_median(outcomes),
    }
    # one transaction (no savepoint of its own: the inner record_run has one, and a repeated ref is
    # caught right there, before anything else was written)
    with transaction.atomic(savepoint=False):
        try:
            run, best = record_run(
                user, SLUG, score=sc['score'], stars=min(3, sc['passes'] // 5),
                accuracy=round(first / len(spoken), 4) if spoken else 0.0, level=level,
                duration_sec=int(duration), lines_said=ok + close, ref=ref, ranked=ranked, meta=meta)
        except IntegrityError:
            stored = find_run(user, SLUG, ref)
            if stored is None:
                raise
            return Response(_stored_payload(stored))
        resp['best'] = best
        resp['new_best'] = bool(sc['score'] > 0 and best == sc['score'])
        resp['run'] = run.id
        # the answer is kept in the run, so a repeated ref gets exactly the same payload
        VoiceGameRun.objects.filter(pk=run.pk).update(meta={**meta, 'resp': resp})
        if rows:
            srs.save_reviews(rows)
        transaction.on_commit(lambda: _after_finish(user))
    return Response(resp)


def _by_kind(spoken):
    out = {}
    for o in spoken:
        b = out.setdefault(o['kind'], [0, 0])
        b[0] += 1
        if o['v'] == 'ok' and o['tries'] <= 1:
            b[1] += 1
    return out


def _ms_median(outcomes):
    """Median time from mic open to the matching result of first-try passes (tunes the windows)."""
    ms = [o['ms'] for o in outcomes if o['v'] in ('ok', 'close') and o['tries'] <= 1
          and isinstance(o.get('ms'), (int, float)) and o['ms'] > 0]
    return int(statistics.median(ms)) if ms else None


def _after_finish(user):
    count_play(user, SLUG)
    for lv in rl.LEVEL_ORDER:
        _cache_delete(ME_KEY.format(user.id, lv))


# ── practice ─────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([PracticeThrottle])
def practice(request):
    body = request.data if isinstance(request.data, dict) else {}
    user = request.user
    tok = _load_deck(body.get('deck'), user)
    if tok is None:
        return _bad('deck')
    raw = body.get('outcomes') if isinstance(body.get('outcomes'), list) else []
    if len(raw) > MAX_PRACTICE:
        return _bad('outcomes')
    outcomes = rl.clean_outcomes(raw, rl.token_index(tok))
    if not outcomes:
        return Response({'fixed': 0})
    now = timezone.now()
    keys = {(o['k'], o['id']) for o in outcomes}
    existing = {(r.kind, r.item_id): r for r in Review.objects.filter(
        user=user, skill='say', item_id__in=[i for _, i in keys]) if (r.kind, r.item_id) in keys}
    rows, fixed = {}, 0
    for o in outcomes:
        key = (o['k'], o['id'])
        r = rows.get(key) or existing.get(key)
        if r is None:
            if o['v'] not in ('ok', 'close'):
                continue
            r = srs.new_review(user.id, o['k'], o['id'], 'say', now)
        if srs.apply(r, o['v'], now, practice=True) == 'fixed':
            fixed += 1
            if o.get('heard'):
                r.last_heard = o['heard'][:80]
            rows[key] = r
    srs.save_reviews(list(rows.values()))
    return Response({'fixed': fixed})


# ── me ───────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    level = str(request.GET.get('level') or 'A1').upper()
    if level not in rl.LEVEL_ORDER:
        return _bad('level')
    user = request.user
    key = ME_KEY.format(user.id, level)
    hit = _cache_get(key)
    if hit is not None:
        return Response(hit)
    cands = candidates(level)
    say = {(k, i): (box, seen) for k, i, box, seen in Review.objects.filter(user=user, skill='say').values_list(
        'kind', 'item_id', 'box', 'seen')}
    topics = {}
    for kind, rows in (('w', cands['w']), ('p', cands['p'])):
        for it in rows:
            if it['level'] != level:
                continue
            t = topics.setdefault(it['topic'] or 'general', {'topic': it['topic'] or 'general', 'total': 0, 'said': 0, 'mastered': 0})
            t['total'] += 1
            s = say.get((kind, it['id']))
            if s and s[1] > 0:
                t['said'] += 1
            if s and s[0] >= srs.MASTERED_BOX:
                t['mastered'] += 1
    for t in topics.values():
        t['pct'] = round(100 * t['mastered'] / t['total']) if t['total'] else 0
    now = timezone.now()
    week = {'lines': 0, 'said': 0, 'first': 0, 'prev_said': 0, 'prev_first': 0, 'new': 0}
    for created, lines, meta in VoiceGameRun.objects.filter(
            user=user, slug=SLUG, created_at__gte=now - timedelta(days=14)).values_list('created_at', 'lines_said', 'meta'):
        meta = meta if isinstance(meta, dict) else {}
        if created >= now - timedelta(days=7):
            week['lines'] += lines
            week['said'] += int(meta.get('said') or 0)
            week['first'] += int(meta.get('first') or 0)
            week['new'] += int(meta.get('new') or 0)
        else:
            week['prev_said'] += int(meta.get('said') or 0)
            week['prev_first'] += int(meta.get('first') or 0)
    out = {
        'level': level,
        'topics': sorted(topics.values(), key=lambda t: (list(TOPICS).index(t['topic']) if t['topic'] in TOPICS else 99, t['topic'])),
        'week': {
            'lines': week['lines'],
            'first_try_rate': round(week['first'] / week['said'], 3) if week['said'] else None,
            'prev_first_try_rate': round(week['prev_first'] / week['prev_said'], 3) if week['prev_said'] else None,
            'mastered_total': sum(1 for box, _ in say.values() if box >= srs.MASTERED_BOX),
            'new_this_week': week['new'],
        },
    }
    _cache_set(key, out, ME_SECONDS)
    return Response(out)


# ── weekly boards (§B7 "Leaderboard"; P4 ?by=said) ───────────────────────────

BOARD_SIZE = 10
PLAYERS_MIN = 30
BOARD_KEY = 'runner:board:v1:{}'
BOARD_SECONDS = 60


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def board(request):
    """This week's top 10 — best score, or (by=said) most lines said — ranked voice runs only."""
    by = 'said' if request.GET.get('by') == 'said' else 'score'
    since = timezone.now() - timedelta(days=7)
    runs = VoiceGameRun.objects.filter(slug=SLUG, created_at__gte=since, ranked=True)
    agg = Sum('lines_said') if by == 'said' else Max('score')
    key = BOARD_KEY.format(by)
    cached = _cache_get(key)
    if cached is None:
        rows = list(runs.values('user', 'user__first_name', 'user__last_name')
                    .annotate(value=agg, first_at=Min('created_at')).filter(value__gt=0)
                    .order_by('-value', 'first_at', 'user')[:BOARD_SIZE])
        top, rank, prev = [], 0, None
        for i, r in enumerate(rows):
            if r['value'] != prev:
                rank, prev = i + 1, r['value']
            top.append({'rank': rank, 'user': r['user'], 'name': _public_name(r['user__first_name'], r['user__last_name']),
                        'value': int(r['value'])})
        players = runs.values('user').distinct().count()
        cached = {'top': top, 'players': players if players >= PLAYERS_MIN else None}
        _cache_set(key, cached, BOARD_SECONDS)
    me = runs.filter(user=request.user).aggregate(v=agg)['v'] or 0
    my_rank = None
    if me:
        my_rank = runs.values('user').annotate(v=agg).filter(v__gt=me).count() + 1
    return Response({
        'by': by,
        'top': [{'rank': r['rank'], 'name': r['name'], 'value': r['value'], 'is_me': r['user'] == request.user.id}
                for r in cached['top']],
        'me': {'value': int(me), 'rank': my_rank},
        'players': cached['players'],
    })


# ── admin: Games → Runner (§B8.6) ────────────────────────────────────────────

STATS_KEY = 'runner:admin:stats:v1:{}'
STATS_SECONDS = 10 * 60
STATS_SAMPLE = 5000              # meta-based numbers come from the latest runs in the range
CLIP_COST = 0.00025              # $ per clip (≈ 2.5 s at whisper-1, $0.006/min)
HARDEST_MIN_SEEN = 20


def _rate(a, b):
    return round(a / b, 3) if b else None


def _hardest():
    """Items said at least 20 times, the lowest ok share first, with their top mis-hears (nightly roll-up)."""
    rate = ExpressionWrapper(F('say_ok') * 1.0 / F('say_seen'), output_field=FloatField())
    words = Word.objects.filter(say_seen__gte=HARDEST_MIN_SEEN).annotate(rate=rate).order_by('rate', '-say_seen').values(
        'id', 'word', 'uz', 'level', 'topic', 'picture', 'say_seen', 'say_ok', 'say_heard', 'rate')[:20]
    phrases = Phrase.objects.filter(say_seen__gte=HARDEST_MIN_SEEN).annotate(rate=rate).order_by('rate', '-say_seen').values(
        'id', 'kind', 'text', 'prompt', 'uz', 'level', 'topic', 'picture', 'voice', 'say_seen', 'say_ok', 'say_heard', 'rate')[:20]
    out = [{'k': 'w', 'id': w['id'], 'kind': 'word', 'text': w['word'], 'uz': w['uz'], 'level': w['level'],
            'topic': w['topic'], 'picture': w['picture'], 'voice': 'teacher', 'seen': w['say_seen'], 'ok': w['say_ok'],
            'rate': round(w['rate'] or 0, 3), 'heard': (w['say_heard'] or [])[:3]} for w in words]
    out += [{'k': 'p', 'id': p['id'], 'kind': p['kind'],
             'text': p['prompt'] if p['kind'] == 'answer' and p['prompt'] else p['text'], 'uz': p['uz'],
             'level': p['level'], 'topic': p['topic'], 'picture': p['picture'], 'voice': p['voice'] or 'narrator',
             'seen': p['say_seen'], 'ok': p['say_ok'], 'rate': round(p['rate'] or 0, 3), 'heard': (p['say_heard'] or [])[:3]}
            for p in phrases]
    out.sort(key=lambda r: (r['rate'], -r['seen']))
    return out[:20]


def _retention(start_day, today):
    """Of the learners whose first Toby Run day is in the range: back the next day / within 7 days."""
    firsts = dict(GameDay.objects.filter(slug=SLUG).values('user').annotate(first=Min('date'))
                  .filter(first__gte=start_day, first__lt=today).values_list('user', 'first')[:5000])
    if not firsts:
        return {'cohort': 0, 'd1': None, 'd7': None}
    days = {}
    for u, d in GameDay.objects.filter(slug=SLUG, user__in=list(firsts), date__gt=start_day).values_list('user', 'date'):
        days.setdefault(u, set()).add(d)
    d1 = sum(1 for u, f in firsts.items() if f + timedelta(days=1) in days.get(u, ()))
    d7 = sum(1 for u, f in firsts.items() if any(f < d <= f + timedelta(days=7) for d in days.get(u, ())))
    n = len(firsts)
    return {'cohort': n, 'd1': _rate(d1, n), 'd7': _rate(d7, n)}


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_stats(request):
    try:
        days = int(request.GET.get('days') or 7)
    except ValueError:
        days = 7
    if days not in (1, 7, 30):
        days = 7
    key = STATS_KEY.format(days)
    if request.GET.get('fresh') != '1':
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)
    today = timezone.localdate()
    start_day = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start_day, time.min))
    runs = VoiceGameRun.objects.filter(slug=SLUG, created_at__gte=since)

    tot = runs.aggregate(runs=Count('id'), players=Count('user', distinct=True), dur=Avg('duration_sec'),
                         lines=Sum('lines_said'), ranked=Count('id', filter=Q(ranked=True)))
    by_day = {r['d']: r for r in runs.annotate(d=TruncDate('created_at')).values('d').annotate(
        runs=Count('id'), players=Count('user', distinct=True), lines=Sum('lines_said')).order_by('d')}
    series = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        r = by_day.get(d) or {}
        series.append({'date': d.isoformat(), 'runs': r.get('runs', 0), 'players': r.get('players', 0),
                       'lines': r.get('lines') or 0})

    sample = list(runs.order_by('-created_at').values_list('level', 'ranked', 'meta')[:STATS_SAMPLE])
    said = first = 0
    kinds, levels = {}, {}
    stt, mode = {'browser': 0, 'server': 0}, {'voice': 0, 'listen': 0, 'card': 0}
    ms = {'browser': [], 'server': []}
    reasons = {}
    for level, ranked, meta in sample:
        meta = meta if isinstance(meta, dict) else {}
        n_said = int(meta.get('said') or 0)
        n_first = int(meta.get('first') or 0)
        said += n_said
        first += n_first
        lv = levels.setdefault(level or '?', {'runs': 0, 'said': 0, 'first': 0})
        lv['runs'] += 1
        lv['said'] += n_said
        lv['first'] += n_first
        for k, pair in (meta.get('by_kind') or {}).items():
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                b = kinds.setdefault(k, {'said': 0, 'first': 0})
                b['said'] += int(pair[0] or 0)
                b['first'] += int(pair[1] or 0)
        s_ = meta.get('stt') if meta.get('stt') in stt else 'browser'
        m_ = meta.get('mode') if meta.get('mode') in mode else 'voice'
        stt[s_] += 1
        mode[m_] += 1
        if isinstance(meta.get('ms_med'), (int, float)):
            ms[s_].append(meta['ms_med'])
        if not ranked:
            flags = [f for f in (meta.get('flags') or []) if isinstance(f, str)]
            for f in flags or ([m_] if m_ != 'voice' else ['leaderboard-off']):
                reasons[f] = reasons.get(f, 0) + 1
    try:
        clips = int(cache.get(game_key(SLUG)) or 0)
    except Exception:
        clips = 0
    n = len(sample)
    out = {
        'days': days, 'from': start_day.isoformat(), 'to': today.isoformat(), 'sample': n,
        'totals': {
            'runs': tot['runs'], 'players': tot['players'], 'ranked': tot['ranked'],
            'unranked': tot['runs'] - tot['ranked'], 'avg_duration_s': round(tot['dur'] or 0),
            'lines_per_run': round((tot['lines'] or 0) / tot['runs'], 1) if tot['runs'] else 0,
            'utterances_per_run': round(said / n, 1) if n else 0, 'first_try_rate': _rate(first, said),
        },
        'series': series,
        'by_kind': {k: {**v, 'rate': _rate(v['first'], v['said'])} for k, v in sorted(kinds.items())},
        'by_level': {k: {**v, 'rate': _rate(v['first'], v['said'])} for k, v in sorted(levels.items())},
        'share': {'stt': stt, 'mode': mode},
        'median_ms': {k: (int(statistics.median(v)) if v else None) for k, v in ms.items()},
        'unranked_reasons': reasons,
        'clips_today': clips, 'cost_today': round(clips * CLIP_COST, 2),
        'retention': _retention(start_day, today),
        'hardest': _hardest(),
        'config': game_config(SLUG),
    }
    _cache_set(key, out, STATS_SECONDS)
    return Response(out)
