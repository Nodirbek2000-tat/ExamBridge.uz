"""How often a lesson is opened, and the numbers an admin sees inside it."""
from django.core.cache import cache

from .models import AILesson, LessonAnswer, LessonProgress, LessonQuestion
from .tests import BASE, STUDENT, StudentBase, make_user


class LessonViewsTest(StudentBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def open(self, headers):
        self.assertEqual(self.client.get(f'{STUDENT}{self.target.id}/', **headers).status_code, 200)

    def views(self):
        return AILesson.objects.get(pk=self.target.pk).views

    def test_an_open_counts_once_per_student_per_half_hour(self):
        self.open(self.s)
        self.open(self.s)                   # reloading, or the page refreshing after the lesson ends
        self.assertEqual(self.views(), 1)
        from .tests import auth
        self.open(auth(make_user('other@test.uz')))
        self.assertEqual(self.views(), 2)

    def test_admins_opening_it_do_not_count(self):
        self.open(self.h)
        self.assertEqual(self.views(), 0)

    def test_the_admin_sees_how_students_used_the_lesson(self):
        AILesson.objects.filter(pk=self.target.pk).update(views=7)
        quiz = LessonQuestion.objects.create(lesson=self.target, is_quiz=True, kind='CHOICE', prompt='?',
                                             options=['a', 'b'], correct=1)
        other = make_user('other@test.uz')
        LessonProgress.objects.create(user=self.student, lesson=self.target, completed_at='2026-01-01T00:00Z')
        LessonProgress.objects.create(user=other, lesson=self.target)
        LessonAnswer.objects.create(user=self.student, question=self.inside, choice=1, is_correct=True, hint_used=True)
        LessonAnswer.objects.create(user=self.student, question=quiz, choice=1, is_correct=True)
        LessonAnswer.objects.create(user=other, question=quiz, choice=0, is_correct=False)

        stats = self.client.get(f'{BASE}{self.target.id}/', **self.h).json()['stats']
        self.assertEqual(stats, {
            'views': 7, 'students': 2, 'finished': 1, 'finished_pct': 50,
            'answers': 3, 'right_pct': 67, 'quiz_answers': 2, 'quiz_right_pct': 50, 'hints_used': 1,
        })
        listed = self.client.get(BASE, **self.h).json()
        self.assertEqual(next(l for l in listed if l['id'] == self.target.id)['views'], 7)
