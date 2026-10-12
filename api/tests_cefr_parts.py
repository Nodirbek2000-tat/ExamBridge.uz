"""CEFR part numbers: a one-part "mock" is listed as that Part; numbers are checked on import."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


def _q(n):
    return {'number': n, 'question_type': 'TFNG', 'content': f'Q{n}?', 'correct_answer': 'TRUE'}


def _part(n, **extra):
    return {'passage_number': n, 'title': f'Part {n}', 'content': 'Text.', 'questions': [_q(1)], **extra}


def _section(n):
    return {'section_number': n, 'title': f'Part {n}', 'questions': [_q(1)]}


class CefrPartNumberTest(TestCase):
    HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='cefr_parts_admin', email='cefr_parts_admin@exambridge.test', password='x')
        self.client.force_login(self.admin)

    def _import(self, payload):
        return self.client.post('/api/import/cefr/', data=json.dumps(payload),
                                content_type='application/json', **self.HEADERS)

    def _list(self, kind):
        return self.client.get(f'/api/cefr/{kind}/', **self.HEADERS).json()

    def test_reading_part_numbers_listed(self):
        one = self._import({'type': 'reading', 'title': 'One part', 'parts': [_part(4)]})
        full = self._import({'type': 'reading', 'title': 'Full', 'parts': [_part(1), _part(2), _part(3)]})
        top = self._import({'type': 'reading', 'title': 'Top level number', 'passage_number': 5,
                            'parts': [{'title': 'P', 'content': 'Text.', 'questions': [_q(1)]}]})
        self.assertEqual((one.status_code, full.status_code, top.status_code), (200, 200, 200), one.content)
        mocks = {m['title']: m['part_numbers'] for m in self._list('reading') if m['item_type'] == 'full_mock'}
        self.assertEqual(mocks, {'One part': [4], 'Full': [1, 2, 3], 'Top level number': [5]})

    def test_reading_bad_numbers_rejected(self):
        for parts in ([_part(6)], [_part(2), _part(2)], [_part('x')]):
            res = self._import({'type': 'reading', 'title': 'Bad', 'parts': parts})
            self.assertEqual(res.status_code, 400, parts)
        res = self._import({'type': 'reading', 'title': 'Bad single', 'passage_number': 0,
                            'content': 'Text.', 'questions': [_q(1)]})
        self.assertEqual(res.status_code, 400)

    def test_listening_part_numbers(self):
        ok = self._import({'type': 'listening', 'title': 'L one', 'sections': [_section(6)]})
        self.assertEqual(ok.status_code, 200, ok.content)
        mocks = [m for m in self._list('listening') if m['item_type'] == 'full_mock']
        self.assertEqual(mocks[0]['part_numbers'], [6])
        bad = self._import({'type': 'listening', 'title': 'L bad', 'sections': [_section(7)]})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('1–6', bad.json()['error'])
