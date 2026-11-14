"""At most two signed-in devices per student account."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .devices import device_name
from .models import UserSession

User = get_user_model()
JSON = 'application/json'

CHROME_WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
SAFARI_IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
                 '(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1')


class DevicesTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(username='s@mail.uz', email='s@mail.uz', password='pass-12345')

    def login(self, device, agent=CHROME_WINDOWS, email='s@mail.uz'):
        res = self.client.post('/api/auth/login/', {'email': email, 'password': 'pass-12345'},
                               content_type='application/json', HTTP_USER_AGENT=agent, HTTP_X_DEVICE_ID=device, HTTP_ACCEPT=JSON)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def me(self, tokens):
        return self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
                               HTTP_USER_AGENT=CHROME_WINDOWS, HTTP_ACCEPT=JSON)

    def refresh(self, refresh):
        return self.client.post('/api/auth/refresh/', {'refresh': refresh}, content_type='application/json',
                                HTTP_USER_AGENT=CHROME_WINDOWS, HTTP_X_DEVICE_ID='device-legacy', HTTP_ACCEPT=JSON)

    def test_a_third_device_signs_out_the_one_used_longest_ago(self):
        laptop = self.login('device-laptop')
        phone = self.login('device-phone', SAFARI_IPHONE)
        self.assertEqual(self.me(laptop).status_code, 200)
        self.assertEqual(self.me(phone).status_code, 200)
        # the laptop was used after the phone signed in, so the phone is the oldest now
        UserSession.objects.filter(device_id='device-laptop').update(last_seen_at=UserSession.objects.get(
            device_id='device-phone').last_seen_at.replace(year=2100))

        tablet = self.login('device-tablet')

        res = self.me(phone)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()['code'], 'session_revoked')
        self.assertIn('another device', res.json()['detail'])
        self.assertEqual(self.me(laptop).status_code, 200)
        self.assertEqual(self.me(tablet).status_code, 200)
        # and it cannot come back with its refresh token either
        res = self.refresh(phone['refresh'])
        self.assertEqual((res.status_code, res.json()['code']), (401, 'session_revoked'))

    def test_signing_in_again_on_the_same_device_signs_out_nobody_else(self):
        laptop = self.login('device-laptop')
        phone = self.login('device-phone')
        again = self.login('device-laptop')
        self.assertEqual(self.me(phone).status_code, 200)
        self.assertEqual(self.me(again).status_code, 200)
        self.assertEqual(self.me(laptop).status_code, 401)        # its own old session was replaced
        self.assertEqual(UserSession.objects.filter(revoked_at__isnull=True).count(), 2)

    def test_admins_are_not_limited(self):
        self.user.is_staff = True
        self.user.save()
        sessions = [self.login(f'device-{i}') for i in range(4)]
        for tokens in sessions:
            self.assertEqual(self.me(tokens).status_code, 200)

    def test_refresh_keeps_a_live_device_going(self):
        laptop = self.login('device-laptop')
        res = self.refresh(laptop['refresh'])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.me(res.json()).status_code, 200)

    def test_a_token_from_before_devices_gets_its_session_at_the_next_refresh(self):
        old = RefreshToken.for_user(self.user)
        self.assertEqual(self.me({'access': str(old.access_token)}).status_code, 200)     # still works
        res = self.refresh(str(old))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(RefreshToken(res.json()['refresh']).get('sid'))
        self.assertEqual(UserSession.objects.get().device_id, 'device-legacy')

    def test_the_device_list_and_signing_another_device_out(self):
        laptop = self.login('device-laptop')
        phone = self.login('device-phone', SAFARI_IPHONE)
        auth = {'HTTP_AUTHORIZATION': f"Bearer {laptop['access']}", 'HTTP_USER_AGENT': CHROME_WINDOWS, 'HTTP_ACCEPT': JSON}

        listed = self.client.get('/api/auth/devices/', **auth).json()
        self.assertEqual(listed['limit'], 2)
        self.assertEqual([(d['name'], d['current'], d['mobile']) for d in listed['devices']],
                         [('Chrome on Windows', True, False), ('Safari on iPhone', False, True)])

        other = listed['devices'][1]['id']
        self.assertEqual(self.client.delete(f'/api/auth/devices/{other}/', **auth).status_code, 204)
        res = self.me(phone)
        self.assertEqual(res.status_code, 401)
        self.assertIn('from another device', res.json()['detail'])
        self.assertEqual(self.client.delete(f'/api/auth/devices/{other}/', **auth).status_code, 404)

    def test_nobody_can_sign_out_someone_elses_device(self):
        User.objects.create_user(username='o@mail.uz', email='o@mail.uz', password='pass-12345')
        theirs = self.login('device-other', email='o@mail.uz')
        mine = self.login('device-laptop')
        session = UserSession.objects.get(device_id='device-other')
        res = self.client.delete(f'/api/auth/devices/{session.id}/',
                                 HTTP_AUTHORIZATION=f"Bearer {mine['access']}", HTTP_USER_AGENT=CHROME_WINDOWS, HTTP_ACCEPT=JSON)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.me(theirs).status_code, 200)

    def test_logging_out_frees_the_device(self):
        laptop = self.login('device-laptop')
        res = self.client.post('/api/auth/logout/', {'refresh': laptop['refresh']}, content_type='application/json',
                               HTTP_AUTHORIZATION=f"Bearer {laptop['access']}", HTTP_USER_AGENT=CHROME_WINDOWS, HTTP_ACCEPT=JSON)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.me(laptop).status_code, 401)
        self.assertEqual(UserSession.objects.get().revoked_reason, 'logout')

    def test_testmakon_students_have_no_sessions(self):
        self.user.platform = User.PLATFORM_TESTMAKON
        self.user.save()
        tokens = self.login('device-laptop')
        self.assertIsNone(RefreshToken(tokens['refresh']).get('sid'))
        self.assertFalse(UserSession.objects.exists())
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 200)


class DeviceNameTest(SimpleTestCase):
    def test_names(self):
        self.assertEqual(device_name(CHROME_WINDOWS), 'Chrome on Windows')
        self.assertEqual(device_name(SAFARI_IPHONE), 'Safari on iPhone')
        self.assertEqual(device_name('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile'),
                         'Chrome on Android')
        self.assertEqual(device_name('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) Version/17.4 Safari/605'),
                         'Safari on Mac')
        self.assertEqual(device_name('Mozilla/5.0 (Windows NT 10.0) Chrome/124 Safari/537 Edg/124'),
                         'Edge on Windows')
        self.assertEqual(device_name('okhttp/4.9.2'), 'ExamBridge app')
        self.assertEqual(device_name(''), 'Unknown device')
