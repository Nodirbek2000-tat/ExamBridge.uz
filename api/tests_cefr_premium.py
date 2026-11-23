"""Premium CEFR tests start only with Premium; the admin switches it per test."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from cefr.models import CEFRListeningSection, CEFRReadingPassage, CEFRTest, CEFRWritingTest

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


def auth(user):
    return {**HEADERS, 'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}


class CEFRPremiumTest(TestCase):
    def setUp(self):
        self.free = User.objects.create_user(username='free@t.uz', email='free@t.uz', password='x')
        self.premium = User.objects.create_user(username='p@t.uz', email='p@t.uz', password='x', is_premium=True,
                                                premium_until=timezone.now() + timedelta(days=30))
        self.admin = User.objects.create_user(username='a@t.uz', email='a@t.uz', password='x', is_staff=True)

        self.reading_mock = CEFRTest.objects.create(title='Reading Test 1', level='B2', test_type='READING', is_premium=True)
        self.mock_part = CEFRReadingPassage.objects.create(test=self.reading_mock, title='P1', content='...', is_standalone=False)
        self.free_part = CEFRReadingPassage.objects.create(title='Free part', content='...')
        self.listening_mock = CEFRTest.objects.create(title='Listening 1', level='B2', test_type='LISTENING', is_premium=True)
        CEFRListeningSection.objects.create(test=self.listening_mock, title='S1', is_standalone=False)
        self.writing = CEFRWritingTest.objects.create(title='Writing 1', task2='Essay', is_premium=True)

    def start(self, user, url):
        return self.client.post(url, **auth(user))

    def test_a_free_account_cannot_start_a_premium_test(self):
        for url in (f'/api/cefr/reading/full-mock/{self.reading_mock.id}/start/',
                    f'/api/cefr/reading/{self.mock_part.id}/start/',          # a part of a Premium mock
                    f'/api/cefr/listening/full-mock/{self.listening_mock.id}/start/',
                    f'/api/cefr/writing/{self.writing.id}/start/'):
            res = self.start(self.free, url)
            self.assertEqual(res.status_code, 403, url)
            self.assertEqual(res.json()['code'], 'premium_required')
        self.assertIn(self.start(self.free, f'/api/cefr/reading/{self.free_part.id}/start/').status_code, (200, 201))

    def test_premium_and_admins_can(self):
        for user in (self.premium, self.admin):
            res = self.start(user, f'/api/cefr/reading/full-mock/{self.reading_mock.id}/start/')
            self.assertIn(res.status_code, (200, 201))
            self.assertIn(self.start(user, f'/api/cefr/writing/{self.writing.id}/start/').status_code, (200, 201))

    def test_the_list_says_which_are_premium(self):
        items = self.client.get('/api/cefr/reading/', **auth(self.free)).json()
        flags = {i['title']: i['is_premium'] for i in items}
        self.assertEqual(flags, {'Reading Test 1': True, 'Free part': False})

    def test_the_admin_switches_premium_per_test(self):
        url = f'/api/admin/cefr/premium/test/{self.reading_mock.id}/'
        res = self.client.patch(url, {'is_premium': False}, content_type='application/json', **auth(self.admin))
        self.assertEqual(res.json(), {'kind': 'test', 'id': self.reading_mock.id, 'is_premium': False})
        self.assertIn(self.start(self.free, f'/api/cefr/reading/full-mock/{self.reading_mock.id}/start/').status_code,
                      (200, 201))
        # an empty body switches it; every kind of CEFR test has the switch
        for kind, item in (('reading', self.free_part), ('writing', self.writing)):
            before = item.is_premium
            res = self.client.patch(f'/api/admin/cefr/premium/{kind}/{item.id}/', {}, content_type='application/json',
                                    **auth(self.admin))
            self.assertEqual(res.json()['is_premium'], not before)
        self.assertEqual(self.client.patch('/api/admin/cefr/premium/nope/1/', {}, content_type='application/json',
                                           **auth(self.admin)).status_code, 400)
        self.assertEqual(self.client.patch(url, {}, content_type='application/json', **auth(self.free)).status_code, 403)
