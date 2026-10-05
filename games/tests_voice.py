"""
Voice games platform (P0 contract): record_run parity, the ranked leaderboard filter, the
runner slug, and the transcribe caps (global daily cap + per-game counter).

    python manage.py test games.tests_voice
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from gamestats.models import GameDay

from . import stt_views
from .models import VOICE_GAME_SLUGS, VoiceGameProgress, VoiceGameRun
from .runs import find_run, record_run

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'voice-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')


@override_settings(CACHES=LOCMEM)
class RunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.u1 = User.objects.create(username='vr1', email='vr1@example.test', first_name='Nodirbek', last_name='Shukurov')
        cls.u2 = User.objects.create(username='vr2', email='vr2@example.test', first_name='Ali', last_name='Valiyev')

    def api(self, user):
        c = APIClient(**H)
        c.force_authenticate(user)
        return c

    def test_runner_is_a_voice_game(self):
        self.assertIn('runner', VOICE_GAME_SLUGS)
        c = self.api(self.u1)
        r = c.put('/api/games/voice/runner/progress/', {'data': {'v': 1, 'coins': 10}}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get('/api/games/voice/runner/progress/').json()['data'], {'v': 1, 'coins': 10})

    def test_run_create_behaves_as_before(self):
        c = self.api(self.u1)
        r = c.post('/api/games/voice/tobys-day/runs/', {'score': 120, 'stars': 3, 'accuracy': 85, 'level': 'Kitchen',
                                                        'duration_sec': 61.4, 'lines_said': 7}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['best_score'], 120)
        r = c.post('/api/games/voice/tobys-day/runs/', {'score': 90, 'stars': 2, 'accuracy': 0.5}, format='json')
        self.assertEqual(r.json()['best_score'], 120)
        run = VoiceGameRun.objects.get(pk=r.json()['id'])
        self.assertEqual((run.score, run.stars, run.accuracy, run.ref, run.ranked, run.meta), (90, 2, 0.5, '', True, {}))
        first = VoiceGameRun.objects.filter(user=self.u1).order_by('id').first()
        self.assertEqual((first.accuracy, first.level, first.duration_sec, first.lines_said), (0.85, 'Kitchen', 61, 7))
        p = VoiceGameProgress.objects.get(user=self.u1, slug='tobys-day')
        self.assertEqual((p.plays, p.stars_total, p.best_score), (2, 5, 120))
        self.assertEqual(GameDay.objects.get(user=self.u1, slug='tobys-day').plays, 2)     # count_play still called
        r = c.post('/api/games/voice/tobys-day/runs/', {'score': -5, 'stars': 'x', 'accuracy': 'nan'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(VoiceGameRun.objects.get(pk=r.json()['id']).score, 0)
        self.assertEqual(c.post('/api/games/voice/nope/runs/', {}, format='json').status_code, 404)

    def test_record_run_ref_is_idempotent(self):
        run, best = record_run(self.u1, 'runner', score=500, stars=1, accuracy=0.5, level='A2', duration_sec=100,
                               lines_said=12, ref='abc123', ranked=False, meta={'mode': 'listen'})
        self.assertEqual(best, 500)
        self.assertEqual((run.ref, run.ranked, run.meta), ('abc123', False, {'mode': 'listen'}))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                record_run(self.u1, 'runner', score=900, stars=1, accuracy=0.5, level='A2', duration_sec=100,
                           lines_said=12, ref='abc123')
        self.assertEqual(find_run(self.u1, 'runner', 'abc123').pk, run.pk)
        self.assertIsNone(find_run(self.u1, 'runner', ''))
        p = VoiceGameProgress.objects.get(user=self.u1, slug='runner')
        self.assertEqual((p.plays, p.best_score), (1, 500))          # the duplicate rolled back completely
        # empty refs never collide; the same ref for another learner is fine
        record_run(self.u1, 'runner', score=1, stars=0, accuracy=0, level='', duration_sec=1, lines_said=0)
        record_run(self.u1, 'runner', score=1, stars=0, accuracy=0, level='', duration_sec=1, lines_said=0)
        record_run(self.u2, 'runner', score=1, stars=0, accuracy=0, level='', duration_sec=1, lines_said=0, ref='abc123')
        self.assertEqual(VoiceGameRun.objects.filter(slug='runner').count(), 4)

    def test_leaderboard_ranks_only_ranked_runs(self):
        record_run(self.u1, 'runner', score=800, stars=0, accuracy=0, level='A1', duration_sec=60, lines_said=5)
        record_run(self.u2, 'runner', score=700, stars=0, accuracy=0, level='A1', duration_sec=60, lines_said=5)
        record_run(self.u2, 'runner', score=9999, stars=0, accuracy=0, level='A1', duration_sec=60, lines_said=5,
                   ranked=False, meta={'flags': ['score-mismatch']})
        d = self.api(self.u2).get('/api/games/voice/runner/leaderboard/').json()
        self.assertEqual([(t['name'], t['score']) for t in d['top']], [('Nodirbek S.', 800), ('Ali V.', 700)])
        self.assertEqual(d['me']['best_score'], 700)
        self.assertEqual(d['me']['rank'], 2)
        self.assertEqual(d['me']['best_all_time'], 9999)


@override_settings(CACHES=LOCMEM, VOICE_STT_GLOBAL_DAILY=3)
class TranscribeCapTests(TestCase):
    URL = '/api/games/voice/transcribe/'

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.u1 = User.objects.create(username='st1', email='st1@example.test')
        cls.u2 = User.objects.create(username='st2', email='st2@example.test')

    def setUp(self):
        cache.clear()
        p = mock.patch('games.stt_views.whisper_transcribe', return_value=' hello ')
        self.whisper = p.start()
        self.addCleanup(p.stop)

    def post(self, user, game=None):
        c = APIClient(**H)
        c.force_authenticate(user)
        data = {'audio': SimpleUploadedFile('c.webm', b'x' * 2000, content_type='audio/webm')}
        if game is not None:
            data['game'] = game
        return c.post(self.URL, data, format='multipart')

    def test_game_counter(self):
        r = self.post(self.u1, 'runner')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'text': 'hello', 'remaining': stt_views.DAILY_FREE - 1})
        self.post(self.u1, 'runner')
        self.post(self.u1, 'not-a-game')
        self.assertEqual(cache.get(stt_views.game_key('runner')), 2)
        self.assertIsNone(cache.get(stt_views.game_key('not-a-game')))
        self.assertEqual(cache.get(stt_views.global_key()), 3)

    def test_global_cap(self):
        for _ in range(2):
            self.assertEqual(self.post(self.u1).status_code, 200)
        self.assertEqual(self.post(self.u2, 'voice-drive').status_code, 200)
        self.assertEqual(stt_views.clips_left(self.u2), 0)
        r = self.post(self.u2, 'voice-drive')
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json(), {'error': 'global-limit', 'remaining': 0})
        self.assertEqual(cache.get(stt_views.user_key(self.u2.id)), 1)       # the refused clip cost the learner nothing
        self.assertEqual(cache.get(stt_views.global_key()), 3)
        self.assertEqual(cache.get(stt_views.game_key('voice-drive')), 1)
        self.assertEqual(self.whisper.call_count, 3)

    @override_settings(VOICE_STT_GLOBAL_DAILY=15000)
    def test_failed_call_is_not_counted(self):
        self.whisper.side_effect = RuntimeError('down')
        with self.assertLogs('games.stt_views', 'WARNING'):
            r = self.post(self.u1, 'runner')
        self.assertEqual(r.status_code, 502)
        self.assertEqual(cache.get(stt_views.user_key(self.u1.id)), 0)
        self.assertEqual(cache.get(stt_views.global_key()), 0)
        self.assertEqual(cache.get(stt_views.game_key('runner')), 0)
        self.assertEqual(stt_views.clips_left(self.u1), stt_views.DAILY_FREE)

    @override_settings(VOICE_STT_GLOBAL_DAILY=15000)
    def test_user_cap_unchanged(self):
        cache.set(stt_views.user_key(self.u1.id), stt_views.DAILY_FREE, 3600)
        r = self.post(self.u1)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['remaining'], 0)
        self.assertNotEqual(r.json()['error'], 'global-limit')
        self.assertIsNone(cache.get(stt_views.global_key()))
        self.assertEqual(stt_views.clips_left(self.u1), 0)

    def test_default_cap_when_the_setting_is_missing(self):
        with self.settings():
            from django.conf import settings
            del settings.VOICE_STT_GLOBAL_DAILY
            self.assertEqual(stt_views.global_daily_cap(), 15000)
