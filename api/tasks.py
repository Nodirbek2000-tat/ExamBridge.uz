"""
Celery tasks for AI analysis (speaking, writing evaluation)
"""
from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def analyze_speaking(self, response_id):
    """AI evaluation of IELTS speaking audio recording."""
    try:
        from ielts.models import SpeakingResponse
        response = SpeakingResponse.objects.get(id=response_id)

        # TODO: Integrate Whisper for transcription + Claude/GPT for band scoring
        # For now, placeholder
        response.transcript = "[Transcript pending AI processing]"
        response.ai_feedback = (
            "Fluency & Coherence: Your speech was generally fluent. "
            "Lexical Resource: Good range of vocabulary. "
            "Grammatical Range: Some errors noted. "
            "Pronunciation: Clear and understandable."
        )
        response.ai_band = 6.5
        response.save(update_fields=['transcript', 'ai_feedback', 'ai_band'])
        logger.info(f"Speaking response {response_id} analyzed.")

    except Exception as exc:
        logger.error(f"Speaking analysis failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def evaluate_writing(self, response_id):
    """AI evaluation of IELTS writing response — haqiqiy OpenAI tahlil.

    ai_criteria shakli (frontend IELTSWritingResult shuni kutadi):
      {task_achievement: {band, label, feedback, strengths, errors}, ...}
    """
    from ielts.models import WritingResponse
    from api.ielts_views import evaluate_writing_response

    try:
        response = WritingResponse.objects.select_related('task').get(id=response_id)
    except WritingResponse.DoesNotExist:
        # Deleted before we got to it — retrying cannot help
        logger.warning('Writing response %s no longer exists', response_id)
        return

    try:
        if evaluate_writing_response(response):
            logger.info('Writing response %s evaluated by AI: band=%s', response_id, response.ai_band)
    except Exception as exc:
        logger.error('Writing evaluation failed for %s: %s', response_id, exc)
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def evaluate_cefr_writing(self, response_id):
    """AI scoring of a CEFR multilevel writing response (0–75)."""
    from cefr.models import CEFRWritingResponse
    from api.cefr_writing import evaluate_response

    try:
        response = CEFRWritingResponse.objects.select_related('test').get(id=response_id)
    except CEFRWritingResponse.DoesNotExist:
        logger.warning('CEFR writing response %s no longer exists', response_id)
        return

    try:
        if evaluate_response(response):
            logger.info('CEFR writing response %s scored: %s/75', response_id, response.score)
    except Exception as exc:
        logger.error('CEFR writing scoring failed for %s: %s', response_id, exc)
        if self.request.retries >= self.max_retries:
            # give the student a clear "try again" instead of an endless spinner
            CEFRWritingResponse.objects.filter(id=response_id, status='SCORING').update(status='FAILED')
            return
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def evaluate_cefr_speaking(self, response_id):
    """AI scoring of a CEFR multilevel speaking response (0–75)."""
    from cefr.models import CEFRSpeakingResponse
    from api.cefr_speaking import evaluate_response

    try:
        response = CEFRSpeakingResponse.objects.select_related('test').get(id=response_id)
    except CEFRSpeakingResponse.DoesNotExist:
        logger.warning('CEFR speaking response %s no longer exists', response_id)
        return

    try:
        if evaluate_response(response):
            logger.info('CEFR speaking response %s scored: %s/75', response_id, response.score)
    except Exception as exc:
        logger.error('CEFR speaking scoring failed for %s: %s', response_id, exc)
        if self.request.retries >= self.max_retries:
            CEFRSpeakingResponse.objects.filter(id=response_id, status='SCORING').update(status='FAILED')
            return
        raise self.retry(exc=exc, countdown=30)


@shared_task
def warm_cefr_speaking_tts(test_id):
    """Synthesize every examiner line of a new speaking test once, so students never wait for it."""
    from cefr.models import CEFRSpeakingTest
    from api.cefr_speaking import warm_tts

    test = CEFRSpeakingTest.objects.filter(id=test_id).first()
    if test:
        logger.info('CEFR speaking test %s: %s examiner lines synthesized', test_id, warm_tts(test))


@shared_task
def purge_old_speaking_audio(days=None):
    """Eski speaking ovoz yozuvlarini o'chiradi.

    NIMA O'CHADI: faqat ovoz faylining o'zi (diskdagi .webm).
    NIMA QOLADI:  transkript, AI izohi, ball, mezonlar — ya'ni o'quvchi
                  o'z natijasini va tahlilini istalgan vaqt ko'raveradi,
                  faqat o'z ovozini qayta eshita olmaydi.

    Nega kerak: ovoz yozuvlari hech qachon o'chmasdi va disk to'lib borardi.
    Bitta to'liq speaking urinishi ~3-4 MB; 4000 kunlik faol o'quvchida bu
    kuniga bir necha GB degani.

    Muddat .env dagi SPEAKING_AUDIO_RETENTION_DAYS bilan boshqariladi
    (standart: 120 kun).
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone
    from ielts.models import SpeakingResponse

    days = int(days or getattr(settings, 'SPEAKING_AUDIO_RETENTION_DAYS', 120))
    cutoff = timezone.now() - timedelta(days=days)

    # Avval ID larni olamiz: yozuvlarni o'zgartirib turib, ayni paytda
    # o'sha jadvalni varaqlash ishonchsiz
    ids = list(
        SpeakingResponse.objects
        .filter(created_at__lt=cutoff)
        .exclude(audio_file='')
        .exclude(audio_file__isnull=True)
        .values_list('id', flat=True)
    )

    deleted, freed_bytes, failed = 0, 0, 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in SpeakingResponse.objects.filter(id__in=chunk):
            if not r.audio_file:
                continue
            try:
                size = r.audio_file.size
            except Exception:
                size = 0            # fayl allaqachon yo'q — hajmi noma'lum
            try:
                # save=False: maydonni o'zimiz tozalab, bir marta saqlaymiz
                r.audio_file.delete(save=False)
                r.audio_file = None
                r.save(update_fields=['audio_file'])
            except Exception as exc:
                failed += 1
                logger.warning('Speaking audio %s o\'chmadi: %s', r.id, exc)
                continue
            deleted += 1
            freed_bytes += size

    # CEFR multilevel speaking: one recording per answer, paths inside `answers`
    from django.core.files.storage import default_storage
    from cefr.models import CEFRSpeakingResponse
    for r in CEFRSpeakingResponse.objects.filter(submitted_at__lt=cutoff).only('id', 'answers').iterator():
        changed = False
        answers = list(r.answers or [])
        for a in answers:
            path = a.get('audio') if isinstance(a, dict) else None
            if not path:
                continue
            try:
                size = default_storage.size(path) if default_storage.exists(path) else 0
                default_storage.delete(path)
            except Exception as exc:
                failed += 1
                logger.warning('CEFR speaking audio %s o\'chmadi: %s', path, exc)
                continue
            a['audio'] = ''
            changed = True
            deleted += 1
            freed_bytes += size
        if changed:
            CEFRSpeakingResponse.objects.filter(id=r.id).update(answers=answers)

    logger.info(
        'Speaking audio tozalandi: %s ta o\'chdi, %.1f MB bo\'shadi, '
        '%s ta xato (muddat: %s kun)',
        deleted, freed_bytes / 1024 / 1024, failed, days,
    )
    return {'deleted': deleted, 'freed_mb': round(freed_bytes / 1024 / 1024, 1),
            'failed': failed, 'days': days}


@shared_task
def update_user_stats(user_id):
    """Update user's overall stats after completing a test."""
    try:
        from accounts.models import UserStats
        from tests_app.models import TestResult
        from django.db.models import Avg, Max

        stats, _ = UserStats.objects.get_or_create(user_id=user_id)
        results = TestResult.objects.filter(user_id=user_id)

        if results.exists():
            agg = results.aggregate(avg=Avg('total_score'), best=Max('total_score'))
            stats.total_tests_taken = results.count()
            stats.best_total_score = agg['best'] or 0
            stats.avg_total_score = agg['avg'] or 0.0
            stats.save()

    except Exception as e:
        logger.error(f"Stats update failed: {e}")
