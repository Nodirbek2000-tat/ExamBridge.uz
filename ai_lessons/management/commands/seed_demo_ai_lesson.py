"""Load the hand-written demo lesson. Safe to run again: it only replaces itself."""
from django.core.management.base import BaseCommand

from ai_lessons import demo_lesson
from ai_lessons.board import replace_steps
from ai_lessons.models import AILesson, LessonQuestion
from ai_lessons.questions import clean_question


class Command(BaseCommand):
    help = 'Create or refresh the demo AI lesson (no AI tokens used).'

    def handle(self, *args, **options):
        lesson, created = AILesson.objects.update_or_create(
            title=demo_lesson.TITLE, defaults=demo_lesson.LESSON,
        )
        lesson.questions.all().delete()
        steps = replace_steps(lesson, demo_lesson.STEPS)

        for order, raw in enumerate(demo_lesson.QUESTIONS):
            after = raw.get('after_step')
            data = {**raw, 'step': steps[after].id if after is not None else None}
            LessonQuestion.objects.create(lesson=lesson, order=order, **clean_question(data, lesson))

        verb = 'Created' if created else 'Refreshed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} "{lesson.title}" (id {lesson.id}, {len(steps)} steps, '
            f'{len(demo_lesson.QUESTIONS)} questions)',
        ))
