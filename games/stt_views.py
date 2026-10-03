"""
Speech-to-text fallback for the voice games.

The games first use the browser's own recogniser (free and instant). When it
is missing or fails, the page sends the short clip it recorded at the same
time, and the server transcribes it with Whisper.

POST /api/games/voice/transcribe/   multipart: audio (≤ 1.5 MB)  →  { text, remaining }
Daily cap per user keeps the cost tiny: 80 clips free, 400 with Premium.
"""
import logging

from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.stt import whisper_transcribe

log = logging.getLogger(__name__)

DAILY_FREE = 80
DAILY_PREMIUM = 400
MAX_BYTES = 1_500_000            # ~20 s of opus — the longest game line is far shorter
DAY_SECONDS = 36 * 3600


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

    limit = DAILY_PREMIUM if getattr(request.user, 'is_premium', False) else DAILY_FREE
    key = f'voice_stt:{request.user.id}:{timezone.localdate().isoformat()}'
    cache.add(key, 0, DAY_SECONDS)
    try:
        used = cache.incr(key)
    except ValueError:                      # expired between add and incr
        cache.set(key, 1, DAY_SECONDS)
        used = 1
    if used > limit:
        return Response({'error': 'Daily voice limit reached. Try again tomorrow.', 'remaining': 0}, status=429)

    try:
        text = whisper_transcribe(clip.read(), ctype or 'audio/webm', timeout=20)
    except Exception:
        log.warning('Voice game transcription failed for user %s', request.user.id, exc_info=True)
        try:
            cache.decr(key)                 # a failed call does not use up the allowance
        except ValueError:
            pass
        return Response({'error': 'Could not hear the recording. Try again.'}, status=502)
    return Response({'text': str(text or '').strip()[:500], 'remaining': max(0, limit - used)})
