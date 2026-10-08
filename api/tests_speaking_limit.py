"""
Hidden daily speaking limit (api/speaking_limit.py): 3 IELTS + CEFR speaking tests per rolling
24 hours, then a 12-hour lock.

    python manage.py test api.tests_speaking_limit

Whisper, the GPT scorer and Celery are always mocked (a real network call fails the test), the
cache is a local in-memory one and recordings go to a temporary MEDIA_ROOT. Time travel patches
django.utils.timezone.now, so every timestamp (attempt starts, uses, locks) follows the clock.
"""
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from itertools import count
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api import speaking_limit
from cefr.models import CEFRSpeakingResponse, CEFRSpeakingTest
from ielts.models import IELTSAttempt, IELTSTest, SpeakingResponse, SpeakingTask, SpeakingUse
from speaking.models import SpeakingAttempt, SpeakingLesson

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'speaking-limit-tests'}}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36'
M4A = b'\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00' + b'\x00' * 500
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 3000
T0 = timezone.make_aware(datetime(2026, 10, 8, 9, 0, 0))          # 09:00 Tashkent
HOUR = timedelta(hours=1)
LIMIT_TEXT = 'Kunlik limitingiz tugadi. Limitingiz {} keyin ochiladi.'
_ips = count(1)
# what a normal response must never contain: the limit stays invisible until it is hit
LEAKS = {'remaining', 'limit', 'limits', 'uses', 'used', 'quota', 'left', 'retry_after_seconds', 'unlock_at',
         'speaking_limit', 'daily_limit', 'speaking_uses', 'locked_until', 'code'}


_real_now = timezone.now


class Clock:
    """A frozen, movable clock; now=None means real time."""
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now or _real_now()

    def advance(self, delta):
        self.now += delta


def m4a(i=0):
    return SimpleUploadedFile(f'answer_{i}.m4a', M4A, content_type='audio/mp4')


