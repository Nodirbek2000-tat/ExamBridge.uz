"""Admin tiers (super / 1 / 2 / 3) — access control tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver

from accounts import admin_access

User = get_user_model()

HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


def make_admin(email, level, superuser=False):
    u = User.objects.create_user(username=email, email=email, password='x')
    u.is_staff = level > 0 or superuser
    u.is_superuser = superuser
    u.admin_level = level
    u.save()
    return u


class AdminUrlCoverageTest(TestCase):
    """Every admin API path must be claimed by the tier table.

    Without this, adding a new /api/admin/... endpoint would silently be
    reachable by every tier.
    """

    def _all_paths(self):
        out = []

        def walk(patterns, prefix=''):
            for p in patterns:
                if hasattr(p, 'url_patterns'):
                    walk(p.url_patterns, prefix + str(p.pattern))
                else:
                    out.append(prefix + str(p.pattern))

        walk(get_resolver().url_patterns)
        return out

    def test_every_admin_path_has_an_area(self):
        missed = []
        for raw in self._all_paths():
            path = '/' + raw.lstrip('^/')
            looks_admin = (
                path.startswith('api/admin/') or path.startswith('/api/admin/')
                or '/admin/' in path and path.startswith('/api/')
                or path.startswith('/api/import/')
                or path.startswith('/api/system/')
            )
            if not looks_admin:
                continue
            if admin_access.area_for_path(path) is None:
                missed.append(path)
        self.assertEqual(missed, [], f'Tier table misses admin paths: {missed}')


class TierAccessTest(TestCase):
    def setUp(self):
        self.owner = make_admin('owner@t.uz', 1, superuser=True)
        self.l1 = make_admin('l1@t.uz', 1)
        self.l2 = make_admin('l2@t.uz', 2)
        self.l3 = make_admin('l3@t.uz', 3)
        self.victim = User.objects.create_user(
            username='v@t.uz', email='v@t.uz', password='x'
        )

    def as_(self, user):
        self.client.force_login(user)

    # ── Level 2: sees content, cannot change it ──────────────────────────────
    def test_level2_can_read_users(self):
        self.as_(self.l2)
        res = self.client.get('/api/admin/users/', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)

    def test_level2_cannot_delete_user(self):
        self.as_(self.l2)
        res = self.client.delete(f'/api/admin/users/{self.victim.id}/', **HEADERS)
        self.assertEqual(res.status_code, 403, res.content)

    def test_level2_cannot_grant_premium(self):
        self.as_(self.l2)
        res = self.client.post(
            f'/api/admin/users/{self.victim.id}/toggle-premium/', **HEADERS
        )
        self.assertEqual(res.status_code, 403)
        self.victim.refresh_from_db()
        self.assertFalse(self.victim.is_premium)

    def test_level2_can_read_mocks_but_not_delete(self):
        self.as_(self.l2)
        self.assertEqual(
            self.client.get('/api/admin/ielts/reading/', **HEADERS).status_code, 200
        )
        self.assertEqual(
            self.client.delete('/api/admin/ielts/reading/1/', **HEADERS).status_code, 403
        )

    def test_level2_cannot_import(self):
        self.as_(self.l2)
        res = self.client.post(
            '/api/import/ielts/reading/', data='{}', content_type='application/json',
            **HEADERS
        )
        self.assertEqual(res.status_code, 403)

    def test_level2_cannot_see_ai_structures(self):
        self.as_(self.l2)
        res = self.client.get('/api/admin/ai/structures/', **HEADERS)
        self.assertEqual(res.status_code, 403)

    # ── Level 3: only users / centers / reports ──────────────────────────────
    def test_level3_can_read_users(self):
        self.as_(self.l3)
        self.assertEqual(
            self.client.get('/api/admin/users/', **HEADERS).status_code, 200
        )

    def test_level3_cannot_see_mocks(self):
        self.as_(self.l3)
        for url in ('/api/admin/ielts/reading/', '/api/admin/sat/tests/',
                    '/api/admin/cefr/reading/', '/api/admin/study/articles/'):
            self.assertEqual(
                self.client.get(url, **HEADERS).status_code, 403, url
            )

    def test_level3_can_read_reports(self):
        self.as_(self.l3)
        self.assertEqual(
            self.client.get('/api/admin/reports/', **HEADERS).status_code, 200
        )

    def test_level3_cannot_delete_user(self):
        self.as_(self.l3)
        res = self.client.delete(f'/api/admin/users/{self.victim.id}/', **HEADERS)
        self.assertEqual(res.status_code, 403)

    # ── Level 1 and owner: full access ───────────────────────────────────────
    def test_level1_can_read_everything(self):
        self.as_(self.l1)
        for url in ('/api/admin/users/', '/api/admin/ielts/reading/',
                    '/api/admin/ai/structures/', '/api/admin/sat/tests/'):
            self.assertEqual(self.client.get(url, **HEADERS).status_code, 200, url)

    def test_level1_can_write(self):
        self.as_(self.l1)
        res = self.client.post(
            f'/api/admin/users/{self.victim.id}/toggle-premium/', **HEADERS
        )
        self.assertEqual(res.status_code, 200, res.content)

    # ── Tests: only the owner deletes them or copies their JSON ──────────────
    def test_level1_cannot_delete_tests(self):
        from ielts.models import IELTSTest, ReadingPassage
        test = IELTSTest.objects.create(title='Mock')
        passage = ReadingPassage.objects.create(test=test, title='P', content='x')
        self.as_(self.l1)
        for url in (f'/api/admin/ielts/reading/{passage.id}/', '/api/admin/ielts/reading/all/',
                    f'/api/admin/ielts/tests/{test.id}/', '/api/admin/cefr/reading/all/',
                    '/api/admin/sat/tests/1/', '/api/admin/sat/questions/1/'):
            res = self.client.delete(url, **HEADERS)
            self.assertEqual(res.status_code, 403, url)
        self.assertTrue(ReadingPassage.objects.filter(id=passage.id).exists())

    def test_level1_cannot_copy_test_json(self):
        self.as_(self.l1)
        for url in ('/api/admin/export/ielts/reading/1/', '/api/admin/export/cefr/test/1/'):
            self.assertEqual(self.client.get(url, **HEADERS).status_code, 403, url)

    def test_owner_deletes_and_exports(self):
        from ielts.models import ReadingPassage
        passage = ReadingPassage.objects.create(title='P', content='x', is_standalone=True)
        self.as_(self.owner)
        self.assertEqual(self.client.get(f'/api/admin/export/ielts/reading/{passage.id}/', **HEADERS).status_code, 200)
        self.assertIn(self.client.delete(f'/api/admin/ielts/reading/{passage.id}/', **HEADERS).status_code, (200, 204))
        self.assertFalse(ReadingPassage.objects.filter(id=passage.id).exists())

    def test_level1_still_edits_tests(self):
        # Editing and importing stay open for level 1 — only deleting is the owner's.
        self.as_(self.l1)
        res = self.client.post('/api/import/ielts/writing/', data={'title': 'T', 'task_type': 2, 'prompt': 'p'},
                               content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)

    # ── Granting levels ──────────────────────────────────────────────────────
    def test_owner_can_set_level(self):
        self.as_(self.owner)
        res = self.client.post(
            f'/api/admin/users/{self.victim.id}/admin-level/',
            data={'level': 2}, content_type='application/json', **HEADERS
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.is_staff)
        self.assertEqual(self.victim.admin_level, 2)
        self.assertEqual(self.victim.admin_tier, 2)

    def test_level1_can_create_admins(self):
        self.as_(self.l1)
        res = self.client.post(
            f'/api/admin/users/{self.victim.id}/admin-level/',
            data={'level': 3}, content_type='application/json', **HEADERS
        )
        self.assertEqual(res.status_code, 200, res.content)

    def test_level2_cannot_create_admins(self):
        self.as_(self.l2)
        res = self.client.post(
            f'/api/admin/users/{self.victim.id}/admin-level/',
            data={'level': 1}, content_type='application/json', **HEADERS
        )
        self.assertEqual(res.status_code, 403)
        self.victim.refresh_from_db()
        self.assertFalse(self.victim.is_staff)

    def test_level1_cannot_touch_owner(self):
        self.as_(self.l1)
        res = self.client.post(
            f'/api/admin/users/{self.owner.id}/admin-level/',
            data={'level': 0}, content_type='application/json', **HEADERS
        )
        self.assertEqual(res.status_code, 403)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_superuser)

    def test_admin_cannot_raise_own_level(self):
        self.as_(self.l1)
        res = self.client.post(
            f'/api/admin/users/{self.l1.id}/admin-level/',
            data={'level': 1}, content_type='application/json', **HEADERS
        )
        self.assertEqual(res.status_code, 403)

    def test_level_removal(self):
        self.as_(self.owner)
        self.client.post(
            f'/api/admin/users/{self.l3.id}/admin-level/',
            data={'level': 0}, content_type='application/json', **HEADERS
        )
        self.l3.refresh_from_db()
        self.assertFalse(self.l3.is_staff)
        self.assertIsNone(self.l3.admin_tier)

    def test_user_detail_shows_saved_level(self):
        # The panel's level picker reads these fields from the detail view.
        self.as_(self.owner)
        self.client.post(
            f'/api/admin/users/{self.victim.id}/admin-level/',
            data={'level': 2}, content_type='application/json', **HEADERS
        )
        res = self.client.get(f'/api/admin/users/{self.victim.id}/', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['admin_level'], 2)
        self.assertEqual(res.json()['admin_tier'], 2)
        self.assertFalse(res.json()['is_superuser'])

    def test_level1_cannot_delete_owner(self):
        self.as_(self.l1)
        res = self.client.delete(f'/api/admin/users/{self.owner.id}/', **HEADERS)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(User.objects.filter(id=self.owner.id).exists())


class DjangoAdminTest(TestCase):
    """Django admin is the owner's alone."""

    def test_owner_gets_in(self):
        owner = make_admin('o2@t.uz', 1, superuser=True)
        self.client.force_login(owner)
        res = self.client.get('/admin/', **HEADERS)
        self.assertEqual(res.status_code, 200)

    def test_level1_is_redirected_to_login(self):
        l1 = make_admin('l1b@t.uz', 1)
        self.client.force_login(l1)
        res = self.client.get('/admin/', **HEADERS)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login', res['Location'])


class TierHelperTest(TestCase):
    def test_existing_staff_without_level_is_treated_as_full(self):
        """Admins created before this feature must keep working."""
        u = User.objects.create_user(username='old@t.uz', email='old@t.uz', password='x')
        u.is_staff = True
        u.save()                       # admin_level stays 0
        self.assertEqual(u.admin_tier, 1)
        self.assertTrue(u.can_write_admin)

    def test_superuser_outranks_level(self):
        u = make_admin('s@t.uz', 3, superuser=True)
        self.assertEqual(u.admin_tier, 'super')
        self.assertTrue(u.can_write_admin)

    def test_non_staff_has_no_tier(self):
        u = User.objects.create_user(username='n@t.uz', email='n@t.uz', password='x')
        self.assertIsNone(u.admin_tier)
        self.assertFalse(u.can_write_admin)
