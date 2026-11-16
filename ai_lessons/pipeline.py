"""
Builds a lesson in stages, saving after each one.

    GENERATE: plan → one section at a time → questions (checks, final test, homework)
              → the AI's own review, which also fixes the questions it finds wrong
              → saved as steps and questions for an admin to read (status REVIEW)
    VOICE:    only after an admin approves, because the voice is the expensive part:
              the steps, what the teacher says to a wrong answer, and — once per
              voice — the short praise for a right one

Every finished stage is kept in LessonJob.data, so a job that stops (no credit, a
crash, a restart) continues from where it was and pays for nothing twice. Nothing
reaches the lesson itself until the whole GENERATE job has succeeded.
"""
import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import praise, prompts
from .board import BoardError, clean_step, repair_step, replace_steps
from .models import AILesson, LessonJob, LessonQuestion
from .providers import BadReply, LessonAIError, get_provider
from .questions import QuestionError, clean_content
from .recommend import clean_topics, known_topics
from .source_text import lesson_context, teaching_structures

logger = logging.getLogger(__name__)

MAX_TOKENS = {'plan': 1500, 'section': 5000, 'questions': 8000, 'review': 6000}
ATTEMPTS = 3                       # more tries, each told what was wrong with the reply before
MAX_STEPS = 60
MAX_SECTION_MINUTES = 30
MIN_SECTIONS = 3
MINUTES_PER_SECTION = 2
STALE_MINUTES = 30                 # longer than any single stage takes
VERDICTS = {LessonQuestion.Verdict.GOOD, LessonQuestion.Verdict.BAD}


class JobBusy(Exception):
    """This lesson already has a job running."""


def section_count(lesson):
    return max(MIN_SECTIONS, round(lesson.duration_minutes / MINUTES_PER_SECTION))


# ── Asking ────────────────────────────────────────────────────────────────────

def _ask(provider, task, lesson, user, context, check):
    """A checked reply. A reply that fails `check` is sent back once with the reason."""
    note = ''
    for attempt in range(ATTEMPTS):
        try:
            reply = provider.complete(task, prompts.role(lesson), user + note, context, MAX_TOKENS[task])
            return check(reply)
        except (BadReply, BoardError, QuestionError, KeyError, TypeError, ValueError, AttributeError) as error:
            logger.warning('Lesson %s: %s reply refused (try %s): %s', lesson.id, task, attempt + 1, error)
            note = f'\n\nYour previous reply was refused: {error}. Fix that and reply again.'
    raise LessonAIError(f'The AI could not produce a valid {task}. Try again.')


def _check_plan(reply, count):
    sections = reply['sections']
    if not isinstance(sections, list) or len(sections) != count:
        raise BadReply(f'Return exactly {count} sections.')
    for item in sections:
        if not isinstance(item, dict) or not str(item.get('title') or '').strip():
            raise BadReply('Every section needs a title.')
        minutes = item.get('minutes', 1)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 0 < minutes <= MAX_SECTION_MINUTES:
            raise BadReply(f'minutes must be a number from 1 to {MAX_SECTION_MINUTES}.')
    return sections


def _check_section(reply):
    steps = reply['steps']
    if not isinstance(steps, list) or not steps:
        raise BadReply('Return at least one step.')
    return [clean_step(repair_step(step)) for step in steps]


def _place(raw, steps_count):
    """Where a question the AI wrote goes: {after_step, is_homework, is_quiz}."""
    if raw.get('is_homework'):
        return {'after_step': None, 'is_homework': True, 'is_quiz': False}
    if raw.get('is_quiz'):
        return {'after_step': None, 'is_homework': False, 'is_quiz': True}
    after = raw.get('after_step')
    if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after < steps_count:
        raise BadReply('after_step must point at a step, or set is_quiz or is_homework.')
    return {'after_step': after, 'is_homework': False, 'is_quiz': False}


def _check_questions(reply, steps_count):
    raw_questions = reply['questions']
    if not isinstance(raw_questions, list) or not raw_questions:
        raise BadReply('Return at least one question.')
    checked, refused = [], []
    for raw in raw_questions:
        try:
            checked.append({**clean_content(raw), **_place(raw, steps_count)})
        except (BadReply, QuestionError, AttributeError, TypeError) as error:
            refused.append(str(error))
    # A few broken questions are left out; a reply that is mostly broken is asked again.
    if not checked or len(refused) > len(checked):
        raise BadReply('; '.join(refused) or 'Return at least one question.')
    if refused:
        logger.warning('%s questions left out: %s', len(refused), '; '.join(refused))
    return checked


def _fixed_question(item):
    """The reviewer's corrected version of a question it found wrong, if it is a valid question."""
    if item.get('verdict') != LessonQuestion.Verdict.BAD or not isinstance(item.get('fix'), dict):
        return None
    try:
        return clean_content(item['fix'])
    except QuestionError:
        return None


