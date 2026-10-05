"""
Background work of the Speaking game (Celery; runs inline locally where
CELERY_TASK_ALWAYS_EAGER = DEBUG).

    score_attempt(attempt_id)         Whisper (word times) -> word-by-word scores -> READY / FAILED
    warm_word_voices(attempt_id)      the teacher voice for the words the learner will tap
    purge_old_attempt_audio(days=30)  deletes old recordings, keeps the scores

Schedule the purge once a day (config/settings.py, CELERY_BEAT_SCHEDULE):
    'purge-speaking-game-audio': {'task': 'speaking.tasks.purge_old_attempt_audio',
                                  'schedule': crontab(hour=4, minute=20)},
"""
import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import SpeakingAttempt
from .scoring import score_reading

log = logging.getLogger(__name__)

WHISPER_URL = 'https://api.openai.com/v1/audio/transcriptions'
EXT = {'audio/webm': 'webm', 'video/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'video/mp4': 'm4a',
       'audio/x-m4a': 'm4a', 'audio/aac': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav'}
MAX_WARM_WORDS = 12


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
    return resp.json()


def _fail(attempt_id, error):
    SpeakingAttempt.objects.filter(id=attempt_id, status=SpeakingAttempt.PROCESSING).update(
        status=SpeakingAttempt.FAILED, error=error)


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

    try:
        whisper = whisper_words(data, a.mime)
    except BadAudio as exc:
        log.warning('Speaking attempt %s: Whisper refused the audio: %s', attempt_id, exc)
        return _fail(attempt_id, 'bad-audio')
    except Exception as exc:
        if self.request.is_eager or self.request.retries >= self.max_retries:
            log.warning('Speaking attempt %s: Whisper failed', attempt_id, exc_info=True)
            return _fail(attempt_id, 'service')
        raise self.retry(exc=exc, countdown=15 * (self.request.retries + 1))

    try:
        result = score_reading(a.lesson.text, whisper)
    except Exception:
        # a bug must not leave the learner waiting on PROCESSING forever
        log.exception('Speaking attempt %s: scoring failed', attempt_id)
        return _fail(attempt_id, 'service')
    # silence (Whisper then writes nothing, or "Thank you." / "you") or a different text
    if not result['heard_any'] or not result['read_the_text']:
        return _fail(attempt_id, 'no-speech')

    try:
        duration = float(whisper.get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0
    updated = SpeakingAttempt.objects.filter(id=attempt_id, status=SpeakingAttempt.PROCESSING).update(
        status=SpeakingAttempt.READY, error='',
        transcript=result['transcript'][:5000], words=result['words'], accuracy=result['accuracy'],
        fluency_wpm=result['fluency_wpm'], ok_count=result['ok'], fix_count=result['fix'],
        skip_count=result['skip'], duration_sec=round(duration or a.duration_sec, 1))
    if not updated:
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
