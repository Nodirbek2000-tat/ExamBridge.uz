"""Learning center tasks: assigning, automatic completion, monitoring."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from cefr.models import CEFRWritingTest, CEFRWritingResponse
from ielts.models import (
    IELTSAttempt, IELTSTest, ReadingAnswer, ReadingPassage, ReadingQuestion,
    WritingResponse, WritingTask,
)

from .models import (
    Assignment, AssignmentSubmission, CenterMembership, Group, GroupMembership,
    LearningCenter,
)

User = get_user_model()

HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


def make_user(email):
    return User.objects.create_user(username=email, email=email, password='x',
                                    first_name=email.split('@')[0])


class CenterTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.center = LearningCenter.objects.create(name='Test center')
        self.director = self.member('dir@t.uz', 'director')
        self.admin = self.member('adm@t.uz', 'admin')
        self.teacher = self.member('t1@t.uz', 'teacher')
        self.other_teacher = self.member('t2@t.uz', 'teacher')
        self.student = self.member('s1@t.uz', 'student')
        self.other_student = self.member('s2@t.uz', 'student')
        self.group = Group.objects.create(center=self.center, name='A', teacher=self.teacher)
        self.other_group = Group.objects.create(center=self.center, name='B', teacher=self.other_teacher)
        GroupMembership.objects.create(group=self.group, student=self.student)
        GroupMembership.objects.create(group=self.other_group, student=self.other_student)

    def member(self, email, role):
        u = make_user(email)
        CenterMembership.objects.create(center=self.center, user=u, role=role)
        return u

    def url(self, tail):
        return f'/api/centers/{self.center.id}/{tail}'

    def assign(self, by, task_type, ref_id, kind, **target):
        self.client.force_login(by)
        return self.client.post(self.url('assignments/'), data={
            'task_type': task_type, 'task_title': 'Task', 'task_ref_id': ref_id,
            'task_ref_kind': kind, **target,
        }, content_type='application/json', **HEADERS)

    def my_tasks(self, user):
        self.client.force_login(user)
        res = self.client.get('/api/centers/my-assignments/', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()


class AssignTest(CenterTestBase):
    def setUp(self):
        super().setUp()
        self.task = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')

    def test_teacher_assigns_own_group(self):
        res = self.assign(self.teacher, 'ielts_writing', self.task.id, 'task', group_id=self.group.id)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(len(self.my_tasks(self.student)), 1)

    def test_teacher_cannot_assign_other_group(self):
        res = self.assign(self.teacher, 'ielts_writing', self.task.id, 'task', group_id=self.other_group.id)
        self.assertEqual(res.status_code, 403)

    def test_teacher_cannot_assign_other_teachers_student(self):
        res = self.assign(self.teacher, 'ielts_writing', self.task.id, 'task',
                          student_id=self.other_student.id)
        self.assertEqual(res.status_code, 403)

    def test_director_and_admin_do_not_give_tasks(self):
        for who in (self.director, self.admin):
            res = self.assign(who, 'ielts_writing', self.task.id, 'task', group_id=self.group.id)
            self.assertEqual(res.status_code, 403)

    def test_unknown_type_refused(self):
        res = self.assign(self.teacher, 'hack', 1, 'task', group_id=self.group.id)
        self.assertEqual(res.status_code, 400)

    def test_only_admin_changes_group_members(self):
        for who in (self.teacher, self.director):
            self.client.force_login(who)
            res = self.client.post(self.url(f'groups/{self.group.id}/students/'),
                                   data={'student_id': self.other_student.id},
                                   content_type='application/json', **HEADERS)
            self.assertEqual(res.status_code, 403)
            res = self.client.delete(self.url(f'groups/{self.group.id}/students/{self.student.id}/remove/'),
                                     **HEADERS)
            self.assertEqual(res.status_code, 403)
        self.client.force_login(self.admin)
        res = self.client.post(self.url(f'groups/{self.group.id}/students/'),
                               data={'student_id': self.other_student.id},
                               content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 200)

    def test_member_roles(self):
        cases = ((self.teacher, 'student', 403), (self.director, 'student', 403),
                 (self.director, 'teacher', 201), (self.admin, 'student', 201), (self.admin, 'admin', 403))
        for i, (who, role, code) in enumerate(cases):
            self.client.force_login(who)
            res = self.client.post(self.url('members/add/'), data={
                'email': f'new{i}@t.uz', 'password': 'Strong-pass-1', 'role': role,
            }, content_type='application/json', **HEADERS)
            self.assertEqual(res.status_code, code, (who.email, role))

    def test_new_group_member_gets_open_tasks(self):
        self.assign(self.teacher, 'ielts_writing', self.task.id, 'task', group_id=self.group.id)
        self.client.force_login(self.admin)
        newcomer = self.member('s3@t.uz', 'student')
        res = self.client.post(self.url(f'groups/{self.group.id}/students/'),
                               data={'student_id': newcomer.id},
                               content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(self.my_tasks(newcomer)), 1)


class AvailableTasksTest(CenterTestBase):
    def test_writing_and_speaking_are_listed(self):
        WritingTask.objects.create(title='IELTS essay', task_type=2, prompt='p')
        CEFRWritingTest.objects.create(title='CEFR letter', kind='FULL', task11='a', task12='b', task2='c')
        self.client.force_login(self.teacher)
        for task_type, title in (('ielts_writing', 'IELTS essay'), ('cefr_writing', 'CEFR letter')):
            res = self.client.get(self.url(f'available-tasks/?type={task_type}'), **HEADERS)
            self.assertEqual(res.status_code, 200)
            self.assertIn(title, [t['title'] for t in res.json()], task_type)
        for task_type in ('ielts_speaking', 'cefr_speaking'):
            res = self.client.get(self.url(f'available-tasks/?type={task_type}'), **HEADERS)
            self.assertEqual(res.status_code, 200, task_type)


class AutoCompleteTest(CenterTestBase):
    def test_ielts_writing_completes_then_gets_score(self):
        task = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')
        self.assign(self.teacher, 'ielts_writing', task.id, 'task', group_id=self.group.id)
        self.assertEqual(self.my_tasks(self.student)[0]['status'], 'pending')

        attempt = IELTSAttempt.objects.create(user=self.student)
        resp = WritingResponse.objects.create(attempt=attempt, task=task, response_text='word ' * 260)
        row = self.my_tasks(self.student)[0]
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['score_detail'], 'Being scored')

        resp.ai_band = 6.5
        resp.save()
        row = self.my_tasks(self.student)[0]
        self.assertEqual(row['score'], 6.5)
        self.assertEqual(row['score_detail'], 'Band 6.5')

    def test_old_result_does_not_count(self):
        task = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')
        attempt = IELTSAttempt.objects.create(user=self.student)
        WritingResponse.objects.create(attempt=attempt, task=task, response_text='old', ai_band=5)
        self.assign(self.teacher, 'ielts_writing', task.id, 'task', group_id=self.group.id)
        a = Assignment.objects.get()
        WritingResponse.objects.filter(task=task).update(created_at=a.created_at - timedelta(days=1))
        self.assertEqual(self.my_tasks(self.student)[0]['status'], 'pending')

    def test_cefr_writing_scored(self):
        test = CEFRWritingTest.objects.create(title='Letter', kind='FULL', task11='a', task12='b', task2='c')
        self.assign(self.teacher, 'cefr_writing', test.id, 'test', group_id=self.group.id)
        CEFRWritingResponse.objects.create(user=self.student, test=test, status='READY', score=52,
                                           level='B2', submitted_at=timezone.now())
        row = self.my_tasks(self.student)[0]
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['score_detail'], '52/75 · B2')

    def test_ielts_mock_needs_every_passage(self):
        test = IELTSTest.objects.create(title='Mock 1')
        passages = [ReadingPassage.objects.create(test=test, title=f'P{n}', content='x', passage_number=n)
                    for n in (1, 2)]
        questions = [ReadingQuestion.objects.create(passage=p, number=1, question_type='GAP',
                                                    content='q', correct_answer='a') for p in passages]
        self.assign(self.teacher, 'ielts_reading', test.id, 'mock', group_id=self.group.id)

        attempt = IELTSAttempt.objects.create(user=self.student, test=test, reading_band=6, status='COMPLETED')
        ReadingAnswer.objects.create(attempt=attempt, question=questions[0], answer='a', is_correct=True)
        self.assertEqual(self.my_tasks(self.student)[0]['status'], 'pending')

        ReadingAnswer.objects.create(attempt=attempt, question=questions[1], answer='a', is_correct=True)
        self.assertEqual(self.my_tasks(self.student)[0]['status'], 'completed')

    def test_manual_submit_cannot_fake_a_score(self):
        task = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')
        self.assign(self.teacher, 'ielts_writing', task.id, 'task', group_id=self.group.id)
        sub = AssignmentSubmission.objects.get()
        self.client.force_login(self.student)
        res = self.client.post(f'/api/centers/submissions/{sub.id}/submit/',
                               data={'score': 9, 'score_detail': 'Band 9'},
                               content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, 'pending')
        self.assertIsNone(sub.score)


class MonitoringTest(CenterTestBase):
    def setUp(self):
        super().setUp()
        task = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')
        self.assign(self.teacher, 'ielts_writing', task.id, 'task', group_id=self.group.id)
        self.assign(self.other_teacher, 'ielts_writing', task.id, 'task', group_id=self.other_group.id)

    def monitoring(self, user):
        cache.clear()
        self.client.force_login(user)
        return self.client.get(self.url('monitoring/'), **HEADERS)

    def test_teacher_sees_only_own_students(self):
        res = self.monitoring(self.teacher)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual([s['email'] for s in data['students']], ['s1@t.uz'])
        self.assertNotIn('teachers', data)

    def test_director_sees_center(self):
        data = self.monitoring(self.director).json()
        self.assertEqual(data['kpis']['students'], 2)
        self.assertEqual(data['kpis']['tasks'], 2)
        self.assertEqual(len(data['teachers']), 2)
        self.assertEqual(len(data['daily']), 14)
        self.assertEqual(data['to_fix']['students_without_group'], [])

    def test_student_is_refused(self):
        self.assertEqual(self.monitoring(self.student).status_code, 403)

    def test_platform_staff_can_watch(self):
        staff = make_user('staff@t.uz')
        staff.is_staff = True
        staff.save()
        self.assertEqual(self.monitoring(staff).status_code, 200)


class LessonTest(CenterTestBase):
    def setUp(self):
        super().setUp()
        self.wt = WritingTask.objects.create(title='Essay', task_type=2, prompt='Write')
        self.cw = CEFRWritingTest.objects.create(title='Letter', kind='FULL', task11='a', task12='b', task2='c')

    def give(self, who, group=None, **extra):
        self.client.force_login(who)
        return self.client.post(self.url('lessons/'), data={
            'topic': 'Past tenses', 'group_id': (group or self.group).id, 'tasks': [
                {'task_type': 'ielts_writing', 'task_ref_id': self.wt.id, 'task_ref_kind': 'task',
                 'task_title': 'Essay'},
                {'task_type': 'cefr_writing', 'task_ref_id': self.cw.id, 'task_ref_kind': 'test',
                 'task_title': 'Letter'},
            ], **extra,
        }, content_type='application/json', **HEADERS)

    def test_teacher_gives_lesson_with_several_tasks(self):
        res = self.give(self.teacher)
        self.assertEqual(res.status_code, 201, res.content)
        tasks = self.my_tasks(self.student)
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t['lesson']['topic'] for t in tasks}, {'Past tenses'})

    def test_only_teachers_give_lessons(self):
        for who in (self.director, self.admin):
            self.assertEqual(self.give(who).status_code, 403)
        self.assertEqual(self.give(self.teacher, group=self.other_group).status_code, 403)

    def test_only_owner_deletes(self):
        lesson_id = self.give(self.teacher).json()['id']
        for who in (self.other_teacher, self.director, self.admin):
            self.client.force_login(who)
            self.assertEqual(self.client.delete(self.url(f'lessons/{lesson_id}/'), **HEADERS).status_code, 403)
        self.client.force_login(self.teacher)
        task_id = Assignment.objects.filter(lesson_id=lesson_id).first().id
        self.assertEqual(self.client.delete(self.url(f'assignments/{task_id}/'), **HEADERS).status_code, 200)
        self.assertEqual(len(self.my_tasks(self.student)), 1)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.delete(self.url(f'lessons/{lesson_id}/'), **HEADERS).status_code, 200)
        self.assertEqual(self.my_tasks(self.student), [])

    def test_journal_and_progress(self):
        self.give(self.teacher)
        CEFRWritingResponse.objects.create(user=self.student, test=self.cw, status='READY', score=50,
                                           level='B2', submitted_at=timezone.now())
        cache.clear()
        self.client.force_login(self.teacher)
        res = self.client.get(self.url(f'groups/{self.group.id}/journal/'), **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        row = res.json()['students'][0]
        self.assertEqual(row['done'], 1)
        self.assertEqual(list(row['cells'].values()), ['partial'])
        self.assertFalse(res.json()['can_manage_members'])

        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(self.url(f'groups/{self.group.id}/journal/'), **HEADERS).status_code, 403)

        self.client.force_login(self.student)
        res = self.client.get('/api/centers/my-progress/', **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['lessons']['partial'], 1)
