"""AI lessons admin API."""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from .board import BoardError, clean_board, replace_steps
from .demo_lesson import GRAPH, QUESTIONS, STEPS, TITLE
from .models import AILesson, LessonAnswer, LessonProgress, LessonQuestion, LessonSource, LessonStep

User = get_user_model()

HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
BASE = '/api/admin/ai-lessons/'
MEDIA = tempfile.mkdtemp(prefix='ai_lessons_tests')


def make_user(email, level=0, superuser=False):
    u = User.objects.create_user(username=email, email=email, password='x')
    u.is_staff = level > 0 or superuser
    u.is_superuser = superuser
    u.admin_level = level
    u.save()
    return u


def auth(user):
    return {**HEADERS, 'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}


@override_settings(MEDIA_ROOT=MEDIA)
class LessonsBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user('owner@test.uz', superuser=True)
        self.h = auth(self.owner)

    def post_json(self, path, body):
        return self.client.post(path, data=body, content_type='application/json', **self.h)

    def lesson(self, **fields):
        return AILesson.objects.create(**{'title': 'Present Perfect', **fields})


class LessonCrudTest(LessonsBase):
    def test_create_starts_as_a_draft_owned_by_the_admin(self):
        res = self.post_json(BASE, {
            'title': ' IELTS Writing Task 2 ', 'subject': 'ielts', 'section': 'writing',
            'duration_minutes': 15, 'brief': 'Opinion essays',
        })
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual((body['title'], body['subject'], body['status']),
                         ('IELTS Writing Task 2', 'IELTS', 'DRAFT'))
        self.assertEqual(body['language'], 'uz')
        self.assertEqual(AILesson.objects.get().created_by, self.owner)

    def test_bad_input_is_refused(self):
        for bad in ({'title': '  '}, {'title': 'x', 'duration_minutes': 12},
                    {'title': 'x', 'duration_minutes': 'long'}, {'title': 'x', 'subject': 'MATH'},
                    {'title': 'x', 'section': 'nope'}, {'title': 'x', 'language': 'ru'},
                    {'title': 'x' * 201}):
            self.assertEqual(self.post_json(BASE, bad).status_code, 400, bad)
        self.assertFalse(AILesson.objects.exists())

    def test_list_filters_and_counts_sources(self):
        kept = self.lesson(subject='SAT')
        self.lesson(title='Other', subject='CEFR')
        LessonSource.objects.create(lesson=kept, kind='TEXT', title='t', text='x')

        rows = self.client.get(f'{BASE}?subject=sat&q=perfect', **self.h).json()
        self.assertEqual([(r['id'], r['sources_count']) for r in rows], [(kept.id, 1)])

    def test_patch_changes_only_what_was_sent(self):
        lesson = self.lesson(subject='CEFR', duration_minutes=20)
        res = self.client.patch(f'{BASE}{lesson.id}/', data={'title': 'New'},
                                content_type='application/json', **self.h)
        self.assertEqual(res.status_code, 200)
        lesson.refresh_from_db()
        self.assertEqual((lesson.title, lesson.subject, lesson.duration_minutes), ('New', 'CEFR', 20))

    def test_status_cannot_be_set_by_hand(self):
        lesson = self.lesson()
        self.client.patch(f'{BASE}{lesson.id}/', data={'status': 'PUBLISHED'},
                          content_type='application/json', **self.h)
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, 'DRAFT')


