"""
Character voices for the voice games: Toby, Mum, the teacher, the car coach…

One OpenAI model that can act (gpt-4o-mini-tts) with a short direction per
character. Each (character, text) clip is synthesized once and kept in the same
disk cache as the examiner lines, so after the first player it costs nothing.

POST /api/games/voice/tts/   {text, voice: <character key>}   →   audio/mpeg
Cache hits are free; only new clips count toward a per-user hourly cap.
"""
import hashlib
import json
import logging
import os
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.ielts_views import _write_file_atomic

log = logging.getLogger(__name__)

MODEL = 'gpt-4o-mini-tts'
MAX_CHARS = 300
NEW_CLIPS_PER_HOUR = 150          # per user; cached clips are never counted

# character → (OpenAI voice, acting direction, speed)
VOICES = {
    'toby': ('coral', 'You are Toby, a cartoon kitten about seven years old. Speak like a cheerful young child: '
                      'bright, high, playful and a little excited, but clear and not too fast for English learners.', 1.0),
    'girl': ('shimmer', 'A friendly eight-year-old girl: bright, curious and clear, natural pace.', 1.0),
    'boy': ('verse', 'A lively ten-year-old boy: friendly, energetic and clear.', 1.0),
    'mum': ('nova', 'A warm, kind mother talking to her child: gentle, smiling and clear.', 0.95),
    'teacher': ('sage', 'A friendly primary-school teacher: clear, encouraging and well paced.', 0.95),
    'man': ('ash', 'A friendly adult man: relaxed, warm and clear.', 0.95),
    'grandma': ('ballad', 'A sweet elderly grandmother: slow, soft and warm.', 0.9),
    'driver': ('onyx', 'A calm, cheerful bus driver: deep, friendly and clear.', 0.95),
    'coach': ('echo', 'An energetic racing-game announcer: punchy and excited, short and very clear.', 1.0),
    'narrator': ('alloy', 'A clear, friendly narrator for learners of English.', 0.95),
}
DEFAULT_VOICE = 'narrator'


def _cache_path(voice_key, text):
    voice, direction, speed = VOICES[voice_key]
    key = f'{MODEL}|{voice}|{direction}|{speed:.2f}|{text}'
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return os.path.join(settings.MEDIA_ROOT, 'tts_cache', digest[:2], f'{digest}.mp3')


def character_audio(voice_key, text):
    """MP3 bytes for one line in one character's voice → (bytes, 'HIT' | 'MISS'). Raises when OpenAI fails."""
    path = _cache_path(voice_key, text)
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data:
            return data, 'HIT'
    except OSError:
        pass
    voice, direction, speed = VOICES[voice_key]
    payload = json.dumps({'model': MODEL, 'input': text, 'voice': voice, 'instructions': direction,
                          'speed': speed, 'response_format': 'mp3'}).encode('utf-8')
    req = urllib.request.Request(
        'https://api.openai.com/v1/audio/speech', data=payload, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {settings.OPENAI_API_KEY}'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if data:                                   # never cache an empty body
        _write_file_atomic(path, data)
    return data, 'MISS'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def character_tts(request):
    text = ' '.join(str(request.data.get('text', '')).split())[:MAX_CHARS]
    if not text:
        return Response({'error': 'text required'}, status=400)
    voice_key = request.data.get('voice')
    if voice_key not in VOICES:
        voice_key = DEFAULT_VOICE

    if not os.path.exists(_cache_path(voice_key, text)):
        if not getattr(settings, 'OPENAI_API_KEY', ''):
            return Response({'error': 'Voice is not available.'}, status=503)
        key = f'game_tts_new:{request.user.id}'
        cache.add(key, 0, 3600)
        try:
            used = cache.incr(key)
        except ValueError:                     # expired between add and incr
            cache.set(key, 1, 3600)
            used = 1
        if used > NEW_CLIPS_PER_HOUR:
            return Response({'error': 'Too many new voice lines. Try again later.'}, status=429)

    try:
        data, status = character_audio(voice_key, text)
    except Exception:
        log.warning('Character TTS failed (%s)', voice_key, exc_info=True)
        return Response({'error': 'Voice is not available right now.'}, status=502)
    response = HttpResponse(data, content_type='audio/mpeg')
    response['Cache-Control'] = 'private, max-age=604800'
    response['X-TTS-Cache'] = status
    return response
