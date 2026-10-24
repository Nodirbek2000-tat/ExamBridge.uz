"""AI analysis: when there is enough to analyse."""
from unittest import mock

from django.test import SimpleTestCase

from api import analytics_ai


class VolumeTest(SimpleTestCase):
    def volume(self, answered, writing=0, speaking=0):
        data = {'totals': {'answered': answered, 'writing_count': writing, 'speaking_count': speaking}}
        with mock.patch('api.exam_analytics.build_exam_analytics', return_value=data), \
                mock.patch('api.exam_analytics.ai_facts', return_value={}):
            return analytics_ai._skill_exam(None, 'ielts', '30d')[1]

    def test_one_essay_is_enough(self):
        # writing / speaking students used to get "not enough data" for ever
        self.assertGreaterEqual(self.volume(0, writing=1), analytics_ai.MIN_QUESTIONS)
        self.assertGreaterEqual(self.volume(0, speaking=1), analytics_ai.MIN_QUESTIONS)

    def test_few_answers_are_not(self):
        self.assertLess(self.volume(3), analytics_ai.MIN_QUESTIONS)
        self.assertGreaterEqual(self.volume(12), analytics_ai.MIN_QUESTIONS)