def _check_review(reply, questions_count):
    verdict = reply['verdict']
    if verdict not in VERDICTS:
        raise BadReply('verdict must be GOOD or BAD.')
    notes = {}
    for item in reply.get('questions') or []:
        if not isinstance(item, dict):
            continue
        index = item.get('index')
        if isinstance(index, int) and 0 <= index < questions_count and item.get('verdict') in VERDICTS:
            notes[str(index)] = {'verdict': item['verdict'], 'note': str(item.get('note') or '')[:1000]}
            fix = _fixed_question(item)
            if fix:
                notes[str(index)]['fix'] = fix
    return {
        'verdict': verdict,
        'summary': str(reply.get('summary') or '')[:1000],
        'issues': [str(i)[:500] for i in (reply.get('issues') or [])][:20],
        'questions': notes,
    }


# ── Job bookkeeping ───────────────────────────────────────────────────────────

def _progress(job, **fields):
    for key, value in fields.items():
        setattr(job, key, value)
    job.save(update_fields=[*fields, 'updated_at'])


def latest_job(lesson):
    """The job that last did anything: the one the panel shows and the only one to continue."""
    return lesson.jobs.order_by('-updated_at', '-id').first()


def expire_stale_jobs(lesson):
    """A job whose worker died would block the lesson for ever."""
    cutoff = timezone.now() - timedelta(minutes=STALE_MINUTES)
    stale = lesson.jobs.filter(status__in=LessonJob.ACTIVE, updated_at__lt=cutoff)
    for job in stale:
        fail_job(job, 'The job stopped responding. Continue it to pick up where it was.')


def fail_job(job, message):
    LessonJob.objects.filter(pk=job.pk).update(
        status=LessonJob.Status.FAILED, error=message[:500], finished_at=timezone.now(), updated_at=timezone.now(),
    )
    AILesson.objects.filter(pk=job.lesson_id).update(status=AILesson.Status.FAILED, updated_at=timezone.now())


def start_job(lesson, kind, restart=False):
    """
    Queue a job. A failed job of the same kind continues from its last saved stage
    unless `restart` is set or the provider changed. Raises JobBusy.
    """
    from .tasks import run_lesson_job

    expire_stale_jobs(lesson)
    provider = get_provider()
    previous = latest_job(lesson)
    resume = (
        previous is not None and not restart and previous.kind == kind
        and previous.status == LessonJob.Status.FAILED and previous.provider == provider.name
    )
    job = previous if resume else LessonJob(lesson=lesson, kind=kind, provider=provider.name)
    lesson_status = AILesson.Status.GENERATING if kind == LessonJob.Kind.GENERATE else AILesson.Status.VOICING

    try:
        with transaction.atomic():
            job.status = LessonJob.Status.QUEUED
            job.error = ''
            job.finished_at = None
            job.save()
            lesson.status = lesson_status
            lesson.save(update_fields=['status', 'updated_at'])
    except IntegrityError as error:
        raise JobBusy from error

    try:
        run_lesson_job.delay(job.id)
    except Exception:
        logger.exception('Could not queue lesson job %s', job.id)
        fail_job(job, 'The job could not be queued. Check that Redis and the lessons worker are running.')
    return job


# ── Generate ──────────────────────────────────────────────────────────────────

def _generate(job, provider):
    lesson = job.lesson
    data = job.data or {}
    material, skipped = lesson_context(lesson)
    structures = teaching_structures(lesson)
    data['skipped'] = skipped

    count = section_count(lesson)
    if 'plan' not in data:
        _progress(job, stage=LessonJob.Stage.PLAN, done=0, total=count)
        planned = _ask(
            provider, 'plan', lesson, prompts.plan(lesson, material, structures, count, known_topics(lesson)),
            {'title': lesson.title, 'sections': count},
            lambda reply: {'sections': _check_plan(reply, count), 'topics': clean_topics(reply.get('topics'))},
        )
        data['plan'], data['topics'] = planned['sections'], planned['topics']
        _progress(job, data=data)

    outline = data['plan']
    written = data.setdefault('sections', [])
    _progress(job, stage=LessonJob.Stage.SCRIPT, done=len(written), total=len(outline))
    for index in range(len(written), len(outline)):
        steps = _ask(
            provider, 'section', lesson, prompts.section(lesson, outline, index, material, structures),
            {'section': outline[index]}, _check_section,
        )
        written.append(steps)
        _progress(job, data=data, done=len(written))

    steps = [step for section in written for step in section]
    if len(steps) > MAX_STEPS:
        raise LessonAIError(f'The lesson came out too long ({len(steps)} steps). Shorten the brief and start over.')

    kept = list(lesson.questions.filter(origin=LessonQuestion.Origin.ADMIN))
    existing = [{'prompt': q.prompt, 'kind': q.kind} for q in kept]

    if 'questions' not in data:
        _progress(job, stage=LessonJob.Stage.QUESTIONS, done=0, total=1)
        data['questions'] = _ask(
            provider, 'questions', lesson, prompts.questions(lesson, written, existing),
            {'steps': len(steps)}, lambda reply: _check_questions(reply, len(steps)),
        )
        _progress(job, data=data, done=1)

    # The teacher's own questions and homework are checked along with the AI's.
    reviewed = [*data['questions'], *[
        {'kind': q.kind, 'prompt': q.prompt, 'options': q.options, 'correct': q.correct,
         'accepted': q.accepted, 'rubric': q.rubric, 'explanation': q.explanation}
        for q in kept
    ]]
    if 'review' not in data:
        _progress(job, stage=LessonJob.Stage.REVIEW, done=0, total=1)
        data['review'] = _ask(
            provider, 'review', lesson, prompts.review(lesson, steps, reviewed),
            {'questions': len(reviewed)}, lambda reply: _check_review(reply, len(reviewed)),
        )
        _progress(job, data=data, done=1)

    _save_lesson(job, provider, steps, data, kept)


