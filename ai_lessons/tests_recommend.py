"""Lessons recommended for a student's weak spots."""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from . import recommend
from .models import AILesson, LessonProgress
from .recommend import _spot, grammar_themes, matching_lessons
from .tests import STUDENT, StudentBase
from .tests_pipeline import PipelineBase

LINEAR = _spot('Linear equations in one variable', 'Math', '3/12 (25%)', ('SAT',))
TFNG = _spot('True / False / Not Given', 'Reading', '2/9 (22%)', ('IELTS',))
TENSES = _spot('Present simple', 'Grammar', '4 mistakes in your writing and speaking', ('IELTS', 'CEFR'),
               recommend.GRAMMAR_THEMES['Present simple'])


class GrammarThemesTest(SimpleTestCase):
    def test_issues_are_found_wherever_the_ai_stored_them(self):
        ielts = {'GRA': {'band': 5.5, 'errors': [{'quote': 'he go', 'issue': 'Subject-verb agreement'}]},
                 'LR': {'errors': [{'quote': 'make a photo', 'issue': 'Wrong collocation'}]}}
        cefr = {'criteria': {'grammar': {'errors': [{'issue': 'Third person -s missing'},
                                                   {'issue': 'Wrong tense: past simple needed'}]}}}
        themes = grammar_themes([ielts, cefr, {}, None, 'text'])
        self.assertEqual(themes['Present simple'], 2)
        self.assertEqual(themes['Past tenses'], 1)
        self.assertEqual(themes['Word choice and collocations'], 1)


class MatchingTest(StudentBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        AILesson.objects.all().delete()

    def published(self, title, subject='SAT', section='general', topics=()):
        return AILesson.objects.create(title=title, subject=subject, section=section, topics=list(topics),
                                       status=AILesson.Status.PUBLISHED)

    def test_topics_and_titles_find_the_lesson_for_a_spot(self):
        line = self.published('SAT Math: the line equation y = mx + b', section='math')
        tagged = self.published('Solving for x', topics=['Linear equations in one variable'])
        self.published('Words in context', section='reading_writing')
        self.published('Linear equations draft', topics=['Linear equations in one variable']).__class__.objects \
            .filter(title='Linear equations draft').update(status=AILesson.Status.REVIEW)

        found = matching_lessons(self.student, [LINEAR])
        self.assertEqual([lesson for lesson, _ in found], [tagged, line])      # an exact topic first
        self.assertEqual(found[0][1][0]['name'], LINEAR['name'])

    def test_a_lesson_for_another_exam_is_not_offered(self):
        self.published('True / False / Not Given in IELTS reading', subject='IELTS')
        self.assertEqual(matching_lessons(self.student, [LINEAR]), [])
        self.assertEqual(len(matching_lessons(self.student, [TFNG])), 1)

    def test_grammar_is_taught_by_a_cefr_lesson_to_an_ielts_student_too(self):
        lesson = self.published('Present Simple: habits and routines', subject='CEFR', section='grammar')
        self.assertEqual([lesson for lesson, _ in matching_lessons(self.student, [TENSES])], [lesson])

    def test_finished_lessons_come_last(self):
        first = self.published('Linear equations: the basics')
        second = self.published('Linear equations: word problems')
        LessonProgress.objects.create(user=self.student, lesson=first, completed_at='2026-01-01T00:00Z')
        self.assertEqual([lesson for lesson, _ in matching_lessons(self.student, [LINEAR])], [second, first])

    def test_the_endpoint_names_the_spots_and_what_each_lesson_covers(self):
        lesson = self.published('Linear equations: the basics', section='math')
        with mock.patch.object(recommend, 'weak_spots', return_value=[LINEAR, TFNG]) as spots:
            body = self.client.get(f'{STUDENT}recommended/?exam=sat', **self.s).json()
        spots.assert_called_once_with(self.student, ('sat',))
        self.assertEqual([s['name'] for s in body['spots']], [LINEAR['name'], TFNG['name']])
        self.assertNotIn('words', body['spots'][0])
        self.assertEqual(body['lessons'][0]['id'], lesson.id)
        self.assertEqual(body['lessons'][0]['covers'], [{'name': LINEAR['name'], 'area': 'Math', 'result': '3/12 (25%)'}])
        self.assertEqual(body['lessons'][0]['progress']['percent'], 0)

    def test_no_weak_spots_no_lessons(self):
        self.published('Linear equations: the basics')
        with mock.patch.object(recommend, 'weak_spots', return_value=[]):
            body = self.client.get(f'{STUDENT}recommended/', **self.s).json()
        self.assertEqual(body, {'spots': [], 'lessons': []})

    def test_the_ai_analysis_can_send_the_student_to_the_lesson(self):
        from api import analytics_ai
        lesson = self.published('Linear equations: the basics')
        sat = ({'questions_attempted': 30}, 30, [{'label': 'Practice', 'url': '/app/sat/practice'}], 'hints')
        with mock.patch.object(analytics_ai, '_sat', return_value=sat), \
                mock.patch.object(recommend, 'weak_spots', return_value=[LINEAR]):
            facts, _, links, _ = analytics_ai._facts(self.student, 'sat', '30d')
        self.assertEqual(facts['recommended_lessons'], [{'title': lesson.title, 'teaches': [LINEAR['name']]}])
        self.assertIn({'label': f'AI lesson: {lesson.title}', 'url': f'/app/lessons/{lesson.id}'}, links)


@override_settings(AI_LESSON_PROVIDER='fake')
class LessonTopicsTest(PipelineBase):
    def test_the_ai_tags_the_lesson_it_writes(self):
        self.generate()
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.topics, [self.lesson.title])

    def test_an_admins_own_topics_are_kept(self):
        self.lesson.topics = ['Linear equations in one variable']
        self.lesson.save()
        self.generate()
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.topics, ['Linear equations in one variable'])
