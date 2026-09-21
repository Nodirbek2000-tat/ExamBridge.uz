"""Vaqtinchalik sinov: eksport qilingan JSON qayta import qilinsa,
xuddi o'sha material chiqadimi (round-trip)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


class ExportRoundTripTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username='admin_test', email='admin@test.uz', password='x'
        )
        self.client.force_login(self.admin)

    # BotBlockerMiddleware User-Agent va Accept yo'q so'rovlarni bloklaydi
    HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}

    def _post(self, url, payload):
        res = self.client.post(url, data=json.dumps(payload),
                               content_type='application/json', **self.HEADERS)
        self.assertIn(res.status_code, (200, 201), res.content)
        return res.json()

    def _get(self, url):
        res = self.client.get(url, **self.HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    # ── IELTS reading (bitta passage) ────────────────────────────────────────
    def test_ielts_reading_single(self):
        payload = {
            'title': 'Coffee History',
            'content': 'Coffee was discovered...',
            'passage_number': 2,
            'time_limit': 25,
            'difficulty': 'HARD',
            'is_standalone': True,
            'is_premium': True,
            'questions': [
                {'number': 1, 'question_type': 'TFNG', 'content': 'Q1?',
                 'correct_answer': 'TRUE', 'explanation': 'because',
                 'group_instruction': 'Questions 1-2', 'answer_review': 'line 4'},
                {'number': 2, 'question_type': 'MCQ', 'content': 'Q2?',
                 'correct_answer': 'B', 'max_selections': 2,
                 'word_bank': ['a', 'b'],
                 'choices': [{'option': 'A', 'text': 'first'},
                             {'option': 'B', 'text': 'second'}]},
            ],
        }
        created = self._post('/api/import/ielts/reading/', payload)
        exported = self._get(f"/api/admin/export/ielts/reading/{created['id']}/")['data']

        # Kiritilgan qiymatlar aynan qaytdimi
        for key in ('title', 'content', 'passage_number', 'time_limit',
                    'difficulty', 'is_standalone', 'is_premium'):
            self.assertEqual(exported[key], payload[key], key)
        self.assertEqual(len(exported['questions']), 2)
        self.assertEqual(exported['questions'][0]['correct_answer'], 'TRUE')
        self.assertEqual(exported['questions'][0]['answer_review'], 'line 4')
        self.assertEqual(exported['questions'][1]['choices'],
                         payload['questions'][1]['choices'])
        self.assertEqual(exported['questions'][1]['word_bank'], ['a', 'b'])
        self.assertEqual(exported['questions'][1]['max_selections'], 2)

        # Round-trip: eksportni qayta import qilib, yana eksport qilamiz
        again = self._post('/api/import/ielts/reading/', exported)
        exported2 = self._get(f"/api/admin/export/ielts/reading/{again['id']}/")['data']
        self.assertEqual(exported, exported2, 'round-trip barqaror emas')

    # ── IELTS listening (bitta section) ──────────────────────────────────────
    def test_ielts_listening_single(self):
        payload = {
            'title': 'Hotel Booking',
            'section_number': 3,
            'difficulty': 'EASY',
            'is_standalone': True,
            'is_premium': False,
            'transcript': 'Hello [1] there.',
            'questions': [
                {'number': 1, 'question_type': 'GAP', 'content': 'Name is ___',
                 'correct_answer': 'John'},
            ],
        }
        created = self._post('/api/import/ielts/listening/', payload)
        exported = self._get(f"/api/admin/export/ielts/listening/{created['id']}/")['data']
        for key in ('title', 'section_number', 'difficulty', 'is_standalone',
                    'is_premium', 'transcript'):
            self.assertEqual(exported[key], payload[key], key)

        again = self._post('/api/import/ielts/listening/', exported)
        exported2 = self._get(f"/api/admin/export/ielts/listening/{again['id']}/")['data']
        self.assertEqual(exported, exported2)

    # ── IELTS full mock (listening, ko'p section) ────────────────────────────
    def test_ielts_listening_mock(self):
        payload = {
            'title': 'IELTS Listening Mock Test 1',
            'difficulty': 'MEDIUM',
            'is_premium': True,
            'sections': [
                {'section_number': 1, 'title': 'Part 1', 'transcript': 't1',
                 'questions': [{'number': 1, 'question_type': 'GAP',
                                'content': 'a ___', 'correct_answer': 'x'}]},
                {'section_number': 2, 'title': 'Part 2', 'transcript': 't2',
                 'questions': [{'number': 2, 'question_type': 'MCQ',
                                'content': 'b?', 'correct_answer': 'A',
                                'choices': [{'option': 'A', 'text': 'yes'}]}]},
            ],
        }
        created = self._post('/api/import/ielts/listening/', payload)
        exported = self._get(f"/api/admin/export/ielts/test/{created['test_id']}/")['data']
        self.assertEqual(exported['title'], payload['title'])
        self.assertEqual(exported['is_premium'], True)
        self.assertEqual(len(exported['sections']), 2)
        self.assertEqual(exported['sections'][1]['questions'][0]['choices'],
                         [{'option': 'A', 'text': 'yes'}])

        again = self._post('/api/import/ielts/listening/', exported)
        exported2 = self._get(f"/api/admin/export/ielts/test/{again['test_id']}/")['data']
        self.assertEqual(exported, exported2)

    # ── IELTS full mock (reading, ko'p part) ─────────────────────────────────
    def test_ielts_reading_mock(self):
        payload = {
            'title': 'IELTS Reading Mock 1',
            'test_type': 'FULL_MOCK',
            'difficulty': 'MEDIUM',
            'is_premium': False,
            'parts': [
                {'passage_number': 1, 'title': 'P1', 'content': 'c1',
                 'questions': [{'number': 1, 'question_type': 'TFNG',
                                'content': 'q', 'correct_answer': 'FALSE'}]},
            ],
        }
        created = self._post('/api/import/ielts/reading/', payload)
        url = f"/api/admin/export/ielts/test/{created['test_id']}/?kind=reading"
        exported = self._get(url)['data']
        self.assertEqual(len(exported['parts']), 1)
        self.assertEqual(exported['parts'][0]['content'], 'c1')

        again = self._post('/api/import/ielts/reading/', exported)
        exported2 = self._get(
            f"/api/admin/export/ielts/test/{again['test_id']}/?kind=reading")['data']
        self.assertEqual(exported, exported2)

    # ── CEFR reading ─────────────────────────────────────────────────────────
    def test_cefr_reading_single(self):
        payload = {
            'type': 'reading',
            'level': 'B2',
            'title': 'Social Media',
            'time_limit': 30,
            'difficulty': 'HARD',
            'is_premium': True,
            'is_mock': False,
            'passage': {'title': 'Social Media', 'content': 'text...',
                        'passage_number': 1, 'is_standalone': True},
            'questions': [{'number': 1, 'question_type': 'TFNG',
                           'content': 'q?', 'correct_answer': 'TRUE'}],
        }
        created = self._post('/api/import/cefr/', payload)
        exported = self._get(f"/api/admin/export/cefr/reading/{created['id']}/")['data']
        self.assertEqual(exported['type'], 'reading')
        self.assertEqual(exported['level'], 'B2')
        self.assertEqual(exported['passage'], payload['passage'])
        self.assertEqual(exported['difficulty'], 'HARD')

        again = self._post('/api/import/cefr/', exported)
        exported2 = self._get(f"/api/admin/export/cefr/reading/{again['id']}/")['data']
        self.assertEqual(exported, exported2)

    # ── CEFR listening ───────────────────────────────────────────────────────
    def test_cefr_listening_single(self):
        payload = {
            'type': 'listening',
            'level': 'B1',
            'title': 'Phone call',
            'time_limit': 25,
            'is_premium': False,
            'is_mock': False,
            'section': {'title': 'Phone call', 'section_number': 1,
                        'audio_url': 'https://example.com/a.mp3',
                        'transcript': 'Hi [1]', 'is_standalone': True},
            'questions': [{'number': 1, 'question_type': 'GAP',
                           'content': 'says ___', 'correct_answer': 'hello'}],
        }
        created = self._post('/api/import/cefr/', payload)
        exported = self._get(f"/api/admin/export/cefr/listening/{created['id']}/")['data']
        self.assertEqual(exported['section'], payload['section'])
        self.assertEqual(exported['level'], 'B1')

        again = self._post('/api/import/cefr/', exported)
        exported2 = self._get(f"/api/admin/export/cefr/listening/{again['id']}/")['data']
        self.assertEqual(exported, exported2)
