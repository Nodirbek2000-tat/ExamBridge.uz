"""
Make one real lesson from start to finish and see what it cost.

    python manage.py ai_lesson_try "Systems of linear equations" --subject SAT --section math \\
        --level "SAT 1100-1300" --brief "Substitution, elimination, number of solutions" --with-voice

Runs right here (not in the queue), so it prints the review, a sample of the text and
the cost of every stage. The lesson is left in the admin panel for you to watch,
edit or publish: Admin panel → AI Lessons.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone

from ai_chat.models import AIUsage
from ai_chat.usage import FEATURES
from ai_lessons.models import AILesson, LessonJob
from ai_lessons.pipeline import fail_job, run_job
from ai_lessons.providers import LessonAIError, get_provider


class Command(BaseCommand):
    help = 'Generate one AI lesson synchronously and report its quality checks and cost.'

    def add_arguments(self, parser):
        parser.add_argument('title')
        parser.add_argument('--subject', default='SAT', choices=AILesson.Subject.values)
        parser.add_argument('--section', default='general', choices=AILesson.Section.values)
        parser.add_argument('--minutes', type=int, default=10, choices=AILesson.DURATIONS)
        parser.add_argument('--level', default='')
        parser.add_argument('--brief', default='')
        parser.add_argument('--language', default='en', choices=AILesson.Language.values)
        parser.add_argument('--voice', default='coral', choices=AILesson.Voice.values)
        parser.add_argument('--with-voice', action='store_true', help='also record the voice (the expensive part)')
        parser.add_argument('--rate', type=int, default=12800, help="so'm per dollar, for the summary")

    def _run(self, lesson, kind, provider):
        job = LessonJob.objects.create(lesson=lesson, kind=kind, provider=provider.name, status=LessonJob.Status.RUNNING)
        lesson.status = AILesson.Status.GENERATING if kind == LessonJob.Kind.GENERATE else AILesson.Status.VOICING
        lesson.save(update_fields=['status', 'updated_at'])
        try:
            run_job(job)
        except LessonAIError as error:
            fail_job(job, str(error))
            raise CommandError(f'{kind} failed: {error}')
        job.refresh_from_db()
        return job

    def handle(self, *args, **o):
        try:
            provider = get_provider()
        except LessonAIError as error:
            raise CommandError(str(error))
        started = timezone.now()
        lesson = AILesson.objects.create(
            title=o['title'], subject=o['subject'], section=o['section'], duration_minutes=o['minutes'],
            level=o['level'], brief=o['brief'], language=o['language'], voice=o['voice'],
        )
        self.stdout.write(f'Dars #{lesson.id} "{lesson.title}" tayyorlanmoqda ({provider.name}, {settings.AI_LESSON_MODEL})...')
        self.stdout.write('Reja → bo\'limlar → savollar → tekshiruv. Odatda 2–5 daqiqa.\n')

        job = self._run(lesson, LessonJob.Kind.GENERATE, provider)
        steps = list(lesson.steps.all())
        questions = lesson.questions.all()
        words = sum(len(s.say.split()) for s in steps)
        review = job.data.get('review') or {}

        self.stdout.write(self.style.SUCCESS(f'✓ Matn tayyor: {len(steps)} qadam, ~{words} so\'z, {questions.count()} savol'))
        verdict = review.get('verdict', '?')
        style = self.style.SUCCESS if verdict == 'GOOD' else self.style.WARNING
        self.stdout.write(style(f'\nAI tekshiruvi: {verdict}'))
        if review.get('summary'):
            self.stdout.write(f"  {review['summary']}")
        for issue in review.get('issues') or []:
            self.stdout.write(f'  • {issue}')

        self.stdout.write('\nNamuna (birinchi 2 qadam, ustoz shunday gapiradi):')
        for step in steps[:2]:
            self.stdout.write(f'  — {step.say}\n')

        if o['with_voice']:
            self.stdout.write(f"Ovoz yozilmoqda ({settings.AI_LESSON_TTS_ENGINE}, {o['voice']})...")
            self._run(lesson, LessonJob.Kind.VOICE, provider)
            seconds = sum(s.audio_seconds or 0 for s in lesson.steps.all())
            self.stdout.write(self.style.SUCCESS(f'✓ Ovoz tayyor: {seconds / 60:.1f} daqiqa'))
        else:
            self.stdout.write("Ovoz yozilmadi: --with-voice qo'shing yoki admin panelda tasdiqlang.")

        rows = (AIUsage.objects.filter(feature__startswith='lesson_', created_at__gte=started)
                .values('feature').annotate(calls=Count('id'), usd=Sum('cost_usd')).order_by('feature'))
        total = sum(float(r['usd'] or 0) for r in rows)
        self.stdout.write('\nXarajat:')
        for r in rows:
            name = FEATURES.get(r['feature'], ('', r['feature']))[1]
            self.stdout.write(f"  {name:<20} {r['calls']:>3} ta   ${float(r['usd'] or 0):.4f}")
        self.stdout.write(self.style.SUCCESS(f"  JAMI                       ${total:.4f}  ≈ {round(total * o['rate']):,} so'm".replace(',', ' ')))

        url = f"{settings.FRONTEND_URL.rstrip('/')}/admin-panel/ai-lessons/{lesson.id}/preview"
        self.stdout.write(f'\nKo\'rish: {url}')
        self.stdout.write('Admin panel → AI Lessons dan tahrirlab, nashr qilishingiz mumkin.')
