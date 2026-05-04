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
    """AI evaluation of IELTS writing response."""
    try:
        from ielts.models import WritingResponse
        response = WritingResponse.objects.get(id=response_id)

        # TODO: Integrate Claude API for detailed IELTS writing evaluation
        word_count = response.word_count

        # Placeholder scoring
        response.ai_feedback = (
            f"Task Achievement: Your response addresses the task adequately ({word_count} words). "
            "Coherence & Cohesion: Good paragraph structure. "
            "Lexical Resource: Varied vocabulary used. "
            "Grammatical Range & Accuracy: Minor errors present."
        )
        response.ai_band = 6.0
        response.ai_criteria = {
            'task_achievement': 6.0,
            'coherence_cohesion': 6.5,
            'lexical_resource': 6.0,
            'grammatical_range': 5.5,
        }
        response.save(update_fields=['ai_feedback', 'ai_band', 'ai_criteria'])
        logger.info(f"Writing response {response_id} evaluated.")

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
