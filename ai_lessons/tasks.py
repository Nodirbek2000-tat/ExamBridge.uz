"""
Lesson jobs run in their own Celery queue (`lessons`), on a worker that does one at
a time. They take minutes, so they must never make writing grading wait.
"""
import logging

from celery import shared_task

from .models import LessonJob
from .pipeline import fail_job, run_job
from .providers import LessonAIError, TemporaryAIError

logger = logging.getLogger(__name__)

RETRY_SECONDS = 60
SOFT_LIMIT_SECONDS = 25 * 60       # a long lesson; what is done by then stays saved
HARD_LIMIT_SECONDS = 27 * 60


@shared_task(bind=True, max_retries=2, ignore_result=True,
             soft_time_limit=SOFT_LIMIT_SECONDS, time_limit=HARD_LIMIT_SECONDS)
def run_lesson_job(self, job_id):
    # Claiming it here means a task delivered twice cannot run twice.
    claimed = LessonJob.objects.filter(id=job_id, status=LessonJob.Status.QUEUED).update(
        status=LessonJob.Status.RUNNING,
    )
    if not claimed:
        return
    job = LessonJob.objects.select_related('lesson').get(id=job_id)

    try:
        run_job(job)
    except TemporaryAIError as error:
        if self.request.retries < self.max_retries:
            LessonJob.objects.filter(id=job_id).update(status=LessonJob.Status.QUEUED)
            raise self.retry(exc=error, countdown=RETRY_SECONDS)
        fail_job(job, str(error))
    except LessonAIError as error:
        fail_job(job, str(error))
    except Exception:
        logger.exception('Lesson job %s failed', job_id)
        fail_job(job, 'The job failed unexpectedly. Details are in the server log.')
