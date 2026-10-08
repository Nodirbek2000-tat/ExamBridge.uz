"""
PATCH /api/auth/profile/ — exam_date used to be saved as the raw string and then user_data()
called .isoformat() on it, so a non-empty date answered 500 (after saving).

    python manage.py test api.tests_auth_profile
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'auth-profile-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36',
         HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
URL = '/api/auth/profile/'


@override_settings(CACHES=LOCMEM)
class ProfileExamDateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create(username='pf1@example.test', email='pf1@example.test',
                                                   first_name='Old', target_band='6.5')

    def setUp(self):
        self.c = APIClient(**H)
        self.c.force_authenticate(self.user)

    def patch(self, data):
        return self.c.patch(URL, data, format='json')

    def stored(self):
        self.user.refresh_from_db()
        return self.user.exam_date

    def test_set_exam_date(self):
        r = self.patch({'exam_date': '2026-12-05'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['exam_date'], '2026-12-05')
        self.assertEqual(self.stored(), datetime.date(2026, 12, 5))
        self.assertEqual(self.c.get('/api/auth/me/').json()['exam_date'], '2026-12-05')

    def test_set_exam_date_with_other_fields(self):
        r = self.patch({'exam_date': ' 2027-01-20 ', 'target_band': '7.5', 'daily_study_minutes': 45})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['exam_date'], body['target_band'], body['daily_study_minutes']), ('2027-01-20', '7.5', 45))
        self.assertEqual(self.stored(), datetime.date(2027, 1, 20))

    def test_multipart_form_works_too(self):
        r = self.c.patch(URL, {'exam_date': '2026-11-01'}, format='multipart')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['exam_date'], '2026-11-01')

    def test_clear_exam_date(self):
        for empty in (None, ''):
            with self.subTest(value=empty):
                self.assertEqual(self.patch({'exam_date': '2026-12-05'}).status_code, 200)
                r = self.patch({'exam_date': empty})
                self.assertEqual(r.status_code, 200, r.content)
                self.assertIsNone(r.json()['exam_date'])
                self.assertIsNone(self.stored())

    def test_invalid_date_is_400_and_nothing_is_saved(self):
        self.assertEqual(self.patch({'exam_date': '2026-12-05'}).status_code, 200)
        for bad in ('not-a-date', '2026-02-30', '05.12.2026', '2026-13-01', 12345):
            with self.subTest(value=bad):
                r = self.patch({'exam_date': bad, 'first_name': 'Changed'})
                self.assertEqual(r.status_code, 400, r.content)
                self.assertEqual(r.json(), {'detail': 'Invalid date format. Use YYYY-MM-DD.'})
                self.assertEqual(self.stored(), datetime.date(2026, 12, 5))
                self.assertEqual(self.user.first_name, 'Old')        # the whole PATCH is refused

    def test_without_exam_date_unchanged(self):
        self.assertEqual(self.patch({'exam_date': '2026-12-05'}).status_code, 200)
        r = self.patch({'first_name': 'New'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['first_name'], r.json()['exam_date']), ('New', '2026-12-05'))
