"""
Server speech-to-text (OpenAI Whisper) for short learner recordings.

Used when the browser could not turn speech into text: Safari / Firefox /
Yandex Browser, a blocked Google speech service, a slow connection. Costs
about $0.006 per minute, so callers keep clips short and cap them per user.
"""
import logging
import os

import requests
from django.conf import settings

log = logging.getLogger(__name__)

EXT = {
    'audio/webm': 'webm', 'video/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/x-m4a': 'm4a',
    'audio/aac': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav',
}
# file name suffix → stored extension, and stored extension → the MIME type to report
NAME_EXT = {'m4a': 'm4a', 'mp4': 'm4a', 'aac': 'm4a', 'webm': 'webm', 'ogg': 'ogg', 'wav': 'wav', 'mp3': 'mp3'}
EXT_MIME = {'m4a': 'audio/mp4', 'webm': 'audio/webm', 'ogg': 'audio/ogg', 'wav': 'audio/wav', 'mp3': 'audio/mpeg'}


def audio_ext(content_type='', name=''):
    """
    Extension to store an uploaded recording under: its MIME type decides, then its file
    name (a browser Blob without a type arrives as application/octet-stream), else webm.
    The phone app sends audio/mp4 'answer_0.m4a'.
    """
    base = (content_type or '').split(';')[0].strip().lower()
    if base in EXT:
        return EXT[base]
    return NAME_EXT.get(os.path.splitext(name or '')[1].lstrip('.').lower(), 'webm')


def whisper_transcribe(data, content_type='audio/webm', timeout=30):
    """bytes → text ('' when nothing was said). Raises on a service / configuration failure."""
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        raise RuntimeError('OpenAI API key is not configured')
    base = (content_type or 'audio/webm').split(';')[0].strip().lower()
    resp = requests.post(
        'https://api.openai.com/v1/audio/transcriptions',
        headers={'Authorization': f'Bearer {api_key}'},
        files={'file': (f'clip.{EXT.get(base, "webm")}', data, base)},
        data={'model': 'whisper-1', 'language': 'en', 'temperature': '0'},
        timeout=timeout,
    )
    resp.raise_for_status()
    return str(resp.json().get('text') or '').strip()