class LessonSourceTest(LessonsBase):
    def setUp(self):
        super().setUp()
        self.target = self.lesson()
        self.url = f'{BASE}{self.target.id}/sources/'

    def upload(self, name='rules.pdf', data=b'%PDF-1.4', **extra):
        return self.client.post(self.url, data={'kind': 'FILE', 'file': SimpleUploadedFile(name, data), **extra},
                                **self.h)

    def test_text_and_link_sources(self):
        self.assertEqual(self.post_json(self.url, {'kind': 'TEXT', 'text': 'Rule one'}).status_code, 201)
        self.assertEqual(self.post_json(self.url, {'kind': 'LINK', 'url': 'https://example.com/a'}).status_code, 201)
        detail = self.client.get(f'{BASE}{self.target.id}/', **self.h).json()
        self.assertEqual([s['kind'] for s in detail['sources']], ['TEXT', 'LINK'])

    def test_empty_oversized_and_unsafe_sources_are_refused(self):
        for bad in ({'kind': 'TEXT', 'text': ' '}, {'kind': 'TEXT', 'text': 'x' * 200_001},
                    {'kind': 'LINK', 'url': 'javascript:alert(1)'}, {'kind': 'LINK', 'url': 'example'},
                    {'kind': 'VIDEO'}):
            self.assertEqual(self.post_json(self.url, bad).status_code, 400, str(bad)[:60])
        self.assertFalse(LessonSource.objects.exists())

    def test_file_gets_a_random_name(self):
        res = self.upload(title='Grammar rules')
        self.assertEqual(res.status_code, 201)
        stored = LessonSource.objects.get().file.name
        self.assertTrue(stored.startswith('ai_lessons/sources/') and stored.endswith('.pdf'))
        self.assertNotIn('rules', stored)

    def test_scripts_and_huge_files_are_refused(self):
        self.assertEqual(self.upload(name='page.html').status_code, 400)
        self.assertEqual(self.upload(name='run.exe').status_code, 400)
        self.assertEqual(self.upload(data=b'x' * (20 * 1024 * 1024 + 1)).status_code, 400)
        self.assertFalse(LessonSource.objects.exists())

    def test_deleting_a_source_removes_its_file(self):
        self.upload()
        source = LessonSource.objects.get()
        stored = source.file.name

        res = self.client.delete(f'{BASE}sources/{source.id}/', **self.h)

        self.assertEqual(res.status_code, 200)
        self.assertFalse(default_storage.exists(stored))

    def test_deleting_a_lesson_removes_everything_it_owns(self):
        self.upload()
        stored = LessonSource.objects.get().file.name
        step = LessonStep.objects.create(lesson=self.target, order=1, say='Salom')
        LessonQuestion.objects.create(lesson=self.target, step=step, prompt='Q?')

        res = self.client.delete(f'{BASE}{self.target.id}/', **self.h)

        self.assertEqual(res.status_code, 200)
        self.assertFalse(default_storage.exists(stored))
        self.assertFalse(LessonSource.objects.exists() or LessonStep.objects.exists()
                         or LessonQuestion.objects.exists())


class BoardCheckTest(TestCase):
    def test_keeps_only_known_fields(self):
        board = clean_board([{'type': 'text', 'text': ' Salom ', 'html': '<b>x</b>', 'at': 0.5}])
        self.assertEqual(board, [{'type': 'text', 'text': 'Salom', 'color': 'chalk', 'at': 0.5}])

    def test_missing_times_are_spaced_evenly(self):
        board = clean_board([{'type': 'heading', 'text': 'a'}, {'type': 'text', 'text': 'b'},
                             {'type': 'math', 'tex': 'x'}, {'type': 'note', 'text': 'd'}])
        self.assertEqual([e['at'] for e in board], [0, 0.25, 0.5, 0.75])

    def test_diagram_shapes_survive(self):
        shape = {'kind': 'line', 'from': [0, 0], 'to': [10, 10], 'arrow': True, 'color': 'sky', 'onclick': 'x'}
        board = clean_board([{'type': 'diagram', 'width': 100, 'height': 80, 'shapes': [shape]}])
        self.assertEqual(board[0]['shapes'], [{
            'kind': 'line', 'color': 'sky', 'from': [0, 0], 'to': [10, 10], 'arrow': True, 'dashed': False,
        }])

    def test_bad_boards_are_refused(self):
        line = {'kind': 'line', 'from': [0, 0], 'to': [1, 1]}
        diagram = {'type': 'diagram', 'width': 10, 'height': 10}
        bad = [
            'not a list',
            [{'type': 'video'}],
            ['text'],
            [{'type': 'text', 'text': ''}],
            [{'type': 'text', 'text': 'x' * 401}],
            [{'type': 'text', 'text': 'x', 'color': 'url(evil)'}],
            [{'type': 'text', 'text': 'x', 'at': 2}],
            [{'type': 'text', 'text': 'x', 'at': 'soon'}],
            [{'type': 'text', 'text': 'a', 'at': 0.5}, {'type': 'text', 'text': 'b', 'at': 0.2}],
            [{'type': 'math', 'tex': 'x' * 301}],
            [{'type': 'list', 'items': []}],
            [{'type': 'list', 'items': ['x'] * 9}],
            [{'type': 'text', 'text': 'x'}] * 13,
            [{**diagram, 'shapes': []}],
            [{**diagram, 'shapes': [line] * 41}],
            [{**diagram, 'width': True, 'shapes': [line]}],
            [{**diagram, 'shapes': [{'kind': 'blob'}]}],
            [{**diagram, 'shapes': [{'kind': 'line', 'from': [0], 'to': [1, 1]}]}],
            [{**diagram, 'shapes': [{'kind': 'line', 'from': [float('nan'), 0], 'to': [1, 1]}]}],
            [{**diagram, 'shapes': [{'kind': 'path', 'd': 'M0 0"/><script>'}]}],
        ]
        for board in bad:
            with self.assertRaises(BoardError, msg=str(board)[:80]):
                clean_board(board)


