"""
Word Battle API — mounted at /api/games/word-battle/

    GET   home/                          levels (words, my best), the active round, my duels, config
    POST  rounds/                        {level}            → the new round (opponent: recorded player or bot)
    GET   rounds/<id>/                   the round (active) or its result (finished)
    POST  rounds/<id>/next/              → the next question {idx, t, prompt, options, …} — never the key
    POST  rounds/<id>/answer/            {idx, choice: 0–3 | null} → {correct, key, points, score, …}
    POST  rounds/<id>/finish/            → the result (idempotent)
    GET   leaderboard/?level=B1          this week's top 10 + my rank
    GET   duels/                         my recent duels
    POST  duels/                         {level} (a fresh set) or {round: <id>} (the set I just played)
    GET   duels/<code>/                  the duel (anyone; a guest sees names and status only)
    POST  duels/<code>/accept/           start (or resume) my side of the duel → round

    staff:
    GET   admin/stats/?days=1|7|30       rounds per day, players, accuracy, hardest words, duels
    (config: GET / PATCH /api/games/stats/admin/games/word-battle/ — validated by wordbattle/config.py)
"""
import uuid

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import SAFE_METHODS, AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from . import services
from .models import LEVELS, Round


class StartThrottle(UserRateThrottle):
    scope = 'wb_start'
    rate = '60/hour'           # a round takes ~2 minutes


class StepThrottle(UserRateThrottle):
    scope = 'wb_step'
    rate = '1500/hour'         # next + answer = 30 requests a round


class DuelThrottle(UserRateThrottle):
    """Creating duels only (reading the list is free)."""
    scope = 'wb_duel'
    rate = '30/day'

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _err(e):
    return Response({'error': e.message, 'code': e.code}, status=e.status)


def _rid(round_id):
    try:
        return uuid.UUID(str(round_id))
    except (TypeError, ValueError):
        return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def home(request):
    return Response(services.home(request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, StartThrottle])
def round_start(request):
    level = _body(request).get('level')
    if not isinstance(level, str) or level.upper() not in LEVELS:
        return Response({'error': 'level: A2, B1, B2, C1 yoki SAT', 'code': 'level'}, status=400)
    try:
        rnd = services.start_solo(request.user, level.upper())
    except services.WBError as e:
        return _err(e)
    return Response(services.round_payload(rnd), status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def round_detail(request, round_id):
    rnd = Round.objects.select_related('qset').filter(pk=round_id, user=request.user).first()
    if rnd is None:
        return Response({'error': 'Raund topilmadi.', 'code': 'not-found'}, status=404)
    rnd = services.close_if_stale(rnd)
    if rnd.status == Round.ACTIVE:
        return Response(services.round_payload(rnd))
    return Response(services.result_payload(rnd, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, StepThrottle])
def round_next(request, round_id):
    try:
        return Response(services.serve_next(request.user, round_id))
    except services.WBError as e:
        return _err(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, StepThrottle])
def round_answer(request, round_id):
    body = _body(request)
    try:
        return Response(services.submit_answer(request.user, round_id, body.get('idx'), body.get('choice')))
    except services.WBError as e:
        return _err(e)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, StepThrottle])
def round_finish(request, round_id):
    try:
        return Response(services.finish(request.user, round_id))
    except services.WBError as e:
        return _err(e)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leaderboard(request):
    level = (request.query_params.get('level') or 'B1').upper()
    if level not in LEVELS:
        return Response({'error': 'level: A2, B1, B2, C1 yoki SAT', 'code': 'level'}, status=400)
    return Response(services.leaderboard(request.user, level))


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, DuelThrottle])
def duels(request):
    if request.method == 'GET':
        return Response({'duels': services.my_duels(request.user, limit=20)})
    return _duel_create(request)


def _duel_create(request):
    body = _body(request)
    level = body.get('level')
    round_id = body.get('round')
    try:
        if round_id:
            rid = _rid(round_id)
            if rid is None:
                return Response({'error': 'round noto‘g‘ri', 'code': 'bad-round'}, status=400)
            duel = services.create_duel(request.user, round_id=rid)
        else:
            if not isinstance(level, str) or level.upper() not in LEVELS:
                return Response({'error': 'level: A2, B1, B2, C1 yoki SAT', 'code': 'level'}, status=400)
            duel = services.create_duel(request.user, level=level.upper())
    except services.WBError as e:
        return _err(e)
    duel = services.get_duel(duel.code)
    return Response(services.duel_payload(duel, request.user), status=201)


@api_view(['GET'])
@permission_classes([AllowAny])
def duel_detail(request, code):
    try:
        duel = services.get_duel(code)
    except services.WBError as e:
        return _err(e)
    return Response(services.duel_payload(duel, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([UserRateThrottle, StartThrottle])
def duel_accept(request, code):
    try:
        rnd = services.play_duel(request.user, code)
    except services.WBError as e:
        return _err(e)
    return Response(services.round_payload(rnd), status=201)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_stats(request):
    try:
        days = int(request.query_params.get('days', 7))
    except (TypeError, ValueError):
        days = 7
    if days not in (1, 7, 30):
        days = 7
    return Response(services.admin_stats(days))
