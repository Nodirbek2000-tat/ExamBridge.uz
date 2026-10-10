"""Cambridge belgisi: import → ro'yxat → admin toggle → eksport, va
imtihon sarlavhasi uchun passage_number."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase


def _q(n):
    return {'number': n, 'question_type': 'TFNG', 'content': f'Q{n}?', 'correct_answer': 'TRUE'}


class CambridgeFlagTest(TestCase):
    # BotBlockerMiddleware User-Agent va Accept yo'q so'rovlarni bloklaydi
    HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='cam_admin', email='cam_admin@exambridge.test', password='x'
        )
        self.client.force_login(self.admin)

    def _post(self, url, payload):
        res = self.client.post(url, data=json.dumps(payload), content_type='application/json', **self.HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def _patch(self, url, payload):
        res = self.client.patch(url, data=json.dumps(payload), content_type='application/json', **self.HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def _get(self, url):
        res = self.client.get(url, **self.HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_reading_import_flag_reaches_lists_and_export(self):
        single = self._post('/api/import/ielts/reading/', {
            'title': 'Cam 18 Passage 2', 'content': 'Text', 'passage_number': 2,
            'is_cambridge': True, 'questions': [_q(1)],
        })
        plain = self._post('/api/import/ielts/reading/', {
            'title': 'Our passage', 'content': 'Text', 'passage_number': 1, 'questions': [_q(1)],
        })
        mock = self._post('/api/import/ielts/reading/', {
            'title': 'Cam 18 Test 1', 'is_cambridge': True,
            'parts': [{'passage_number': i, 'title': f'P{i}', 'content': 'T', 'questions': [_q(i)]} for i in (1, 2, 3)],
        })

        data = self._get('/api/ielts/reading/')
        practices = {p['id']: p for p in data['practices']}
        self.assertTrue(practices[single['id']]['is_cambridge'])
        self.assertFalse(practices[plain['id']]['is_cambridge'])
        self.assertEqual(practices[single['id']]['passage_number'], 2)
        m = next(m for m in data['mocks'] if m['id'] == mock['test_id'])
        self.assertTrue(m['is_cambridge'])
        self.assertEqual([p['passage_number'] for p in m['parts']], [1, 2, 3])

        admin_rows = {r['id']: r for r in self._get('/api/admin/ielts/reading/')}
        self.assertTrue(admin_rows[single['id']]['is_cambridge'])
        part_id = mock['passages'][0]['id']
        self.assertTrue(admin_rows[part_id]['test_is_cambridge'])

        exported = self._get(f'/api/admin/export/ielts/reading/{single["id"]}/')['data']
        self.assertTrue(exported['is_cambridge'])

        detail = self._get(f'/api/ielts/reading/{single["id"]}/')
        self.assertEqual(detail['passage_number'], 2)

    def test_listening_import_and_admin_toggles(self):
        single = self._post('/api/import/ielts/listening/', {
            'title': 'Section 4', 'section_number': 4, 'questions': [_q(1)],
        })
        mock = self._post('/api/import/ielts/listening/', {
            'title': 'Our listening mock',
            'sections': [{'section_number': i, 'title': f'S{i}', 'questions': [_q(i)]} for i in (1, 2, 3, 4)],
        })
        data = self._get('/api/ielts/listening/')
        self.assertFalse(next(p for p in data['practices'] if p['id'] == single['id'])['is_cambridge'])
        self.assertFalse(next(m for m in data['mocks'] if m['id'] == mock['test_id'])['is_cambridge'])

        # standalone section: the update endpoint sets the flag
        res = self._patch(f'/api/admin/ielts/listening/{single["id"]}/update/', {'is_cambridge': True})
        self.assertTrue(res['is_cambridge'])

        # mock test: Cambridge toggle leaves premium alone and reaches every section
        res = self._patch(f'/api/admin/ielts/tests/{mock["test_id"]}/premium/', {'is_cambridge': True})
        self.assertEqual((res['is_cambridge'], res['is_premium']), (True, False))
        data = self._get('/api/ielts/listening/')
        self.assertTrue(next(p for p in data['practices'] if p['id'] == single['id'])['is_cambridge'])
        self.assertTrue(next(m for m in data['mocks'] if m['id'] == mock['test_id'])['is_cambridge'])
        rows = [r for r in self._get('/api/admin/ielts/listening/') if r['test_id'] == mock['test_id']]
        self.assertTrue(all(r['is_cambridge'] and r['test_is_cambridge'] for r in rows))

        # old premium behaviour still works: empty body toggles premium only
        res = self._patch(f'/api/admin/ielts/tests/{mock["test_id"]}/premium/', {})
        self.assertEqual((res['is_cambridge'], res['is_premium']), (True, True))

    def test_standalone_premium_toggle_uses_update_endpoint(self):
        p = self._post('/api/import/ielts/reading/', {
            'title': 'Premium me', 'content': 'T', 'questions': [_q(1)],
        })
        res = self._patch(f'/api/admin/ielts/reading/{p["id"]}/update/', {'is_premium': True, 'is_cambridge': True})
        self.assertEqual((res['is_premium'], res['is_cambridge']), (True, True))
