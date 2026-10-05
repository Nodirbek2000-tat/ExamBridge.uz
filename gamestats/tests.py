"""
gamestats: the admin config PATCH (RUNNER_PLAN §B8.3 "Runner config") and the 0003 migration.

    python manage.py test gamestats
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .configs import RUNNER_DEFAULTS, game_config, validate_runner_config, ConfigError
from .models import Game

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'gamestats-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
URL = '/api/games/stats/admin/games/{}/'


@override_settings(CACHES=LOCMEM)
class ConfigPatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create(username='gs_staff', email='gs_staff@example.test', is_staff=True)
        cls.learner = User.objects.create(username='gs_learner', email='gs_learner@example.test')

    def setUp(self):
        self.api = APIClient(**H)
        self.api.force_authenticate(self.staff)

    def patch(self, slug, body):
        return self.api.patch(URL.format(slug), body, format='json')

    def test_migration_seeded_and_renamed_the_runner(self):
        g = Game.objects.get(slug='runner')
        self.assertEqual(g.title, 'TOBY RUN')
        self.assertEqual(g.config, {})

    def test_get_shows_defaults_and_effective(self):
        r = self.api.get(URL.format('runner'))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['config'], {})
        self.assertEqual(d['defaults'], RUNNER_DEFAULTS)
        self.assertEqual(d['effective'], RUNNER_DEFAULTS)
        self.assertEqual(d['slug'], 'runner')

    def test_merge_and_reset(self):
        r = self.patch('runner', {'config': {'speed_scale': 1.1, 'clips_per_run': 20}})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.patch('runner', {'config': {'server_stt': False, 'levels': ['B1', 'A1']}})
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['config'], {'speed_scale': 1.1, 'clips_per_run': 20, 'server_stt': False, 'levels': ['A1', 'B1']})
        self.assertEqual(d['effective']['window_scale'], 1.0)
        self.assertFalse(d['effective']['server_stt'])
        self.assertEqual(game_config('runner')['clips_per_run'], 20)
        r = self.patch('runner', {'config': {'clips_per_run': None, 'station_every_m': 900}})
        self.assertEqual(r.status_code, 200)
        g = Game.objects.get(slug='runner')
        self.assertNotIn('clips_per_run', g.config)
        self.assertEqual(g.config['station_every_m'], 900)
        self.assertEqual(game_config('runner')['clips_per_run'], 30)      # the cache was dropped on PATCH
        self.assertEqual(game_config('runner')['station_every_m'], 900)

    def test_rejects_bad_config(self):
        bad = [
            {'nope': 1},
            {'speed_scale': 1.5},
            {'speed_scale': '1.0'},
            {'window_scale': 0.5},
            {'balloon_gap_scale': 2},
            {'station_every_m': 100},
            {'station_every_m': 700.5},
            {'clips_per_run': 61},
            {'clips_per_run': True},
            {'server_stt': 'yes'},
            {'twister': 1},
            {'levels': []},
            {'levels': ['A1', 'C2']},
            {'levels': ['A1', 'A1']},
            {'levels': 'A1'},
            {'speed_scale': float('nan')},
        ]
        for body in bad:
            with self.subTest(body=body):
                if body.get('speed_scale') != body.get('speed_scale'):       # NaN cannot travel as JSON
                    with self.assertRaises(ConfigError):
                        validate_runner_config(body)
                    continue
                r = self.patch('runner', {'config': body})
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn('error', r.json())
        self.assertEqual(self.patch('runner', {'config': [1]}).status_code, 400)
        self.assertEqual(Game.objects.get(slug='runner').config, {})

    def test_game_without_a_schema(self):
        r = self.patch('speaking', {'config': {'a': 1}})
        self.assertEqual(r.status_code, 400)

    def test_status_and_order_still_work(self):
        r = self.patch('runner', {'status': 'live', 'order': 15})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'slug': 'runner', 'title': 'TOBY RUN', 'status': 'live', 'order': 15})
        self.assertEqual(self.patch('runner', {}).status_code, 400)
        self.assertEqual(self.patch('runner', {'status': 'gone'}).status_code, 400)

    def test_staff_only(self):
        api = APIClient(**H)
        api.force_authenticate(self.learner)
        self.assertEqual(api.patch(URL.format('runner'), {'config': {}}, format='json').status_code, 403)
        self.assertEqual(api.get(URL.format('runner')).status_code, 403)
