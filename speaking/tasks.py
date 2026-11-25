"""
Background work of the Speaking game (Celery; runs inline locally where
CELERY_TASK_ALWAYS_EAGER = DEBUG).

    score_attempt(attempt_id)         Azure pronunciation assessment (per word and per sound) -> READY / FAILED;
                                      Whisper (word times) when Azure is off, busy or fails
    warm_word_voices(attempt_id)      the teacher voice for the words the learner will tap
    purge_old_attempt_audio(days=30)  deletes old recordings, keeps the scores

Azure (speaking/azure.py) is used while AZURE_SPEECH_KEY is set:
    429 (busy)                -> up to 3 retries with a growing pause, then Whisper for this attempt
    quota used up / 401 / 403 -> Azure skipped until the 1st of next month (UTC), Whisper meanwhile
    any other failure         -> Whisper for this attempt

Schedule the purge once a day (config/settings.py, CELERY_BEAT_SCHEDULE):
    'purge-speaking-game-audio': {'task': 'speaking.tasks.purge_old_attempt_audio',
                                  'schedule': crontab(hour=4, minute=20)},
"""
import logging
import random
import time
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from ai_chat.usage import record

from . import azure
from .models import SpeakingAttempt
from .scoring import score_reading

log = logging.getLogger(__name__)

