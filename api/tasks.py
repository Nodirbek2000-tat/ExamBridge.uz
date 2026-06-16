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
