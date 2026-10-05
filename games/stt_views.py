"""
Speech-to-text fallback for the voice games.

The games first use the browser's own recogniser (free and instant). When it
is missing or fails, the page sends the short clip it recorded at the same
time, and the server transcribes it with Whisper.

POST /api/games/voice/transcribe/   multipart: audio (≤ 1.5 MB), game? (slug)  →  { text, remaining }
Daily cap per user keeps the cost tiny: 80 clips free, 400 with Premium. A global
daily cap for everyone (settings.VOICE_STT_GLOBAL_DAILY, default 15,000 ≈ $3.75/day)
answers 429 {error: 'global-limit', remaining: 0} once reached. The optional `game`
field (a voice-game slug) counts the day's clips per game for the admin cost display
(cache key voice_stt_game:<slug>:<date>).
At most WHISPER_SLOTS calls run at once in one server process; when they are
all busy a clip waits up to SLOT_WAIT_SECONDS for a free slot, then is refused
with 503 {error: 'busy', retry: true} + Retry-After — a crowd can never pile up
threads waiting on Whisper, and a refused clip does not use up the learner's
allowance. The slot is released in `finally`, whatever Whisper does.
"""
import logging
import threading

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.stt import whisper_transcribe

from .models import VOICE_GAME_SLUGS

log = logging.getLogger(__name__)

DAILY_FREE = 80
DAILY_PREMIUM = 400
MAX_BYTES = 1_500_000            # ~20 s of opus — the longest game line is far shorter
DAY_SECONDS = 36 * 3600
WHISPER_SLOTS = 6
SLOT_WAIT_SECONDS = 2.0
GLOBAL_DAILY_DEFAULT = 15_000

_slots = threading.BoundedSemaphore(WHISPER_SLOTS)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def transcribe(request):
    clip = request.FILES.get('audio')
    if not clip:
        return Response({'error': 'audio required'}, status=400)
    if clip.size > MAX_BYTES:
        return Response({'error': 'Audio is too long.'}, status=413)
    ctype = (clip.content_type or '').lower()
    if not ctype.startswith(('audio/', 'video/webm', 'application/octet-stream')):
        return Response({'error': 'Not an audio file.'}, status=400)

    if not _slots.acquire(timeout=SLOT_WAIT_SECONDS):
        response = Response({'error': 'busy', 'retry': True}, status=503)
        response['Retry-After'] = '2'
        return response
    try:
        return _transcribe(request, clip, ctype)
    finally:
        _slots.release()


def _day():
    return timezone.localdate().isoformat()


def user_key(user_id, day=None):
    return f'voice_stt:{user_id}:{day or _day()}'


def global_key(day=None):
    return f'voice_stt_all:{day or _day()}'


def game_key(slug, day=None):
    return f'voice_stt_game:{slug}:{day or _day()}'


def global_daily_cap():
    return int(getattr(settings, 'VOICE_STT_GLOBAL_DAILY', GLOBAL_DAILY_DEFAULT))


def _incr(key):
    cache.add(key, 0, DAY_SECONDS)
    try:
        return cache.incr(key)
    except ValueError:                      # expired between add and incr
        cache.set(key, 1, DAY_SECONDS)
        return 1


def _decr(key):
    try:
        cache.decr(key)
    except ValueError:
        pass


def daily_limit(user):
    return DAILY_PREMIUM if getattr(user, 'is_premium', False) else DAILY_FREE


def clips_left(user):
    """Transcribe clips this learner can still use today (their own cap and the global one)."""
    try:
        used = cache.get(user_key(user.id)) or 0
        used_all = cache.get(global_key()) or 0
    except Exception:
        return 0
    return max(0, min(daily_limit(user) - used, global_daily_cap() - used_all))


def _transcribe(request, clip, ctype):
    limit = daily_limit(request.user)
    key = user_key(request.user.id)
    used = _incr(key)
    if used > limit:
        return Response({'error': 'Daily voice limit reached. Try again tomorrow.', 'remaining': 0}, status=429)

    # everyone together: a hard ceiling on the day's Whisper bill
    all_key = global_key()
    if _incr(all_key) > global_daily_cap():
        _decr(all_key)
        _decr(key)                          # refused here — the learner's own allowance is not used up
        return Response({'error': 'global-limit', 'remaining': 0}, status=429)

    game = request.data.get('game') if hasattr(request, 'data') else None
    g_key = game_key(game) if isinstance(game, str) and game in VOICE_GAME_SLUGS else None
    if g_key:
        _incr(g_key)

    try:
        text = whisper_transcribe(clip.read(), ctype or 'audio/webm', timeout=20)
    except Exception:
        log.warning('Voice game transcription failed for user %s', request.user.id, exc_info=True)
        # a failed call does not use up the allowance (nor the day's totals)
        _decr(key)
        _decr(all_key)
        if g_key:
            _decr(g_key)
        return Response({'error': 'Could not hear the recording. Try again.'}, status=502)
    return Response({'text': str(text or '').strip()[:500], 'remaining': max(0, limit - used)})
