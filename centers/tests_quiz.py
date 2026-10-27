"""A center's own A/B/C/D tests."""
from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from .models import CenterMembership, Group, GroupMembership, LearningCenter, Quiz, QuizAttempt

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
JSON = {'content_type': 'application/json', **HEADERS}


def bank(n):
    return [{'question': f'{i} + 1 = ?', 'options': [str(i), str(i + 1), str(i + 2)], 'answer': 'B'} for i in range(n)]


class QuizTest(TestCase):
    def setUp(self):
        cache.clear()
        self.center = LearningCenter.objects.create(name='C')
        self.teacher = self.member('t@t.uz', 'teacher')
        self.director = self.member('d@t.uz', 'director')
        self.students = [self.member(f's{i}@t.uz', 'student') for i in range(5)]
        self.outsider = User.objects.create_user(username='o@t.uz', email='o@t.uz', password='x')
        self.group = Group.objects.create(center=self.center, name='G', teacher=self.teacher)
        for s in self.students:
            GroupMembership.objects.create(group=self.group, student=s)

    def member(self, email, role):
        u = User.objects.create_user(username=email, email=email, password='x')
        CenterMembership.objects.create(center=self.center, user=u, role=role)
        return u

    def url(self, tail):
        return f'/api/centers/{self.center.id}/{tail}'

    def make(self, **settings):
        self.client.force_login(self.teacher)
        res = self.client.post(self.url('quizzes/'), data={'title': 'Math', 'time_minutes': 10, **settings}, **JSON)
        self.assertEqual(res.status_code, 201, res.content)
        return Quiz.objects.get(id=res.json()['id'])

    def take(self, student, quiz):
        self.client.force_login(student)
        res = self.client.post(f'/api/centers/quizzes/{quiz.id}/start/', **HEADERS)
        self.assertIn(res.status_code, (200, 201), res.content)
        return QuizAttempt.objects.get(id=res.json()['attempt_id'])

    # ── building ──
    def test_import_formats_and_errors(self):
        quiz = self.make()
        res = self.client.post(self.url(f'quizzes/{quiz.id}/import/'), data={'questions': [
            {'question': 'Capital of Uzbekistan?', 'options': ['Samarkand', 'Tashkent'], 'answer': 'B'},
            {'question': '2+2', 'options': {'A': '3', 'B': '4', 'C': '5', 'D': '6'}, 'answer': '4'},
        ]}, **JSON)
        self.assertEqual(res.json()['added'], 2)
        self.assertEqual(list(quiz.questions.values_list('correct', flat=True)), [1, 1])
        bad = self.client.post(self.url(f'quizzes/{quiz.id}/import/'), data={'questions': [
            {'question': 'x', 'options': ['a', 'b', 'c', 'd', 'e'], 'answer': 'A'}]}, **JSON)
        self.assertEqual(bad.status_code, 400)
        self.assertIn('2 to 4', bad.json()['error'])

    def test_add_one_question_by_hand(self):
        quiz = self.make()
        res = self.client.post(self.url(f'quizzes/{quiz.id}/questions/'),
                               data={'question': 'Sky colour?', 'options': ['Blue', 'Green', 'Red'], 'answer': 0}, **JSON)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['answer'], 'A')

    def test_cannot_activate_empty(self):
        quiz = self.make()
        res = self.client.patch(self.url(f'quizzes/{quiz.id}/'), data={'is_active': True}, **JSON)
        self.assertEqual(res.status_code, 400)

    def test_students_and_outsiders_cannot_manage(self):
        for who in (self.students[0], self.outsider):
            self.client.force_login(who)
            self.assertEqual(self.client.post(self.url('quizzes/'), data={'title': 'x'}, **JSON).status_code, 403)

    # ── drawing ──
    def test_same_for_all(self):
        quiz = self.make(questions=bank(30), pick_count=10, same_for_all=True, is_active=True)
        sets = {tuple(self.take(s, quiz).question_ids) for s in self.students}
        self.assertEqual(len(sets), 1)
        self.assertEqual(len(next(iter(sets))), 10)

    def test_different_draws_spread_the_bank(self):
        quiz = self.make(questions=bank(20), pick_count=8, same_for_all=False, is_active=True)
        used = Counter()
        draws = []
        for s in self.students:
            ids = self.take(s, quiz).question_ids
            self.assertEqual(len(ids), 8)
            self.assertEqual(len(set(ids)), 8)
            used.update(ids)
            draws.append(tuple(ids))
        self.assertGreater(len(set(draws)), 1)
        # 5 × 8 = 40 draws over 20 questions → every question exactly twice
        self.assertEqual(set(used.values()), {2})

    def test_pick_count_never_exceeds_bank(self):
        quiz = self.make(questions=bank(5), pick_count=50, is_active=True)
        self.assertEqual(len(self.take(self.students[0], quiz).question_ids), 5)

    # ── taking ──
    def test_take_and_grade_with_shuffled_options(self):
        quiz = self.make(questions=bank(6), shuffle_options=True, is_active=True)
        a = self.take(self.students[0], quiz)
        data = self.client.get(f'/api/centers/quiz-attempts/{a.id}/', **HEADERS).json()
        self.assertNotIn('correct', data['questions'][0])          # no answers before the end
        for i, q in enumerate(data['questions']):
            right = q['options'].index(str(int(q['text'].split(' ')[0]) + 1))
            self.client.post(f'/api/centers/quiz-attempts/{a.id}/answer/',
                             data={'question_id': q['id'], 'option': right if i < 4 else (right + 1) % 3}, **JSON)
        res = self.client.post(f'/api/centers/quiz-attempts/{a.id}/submit/', **HEADERS).json()
        self.assertEqual((res['result']['correct'], res['result']['total']), (4, 6))
        self.assertIn('correct', res['questions'][0])               # shown after the end

    def test_time_up_hands_in(self):
        quiz = self.make(questions=bank(3), is_active=True)
        a = self.take(self.students[0], quiz)
        QuizAttempt.objects.filter(id=a.id).update(started_at=timezone.now() - timedelta(minutes=11))
        data = self.client.get(f'/api/centers/quiz-attempts/{a.id}/', **HEADERS).json()
        self.assertTrue(data['finished'])

    def test_only_active_or_assigned(self):
        quiz = self.make(questions=bank(3))
        self.client.force_login(self.students[0])
        self.assertEqual(self.client.post(f'/api/centers/quizzes/{quiz.id}/start/', **HEADERS).status_code, 404)
        self.assertEqual(self.client.get('/api/centers/my-quizzes/', **HEADERS).json(), [])
        # given as homework → can take it even though not active
        self.client.force_login(self.teacher)
        self.client.post(self.url('lessons/'), data={'topic': 'Quiz day', 'group_id': self.group.id, 'tasks': [
            {'task_type': 'center_quiz', 'task_ref_id': quiz.id, 'task_ref_kind': 'quiz', 'task_title': 'Math'}]}, **JSON)
        a = self.take(self.students[0], quiz)
        self.client.post(f'/api/centers/quiz-attempts/{a.id}/submit/', **HEADERS)
        self.client.force_login(self.students[0])
        task = self.client.get('/api/centers/my-assignments/', **HEADERS).json()[0]
        self.assertEqual(task['status'], 'completed')
        self.assertTrue(task['score_detail'].startswith('0/3'))
        # outsiders never
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(f'/api/centers/quizzes/{quiz.id}/start/', **HEADERS).status_code, 404)

    def test_results_for_staff(self):
        quiz = self.make(questions=bank(4), is_active=True)
        a = self.take(self.students[1], quiz)
        self.client.post(f'/api/centers/quiz-attempts/{a.id}/submit/', **HEADERS)
        self.client.force_login(self.director)
        res = self.client.get(self.url(f'quizzes/{quiz.id}/results/'), **HEADERS).json()
        self.assertEqual(res['attempts'], 1)
        self.assertEqual(res['rows'][0]['student_id'], self.students[1].id)

    def test_quiz_in_pickers_with_its_time(self):
        quiz = self.make(questions=bank(4), time_minutes=15)
        rows = self.client.get(self.url('available-tasks/?type=center_quiz'), **HEADERS).json()
        self.assertEqual([(r['id'], r['minutes']) for r in rows], [(quiz.id, 15)])