class StepsTest(LessonsBase):
    def test_a_bad_step_leaves_the_lesson_untouched(self):
        lesson = self.lesson()
        replace_steps(lesson, [{'say': 'Birinchi', 'board': []}])

        with self.assertRaises(BoardError):
            replace_steps(lesson, [{'say': 'Yangi', 'board': []}, {'say': '', 'board': []}])

        self.assertEqual([s.say for s in lesson.steps.all()], ['Birinchi'])

    def test_replacing_steps_removes_their_old_audio(self):
        lesson = self.lesson()
        step = LessonStep.objects.create(lesson=lesson, order=0, say='x')
        step.audio.save('a.mp3', ContentFile(b'mp3'))
        stored = step.audio.name
        self.assertTrue(default_storage.exists(stored))

        with self.captureOnCommitCallbacks(execute=True):
            replace_steps(lesson, [{'say': 'Yangi'}])

        self.assertFalse(default_storage.exists(stored))

    def test_play_gives_steps_in_order_with_their_audio(self):
        lesson = self.lesson()
        replace_steps(lesson, [{'say': 'Bir', 'board': [{'type': 'math', 'tex': 'x'}]}, {'say': 'Ikki'}])
        LessonStep.objects.filter(lesson=lesson, order=1).update(audio='ai_lessons/audio/1/a.mp3', audio_seconds=4.5)

        body = self.client.get(f'{BASE}{lesson.id}/play/', **self.h).json()

        self.assertEqual([s['say'] for s in body['steps']], ['Bir', 'Ikki'])
        self.assertIsNone(body['steps'][0]['audio'])
        self.assertEqual(body['steps'][0]['board'][0]['tex'], 'x')
        self.assertTrue(body['steps'][1]['audio'].endswith('ai_lessons/audio/1/a.mp3'))
        self.assertEqual(body['steps'][1]['audio_seconds'], 4.5)

    def test_detail_counts_steps(self):
        lesson = self.lesson()
        replace_steps(lesson, [{'say': 'Bir'}, {'say': 'Ikki'}])
        self.assertEqual(self.client.get(f'{BASE}{lesson.id}/', **self.h).json()['steps_count'], 2)

    def test_deleting_a_lesson_removes_its_audio(self):
        lesson = self.lesson()
        step = LessonStep.objects.create(lesson=lesson, order=0, say='x')
        step.audio.save('a.mp3', ContentFile(b'mp3'))
        stored = step.audio.name

        self.client.delete(f'{BASE}{lesson.id}/', **self.h)

        self.assertFalse(default_storage.exists(stored))


class DemoLessonTest(LessonsBase):
    def test_graph_line_passes_through_its_points(self):
        shapes = GRAPH['shapes']
        line = next(s for s in shapes if s.get('color') == 'yellow')
        (x1, y1), (x2, y2) = line['from'], line['to']
        for point in (s['center'] for s in shapes if s['kind'] == 'circle'):
            on_line = y1 + (point[0] - x1) * (y2 - y1) / (x2 - x1)
            self.assertAlmostEqual(on_line, point[1])

    def test_seed_can_run_twice(self):
        call_command('seed_demo_ai_lesson', stdout=io.StringIO())
        call_command('seed_demo_ai_lesson', stdout=io.StringIO())

        lesson = AILesson.objects.get(title=TITLE)
        self.assertEqual(lesson.steps.count(), len(STEPS))
        self.assertEqual(lesson.questions.count(), len(QUESTIONS))
        self.assertEqual(lesson.status, 'DRAFT')

    def test_seed_keeps_a_published_demo_published(self):
        call_command('seed_demo_ai_lesson', stdout=io.StringIO())
        AILesson.objects.filter(title=TITLE).update(status='PUBLISHED')

        call_command('seed_demo_ai_lesson', stdout=io.StringIO())

        self.assertEqual(AILesson.objects.get(title=TITLE).status, 'PUBLISHED')


