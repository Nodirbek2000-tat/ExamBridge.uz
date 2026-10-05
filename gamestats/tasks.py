"""
"Ovozlarni tayyorlash" — synthesize every voice line of the games once, ahead
of the players, so nobody waits for a new clip (and the per-user cap of new
clips is never hit by a learner).

The admin page posts the lines; one background job walks them one by one,
skips clips already on disk and keeps its progress in the cache, where
GET /api/games/stats/admin/warm-voices/status/ reads it.
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.utils import timezone

log = logging.getLogger(__name__)

WARM_KEY = 'gamestats:warm:v1'
WARM_TTL = 24 * 3600
WARM_MAX_LINES = 3000
# no progress for this long → the job died (worker restart); a new one may start
WARM_STALE_SECONDS = 5 * 60
# this many failures in a row → OpenAI is down / no key; stop instead of spinning
WARM_MAX_FAILS_IN_ROW = 8
# a plain sayLine(text) (no character) is the examiner voice at this speed
DEFAULT_VOICE = ('nova', 0.95)


def _now():
    return timezone.now().isoformat()


def warm_status():
    """The latest job's progress, or {'state': 'idle'}."""
    return cache.get(WARM_KEY) or {'state': 'idle'}


def _save(st):
    st['updated_at'] = _now()
    cache.set(WARM_KEY, st, WARM_TTL)


def _save_if_mine(st):
    """Save progress unless a newer job took over the status (→ False: stop working)."""
    latest = cache.get(WARM_KEY) or {}
    if latest.get('job') not in (None, st['job']):
        return False
    _save(st)
    return True


def warm_running():
    """True while a job is queued / running and still making progress."""
    st = cache.get(WARM_KEY)
    if not st or st.get('state') not in ('queued', 'running'):
        return False
    try:
        last = datetime.fromisoformat(st.get('updated_at') or st.get('started_at'))
    except (TypeError, ValueError):
        return False
    return (timezone.now() - last).total_seconds() < WARM_STALE_SECONDS


def _clip(text, voice):
    """(is_cached, synthesize) for one line — the same cache the games read."""
    if voice:
        from games.tts_views import _cache_path, character_audio
        return os.path.exists(_cache_path(voice, text)), lambda: character_audio(voice, text)
    from api.ielts_views import _tts_cache_path, tts_audio
    name, speed = DEFAULT_VOICE
    return os.path.exists(_tts_cache_path(text, name, speed)), lambda: tts_audio(text, name, speed)


@shared_task(ignore_result=True)
def warm_voice_lines(job_id, lines):
    """lines: [[text, voice | ''], …] already cleaned by the view. One clip at a time."""
    st = cache.get(WARM_KEY) or {}
    if st.get('job') != job_id:              # a newer job replaced this one
        return
    st.update(state='running', started_at=st.get('started_at') or _now())
    _save(st)
    fails_in_row = 0
    last_save = time.monotonic()
    for text, voice in lines:
        made = False
        try:
            cached, synthesize = _clip(text, voice)
            if cached:
                st['cached'] += 1
            else:
                _, how = synthesize()
                made = how == 'MISS'
                st['made' if made else 'cached'] += 1
            fails_in_row = 0
        except Exception:
            log.warning('warm voices: %s / %r failed', voice or 'default', text[:60], exc_info=not fails_in_row)
            st['failed'] += 1
            fails_in_row += 1
        st['done'] += 1
        if fails_in_row >= WARM_MAX_FAILS_IN_ROW:
            st.update(state='failed', error='Ovoz xizmati javob bermayapti (OpenAI). Keyinroq qayta urinib ko‘ring.',
                      finished_at=_now())
            _save_if_mine(st)
            return
        if made or time.monotonic() - last_save > 1:
            if not _save_if_mine(st):        # a newer job replaced this one
                return
            last_save = time.monotonic()
    st.update(state='done', finished_at=_now())
    _save_if_mine(st)


def _run_here(job_id, lines):
    try:
        warm_voice_lines(job_id, lines)
    except Exception:
        log.exception('warm voices job %s crashed', job_id)
        st = cache.get(WARM_KEY) or {}
        if st.get('job') == job_id:
            st.update(state='failed', error='Ichki xato', finished_at=_now())
            _save(st)
    finally:
        connections.close_all()


def start_warm(lines):
    """Queue a job for `lines` and return its first status."""
    job_id = uuid.uuid4().hex
    st = {
        'job': job_id, 'state': 'queued', 'total': len(lines),
        'done': 0, 'cached': 0, 'made': 0, 'failed': 0, 'error': '',
        'started_at': _now(), 'finished_at': None,
    }
    _save(st)
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        # local dev runs tasks inline — a thread keeps the request from waiting for every clip
        threading.Thread(target=_run_here, args=(job_id, lines), daemon=True, name=f'warm-{job_id[:6]}').start()
    else:
        try:
            warm_voice_lines.delay(job_id, lines)
        except Exception:
            log.exception('warm voices: could not queue the job')
            st.update(state='failed', error='Navbatga qo‘yib bo‘lmadi (Celery).', finished_at=_now())
            _save(st)
    return st
