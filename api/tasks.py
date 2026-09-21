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
    try:
        from ielts.models import WritingResponse
        from api.ielts_views import run_writing_ai

        response = WritingResponse.objects.get(id=response_id)
        task = response.task

        result = run_writing_ai(
            text=response.response_text,
            task_type=task.task_type if task else 2,
            prompt_txt=task.prompt if task else '',
            word_count=response.word_count,
        )

        criteria_keys = ('task_achievement', 'coherence_cohesion', 'lexical_resource', 'grammatical_range')
        criteria = {k: result.get(k) or {} for k in criteria_keys}

        # ai_feedback — "ready" flag sifatida ishlatiladi; mezon fikrlarini birlashtirib saqlaymiz
        feedback_parts = []
        for k in criteria_keys:
            c = criteria[k]
            if isinstance(c, dict) and c.get('feedback'):
                feedback_parts.append(f"{c.get('label', k)}: {c['feedback']}")
        response.ai_feedback = ' '.join(feedback_parts) or 'Evaluated.'
        response.ai_band = result.get('overall_band') or 0
        response.ai_criteria = criteria
        response.save(update_fields=['ai_feedback', 'ai_band', 'ai_criteria'])
        logger.info(f"Writing response {response_id} evaluated by AI: band={response.ai_band}")

    except Exception as exc:
        logger.error(f"Writing evaluation failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


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
