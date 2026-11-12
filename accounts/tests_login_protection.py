"""
Sign-in protection when a whole class shares one network address.

A learning centre puts every student behind the same address. One student
typing a wrong password again and again must lock only that student.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.middleware import BruteForceProtectionMiddleware, _unregister_block
from accounts.models import LOGIN_FAILURES_BEFORE_LOCK

User = get_user_model()

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CENTRE = '203.0.113.10'
HOME = '198.51.100.7'
PASSWORD = 'right-password-1'


def headers(ip=CENTRE, **extra):
    return {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json', 'HTTP_X_REAL_IP': ip, **extra}


@override_settings(CACHES=LOCMEM, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LoginProtectionBase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def student(name):
        return User.objects.create_user(username=name, email=f'{name}@centre.uz', password=PASSWORD)

    def login(self, email, password=PASSWORD, ip=CENTRE, **extra):
        return self.client.post('/api/auth/login/', data={'email': email, 'password': password},
                                content_type='application/json', **headers(ip, **extra))


class AccountLockTest(LoginProtectionBase):
    def test_one_students_wrong_passwords_do_not_lock_out_the_class(self):
        guesser, classmate = self.student('guesser'), self.student('classmate')
        for _ in range(LOGIN_FAILURES_BEFORE_LOCK + 2):
            self.login(guesser.email, 'wrong')

        self.assertEqual(self.login(classmate.email).status_code, 200)
        locked = self.login(guesser.email)
        self.assertEqual(locked.status_code, 403)
        self.assertIn('15 minutes', locked.json()['detail'])

    def test_the_lock_ends_by_itself(self):
        user = self.student('later')
        for _ in range(LOGIN_FAILURES_BEFORE_LOCK):
            self.login(user.email, 'wrong')
        User.objects.filter(pk=user.pk).update(locked_until=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self.login(user.email).status_code, 200)

    def test_a_few_mistakes_then_the_right_password_starts_over(self):
        user = self.student('typo')
        for _ in range(LOGIN_FAILURES_BEFORE_LOCK - 1):
            self.login(user.email, 'wrong')
        self.assertEqual(self.login(user.email).status_code, 200)

        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)
        self.login(user.email, 'wrong')
        self.assertEqual(self.login(user.email).status_code, 200)

    def test_an_unknown_email_is_just_wrong(self):
        self.assertEqual(self.login('nobody@centre.uz').status_code, 401)


class ClassroomTest(LoginProtectionBase):
    def test_a_whole_class_can_sign_in_and_refresh(self):
        students = [self.student(f's{i}') for i in range(70)]
        for s in students:
            self.assertEqual(self.login(s.email).status_code, 200, s.email)

        refresh = str(RefreshToken.for_user(students[0]))
        for _ in range(70):
            res = self.client.post('/api/auth/refresh/', data={'refresh': refresh},
                                   content_type='application/json', **headers())
            self.assertEqual(res.status_code, 200)

    def test_a_class_can_register_in_one_lesson(self):
        for i in range(30):
            res = self.client.post('/api/auth/register/', content_type='application/json', **headers(), data={
                'email': f'new{i}@centre.uz', 'password': 'long-password-1', 'first_name': 'N', 'last_name': 'S',
            })
            self.assertEqual(res.status_code, 201, i)

    def test_signed_in_students_working_together_are_not_cut_off(self):
        tokens = [AccessToken.for_user(self.student(f'w{i}')) for i in range(5)]
        with mock.patch.object(BruteForceProtectionMiddleware, 'API_RATE_LIMIT_SIGNED_IN', 40), \
                mock.patch.object(BruteForceProtectionMiddleware, 'API_RATE_LIMIT', 3):
            for i in range(30):
                res = self.client.get('/api/auth/ping/', **headers(HTTP_AUTHORIZATION=f'Bearer {tokens[i % 5]}'))
                self.assertEqual(res.status_code, 204, i)

    def test_too_many_anonymous_requests_refuse_only_themselves(self):
        token = AccessToken.for_user(self.student('busy'))
        with mock.patch.object(BruteForceProtectionMiddleware, 'API_RATE_LIMIT', 3):
            codes = [self.client.get('/api/auth/csrf/', **headers()).status_code for _ in range(5)]
            signed_in = self.client.get('/api/auth/ping/', **headers(HTTP_AUTHORIZATION=f'Bearer {token}'))

        self.assertEqual(codes, [200, 200, 200, 429, 429])
        self.assertEqual(signed_in.status_code, 204)


class AttackTest(LoginProtectionBase):
    def spray(self, count, ip=CENTRE, **extra):
        for i in range(count):
            self.login(f'victim{i}@centre.uz', 'guess', ip=ip, **extra)

    def test_password_spraying_closes_only_the_login(self):
        token = AccessToken.for_user(self.student('innocent'))
        with mock.patch.object(BruteForceProtectionMiddleware, 'MAX_FAILURES', 5):
            self.spray(5)

            self.assertEqual(self.login('innocent@centre.uz').status_code, 429)
            me = self.client.get('/api/auth/me/', **headers(HTTP_AUTHORIZATION=f'Bearer {token}'))
            self.assertEqual(me.status_code, 200)
            self.assertEqual(self.login('innocent@centre.uz', ip=HOME).status_code, 200)

    def test_a_faked_forwarded_for_cannot_get_another_network_blocked(self):
        self.student('home')
        with mock.patch.object(BruteForceProtectionMiddleware, 'MAX_FAILURES', 3):
            self.spray(3, ip=CENTRE, HTTP_X_FORWARDED_FOR=HOME)

            self.assertEqual(self.login('home@centre.uz', ip=HOME).status_code, 200)

    def test_without_x_real_ip_the_proxys_own_entry_counts(self):
        self.student('proxy')
        with mock.patch.object(BruteForceProtectionMiddleware, 'MAX_FAILURES', 3):
            for i in range(3):
                self.client.post('/api/auth/login/', data={'email': f'x{i}@centre.uz', 'password': 'g'},
                                 content_type='application/json', HTTP_USER_AGENT='Mozilla/5.0 (test)',
                                 HTTP_ACCEPT='application/json', HTTP_X_FORWARDED_FOR=f'{HOME}, {CENTRE}')
            blocked = self.client.post('/api/auth/login/', data={'email': 'proxy@centre.uz', 'password': PASSWORD},
                                       content_type='application/json', HTTP_USER_AGENT='Mozilla/5.0 (test)',
                                       HTTP_ACCEPT='application/json', HTTP_X_FORWARDED_FOR=f'1.2.3.4, {CENTRE}')

        self.assertEqual(blocked.status_code, 429)

    def test_the_admin_unblock_reopens_the_login(self):
        self.student('again')
        with mock.patch.object(BruteForceProtectionMiddleware, 'MAX_FAILURES', 3):
            self.spray(3)
            _unregister_block(CENTRE)
            self.assertEqual(self.login('again@centre.uz').status_code, 200)

    def test_blocks_from_before_this_change_no_longer_close_the_site(self):
        self.student('old')
        cache.set(f'brute_block:{CENTRE}', True, 3600)
        self.assertEqual(self.login('old@centre.uz').status_code, 200)