def _save_lesson(job, provider, steps, data, kept):
    """All or nothing: the old lesson stays whole until the new one is ready."""
    lesson = job.lesson
    notes = data['review']['questions']
    ai_questions = data['questions']

    with transaction.atomic():
        # The teacher's questions stay at the same position in the new lesson.
        positions = {q.pk: q.step.order for q in kept if q.step_id}
        lesson.questions.filter(origin=LessonQuestion.Origin.AI).delete()
        new_steps = replace_steps(lesson, steps)
        last = len(new_steps) - 1

        for order, raw in enumerate(ai_questions):
            note = notes.get(str(order), {})
            fields = {k: v for k, v in raw.items() if k != 'after_step'}
            verdict = note.get('verdict', LessonQuestion.Verdict.PENDING)
            ai_note = note.get('note', '')
            if note.get('fix'):
                # the reviewer's corrected question takes its place; the admin still sees why
                fields.update(note['fix'])
                verdict = LessonQuestion.Verdict.GOOD
                ai_note = f'Fixed by the AI check: {ai_note}'.strip()
            after = raw['after_step']
            LessonQuestion.objects.create(
                lesson=lesson, order=order, origin=LessonQuestion.Origin.AI,
                step=new_steps[after] if after is not None else None,
                ai_verdict=verdict,
                ai_note=ai_note[:2000],
                **fields,
            )

        for offset, question in enumerate(kept):
            note = notes.get(str(len(ai_questions) + offset), {})
            question.ai_verdict = note.get('verdict', LessonQuestion.Verdict.PENDING)
            question.ai_note = note.get('note', '')
            if question.pk in positions:
                question.step = new_steps[min(positions[question.pk], last)]
            LessonQuestion.objects.filter(pk=question.pk).update(
                ai_verdict=question.ai_verdict, ai_note=question.ai_note, step=question.step,
            )

        lesson.status = AILesson.Status.REVIEW
        lesson.ai_provider = provider.name
        fields = ['status', 'ai_provider', 'updated_at']
        if data.get('topics') and not lesson.topics:      # an admin's own topics are kept
            lesson.topics = data['topics']
            fields.append('topics')
        lesson.save(update_fields=fields)
        _finish(job)


# ── Voice ─────────────────────────────────────────────────────────────────────

def _voice(job, provider):
    lesson = job.lesson
    steps = list(lesson.steps.all())
    waiting = [step for step in steps if not step.audio]
    # homework is done on the page, after the lesson: only the lesson's own questions are spoken to
    spoken = [q for q in lesson.questions.filter(is_homework=False).exclude(wrong_say='')]
    unsaid = [q for q in spoken if not q.wrong_audio]
    lines = praise.missing(lesson.language, lesson.voice)
    total = len(steps) + len(spoken) + len(lines)
    _progress(job, stage=LessonJob.Stage.VOICE, done=total - len(waiting) - len(unsaid) - len(lines), total=total)

    for step in waiting:
        audio, seconds = provider.speak(step.say, lesson.voice, lesson.language)
        step.audio.save('voice.mp3', ContentFile(audio), save=False)
        step.audio_seconds = seconds
        step.save(update_fields=['audio', 'audio_seconds'])
        _progress(job, done=job.done + 1)

    for question in unsaid:
        audio, seconds = provider.speak(question.wrong_say, lesson.voice, lesson.language)
        question.wrong_audio.save('voice.mp3', ContentFile(audio), save=False)
        question.wrong_audio_seconds = seconds
        question.save(update_fields=['wrong_audio', 'wrong_audio_seconds'])
        _progress(job, done=job.done + 1)

    for index, text in lines:
        audio, _ = provider.speak(text, lesson.voice, lesson.language)
        praise.save(lesson.language, lesson.voice, index, audio)
        _progress(job, done=job.done + 1)

    with transaction.atomic():
        lesson.status = AILesson.Status.APPROVED
        lesson.save(update_fields=['status', 'updated_at'])
        _finish(job)


def _finish(job):
    job.status = LessonJob.Status.DONE
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'finished_at', 'updated_at'])


def run_job(job):
    """Do the job's work. Failures are raised for the task to handle."""
    provider = get_provider()
    if job.kind == LessonJob.Kind.GENERATE:
        _generate(job, provider)
    else:
        _voice(job, provider)