def keys_of(value):
    """Every dict key anywhere in a JSON value."""
    if isinstance(value, dict):
        return set(value) | set().union(*(keys_of(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys_of(v) for v in value)) if value else set()
    return set()


@override_settings(CACHES=LOCMEM, OPENAI_API_KEY='sk-test-never-used')
class SpeakingLimitBase(TestCase):
    user_kwargs = {}

    @classmethod
    def setUpTestData(cls):
        n = next(_ips)
        cls.user = get_user_model().objects.create(username=f'lim{n}', email=f'lim{n}@example.test', **cls.user_kwargs)
        cls.other = get_user_model().objects.create(username=f'oth{n}', email=f'oth{n}@example.test')
        cls.task = SpeakingTask.objects.create(title='Hometown', part=1, questions=['Where are you from?'])
        cls.legacy_cefr = SpeakingTask.objects.create(title='Family', part=1, source='CEFR', questions=['Tell me about your family.'])
        cls.ctest = CEFRSpeakingTest.objects.create(title='CEFR 1', part11=['What do you do?', 'Where do you live?'])

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix='speaking-limit-tests-')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        self.assertTrue(os.path.samefile(default_storage.location, self.media))

        # no real OpenAI / Celery call can happen
        for target in ('api.stt.requests.post', 'api.ielts_views.urllib.request.urlopen'):
            p = mock.patch(target, side_effect=AssertionError(f'real network call: {target}'))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch('api.ielts_views.whisper_transcribe', return_value='I am from Tashkent.')
        self.whisper = p.start()
        self.addCleanup(p.stop)
        for target in ('api.tasks.evaluate_cefr_speaking', 'speaking.views.score_attempt'):
            p = mock.patch(target)
            p.start()
            self.addCleanup(p.stop)

        self.clock = Clock(T0)
        p = mock.patch('django.utils.timezone.now', self.clock)
        p.start()
        self.addCleanup(p.stop)

        # a fresh address per test: the API rate limiter counts requests per IP
        self.ip = f'10.20.{next(_ips) % 250}.{next(_ips) % 250 + 1}'
        self.c = self.client_for(self.user)

    def client_for(self, user):
        c = APIClient(HTTP_USER_AGENT=UA, HTTP_ACCEPT='application/json', HTTP_HOST='localhost', REMOTE_ADDR=self.ip)
        c.force_authenticate(user)
        return c

    # ── flows ──────────────────────────────────────────────────────────────

    def ielts_start(self, task=None):
        return self.c.post('/api/ielts/attempt/start/', {'task_type': 'speaking', 'task_id': (task or self.task).id},
                           format='json')

    def ielts_submit(self, attempt_id, answers=None, files=None, task=None):
        answers = answers or [{'question': 'Where are you from?', 'transcript': 'I am from Samarkand.'}]
        data = {'task_id': str((task or self.task).id), 'transcripts': json.dumps(answers), **(files or {})}
        return self.c.post(f'/api/ielts/speaking/{attempt_id}/submit/', data, format='multipart')

    def ielts_test(self, task=None):
        """One full IELTS speaking test: start + submit. Returns the attempt id."""
        r = self.ielts_start(task)
        self.assertIn(r.status_code, (200, 201), r.content)
        attempt_id = r.json()['attempt_id']
        s = self.ielts_submit(attempt_id, task=task)
        self.assertEqual(s.status_code, 201, s.content)
        return attempt_id

    def cefr_start(self):
        return self.c.post(f'/api/cefr/speaking/{self.ctest.id}/start/')

    def cefr_submit(self, response_id):
        answers = [{'part': '1.1', 'q': 0, 'transcript': 'I am a student at a university.', 'seconds': 25}]
        return self.c.post(f'/api/cefr/speaking/responses/{response_id}/submit/', {'answers': json.dumps(answers)},
                           format='multipart')

    def cefr_test(self):
        r = self.cefr_start()
        self.assertEqual(r.status_code, 201, r.content)
        s = self.cefr_submit(r.json()['response_id'])
        self.assertEqual(s.status_code, 200, s.content)
        self.assertEqual(s.json()['status'], 'SCORING')
        return r.json()['response_id']

    def use_up(self, n=3):
        for _ in range(n):
            self.ielts_test()
            self.clock.advance(timedelta(minutes=10))

    def assert_limited(self, r, wait):
        self.assertEqual(r.status_code, 429, r.content)
        body = r.json()
        self.assertEqual(set(body), {'code', 'detail', 'retry_after_seconds', 'unlock_at'})
        self.assertEqual(body['code'], 'speaking_daily_limit')
        self.assertEqual(body['detail'], LIMIT_TEXT.format(wait))
        self.assertIsInstance(body['retry_after_seconds'], int)
        self.assertEqual(r['Retry-After'], str(body['retry_after_seconds']))
        return body


