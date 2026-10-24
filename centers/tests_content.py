"""A learning center's own tests: who sees them, who manages them."""
import base64

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from ielts.models import WritingTask

from .models import CenterMembership, LearningCenter

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


class CenterContentTest(TestCase):
    def setUp(self):
        cache.clear()
        self.center = LearningCenter.objects.create(name='A')
        self.other_center = LearningCenter.objects.create(name='B')
        self.director = self.member('d@t.uz', 'director')
        self.teacher = self.member('t@t.uz', 'teacher')
        self.student = self.member('s@t.uz', 'student')
        self.other_teacher = self.member('ot@t.uz', 'teacher', self.other_center)
        self.outsider = User.objects.create_user(username='u@t.uz', email='u@t.uz', password='x')
        self.site_task = WritingTask.objects.create(title='Site essay', task_type=2, prompt='p')

    def member(self, email, role, center=None):
        u = User.objects.create_user(username=email, email=email, password='x')
        CenterMembership.objects.create(center=center or self.center, user=u, role=role)
        return u

    def as_(self, user):
        self.client.force_login(user)

    def import_essay(self, user, center=None, title='Center essay'):
        self.as_(user)
        return self.client.post(f'/api/centers/{(center or self.center).id}/content/import/ielts-writing/',
                                data={'title': title, 'task_type': 2, 'prompt': 'Discuss.'},
                                content_type='application/json', **HEADERS)

    def writing_titles(self, user):
        self.as_(user)
        return {t['title'] for t in self.client.get('/api/ielts/writing/', **HEADERS).json()}

    def test_teacher_import_belongs_to_center(self):
        res = self.import_essay(self.teacher)
        self.assertEqual(res.status_code, 200, res.content)
        task = WritingTask.objects.get(title='Center essay')
        self.assertEqual(task.center_id, self.center.id)
        listing = self.client.get(f'/api/centers/{self.center.id}/content/?kind=ielts-writing', **HEADERS).json()
        self.assertEqual([i['title'] for i in listing['items']], ['Center essay'])

    def test_who_sees_what(self):
        self.import_essay(self.teacher)
        self.assertEqual(self.writing_titles(self.outsider), {'Site essay'})
        self.assertEqual(self.writing_titles(self.student), {'Site essay', 'Center essay'})
        self.assertEqual(self.writing_titles(self.other_teacher), {'Site essay'})

    def test_director_hides_site_tests(self):
        self.import_essay(self.teacher)
        self.as_(self.teacher)
        res = self.client.patch(f'/api/centers/{self.center.id}/settings/', data={'show_site_mocks': False},
                                content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 403)
        self.as_(self.director)
        res = self.client.patch(f'/api/centers/{self.center.id}/settings/', data={'show_site_mocks': False},
                                content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.writing_titles(self.student), {'Center essay'})
        # staff of the center still see everything; outsiders are unaffected
        self.assertEqual(self.writing_titles(self.teacher), {'Site essay', 'Center essay'})
        self.assertEqual(self.writing_titles(self.outsider), {'Site essay'})
        # a hidden site test cannot be opened by id either
        self.as_(self.student)
        self.assertEqual(self.client.post(f'/api/ielts/writing/{self.site_task.id}/start/', **HEADERS).status_code, 404)

    def test_outsider_cannot_open_center_test(self):
        self.import_essay(self.teacher)
        task = WritingTask.objects.get(title='Center essay')
        self.as_(self.outsider)
        self.assertEqual(self.client.post(f'/api/ielts/writing/{task.id}/start/', **HEADERS).status_code, 404)
        self.as_(self.student)
        self.assertIn(self.client.post(f'/api/ielts/writing/{task.id}/start/', **HEADERS).status_code, (200, 201))

    def test_only_admin_and_teacher_manage(self):
        self.assertEqual(self.import_essay(self.student).status_code, 403)
        self.assertEqual(self.import_essay(self.director).status_code, 403)
        self.assertEqual(self.import_essay(self.other_teacher).status_code, 403)
        self.import_essay(self.teacher)
        task = WritingTask.objects.get(title='Center essay')
        self.as_(self.other_teacher)
        url = f'/api/centers/{self.other_center.id}/content/ielts-writing/{task.id}/'
        self.assertEqual(self.client.delete(url, **HEADERS).status_code, 404)   # not their center's test
        self.as_(self.teacher)
        self.assertEqual(self.client.delete(f'/api/centers/{self.center.id}/content/ielts-writing/{task.id}/',
                                            **HEADERS).status_code, 200)
        self.assertFalse(WritingTask.objects.filter(id=task.id).exists())

    def test_center_cannot_delete_site_tests(self):
        self.as_(self.teacher)
        url = f'/api/centers/{self.center.id}/content/ielts-writing/{self.site_task.id}/'
        self.assertEqual(self.client.delete(url, **HEADERS).status_code, 404)

    def test_picture_upload_goes_through(self):
        self.import_essay(self.teacher)
        task = WritingTask.objects.get(title='Center essay')
        self.as_(self.teacher)
        res = self.client.post(f'/api/centers/{self.center.id}/content/ielts-writing/{task.id}/media/',
                               data={'image': SimpleUploadedFile('c.png', PNG, content_type='image/png')}, **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        task.refresh_from_db()
        self.assertTrue(task.image)
        task.image.delete(save=False)

    def test_admin_panel_lists_site_tests_only(self):
        self.import_essay(self.teacher)
        owner = User.objects.create_superuser(username='o@t.uz', email='o@t.uz', password='x')
        self.as_(owner)
        titles = {t['title'] for t in self.client.get('/api/admin/ielts/writing/', **HEADERS).json()}
        self.assertEqual(titles, {'Site essay'})

    def test_teacher_picker_includes_center_tests(self):
        self.import_essay(self.teacher)
        self.import_essay(self.other_teacher, self.other_center, 'Other essay')
        self.as_(self.teacher)
        titles = {t['title'] for t in self.client.get(
            f'/api/centers/{self.center.id}/available-tasks/?type=ielts_writing', **HEADERS).json()}
        self.assertEqual(titles, {'Site essay', 'Center essay'})
