"""Staff leaderboards of the Speaking game: manage.py test speaking"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from .admin_views import _has_engine
from .models import SpeakingAttempt, SpeakingLesson

User = get_user_model()
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'speaking-admin-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
URL = '/api/games/speaking/admin/leaders/'
TEXT = 'I wake up at seven every day and I eat bread for breakfast.'


def learner(name):
    return User.objects.create(username=name, email=f'{name}@example.test', first_name=name.title())


def attempt(user, lesson, accuracy, days_ago=0, status=SpeakingAttempt.READY, engine=None):
    a = SpeakingAttempt.objects.create(user=user, lesson=lesson, accuracy=accuracy, status=status,
                                       **({'engine': engine} if engine and _has_engine() else {}))
    # created_at is auto_now_add: set the time afterwards (minutes keep same-day attempts in order)
    SpeakingAttempt.objects.filter(pk=a.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago, minutes=SpeakingAttempt.objects.count()))
    return a


def names(rows):
    return [r['name'] for r in rows]


@override_settings(CACHES=LOCMEM)
class LeadersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create(username='sp_staff', email='sp_staff@example.test', is_staff=True)
        cls.a1 = SpeakingLesson.objects.create(title='Morning', level='A1', text=TEXT)
        cls.a1b = SpeakingLesson.objects.create(title='Evening', level='A1', text=TEXT)
        cls.b1 = SpeakingLesson.objects.create(title='Habits', level='B1', text=TEXT)

    def setUp(self):
        self.api = APIClient(**H)
        self.api.force_authenticate(self.staff)

    def get(self, **params):
        res = self.api.get(URL, params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_only_staff_may_see_it(self):
        self.assertIn(APIClient(**H).get(URL).status_code, (401, 403))
        api = APIClient(**H)
        api.force_authenticate(learner('ali'))
        self.assertEqual(api.get(URL).status_code, 403)

    def test_best_board_orders_by_best_then_attempts_then_who_got_there_first(self):
        ali, vali, gani, sami, bad = (learner(n) for n in ('ali', 'vali', 'gani', 'sami', 'bad'))
        attempt(ali, self.a1, 90, days_ago=9)                       # 90 once
        first_90 = attempt(vali, self.a1, 90, days_ago=8)           # 90 with 3 attempts, reached on day 8
        attempt(vali, self.a1b, 70, days_ago=7)
        last = attempt(vali, self.a1, 90, days_ago=1)
        attempt(gani, self.a1, 60, days_ago=9)                      # 90 with 3 attempts, reached later
        attempt(gani, self.a1, 90, days_ago=6)
        attempt(gani, self.a1b, 80, days_ago=2)
        attempt(sami, self.a1, 95, days_ago=5, engine='azure')      # the best
        attempt(sami, self.a1, 50, days_ago=0, engine='whisper')
        attempt(bad, self.a1, 100, status=SpeakingAttempt.FAILED)   # only READY attempts count
        attempt(bad, self.a1, 99, status=SpeakingAttempt.PROCESSING)

        best = self.get()['boards']['A1']['best']
        self.assertEqual(names(best), ['Sami', 'Vali', 'Gani', 'Ali'])
        self.assertEqual([r['rank'] for r in best], [1, 2, 3, 4])
        self.assertEqual([r['best_accuracy'] for r in best], [95, 90, 90, 90])
        self.assertEqual([r['attempts'] for r in best], [2, 3, 3, 1])
        self.assertEqual(best[0]['engine'], 'azure' if _has_engine() else 'whisper')
        self.assertEqual(best[0]['email'], 'sami@example.test')
        self.assertFalse(best[0]['is_staff'])
        # Vali's best is the first 90 (day 8), the last attempt is day 1
        first_90.refresh_from_db()
        last.refresh_from_db()
        self.assertEqual(parse_datetime(best[1]['best_at']), first_90.created_at)
        self.assertEqual(parse_datetime(best[1]['last_attempt']), last.created_at)

    def test_boards_are_per_level(self):
        ali, vali = learner('ali'), learner('vali')
        attempt(ali, self.a1, 70)
        attempt(ali, self.b1, 40)
        attempt(vali, self.b1, 85)
        data = self.get()
        self.assertEqual(names(data['boards']['A1']['best']), ['Ali'])
        self.assertEqual(names(data['boards']['B1']['best']), ['Vali', 'Ali'])
        self.assertEqual(data['boards']['B1']['best'][1]['best_accuracy'], 40)
        self.assertEqual(data['boards']['C2'], {'best': [], 'active': [], 'active_30d': []})
        levels = {r['level']: r for r in data['levels']}
        self.assertEqual(list(levels), ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'])
        self.assertEqual((levels['B1']['learners'], levels['B1']['attempts']), (2, 2))
        self.assertEqual(levels['A2']['attempts'], 0)

        one = self.get(level='b1')
        self.assertEqual(list(one['boards']), ['B1'])
        self.assertEqual(len(one['levels']), 6)
        self.assertEqual(self.api.get(URL, {'level': 'X9'}).status_code, 400)

    def test_active_boards_all_time_and_last_30_days(self):
        ali, vali, gani = learner('ali'), learner('vali'), learner('gani')
        for _ in range(4):
            attempt(ali, self.a1, 50, days_ago=40)                  # most active, but long ago
        attempt(vali, self.a1, 60, days_ago=40)
        attempt(vali, self.a1, 75, days_ago=3)
        attempt(vali, self.a1b, 65, days_ago=2)
        attempt(gani, self.a1, 90, days_ago=1)
        attempt(gani, self.a1, 10, days_ago=1, status=SpeakingAttempt.FAILED)
        board = self.get()['boards']['A1']
        self.assertEqual(names(board['active']), ['Ali', 'Vali', 'Gani'])
        self.assertEqual([r['attempts'] for r in board['active']], [4, 3, 1])
        recent = board['active_30d']
        self.assertEqual(names(recent), ['Vali', 'Gani'])
        self.assertEqual([r['attempts'] for r in recent], [2, 1])
        self.assertEqual(recent[0]['best_accuracy'], 75)            # the best of those 30 days
        levels = {r['level']: r for r in self.get()['levels']}
        self.assertEqual((levels['A1']['attempts_30d'], levels['A1']['learners_30d']), (3, 2))

    def test_ten_rows_and_the_same_number_of_queries_however_many_learners(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                data = self.get(level='A1')
            return len(ctx.captured_queries), data

        for k in range(3):
            attempt(learner(f'first{k}'), self.a1, 50 + k)
        before, _ = count()
        for k in range(12):
            attempt(learner(f'more{k}'), self.a1, 60 + k)
            attempt(learner(f'more{k}'.replace('more', 'old')), self.a1, 30, days_ago=60)
        after, data = count()
        self.assertEqual(before, after)
        board = data['boards']['A1']
        self.assertEqual(len(board['best']), 10)
        self.assertEqual(len(board['active']), 10)
        self.assertEqual(len(board['active_30d']), 10)
        self.assertEqual(board['best'][0]['best_accuracy'], 71)