class LimitTests(SpeakingLimitBase):
    def test_three_tests_then_the_fourth_start_is_refused_for_12_hours(self):
        self.ielts_test()
        self.clock.advance(timedelta(minutes=20))
        self.ielts_test()
        self.clock.advance(timedelta(minutes=20))
        self.ielts_test()                                          # the 3rd test, at 09:40 → locked until 21:40
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 3)

        body = self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertEqual(body['retry_after_seconds'], 12 * 3600)
        self.assertEqual(body['unlock_at'], '2026-10-08T21:40:00+05:00')
        self.assert_limited(self.cefr_start(), '12 soatdan')       # CEFR is locked too
        self.assert_limited(self.ielts_start(self.legacy_cefr), '12 soatdan')
        # nothing was started while locked
        self.assertFalse(IELTSAttempt.objects.filter(user=self.user, status='IN_PROGRESS').exists())
        self.assertFalse(CEFRSpeakingResponse.objects.filter(user=self.user).exists())

    def test_twelve_hours_right_after_the_limit_in_real_time(self):
        self.clock.now = None
        for _ in range(3):
            self.ielts_test()
        body = self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertTrue(12 * 3600 - 60 < body['retry_after_seconds'] <= 12 * 3600)

    def test_time_travel_hours_minutes_and_unlock(self):
        self.use_up()                                              # 3rd use at 09:20 → lock until 21:20
        lock_at = self.clock.now - timedelta(minutes=10)
        self.clock.now = lock_at + 5 * HOUR
        self.assertEqual(self.assert_limited(self.ielts_start(), '7 soatdan')['retry_after_seconds'], 7 * 3600)
        self.clock.now = lock_at + 5 * HOUR + timedelta(minutes=1)            # 6 h 59 min left → 7
        self.assert_limited(self.cefr_start(), '7 soatdan')
        self.clock.now = lock_at + 11 * HOUR                                  # exactly 1 h left
        self.assert_limited(self.ielts_start(), '1 soatdan')
        self.clock.now = lock_at + 11 * HOUR + timedelta(minutes=30)
        self.assertEqual(self.assert_limited(self.ielts_start(), '30 daqiqadan')['retry_after_seconds'], 1800)
        self.clock.now = lock_at + 12 * HOUR - timedelta(seconds=20)
        self.assertEqual(self.assert_limited(self.cefr_start(), '1 daqiqadan')['retry_after_seconds'], 20)

        # the lock is over: a fresh allowance of three, then locked again for 12 hours
        self.clock.now = lock_at + 12 * HOUR
        self.ielts_test()
        self.cefr_test()
        self.ielts_test()
        body = self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertEqual(body['unlock_at'], timezone.localtime(lock_at + 24 * HOUR).isoformat(timespec='seconds'))

    def test_window_is_rolling_24_hours(self):
        self.ielts_test()                                          # 09:00
        self.clock.advance(20 * HOUR)
        self.ielts_test()                                          # 05:00 next day
        self.clock.advance(5 * HOUR)                               # the 09:00 one is out of the window now
        self.ielts_test()
        r = self.ielts_start()
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertIsNone(speaking_limit.locked_until(self.user))
        self.ielts_submit(r.json()['attempt_id'])                  # 3 within 24 h → locked
        self.assert_limited(self.ielts_start(), '12 soatdan')

    def test_ielts_and_cefr_share_one_limit(self):
        self.ielts_test()
        self.cefr_test()
        self.ielts_test(self.legacy_cefr)                          # legacy CEFR practice task on the IELTS endpoints
        self.assert_limited(self.cefr_start(), '12 soatdan')
        self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertEqual(sorted(SpeakingUse.objects.filter(user=self.user).values_list('kind', flat=True)),
                         ['cefr', 'ielts', 'ielts'])

    def test_other_users_are_not_affected(self):
        self.use_up()
        self.c = self.client_for(self.other)
        self.ielts_test()
        self.cefr_test()

    def test_uncounted_submit_is_refused_while_locked(self):
        self.use_up()
        attempt = IELTSAttempt.objects.create(user=self.user)      # started after the lock (e.g. a crafted client)
        r = self.ielts_submit(attempt.id, [{'question': 'Where are you from?', 'transcript': ''}], {'audio_0': m4a()})
        self.assert_limited(r, '12 soatdan')
        self.whisper.assert_not_called()                           # refused before any upload or Whisper call
        self.assertFalse(SpeakingResponse.objects.filter(attempt=attempt).exists())
        self.assertFalse(os.path.exists(os.path.join(self.media, 'ielts', 'speaking', f'attempt_{attempt.id}_q0.m4a')))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'IN_PROGRESS')

        cefr = CEFRSpeakingResponse.objects.create(user=self.user, test=self.ctest)
        self.assert_limited(self.cefr_submit(cefr.id), '12 soatdan')
        cefr.refresh_from_db()
        self.assertEqual(cefr.status, 'IN_PROGRESS')
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 3)

    def test_a_test_already_running_when_the_lock_began_may_be_submitted_once(self):
        running = [IELTSAttempt.objects.create(user=self.user) for _ in range(2)]   # tabs opened at 09:00
        other_test = CEFRSpeakingTest.objects.create(title='CEFR 2', part11=['What do you do?'])
        running_cefr = CEFRSpeakingResponse.objects.create(user=self.user, test=other_test)
        self.clock.advance(timedelta(minutes=5))
        for _ in range(3):                                         # CEFR tests at 09:05, 09:15, 09:25 → locked
            self.cefr_test()
            self.clock.advance(timedelta(minutes=10))
        self.clock.advance(timedelta(minutes=10))
        self.assertEqual(self.ielts_submit(running[0].id).status_code, 201)      # the grace test
        self.assert_limited(self.ielts_submit(running[1].id), '12 soatdan')     # only one per lock
        self.assert_limited(self.cefr_submit(running_cefr.id), '12 soatdan')
        self.assertEqual(self.ielts_submit(running[0].id).status_code, 201)      # its retry stays free
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 4)
        lock = SpeakingUse.objects.get(user=self.user, locked_until__isnull=False)
        self.assertEqual(lock.locked_until, T0 + timedelta(minutes=25) + 12 * HOUR)  # the grace test did not extend it

        # after the lock the count starts from zero: the grace test does not count either
        self.clock.now = lock.locked_until
        self.assertEqual(self.ielts_submit(running[1].id).status_code, 201)
        self.assertEqual(self.cefr_submit(running_cefr.id).status_code, 200)
        self.assertIsNone(speaking_limit.locked_until(self.user))
        self.ielts_test()
        self.assert_limited(self.ielts_start(), '12 soatdan')

    def test_resubmitting_a_counted_attempt_is_free_and_never_counted_again(self):
        first = self.ielts_test()
        for _ in range(3):                                         # network retries
            self.clock.advance(timedelta(minutes=5))
            self.assertEqual(self.ielts_submit(first).status_code, 201)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 1)
        # a full-test attempt sent task by task counts once
        full = IELTSAttempt.objects.create(user=self.user, test=IELTSTest.objects.create(title='Mock 1'))
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(full.id).status_code, 201)
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(full.id, task=self.legacy_cefr).status_code, 201)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 2)

        self.clock.advance(timedelta(minutes=5))
        self.ielts_test()                                          # the 3rd test: locked now
        self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertEqual(self.ielts_submit(first).status_code, 201)            # a retry of a counted attempt is allowed
        self.assertEqual(self.ielts_submit(full.id, task=self.legacy_cefr).status_code, 201)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 3)

    def test_practice_tasks_sharing_one_attempt_are_separate_tests(self):
        # two tabs opened before the first submit get the same IN_PROGRESS practice attempt
        a = self.ielts_start(self.task).json()['attempt_id']
        b = self.ielts_start(self.legacy_cefr).json()['attempt_id']
        self.assertEqual(a, b)
        family = [{'question': 'Tell me about your family.', 'transcript': 'There are four of us.'}]
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(a, task=self.task).status_code, 201)
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(b, family, task=self.legacy_cefr).status_code, 201)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 2)
        self.assertEqual(sorted(SpeakingUse.objects.filter(user=self.user).values_list('task_ref', flat=True)),
                         sorted([self.task.id, self.legacy_cefr.id]))
        # each one's retry stays free
        self.clock.advance(timedelta(minutes=1))
        self.assertEqual(self.ielts_submit(a, task=self.task).status_code, 201)
        self.assertEqual(self.ielts_submit(b, family, task=self.legacy_cefr).status_code, 201)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 2)

        # a third task on the same attempt is the 3rd test: locked
        extra = [SpeakingTask.objects.create(title=f'Extra {i}', part=1, questions=['Where are you from?'])
                 for i in range(3)]
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(a, task=extra[0]).status_code, 201)
        self.assert_limited(self.ielts_start(), '12 soatdan')
        # the attempt was running before the lock: one more task on it is the single grace test, no more
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.ielts_submit(a, task=extra[1]).status_code, 201)
        self.assert_limited(self.ielts_submit(a, task=extra[2]), '12 soatdan')
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 4)

    def test_unlock_at_is_never_earlier_than_the_real_unlock(self):
        self.clock.now = T0 + timedelta(microseconds=250_000)
        self.use_up()                                              # 3rd use at 09:20:00.25 → unlock 21:20:00.25
        body = self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assertEqual(body['unlock_at'], '2026-10-08T21:20:01+05:00')
        self.assertEqual(body['retry_after_seconds'], 11 * 3600 + 50 * 60)
        # one second before the real unlock: still the minutes text, at least 1
        self.clock.now = T0 + timedelta(minutes=20, microseconds=250_000) + 12 * HOUR - timedelta(milliseconds=300)
        body = self.assert_limited(self.cefr_start(), '1 daqiqadan')
        self.assertEqual(body['retry_after_seconds'], 1)
        self.clock.advance(timedelta(milliseconds=300))
        self.assertEqual(self.cefr_start().status_code, 201)

    def test_cefr_resubmit_is_free(self):
        rid = self.cefr_test()
        again = self.cefr_submit(rid)
        self.assertEqual((again.status_code, again.json()), (200, {'id': rid, 'status': 'SCORING'}))
        self.assertEqual(SpeakingUse.objects.filter(user=self.user, kind='cefr', ref_id=rid).count(), 1)
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 1)

    def test_an_old_attempt_reused_hours_later_is_a_new_test(self):
        first = self.ielts_test()                                  # 09:00
        self.clock.advance(timedelta(minutes=5))
        self.ielts_test()
        self.clock.advance(3 * HOUR)                               # past the 2-hour retry grace
        self.assertEqual(self.ielts_submit(first).status_code, 201)            # counts again → the 3rd test
        self.assertEqual(SpeakingUse.objects.filter(user=self.user, ref_id=first).count(), 2)
        self.assert_limited(self.ielts_start(), '12 soatdan')
        self.clock.advance(3 * HOUR)
        self.assert_limited(self.ielts_submit(first), '9 soatdan')             # and never a "running" grace test

    def test_failed_submission_does_not_use_the_allowance(self):
        attempt = IELTSAttempt.objects.create(user=self.user)
        self.c.raise_request_exception = False
        with mock.patch('api.ielts_views.default_storage.save', side_effect=OSError('disk full')),                 self.assertLogs('django.request', 'ERROR'):
            r = self.ielts_submit(attempt.id, files={'audio_0': m4a()})
        self.assertEqual(r.status_code, 500)
        cefr = CEFRSpeakingResponse.objects.create(user=self.user, test=self.ctest)
        with mock.patch('api.cefr_speaking.score_answers', side_effect=RuntimeError('boom')),                 self.assertLogs('django.request', 'ERROR'):
            r = self.c.post(f'/api/cefr/speaking/responses/{cefr.id}/submit/', {'answers': '[]'}, format='multipart')
        self.assertEqual(r.status_code, 500)
        self.assertFalse(SpeakingUse.objects.filter(user=self.user).exists())

    def test_staff_superusers_and_premium_are_limited_too(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True, is_superuser=True, is_premium=True)
        self.user.refresh_from_db()
        self.c = self.client_for(self.user)
        self.use_up()
        self.assert_limited(self.ielts_start(), '12 soatdan')
        self.assert_limited(self.cefr_start(), '12 soatdan')

    def test_unsaved_answers_cannot_be_scored_while_locked(self):
        self.use_up()
        saved = SpeakingResponse.objects.filter(attempt__user=self.user).first()
        txs = [{'question': 'Where are you from?', 'transcript': 'I am from Samarkand.'}]
        # no response (the no-attempt fallback) or somebody else's response → refused
        self.assert_limited(self.c.post('/api/ielts/speaking/analyze/', {'transcripts': txs}, format='json'), '12 soatdan')
        theirs = SpeakingResponse.objects.create(attempt=IELTSAttempt.objects.create(user=self.other), task=self.task)
        self.assert_limited(self.c.post('/api/ielts/speaking/analyze/', {'transcripts': txs, 'response_id': theirs.id},
                                        format='json'), '12 soatdan')
        # a saved (counted) response is still scored
        reply = mock.MagicMock()
        reply.__enter__.return_value.read.return_value = json.dumps({'choices': [{'message': {'content': json.dumps(
            {'overall_band': 6.0, 'fluency_coherence': {'band': 6}, 'answer_corrections': []})}}]}).encode()
        with mock.patch('api.ielts_views.urllib.request.urlopen', return_value=reply) as ai:
            r = self.c.post('/api/ielts/speaking/analyze/', {'transcripts': txs, 'response_id': saved.id}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ai.call_count, 1)

    def test_speaking_game_is_not_limited(self):
        self.use_up()
        self.assert_limited(self.ielts_start(), '12 soatdan')
        lesson = SpeakingLesson.objects.create(title='Reading 1', text='The cat sat on the mat. It was happy there.')
        clip = SimpleUploadedFile('reading.webm', WEBM, content_type='audio/webm')
        r = self.c.post(f'/api/games/speaking/lessons/{lesson.id}/attempts/', {'audio': clip, 'duration_sec': '9'},
                        format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(SpeakingAttempt.objects.filter(user=self.user).exists())
        self.assertEqual(SpeakingUse.objects.filter(user=self.user).count(), 3)

    def test_nothing_shows_the_limit_before_it_is_hit(self):
        bodies = []

        def check(r, expected_keys=None):
            self.assertNotEqual(r.status_code, 429)
            self.assertNotIn('Retry-After', r)
            self.assertFalse([h for h in r.headers if 'limit' in h.lower() or 'remaining' in h.lower()], r.headers)
            body = r.json()
            if expected_keys is not None:
                self.assertEqual(set(body), expected_keys)
            bodies.append(body)
            return body

        for _ in range(2):
            start = check(self.ielts_start(), {'attempt_id', 'resumed'})
            check(self.ielts_submit(start['attempt_id']), {'id', 'status', 'transcripts'})
            self.clock.advance(timedelta(minutes=5))
        cefr = check(self.cefr_start(), {'response_id'})
        check(self.cefr_submit(cefr['response_id']), {'id', 'status'})       # the 3rd test: still no hint
        check(self.c.get('/api/ielts/speaking/'))
        check(self.c.get('/api/ielts/speaking/?source=CEFR'))
        check(self.c.get('/api/cefr/speaking/'))
        check(self.c.get('/api/ielts/speaking/history/'))
        check(self.c.get(f'/api/cefr/speaking/responses/{cefr["response_id"]}/'))
        check(self.c.get('/api/auth/me/'))
        found = set().union(*(keys_of(b) for b in bodies)) & LEAKS
        self.assertFalse(found, f'limit details leaked: {found}')


class WhisperReuseTests(SpeakingLimitBase):
    def test_resubmission_reuses_the_stored_whisper_text(self):
        attempt = IELTSAttempt.objects.create(user=self.user)
        answers = [{'question': 'Where are you from?', 'transcript': ''}]
        first = self.ielts_submit(attempt.id, answers, {'audio_0': m4a()})
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.whisper.call_count, 1)

        for _ in range(2):                                         # the same answers again (a network retry)
            again = self.ielts_submit(attempt.id, answers, {'audio_0': m4a()})
            self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(self.whisper.call_count, 1)               # Whisper was not paid again
        answer = again.json()['transcripts'][0]
        self.assertEqual((answer['transcript'], answer['stt']), ('I am from Tashkent.', 'whisper'))
        self.assertTrue(answer['audio_url'].endswith(f'attempt_{attempt.id}_q0.m4a'))
        stored = SpeakingResponse.objects.get(attempt=attempt, task=self.task).transcripts[0]
        self.assertEqual((stored['transcript'], stored['stt']), ('I am from Tashkent.', 'whisper'))

    def test_only_answers_with_stored_text_are_reused(self):
        attempt = IELTSAttempt.objects.create(user=self.user)
        self.whisper.side_effect = ['', 'Second answer.']          # the first clip was silent the first time
        answers = [{'question': 'Where are you from?', 'transcript': ''},
                   {'question': 'Do you like it?', 'transcript': ''}]
        self.ielts_submit(attempt.id, answers, {'audio_0': m4a(0), 'audio_1': m4a(1)})
        self.assertEqual(self.whisper.call_count, 2)

        self.whisper.side_effect = ['Now it is heard.']
        again = self.ielts_submit(attempt.id, answers, {'audio_0': m4a(0), 'audio_1': m4a(1)})
        self.assertEqual(self.whisper.call_count, 3)               # only the answer without stored text
        self.assertEqual([t['transcript'] for t in again.json()['transcripts']], ['Now it is heard.', 'Second answer.'])

        # a different question at the same position is a different answer: transcribed
        self.whisper.side_effect = None
        self.whisper.return_value = 'Another one.'
        changed = [{'question': 'What is your job?', 'transcript': ''}, answers[1]]
        r = self.ielts_submit(attempt.id, changed, {'audio_0': m4a(0), 'audio_1': m4a(1)})
        self.assertEqual(self.whisper.call_count, 4)
        self.assertEqual([t['transcript'] for t in r.json()['transcripts']], ['Another one.', 'Second answer.'])

    def test_browser_text_always_wins(self):
        attempt = IELTSAttempt.objects.create(user=self.user)
        self.ielts_submit(attempt.id, [{'question': 'Where are you from?', 'transcript': ''}], {'audio_0': m4a()})
        r = self.ielts_submit(attempt.id, [{'question': 'Where are you from?', 'transcript': 'Typed by the browser.'}],
                              {'audio_0': m4a()})
        self.assertEqual(r.json()['transcripts'][0]['transcript'], 'Typed by the browser.')
        self.assertNotIn('stt', r.json()['transcripts'][0])
        self.assertEqual(self.whisper.call_count, 1)