CHOICE = {'kind': 'CHOICE', 'prompt': '2 + 2?', 'options': ['3', '4'], 'correct': 1, 'explanation': 'Ikki va ikki'}


class AdminQuestionTest(LessonsBase):
    def setUp(self):
        super().setUp()
        self.target = self.lesson()
        replace_steps(self.target, [{'say': 'Bir'}, {'say': 'Ikki'}])
        self.step = self.target.steps.first()
        self.url = f'{BASE}{self.target.id}/questions/'

    def test_add_a_question_after_a_step_and_as_homework(self):
        self.assertEqual(self.post_json(self.url, {**CHOICE, 'step': self.step.id}).status_code, 201)
        res = self.post_json(self.url, {'kind': 'WRITTEN', 'prompt': 'Why?', 'rubric': 'Reason', 'is_homework': True})
        self.assertEqual(res.status_code, 201)

        rows = res.json()['questions']
        self.assertEqual([(r['kind'], r['step'], r['is_homework']) for r in rows],
                         [('CHOICE', self.step.id, False), ('WRITTEN', None, True)])

    def test_bad_questions_are_refused(self):
        other = self.lesson(title='Other')
        replace_steps(other, [{'say': 'Boshqa'}])
        foreign = other.steps.first().id
        here = {'step': self.step.id}
        bad = [
            {**CHOICE},                                                # no place in the lesson
            {**CHOICE, 'step': foreign},                               # a step of another lesson
            {**CHOICE, **here, 'prompt': ' '},
            {**CHOICE, **here, 'kind': 'ESSAY'},
            {**CHOICE, **here, 'options': ['one']},
            {**CHOICE, **here, 'options': ['a', 'b', 'c', 'd', 'e', 'f', 'g']},
            {**CHOICE, **here, 'options': ['same', 'same']},
            {**CHOICE, **here, 'options': ['a', ' ']},
            {**CHOICE, **here, 'correct': 2},
            {**CHOICE, **here, 'correct': None},
            {**CHOICE, **here, 'correct': True},
            {'kind': 'WRITTEN', 'prompt': 'x' * 1001, 'is_homework': True},
        ]
        for question in bad:
            self.assertEqual(self.post_json(self.url, question).status_code, 400, str(question)[:70])
        self.assertFalse(self.target.questions.exists())

    def test_edit_and_delete(self):
        self.post_json(self.url, {**CHOICE, 'step': self.step.id})
        question = self.target.questions.get()
        url = f'{BASE}questions/{question.id}/'

        res = self.client.put(url, data={**CHOICE, 'is_homework': True, 'correct': 0},
                              content_type='application/json', **self.h)
        self.assertEqual(res.status_code, 200)
        question.refresh_from_db()
        self.assertEqual((question.step, question.is_homework, question.correct), (None, True, 0))

        self.assertEqual(self.client.delete(url, **self.h).status_code, 200)
        self.assertFalse(self.target.questions.exists())

    def test_answer_counts_show_which_questions_are_too_hard(self):
        self.post_json(self.url, {**CHOICE, 'step': self.step.id})
        question = self.target.questions.get()
        for name, right in (('a', True), ('b', False), ('c', False)):
            LessonAnswer.objects.create(user=make_user(f'{name}@test.uz'), question=question, is_correct=right)

        row = self.client.get(f'{BASE}{self.target.id}/', **self.h).json()['questions'][0]

        self.assertEqual((row['answers'], row['right_answers']), (3, 1))

    def test_a_lesson_without_steps_cannot_be_published(self):
        empty = self.lesson(title='Empty')
        res = self.post_json(f'{BASE}{empty.id}/publish/', {'publish': True})
        self.assertEqual(res.status_code, 400)
        empty.refresh_from_db()
        self.assertEqual(empty.status, 'DRAFT')

    def test_publish_and_take_back(self):
        url = f'{BASE}{self.target.id}/publish/'
        self.assertEqual(self.post_json(url, {'publish': True}).json()['status'], 'PUBLISHED')
        self.assertEqual(self.post_json(url, {'publish': False}).json()['status'], 'DRAFT')

    def test_lesson_list_counts_students(self):
        LessonProgress.objects.create(user=make_user('a@test.uz'), lesson=self.target)
        LessonProgress.objects.create(user=make_user('b@test.uz'), lesson=self.target, completed_at=timezone.now())
        LessonSource.objects.create(lesson=self.target, kind='TEXT', title='t', text='x')

        row = self.client.get(BASE, **self.h).json()[0]

        self.assertEqual((row['students'], row['finished'], row['sources_count']), (2, 1, 1))