WHISPER_URL = 'https://api.openai.com/v1/audio/transcriptions'
EXT = {'audio/webm': 'webm', 'video/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'video/mp4': 'm4a',
       'audio/x-m4a': 'm4a', 'audio/aac': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav'}
MAX_WARM_WORDS = 12
# seconds to wait before each retry when Azure answers 429 (the free tier takes very few readings at once)
AZURE_BACKOFF = (5, 15, 30)


class BadAudio(Exception):
    """Whisper refused the file itself — retrying will not help."""


def whisper_words(data, mime='audio/webm', timeout=120):
    """Recording bytes -> Whisper verbose_json with a start / end time for every word."""
    key = getattr(settings, 'OPENAI_API_KEY', '')
    if not key:
        raise RuntimeError('OpenAI API key is not configured')
    base = (mime or 'audio/webm').split(';')[0].strip().lower()
    resp = requests.post(
        WHISPER_URL,
        headers={'Authorization': f'Bearer {key}'},
        files={'file': (f'reading.{EXT.get(base, "webm")}', data, base)},
        # no `prompt`: showing Whisper the text would make it "hear" words that were never said
        data=[('model', 'whisper-1'), ('language', 'en'), ('temperature', '0'),
              ('response_format', 'verbose_json'), ('timestamp_granularities[]', 'word'),
              ('timestamp_granularities[]', 'segment')],
        timeout=timeout,
    )
    if resp.status_code in (400, 413, 415):
        raise BadAudio(resp.text[:300])
    resp.raise_for_status()
    body = resp.json()
    record('speaking_game_stt', 'whisper-1', seconds=float(body.get('duration') or 0))
    return body


def _fail(attempt_id, error):
    SpeakingAttempt.objects.filter(id=attempt_id, status=SpeakingAttempt.PROCESSING).update(
        status=SpeakingAttempt.FAILED, error=error)


def _pause(seconds):
    time.sleep(seconds)


def azure_scoring(attempt_id, data, text):
    """Azure pronunciation assessment of one recording → azure.score_segments() + 'duration',
    or None when Whisper must score it (Azure off, still busy after the retries, or failing)."""
    if not azure.enabled():
        return None
    try:
        with azure.wav_file(data) as (path, seconds):
            for n in range(len(AZURE_BACKOFF) + 1):
                try:
                    segments = azure.recognize(path, text, seconds)
                    break
                except azure.AzureBusy as exc:
                    if n >= len(AZURE_BACKOFF):
                        raise
                    wait = AZURE_BACKOFF[n] + random.uniform(0, 2)
                    log.info('Speaking attempt %s: Azure busy (%s), retry %s in %.0f s', attempt_id, exc, n + 1, wait)
                    _pause(wait)
    except azure.AzureOff as exc:
        azure.switch_off_until_next_month(exc)
        return None
    except azure.AzureBusy as exc:
        log.warning('Speaking attempt %s: Azure still busy after %s retries (%s) — Whisper scores it',
                    attempt_id, len(AZURE_BACKOFF), exc)
        return None
    except Exception as exc:          # AzureError, or anything unexpected from the SDK
        log.warning('Speaking attempt %s: Azure failed (%s: %s) — Whisper scores it',
                    attempt_id, type(exc).__name__, exc)
        return None
    record('speaking_game_pron', 'azure-pronunciation', seconds=round(seconds, 2))
    try:
        result = azure.score_segments(text, segments)
    except Exception:
        log.exception('Speaking attempt %s: Azure result could not be scored — Whisper scores it', attempt_id)
        return None
    result['duration'] = seconds
    return result


def _save(a, result, engine, duration):
    """READY with the word-by-word result; False when the attempt was no longer PROCESSING."""
    return bool(SpeakingAttempt.objects.filter(id=a.id, status=SpeakingAttempt.PROCESSING).update(
        status=SpeakingAttempt.READY, error='', engine=engine,
        transcript=result['transcript'][:5000], words=result['words'], accuracy=result['accuracy'],
        fluency_wpm=result['fluency_wpm'], ok_count=result['ok'], fix_count=result['fix'] + result.get('bad', 0),
        skip_count=result['skip'], duration_sec=round(duration or a.duration_sec, 1),
        pron_score=result.get('pron_score'), fluency_score=result.get('fluency_score'),
        completeness_score=result.get('completeness_score'), prosody_score=result.get('prosody_score')))


def _whisper_scoring(task, a, data):
    """The Whisper path → (result, duration) or the error code to fail with. Raises Retry."""
    try:
        whisper = whisper_words(data, a.mime)
    except BadAudio as exc:
        log.warning('Speaking attempt %s: Whisper refused the audio: %s', a.id, exc)
        return None, 'bad-audio'
    except Exception as exc:
        if task.request.is_eager or task.request.retries >= task.max_retries:
            log.warning('Speaking attempt %s: Whisper failed', a.id, exc_info=True)
            return None, 'service'
        raise task.retry(exc=exc, countdown=15 * (task.request.retries + 1))
    try:
        result = score_reading(a.lesson.text, whisper)
    except Exception:
        # a bug must not leave the learner waiting on PROCESSING forever
        log.exception('Speaking attempt %s: scoring failed', a.id)
        return None, 'service'
    try:
        result['duration'] = float(whisper.get('duration') or 0)
    except (TypeError, ValueError):
        result['duration'] = 0
    return result, ''


@shared_task(bind=True, max_retries=2, ignore_result=True)
def score_attempt(self, attempt_id):
    a = SpeakingAttempt.objects.select_related('lesson', 'user').filter(id=attempt_id).first()
    if not a or a.status != SpeakingAttempt.PROCESSING:
        return
    try:
        with a.audio.open('rb') as f:
            data = f.read()
    except Exception:
        log.warning('Speaking attempt %s: recording is missing', attempt_id, exc_info=True)
        return _fail(attempt_id, 'service')

    # a Celery retry only follows a Whisper failure, i.e. Azure already failed for this attempt
    result = azure_scoring(attempt_id, data, a.lesson.text) if not self.request.retries else None
    engine = SpeakingAttempt.AZURE
    if result is None:
        engine = SpeakingAttempt.WHISPER
        result, error = _whisper_scoring(self, a, data)
        if error:
            return _fail(attempt_id, error)
    # silence (Azure hears nothing; Whisper writes nothing, or "Thank you." / "you") or a different text
    if not result['heard_any'] or not result['read_the_text']:
        return _fail(attempt_id, 'no-speech')

    if not _save(a, result, engine, result.get('duration')):
        return
    try:
        from gamestats.services import count_play
        count_play(a.user, 'speaking')
    except Exception:
        log.warning('count_play failed for speaking attempt %s', attempt_id, exc_info=True)
    # inline (local) runs would only make the learner wait longer
    if not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        warm_word_voices.delay(attempt_id)


@shared_task(ignore_result=True)
def warm_word_voices(attempt_id):
    """Synthesise the teacher voice for the words to fix, so tapping them plays at once."""
    from games.tts_views import character_audio

    a = SpeakingAttempt.objects.filter(id=attempt_id).only('words').first()
    if not a:
        return
    todo = []
    for w in sorted(a.words or [], key=lambda x: x.get('score', 0)):
        say = (w.get('say') or '').strip()
        if w.get('status') != 'ok' and say and say not in todo:
            todo.append(say)
        if len(todo) >= MAX_WARM_WORDS:
            break
    for say in todo:
        try:
            character_audio('teacher', say)
        except Exception:
            log.info('Teacher voice for %r not warmed', say, exc_info=True)
            break


@shared_task(ignore_result=True)
def purge_old_attempt_audio(days=30):
    """Delete recordings older than `days` days. The scores and word results stay."""
    cutoff = timezone.now() - timedelta(days=int(days or 30))
    ids = list(SpeakingAttempt.objects.filter(created_at__lt=cutoff).exclude(audio='').values_list('id', flat=True))
    deleted = failed = 0
    for start in range(0, len(ids), 500):
        for a in SpeakingAttempt.objects.filter(id__in=ids[start:start + 500]).only('id', 'audio'):
            try:
                a.audio.delete(save=False)
            except Exception:
                failed += 1
                log.warning('Speaking game audio %s was not deleted', a.id, exc_info=True)
                continue
            SpeakingAttempt.objects.filter(id=a.id).update(audio='')
            deleted += 1
    log.info('Speaking game audio purged: %s deleted, %s failed (older than %s days)', deleted, failed, days)
    return {'deleted': deleted, 'failed': failed}
