"""
A student's written answer is checked by the AI teacher right after it is sent.

Checking is best effort: if the AI is slow, out of credit or not set up, the
answer stays saved without a verdict and the student still sees the model
explanation. Each student answers a question once, so the cost is bounded.
"""
import logging

from django.conf import settings

from . import prompts
from .models import LessonAnswer
from .providers import LessonAIError, get_provider

logger = logging.getLogger(__name__)

MAX_TOKENS = 500
VERDICTS = {'CORRECT': 'correct', 'PARTLY': 'partly', 'WRONG': 'wrong'}


def mark_written(answer, save=True):
    """
    Fill in verdict, feedback and is_correct, and store them unless `save` is False
    (an admin trying the lesson). Returns True when the answer was marked.
    """
    question = answer.question
    try:
        provider = get_provider()
        reply = provider.complete(
            'mark', prompts.mark_role(question.lesson), prompts.mark(question, answer.text),
            {'text': answer.text}, MAX_TOKENS, model=settings.AI_LESSON_MARK_MODEL,
        )
    except LessonAIError as error:
        logger.warning('Could not mark lesson answer %s: %s', answer.pk, error)
        return False
    except Exception:
        logger.exception('Marking lesson answer %s failed', answer.pk)
        return False

    verdict = VERDICTS.get(str(reply.get('verdict', '')).upper())
    feedback = str(reply.get('feedback') or '').strip()[:1500]
    if verdict is None:
        logger.warning('Lesson answer %s got an unusable verdict: %r', answer.pk, reply.get('verdict'))
        return False

    answer.verdict = verdict
    answer.feedback = feedback
    answer.is_correct = verdict == 'correct'
    if not save:
        return True
    LessonAnswer.objects.filter(pk=answer.pk).update(
        verdict=answer.verdict, feedback=answer.feedback, is_correct=answer.is_correct,
    )
    return True
