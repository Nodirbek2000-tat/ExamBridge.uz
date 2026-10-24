"""School classes and olympiads."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from cefr.models import CEFRWritingResponse, CEFRWritingTest

from .models import CenterMembership, CenterNotification, Group, GroupMembership, LearningCenter, Olympiad

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


class OlympiadTest(TestCase):
    def setUp(self):
        cache.clear()
        self.center = LearningCenter.objects.create(name='C')
        self.admin = self.member('a@t.uz', 'admin')
        self.teacher = self.member('t@t.uz', 'teacher')
        self.s5a1 = self.member('s1@t.uz', 'student', 5, 'A')
        self.s5a2 = self.member('s2@t.uz', 'student', 5, 'A')
        self.s9b = self.member('s3@t.uz', 'student', 9, 'B')
        self.group = Group.objects.create(center=self.center, name='G', teacher=self.teacher)
        GroupMembership.objects.create(group=self.group, student=self.s9b)
        self.other_group = Group.objects.create(center=self.center, name='H')
        self.test = CEFRWritingTest.objects.create(title='Letter', kind='FULL', task11='a', task12='b', task2='c')

    def member(self, email, role, grade=None, letter=''):
        u = User.objects.create_user(username=email, email=email, password='x', first_name=email[:2])
        CenterMembership.objects.create(center=self.center, user=u, role=role, grade=grade, class_letter=letter)
        return u

    def url(self, tail):
        return f'/api/centers/{self.center.id}/{tail}'

    def create(self, user, audience, start_in=timedelta(hours=1)):
        self.client.force_login(user)
        return self.client.post(self.url('olympiads/'), data={
            'title': 'Spring Olympiad', 'starts_at': (timezone.now() + start_in).isoformat(), 'duration_minutes': 60,
            'task_type': 'cefr_writing', 'task_ref_id': self.test.id, 'task_ref_kind': 'test',
            'task_title': 'Letter', 'audience': audience,
        }, content_type='application/json', **HEADERS)

    # ── classes ──
    def test_admin_adds_student_with_class(self):
        self.client.force_login(self.admin)
        res = self.client.post(self.url('members/add/'), data={
            'email': 'new@t.uz', 'password': 'Strong-pass-1', 'role': 'student', 'grade': '7', 'class_letter': 'v'},
            content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 201, res.content)
        m = CenterMembership.objects.get(user__email='new@t.uz')
        self.assertEqual(m.school_class, '7V')
        res = self.client.post(self.url('members/add/'), data={
            'email': 'bad@t.uz', 'password': 'Strong-pass-1', 'role': 'student', 'grade': '12'},
            content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 400)

    def test_only_admin_changes_class(self):
        self.client.force_login(self.teacher)
        res = self.client.patch(self.url(f'members/{self.s9b.id}/class/'), data={'grade': 10, 'class_letter': 'A'},
                                content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 403)
        self.client.force_login(self.admin)
        res = self.client.patch(self.url(f'members/{self.s9b.id}/class/'), data={'grade': 10, 'class_letter': 'a'},
                                content_type='application/json', **HEADERS)
        self.assertEqual(res.json()['school_class'], '10A')
        labels = [c['label'] for c in self.client.get(self.url('classes/'), **HEADERS).json()]
        self.assertEqual(labels, ['5A', '10A'])

    # ── creating ──
    def test_admin_invites_a_class(self):
        res = self.create(self.admin, {'classes': ['5A']})
        self.assertEqual(res.status_code, 201, res.content)
        o = Olympiad.objects.get()
        self.assertEqual(set(o.participants.values_list('student_id', flat=True)), {self.s5a1.id, self.s5a2.id})
        self.assertTrue(CenterNotification.objects.filter(recipient=self.s5a1, notification_type='olympiad').exists())
        self.assertFalse(CenterNotification.objects.filter(recipient=self.s9b, notification_type='olympiad').exists())

    def test_teacher_only_own_groups(self):
        self.assertEqual(self.create(self.teacher, {'classes': ['5A']}).status_code, 403)
        self.assertEqual(self.create(self.teacher, {'groups': [self.other_group.id]}).status_code, 403)
        res = self.create(self.teacher, {'groups': [self.group.id]})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['participants'], 1)

    def test_students_cannot_create(self):
        self.assertEqual(self.create(self.s5a1, {'all': True}).status_code, 403)

    # ── taking it ──
    def test_cannot_begin_early(self):
        self.create(self.admin, {'all': True})
        o = Olympiad.objects.get()
        self.client.force_login(self.s5a1)
        res = self.client.post(f'/api/centers/olympiads/{o.id}/begin/', **HEADERS)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get('/api/centers/my-olympiads/', **HEADERS).json()['olympiads'][0]['phase'],
                         'upcoming')

    def test_ranking(self):
        self.create(self.admin, {'all': True})
        o = Olympiad.objects.get()
        Olympiad.objects.filter(id=o.id).update(starts_at=timezone.now() - timedelta(minutes=30))
        o.refresh_from_db()
        start = o.starts_at
        for student, score, minutes in ((self.s5a1, 50, 20), (self.s5a2, 60, 25), (self.s9b, 60, 15)):
            self.client.force_login(student)
            res = self.client.post(f'/api/centers/olympiads/{o.id}/begin/', **HEADERS)
            self.assertEqual(res.json()['test']['type'], 'cefr_writing')
            CEFRWritingResponse.objects.create(user=student, test=self.test, status='READY', score=score,
                                               submitted_at=start + timedelta(minutes=minutes))
        cache.clear()
        self.client.force_login(self.admin)
        board = self.client.get(self.url(f'olympiads/{o.id}/'), **HEADERS).json()['board']
        # same score → the faster one is first
        self.assertEqual([(r['student_id'], r['rank']) for r in board[:3]],
                         [(self.s9b.id, 1), (self.s5a2.id, 2), (self.s5a1.id, 3)])
        self.client.force_login(self.s5a1)
        me = self.client.get(f'/api/centers/olympiads/{o.id}/', **HEADERS).json()['me']
        self.assertEqual(me['rank'], 3)

    def test_full_mock_is_a_task_type(self):
        self.client.force_login(self.teacher)
        res = self.client.get(self.url('available-tasks/?type=ielts_full_mock'), **HEADERS)
        self.assertEqual(res.status_code, 200)