@override_settings(CACHES=LOCMEM)
class ConcurrencyTests(TransactionTestCase):
    """Parallel submits of one user are decided one after another (row lock on the user)."""

    def test_parallel_submits_never_exceed_the_limit(self):
        if connection.vendor != 'postgresql':
            self.skipTest('needs a database with row locks')
        user = get_user_model().objects.create(username='race', email='race@example.test')
        now = timezone.now()
        for ref in (1, 2):
            SpeakingUse.objects.create(user=user, kind='ielts', ref_id=900_000 + ref, used_at=now - timedelta(minutes=30 - ref))
        attempts = [IELTSAttempt.objects.create(user=user) for _ in range(4)]   # four tabs, all started before
        results, barrier = [], threading.Barrier(len(attempts))

        def submit(attempt):
            try:
                barrier.wait()
                refused, _ = speaking_limit.claim(user, speaking_limit.IELTS, attempt.id, attempt.started_at)
                results.append('refused' if refused is not None else 'ok')
            finally:
                connection.close()

        threads = [threading.Thread(target=submit, args=(a,)) for a in attempts]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        # the 3rd test locks; one test already running may still finish; the rest are refused
        self.assertEqual(sorted(results), ['ok', 'ok', 'refused', 'refused'])
        self.assertEqual(SpeakingUse.objects.filter(user=user).count(), 4)
        self.assertEqual(SpeakingUse.objects.filter(user=user, locked_until__isnull=False).count(), 1)
