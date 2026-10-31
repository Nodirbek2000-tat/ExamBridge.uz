"""Admin dashboard statistics and last-seen tracking."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import UserDailyVisit
from cefr.models import CEFRAttempt
from ielts.models import IELTSAttempt, IELTSTest
from tests_app.models import Test, TestAttempt, TestResult

User = get_user_model()

HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def make_user(email, staff=False):
    u = User.objects.create_user(username=email, email=email, password='x')
    u.is_staff = staff
    u.is_superuser = staff
    u.save()
    return u


def auth(user):
    return {**HEADERS, 'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}


def make_sat_test():
    return Test.objects.create(year=2024, month=11, form='A')


@override_settings(CACHES=LOCMEM)
class StatsBase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user('admin@test.uz', staff=True)
        self.student = make_user('student@test.uz')


class LastSeenTest(StatsBase):
    def test_jwt_request_records_last_seen_and_visit(self):
        res = self.client.get('/api/auth/ping/', **auth(self.student))
        self.assertEqual(res.status_code, 204)

        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.last_seen_at)
        self.assertEqual(UserDailyVisit.objects.filter(user=self.student).count(), 1)

    def test_second_request_inside_a_minute_writes_nothing(self):
        self.client.get('/api/auth/ping/', **auth(self.student))
        self.student.refresh_from_db()
        first = self.student.last_seen_at

        self.client.get('/api/auth/ping/', **auth(self.student))
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_seen_at, first)

    def test_new_day_adds_a_visit(self):
        yesterday = timezone.now() - timedelta(days=1)
        User.objects.filter(pk=self.student.pk).update(last_seen_at=yesterday)

        self.client.get('/api/auth/ping/', **auth(self.student))
        self.assertEqual(UserDailyVisit.objects.filter(user=self.student).count(), 1)

    def test_anonymous_request_is_not_recorded(self):
        self.client.get('/api/auth/ping/', **HEADERS)
        self.assertFalse(UserDailyVisit.objects.exists())


class LiveStatsTest(StatsBase):
    def test_counts_online_and_who_is_in_a_test(self):
        test = make_sat_test()
        TestAttempt.objects.create(user=self.student, test=test)
        User.objects.filter(pk=self.student.pk).update(last_seen_at=timezone.now())

        data = self.client.get('/api/system/live/', **auth(self.admin)).json()

        # recorded after the view runs, so the admin shows up from their next request
        self.assertEqual(data['online_now'], 1)
        self.assertEqual(data['in_test_now'], 1)
        row = next(u for u in data['users'] if u['email'] == 'student@test.uz')
        self.assertEqual(row['activity']['section'], 'SAT')

    def test_someone_gone_for_ten_minutes_is_offline(self):
        gone = timezone.now() - timedelta(minutes=10)
        User.objects.filter(pk=self.student.pk).update(last_seen_at=gone)

        data = self.client.get('/api/system/live/', **auth(self.admin)).json()
        self.assertNotIn('student@test.uz', [u['email'] for u in data['users']])

    def test_stale_in_progress_attempt_is_not_a_running_test(self):
        attempt = TestAttempt.objects.create(user=self.student, test=make_sat_test())
        TestAttempt.objects.filter(pk=attempt.pk).update(
            started_at=timezone.now() - timedelta(days=2),
        )
        User.objects.filter(pk=self.student.pk).update(last_seen_at=timezone.now())

        data = self.client.get('/api/system/live/', **auth(self.admin)).json()
        self.assertEqual(data['in_test_now'], 0)

    def test_students_are_refused(self):
        res = self.client.get('/api/system/live/', **auth(self.student))
        self.assertEqual(res.status_code, 403)


class AnalyticsTest(StatsBase):
    def test_mock_and_practice_are_counted_apart(self):
        test = make_sat_test()
        TestAttempt.objects.create(user=self.student, test=test, status='COMPLETED',
                                   finished_at=timezone.now())
        TestAttempt.objects.create(user=self.student, test=test, status='COMPLETED',
                                   is_individual=True, finished_at=timezone.now())
        TestAttempt.objects.create(user=self.student, test=test)
        IELTSAttempt.objects.create(user=self.student, status='COMPLETED')
        CEFRAttempt.objects.create(user=self.student)

        data = self.client.get('/api/system/analytics/?days=7', **auth(self.admin)).json()

        sat = next(s for s in data['sections'] if s['section'] == 'SAT')
        self.assertEqual((sat['started'], sat['completed']), (2, 1))
        self.assertEqual(sat['practice_completed'], 1)
        self.assertEqual(data['mocks']['completed_today'], 1)
        self.assertEqual(data['mocks']['practice_completed'], 2)
        self.assertEqual(len(data['trend']), 7)
        self.assertEqual(data['trend'][-1]['completed'], 3)
        self.assertEqual(sum(data['hours']), 5)
        self.assertEqual(data['funnel']['completed_two_plus'], 1)

    def test_completed_without_finished_at_still_counts(self):
        TestAttempt.objects.create(user=self.student, test=make_sat_test(), status='COMPLETED')

        data = self.client.get('/api/system/analytics/', **auth(self.admin)).json()
        self.assertEqual(data['mocks']['completed_today'], 1)
        self.assertEqual(data['days'], 30)

    def test_unknown_days_falls_back_to_thirty(self):
        data = self.client.get('/api/system/analytics/?days=abc', **auth(self.admin)).json()
        self.assertEqual(data['days'], 30)


class MockStatsTest(StatsBase):
    def test_counts_unique_users_and_average(self):
        test = make_sat_test()
        other = make_user('other@test.uz')
        for user, score in ((self.student, 1200), (other, 1400)):
            attempt = TestAttempt.objects.create(user=user, test=test, status='COMPLETED')
            TestResult.objects.create(attempt=attempt, user=user, total_score=score)
        TestAttempt.objects.create(user=self.student, test=test)
        TestAttempt.objects.create(user=make_user('mod@test.uz'), test=test, is_individual=True)

        data = self.client.get('/api/system/mocks/?section=SAT', **auth(self.admin)).json()

        row = data['results'][0]
        self.assertEqual(row['unique_users'], 2)
        self.assertEqual(row['completed_users'], 2)
        self.assertEqual(row['started'], 3)
        self.assertEqual(row['completed'], 2)
        self.assertEqual(row['avg_score'], 1300)

    def test_mock_nobody_took_is_listed_with_zeros(self):
        IELTSTest.objects.create(title='Empty')

        data = self.client.get('/api/system/mocks/?section=ielts', **auth(self.admin)).json()
        self.assertEqual(data['results'][0]['unique_users'], 0)
        self.assertIsNone(data['results'][0]['avg_score'])

    def test_unknown_section_is_refused(self):
        res = self.client.get('/api/system/mocks/?section=NOPE', **auth(self.admin))
        self.assertEqual(res.status_code, 400)

    def test_attempts_of_one_mock(self):
        test = make_sat_test()
        attempt = TestAttempt.objects.create(user=self.student, test=test, status='COMPLETED')
        TestResult.objects.create(attempt=attempt, user=self.student, total_score=1250)
        TestAttempt.objects.create(user=self.student, test=test, is_individual=True)

        data = self.client.get(f'/api/system/mocks/SAT/{test.id}/', **auth(self.admin)).json()

        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['score'], 1250)
        self.assertEqual(data['results'][0]['user']['email'], 'student@test.uz')


class VisitorStatsTest(StatsBase):
    def test_newest_visitor_first_with_their_last_test(self):
        now = timezone.now()
        other = make_user('other@test.uz')
        User.objects.filter(pk=self.student.pk).update(last_seen_at=now)
        User.objects.filter(pk=other.pk).update(last_seen_at=now - timedelta(hours=5))
        TestAttempt.objects.create(user=self.student, test=make_sat_test(), status='COMPLETED')

        data = self.client.get('/api/system/visitors/', **auth(self.admin)).json()

        emails = [r['email'] for r in data['results']]
        self.assertLess(emails.index('student@test.uz'), emails.index('other@test.uz'))
        row = data['results'][emails.index('student@test.uz')]
        self.assertTrue(row['online'])
        self.assertEqual(row['tests_completed'], 1)
        self.assertEqual(row['last_test']['section'], 'SAT')

    def test_last_login_stands_in_before_tracking_began(self):
        User.objects.filter(pk=self.student.pk).update(last_login=timezone.now())

        data = self.client.get('/api/system/visitors/', **auth(self.admin)).json()
        row = next(r for r in data['results'] if r['email'] == 'student@test.uz')
        self.assertFalse(row['online'])
        self.assertIsNotNone(row['last_seen'])

    def test_search(self):
        for u in (self.student, self.admin):
            User.objects.filter(pk=u.pk).update(last_seen_at=timezone.now())

        data = self.client.get('/api/system/visitors/?q=STUDENT', **auth(self.admin)).json()
        self.assertEqual([r['email'] for r in data['results']], ['student@test.uz'])