STUDENT = '/api/ai-lessons/'


class StudentBase(LessonsBase):
    def setUp(self):
        super().setUp()
        self.student = make_user('student@test.uz')
        self.s = auth(self.student)
        self.target = self.lesson(status='PUBLISHED', subject='SAT')
        replace_steps(self.target, [{'say': 'Bir'}, {'say': 'Ikki'}, {'say': 'Uch'}])
        self.step = self.target.steps.first()
        self.inside = LessonQuestion.objects.create(
            lesson=self.target, step=self.step, kind='CHOICE', prompt='2 + 2?', options=['3', '4'],
            correct=1, rubric='secret rubric', explanation='Ikki va ikki',
        )
        self.written = LessonQuestion.objects.create(
            lesson=self.target, step=self.step, kind='WRITTEN', prompt='Nega?', explanation='Namuna',
        )
        self.homework = LessonQuestion.objects.create(
            lesson=self.target, is_homework=True, kind='CHOICE', prompt='3 + 3?', options=['5', '6'], correct=1,
        )

    def post(self, path, body=None):
        return self.client.post(path, data=body or {}, content_type='application/json', **self.s)

    def answer(self, question, **body):
        return self.post(f'{STUDENT}questions/{question.id}/answer/', body)

    def finish(self):
        return self.post(f'{STUDENT}{self.target.id}/progress/', {'step': 2, 'completed': True})


class StudentLessonTest(StudentBase):
    def test_only_published_lessons_exist_for_students(self):
        draft = self.lesson(title='Draft')
        rows = self.client.get(STUDENT, **self.s).json()
        self.assertEqual([r['id'] for r in rows], [self.target.id])
        self.assertEqual(self.client.get(f'{STUDENT}{draft.id}/', **self.s).status_code, 404)
        self.assertEqual(self.post(f'{STUDENT}{draft.id}/progress/', {'step': 0}).status_code, 404)

    def test_list_filters_by_subject(self):
        self.assertEqual(self.client.get(f'{STUDENT}?subject=ielts', **self.s).json(), [])

    def test_correct_answers_never_reach_the_page_before_answering(self):
        self.finish()
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()

        for question in body['questions'] + body['homework']:
            self.assertEqual(set(question), {'id', 'step', 'kind', 'prompt', 'options', 'answer'})
            self.assertIsNone(question['answer'])
        self.assertNotIn('secret rubric', str(body))

    def test_homework_stays_closed_until_the_lesson_is_finished(self):
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        self.assertIsNone(body['homework'])
        self.assertEqual(body['homework_count'], 1)
        self.assertEqual(self.answer(self.homework, choice=1).status_code, 403)

        self.finish()

        self.assertEqual(self.answer(self.homework, choice=1).status_code, 201)
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        self.assertTrue(body['homework'][0]['answer']['is_correct'])

    def test_a_question_that_lost_its_step_disappears(self):
        LessonQuestion.objects.filter(pk=self.inside.pk).update(step=None)
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        self.assertEqual([q['id'] for q in body['questions']], [self.written.id])
        self.assertEqual(self.answer(self.inside, choice=1).status_code, 404)

    def test_anonymous_is_refused(self):
        self.assertIn(self.client.get(STUDENT, **HEADERS).status_code, (401, 403))


