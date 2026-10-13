"""CEFR writing import: a part test may hold a single letter (Task 1.1 or 1.2)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from cefr.models import CEFRWritingTest


class WritingImportTest(TestCase):
    HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='writing_import_admin', email='writing_import_admin@exambridge.test', password='x')
        self.client.force_login(self.admin)

    def _import(self, payload):
        return self.client.post('/api/import/cefr/writing/', data=json.dumps(payload),
                                content_type='application/json', **self.HEADERS)

    def test_single_letters_and_parts(self):
        res = self._import([
            {'title': 'Only 1.2', 'situation': 'Club news.', 'task_1_2': 'Write to the leader. 120-150 words.'},
            {'title': 'Only 1.1', 'situation': 'Club news.', 'task_1_1': 'Write to a friend. 50 words.'},
            {'title': 'Both letters', 'situation': 'Club.', 'task_1_1': 'A', 'task_1_2': 'B'},
            {'title': 'Essay', 'part_2': 'Do you agree?'},
            {'title': 'Full', 'situation': 'S', 'task_1_1': 'A', 'task_1_2': 'B', 'part_2': 'C'},
        ])
        self.assertEqual(res.status_code, 201, res.content)
        got = {t.title: (t.kind, t.time_limit) for t in CEFRWritingTest.objects.all()}
        self.assertEqual(got, {'Only 1.2': ('PART1', 20), 'Only 1.1': ('PART1', 10), 'Both letters': ('PART1', 25),
                               'Essay': ('PART2', 35), 'Full': ('FULL', 60)})
        tasks = {t['title']: t['tasks'] for t in self.client.get('/api/cefr/writing/', **self.HEADERS).json()}
        self.assertEqual(tasks['Only 1.2'], ['1.2'])
        self.assertEqual(tasks['Full'], ['1.1', '1.2', '2'])

    def test_rejects_letter_plus_essay_and_missing_situation(self):
        self.assertEqual(self._import({'title': 'Odd', 'situation': 'S', 'task_1_2': 'B', 'part_2': 'C'}).status_code, 400)
        self.assertEqual(self._import({'title': 'No situation', 'task_1_2': 'B'}).status_code, 400)
        self.assertEqual(CEFRWritingTest.objects.count(), 0)
