"""
BotBlockerMiddleware: the mobile app's User-Agent ("ExamBridgeApp/...") is never treated as a
bot; every other rule is unchanged.

    python manage.py test accounts.tests_bot_blocker

The cache is swapped for a local in-memory one, so block entries never reach the shared Redis.
"""
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from accounts.middleware import BLOCKED_LOG_PREFIX, BLOCKED_SET_KEY, BOT_BLOCK_SECONDS, BotBlockerMiddleware

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'bot-blocker-tests'}}
BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'
APP = 'ExamBridgeApp/1.0 (Android)'
IP = '203.0.113.7'          # TEST-NET-3: never a real client


@override_settings(CACHES=LOCMEM)
class BotBlockerMiddlewareTests(SimpleTestCase):
    """The middleware alone, in front of a view that answers 200."""

    def setUp(self):
        self.mw = BotBlockerMiddleware(lambda request: HttpResponse('ok'))
        self.rf = RequestFactory()
        self._forget()
        self.addCleanup(self._forget)

    def _forget(self):
        cache.delete(f'{BLOCKED_LOG_PREFIX}{IP}')
        cache.delete(BLOCKED_SET_KEY)

    def call(self, path, ua=None, accept='application/json'):
        meta = {'REMOTE_ADDR': IP}
        if ua is not None:
            meta['HTTP_USER_AGENT'] = ua
        if accept is not None:
            meta['HTTP_ACCEPT'] = accept
        return self.mw(self.rf.get(path, **meta))

    def assertPassed(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertIsNone(cache.get(f'{BLOCKED_LOG_PREFIX}{IP}'))

    def assertBlocked(self, response, reason_prefix):
        self.assertEqual(response.status_code, 403)
        entry = cache.get(f'{BLOCKED_LOG_PREFIX}{IP}')
        self.assertIsNotNone(entry)
        self.assertTrue(entry['reason'].startswith(reason_prefix), entry['reason'])
        return entry

    def test_app_ua_passes_on_api_and_media(self):
        self.assertPassed(self.call('/api/ielts/speaking/', APP))
        self.assertPassed(self.call('/media/ielts/speaking/attempt_1_q0.m4a', APP, accept='*/*'))

    def test_app_ua_passes_even_when_the_rest_matches_a_bot_pattern(self):
        # Android device codenames / library names collide with the short tokens ("ruby", "okhttp")
        for ua in ('ExamBridgeApp/1.0 (Android 14; ruby)', 'ExamBridgeApp/1.2.3 (android; sdk57) okhttp/4.12.0',
                   'ExamBridgeApp/1.0 (iOS 18.0; iPhone) axios/1.7'):
            with self.subTest(ua=ua):
                self.assertPassed(self.call('/api/auth/me/', ua))
                self.assertPassed(self.call('/media/study/a.pdf', ua, accept='*/*'))

    def test_okhttp_is_still_a_bot(self):
        entry = self.assertBlocked(self.call('/api/auth/me/', 'okhttp/4.9.2'), 'bot_ua:okhttp/4.9.2')
        self.assertEqual(entry['unblock_at'] - entry['blocked_at'], BOT_BLOCK_SECONDS)
        self._forget()
        self.assertBlocked(self.call('/media/x.m4a', 'okhttp/4.9.2', accept='*/*'), 'bot_ua:')

    def test_prefix_must_come_first(self):
        # the allowance is a prefix, not a substring: a bot cannot just append it
        self.assertBlocked(self.call('/api/auth/me/', 'okhttp/4.9.2 ExamBridgeApp/1.0'), 'bot_ua:')
        self._forget()
        self.assertBlocked(self.call('/api/auth/me/', 'python-requests/2.31 (ExamBridgeApp/1.0)'), 'bot_ua:')

    def test_other_bots_still_blocked(self):
        for ua in ('curl/8.4.0', 'python-requests/2.31.0', 'Mozilla/5.0 HeadlessChrome/120.0', 'axios/1.6.0'):
            with self.subTest(ua=ua):
                self._forget()
                self.assertBlocked(self.call('/api/auth/me/', ua), 'bot_ua:')

    def test_browser_ua_passes(self):
        self.assertPassed(self.call('/api/auth/me/', BROWSER))
        self.assertPassed(self.call('/media/x.webm', BROWSER, accept='*/*'))

    def test_empty_ua_still_blocked_on_api(self):
        self.assertBlocked(self.call('/api/auth/me/', ua=None), 'no_user_agent')
        self._forget()
        self.assertBlocked(self.call('/api/auth/me/', ua=''), 'no_user_agent')
        self._forget()
        self.assertPassed(self.call('/media/x.webm', ua=None, accept='*/*'))      # unchanged: only /api/ and /admin/

    def test_missing_accept_still_blocked_on_api_even_for_the_app(self):
        self.assertBlocked(self.call('/api/auth/me/', APP, accept=None), 'no_accept_header')
        self._forget()
        self.assertBlocked(self.call('/api/auth/me/', BROWSER, accept=None), 'no_accept_header')


@override_settings(CACHES=LOCMEM)
class BotBlockerThroughTheStackTests(SimpleTestCase):
    """The real middleware stack and URLs (the test client is 127.0.0.1)."""

    def setUp(self):
        self._forget()
        self.addCleanup(self._forget)

    def _forget(self):
        for key in (f'{BLOCKED_LOG_PREFIX}127.0.0.1', BLOCKED_SET_KEY, 'api_rate:127.0.0.1', 'brute_block:127.0.0.1'):
            cache.delete(key)

    def get(self, path, ua, accept='application/json'):
        return self.client.get(path, HTTP_USER_AGENT=ua, HTTP_ACCEPT=accept, HTTP_HOST='localhost')

    def test_app_ua_reaches_the_api(self):
        r = self.get('/api/auth/csrf/', 'ExamBridgeApp/1.0 (Android 14; ruby)')
        self.assertEqual(r.status_code, 200)
        self.assertIn('csrfToken', r.json())

    def test_app_ua_reaches_media(self):
        # nothing stored there: Django answers 404, not the blocker's 403
        r = self.get('/media/ielts/speaking/does-not-exist.m4a', APP, accept='*/*')
        self.assertEqual(r.status_code, 404)

    def test_okhttp_is_refused(self):
        r = self.get('/api/auth/csrf/', 'okhttp/4.9.2')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {'detail': 'Access denied.'})

    def test_browser_reaches_the_api(self):
        self.assertEqual(self.get('/api/auth/csrf/', BROWSER).status_code, 200)