class StudentAnswerTest(StudentBase):
    def test_right_and_wrong_choices_are_marked_on_the_server(self):
        other = LessonQuestion.objects.create(
            lesson=self.target, step=self.step, kind='CHOICE', prompt='1 + 1?', options=['1', '2'], correct=1,
        )
        right = self.answer(self.inside, choice=1, is_correct=False)
        wrong = self.answer(other, choice=0, is_correct=True)

        self.assertEqual(right.status_code, 201)
        self.assertEqual((right.json()['is_correct'], right.json()['correct']), (True, 1))
        self.assertEqual(right.json()['explanation'], 'Ikki va ikki')
        self.assertFalse(wrong.json()['is_correct'])

    def test_an_answer_cannot_be_changed(self):
        self.answer(self.inside, choice=0)

        res = self.answer(self.inside, choice=1)

        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.json()['choice'], res.json()['is_correct']), (0, False))
        self.assertEqual(LessonAnswer.objects.count(), 1)

    def test_a_written_answer_is_saved_unmarked(self):
        res = self.answer(self.written, text=' Chunki ')
        self.assertEqual(res.status_code, 201)
        self.assertEqual((res.json()['text'], res.json()['is_correct']), ('Chunki', None))
        self.assertEqual(res.json()['explanation'], 'Namuna')

    def test_bad_answers_are_refused(self):
        for bad in ({}, {'choice': 5}, {'choice': -1}, {'choice': '1'}, {'choice': True}):
            self.assertEqual(self.answer(self.inside, **bad).status_code, 400, bad)
        for bad in ({}, {'text': '  '}, {'text': 'x' * 2001}):
            self.assertEqual(self.answer(self.written, **bad).status_code, 400, str(bad)[:30])
        self.assertFalse(LessonAnswer.objects.exists())

    def test_questions_of_unpublished_lessons_are_closed(self):
        AILesson.objects.filter(pk=self.target.pk).update(status='DRAFT')
        self.assertEqual(self.answer(self.inside, choice=1).status_code, 404)

    def test_answers_belong_to_their_student(self):
        self.answer(self.inside, choice=1)
        other = auth(make_user('other@test.uz'))
        body = self.client.get(f'{STUDENT}{self.target.id}/', **other).json()
        self.assertIsNone(body['questions'][0]['answer'])


class StudentProgressTest(StudentBase):
    def url(self):
        return f'{STUDENT}{self.target.id}/progress/'

    def test_progress_is_kept_and_shown_in_the_list(self):
        self.assertEqual(self.post(self.url(), {'step': 1}).json()['last_step'], 1)
        row = self.client.get(STUDENT, **self.s).json()[0]
        self.assertEqual(row['progress'], {'last_step': 1, 'percent': 33, 'completed': False})

    def test_finished_stays_finished_when_rewatching(self):
        self.finish()
        res = self.post(self.url(), {'step': 0})
        self.assertEqual(res.json(), {'last_step': 0, 'percent': 100, 'completed': True})

    def test_step_is_kept_inside_the_lesson(self):
        self.assertEqual(self.post(self.url(), {'step': 99}).json()['last_step'], 2)
        self.assertEqual(self.post(self.url(), {'step': -4}).json()['last_step'], 0)
        self.assertEqual(self.post(self.url(), {'step': 'x'}).status_code, 400)

    def test_only_a_real_true_finishes(self):
        res = self.post(self.url(), {'step': 2, 'completed': 'false'})
        self.assertFalse(res.json()['completed'])

    def test_opening_a_lesson_twice_keeps_one_row(self):
        self.post(self.url(), {'step': 1})
        self.post(self.url(), {'step': 2})
        self.assertEqual(LessonProgress.objects.count(), 1)


class LessonAccessTest(LessonsBase):
    def test_player_data_is_admin_only(self):
        lesson = self.lesson()
        res = self.client.get(f'{BASE}{lesson.id}/play/', **auth(make_user('student2@test.uz')))
        self.assertEqual(res.status_code, 403)

    def test_full_admins_may_work_with_lessons(self):
        h = auth(make_user('one@test.uz', level=1))
        self.assertEqual(self.client.get(BASE, **h).status_code, 200)

    def test_lower_admins_and_students_are_refused(self):
        for user in (make_user('two@test.uz', level=2), make_user('three@test.uz', level=3),
                     make_user('student@test.uz')):
            self.assertEqual(self.client.get(BASE, **auth(user)).status_code, 403, user.email)

    def test_anonymous_is_refused(self):
        self.assertIn(self.client.get(BASE, **HEADERS).status_code, (401, 403))


class EdgeCaseTest(StudentBase):
    def test_a_link_longer_than_the_column_is_refused(self):
        url = f'{BASE}{self.target.id}/sources/'
        res = self.client.post(url, data={'kind': 'LINK', 'url': 'https://example.com/' + 'a' * 600},
                               content_type='application/json', **self.h)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(LessonSource.objects.exists())

    def test_general_lessons_show_under_every_exam(self):
        general = self.lesson(title='Umumiy', status='PUBLISHED', subject='GENERAL')
        replace_steps(general, [{'say': 'Bir'}])
        for subject in ('SAT', 'IELTS', 'CEFR'):
            ids = [r['id'] for r in self.client.get(f'{STUDENT}?subject={subject}', **self.s).json()]
            self.assertIn(general.id, ids, subject)
        ids = [r['id'] for r in self.client.get(f'{STUDENT}?subject=IELTS', **self.s).json()]
        self.assertNotIn(self.target.id, ids)
