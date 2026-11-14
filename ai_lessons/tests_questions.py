"""The final test, the new question kinds, think pauses and the exam-secret board elements."""
from django.test import SimpleTestCase, override_settings

from .board import BoardError, clean_step, repair_step, replace_steps
from .models import LessonAnswer, LessonQuestion
from .questions import QuestionError, clean_content, gap_matches, mark
from .tests import BASE, STUDENT, LessonsBase, StudentBase
from .tests_pipeline import PipelineBase, ScriptedProvider


class QuestionKindsTest(SimpleTestCase):
    def test_true_false_takes_true_or_false_and_has_fixed_options(self):
        content = clean_content({'kind': 'TRUE_FALSE', 'prompt': 'Water boils at 100 C.', 'correct': True})
        self.assertEqual((content['options'], content['correct']), (['True', 'False'], 0))
        self.assertEqual(clean_content({'kind': 'TRUE_FALSE', 'prompt': 'x', 'correct': False})['correct'], 1)
        self.assertEqual(clean_content({'kind': 'TRUE_FALSE', 'prompt': 'x', 'correct': 1})['correct'], 1)
        with self.assertRaises(QuestionError):
            clean_content({'kind': 'TRUE_FALSE', 'prompt': 'x'})

    def test_a_gap_needs_exactly_one_gap_and_its_answers(self):
        content = clean_content({'kind': 'GAP', 'prompt': 'She ___ to school.', 'accepted': ['goes', 'goes', ' walks ']})
        self.assertEqual(content['accepted'], ['goes', 'walks'])
        self.assertEqual(content['options'], [])
        for bad in (
            {'prompt': 'No gap here.', 'accepted': ['goes']},
            {'prompt': 'Two ___ gaps ___.', 'accepted': ['a']},
            {'prompt': 'She ___.', 'accepted': []},
            {'prompt': 'She ___.', 'accepted': ['']},
            {'prompt': 'She ___.'},
        ):
            with self.assertRaises(QuestionError, msg=bad):
                clean_content({'kind': 'GAP', **bad})

    def test_find_the_mistake_has_three_to_eight_parts_and_may_repeat_words(self):
        content = clean_content({
            'kind': 'MISTAKE', 'prompt': 'Find it.', 'options': ['the cat', 'eat', 'the fish'], 'correct': 1,
        })
        self.assertEqual(content['correct'], 1)
        clean_content({'kind': 'MISTAKE', 'prompt': 'x', 'options': ['a', 'a', 'b'], 'correct': 0})
        for parts, correct in ((['a', 'b'], 0), (['a'] * 9, 0), (['a', 'b', 'c'], 3)):
            with self.assertRaises(QuestionError):
                clean_content({'kind': 'MISTAKE', 'prompt': 'x', 'options': parts, 'correct': correct})

    def test_gap_answers_are_compared_the_way_a_teacher_would(self):
        self.assertTrue(gap_matches('Goes', ['goes']))
        self.assertTrue(gap_matches('  goes. ', ['goes']))
        self.assertTrue(gap_matches('doesn’t', ["doesn't"]))
        self.assertTrue(gap_matches('does  not', ["doesn't", 'does not']))
        self.assertTrue(gap_matches('0.5', ['1/2']))
        self.assertTrue(gap_matches('4.0', ['4']))
        self.assertFalse(gap_matches('go', ['goes']))
        self.assertFalse(gap_matches('5', ['4']))
        self.assertFalse(gap_matches('1/0', ['0']))

    def test_marking_each_kind(self):
        tf = LessonQuestion(kind='TRUE_FALSE', options=['True', 'False'], correct=1)
        self.assertEqual(mark(tf, 1, ''), (1, '', True))
        gap = LessonQuestion(kind='GAP', accepted=['goes'])
        self.assertEqual(mark(gap, None, ' Goes '), (None, 'Goes', True))
        self.assertEqual(mark(gap, None, 'go'), (None, 'go', False))
        with self.assertRaises(QuestionError):
            mark(gap, None, '   ')
        with self.assertRaises(QuestionError):
            mark(gap, None, 'x' * 201)
        mistake = LessonQuestion(kind='MISTAKE', options=['a', 'b', 'c'], correct=2)
        self.assertEqual(mark(mistake, 0, '')[2], False)
        with self.assertRaises(QuestionError):
            mark(mistake, 3, '')


class BoardSecretsAndThinkTest(SimpleTestCase):
    def test_secret_and_fix_elements(self):
        step = clean_step({'say': 'x', 'board': [
            {'type': 'secret', 'text': 'The answer is often the middle option.', 'at': 0},
            {'type': 'fix', 'wrong': 'He go', 'right': 'He goes', 'at': 0.5, 'extra': 1},
        ]})
        self.assertEqual(step['board'][1], {'type': 'fix', 'wrong': 'He go', 'right': 'He goes', 'at': 0.5})
        with self.assertRaises(BoardError):
            clean_step({'say': 'x', 'board': [{'type': 'fix', 'wrong': 'He go'}]})

    def test_think_is_whole_seconds_up_to_eight(self):
        self.assertEqual(clean_step({'say': 'x'})['think'], 0)
        self.assertEqual(clean_step({'say': 'x', 'think': 4.6})['think'], 5)
        for bad in (9, -1, 'long', True):
            with self.assertRaises(BoardError, msg=bad):
                clean_step({'say': 'x', 'think': bad})

    def test_repair_clamps_think_and_shortens_a_long_fix(self):
        self.assertEqual(repair_step({'say': 'x', 'think': 30})['think'], 8)
        self.assertEqual(repair_step({'say': 'x', 'think': 'soon'})['think'], 0)
        mended = repair_step({'say': 'x', 'board': [{'type': 'fix', 'wrong': 'w' * 300, 'right': 'r'}]})
        self.assertEqual(len(clean_step(mended)['board'][0]['wrong']), 200)


class AdminNewKindsTest(LessonsBase):
    def setUp(self):
        super().setUp()
        self.target = self.lesson()
        replace_steps(self.target, [{'say': 'Bir'}, {'say': 'Ikki', 'think': 4}])
        self.url = f'{BASE}{self.target.id}/questions/'

    def test_add_a_final_test_gap_question_and_see_it_with_its_answers(self):
        body = {'kind': 'GAP', 'prompt': 'He ___ tea.', 'accepted': ['drinks'], 'is_quiz': True}
        res = self.post_json(self.url, body)
        self.assertEqual(res.status_code, 201)
        question = res.json()['questions'][0]
        self.assertEqual((question['is_quiz'], question['step'], question['accepted']), (True, None, ['drinks']))
        self.assertEqual(res.json()['steps'][1]['think'], 4)

    def test_a_steps_think_time_can_be_edited(self):
        step = self.target.steps.first()
        res = self.client.put(f'{BASE}steps/{step.id}/', data={'say': 'Bir', 'board': [], 'think': 6},
                              content_type='application/json', **self.h)
        self.assertEqual(res.status_code, 200)
        step.refresh_from_db()
        self.assertEqual(step.think, 6)

    def test_the_preview_has_the_questions_and_tries_answers_without_keeping_them(self):
        last = self.target.steps.last()
        quiz = LessonQuestion.objects.create(
            lesson=self.target, is_quiz=True, kind='GAP', prompt='He ___ tea.', accepted=['drinks'],
        )
        LessonQuestion.objects.create(lesson=self.target, is_homework=True, kind='WRITTEN', prompt='Uy')

        play = self.client.get(f'{BASE}{self.target.id}/play/', **self.h).json()
        self.assertEqual([(q['id'], q['step'], q['quiz']) for q in play['questions']], [(quiz.id, last.id, True)])
        self.assertNotIn('accepted', play['questions'][0])

        res = self.post_json(f'{BASE}questions/{quiz.id}/try/', {'text': 'Drinks'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.json()['is_correct'], res.json()['accepted']), (True, ['drinks']))
        self.assertFalse(LessonAnswer.objects.exists())
        self.assertEqual(self.post_json(f'{BASE}questions/{quiz.id}/try/', {'text': ''}).status_code, 400)

    @override_settings(AI_LESSON_PROVIDER='fake')
    def test_trying_a_written_answer_gets_the_teachers_verdict(self):
        question = LessonQuestion.objects.create(lesson=self.target, is_quiz=True, kind='WRITTEN', prompt='Why?')
        res = self.post_json(f'{BASE}questions/{question.id}/try/', {'text': 'because it is a habit of mine'})
        self.assertEqual(res.json()['verdict'], 'correct')
        self.assertFalse(LessonAnswer.objects.exists())

    def test_students_cannot_try(self):
        student = self.client.post(
            f'{BASE}questions/1/try/', data={}, content_type='application/json', HTTP_USER_AGENT='Mozilla/5.0',
        )
        self.assertIn(student.status_code, (401, 403))


class StudentFinalTestTest(StudentBase):
    def setUp(self):
        super().setUp()
        self.quiz = LessonQuestion.objects.create(
            lesson=self.target, is_quiz=True, kind='GAP', prompt='He ___ tea.', accepted=['drinks'],
            explanation='he/she/it + s',
        )

    def test_the_final_test_follows_the_last_step(self):
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        last = self.target.steps.last().id
        asked = [(q['id'], q['step'], q['quiz']) for q in body['questions']]
        self.assertEqual(asked[-1], (self.quiz.id, last, True))
        self.assertEqual(asked[0][2], False)
        self.assertNotIn('drinks', str(body))

    def test_it_follows_the_new_last_step_after_editing(self):
        replace_steps(self.target, [{'say': 'Faqat bitta'}])
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        quiz = next(q for q in body['questions'] if q['quiz'])
        self.assertEqual(quiz['step'], self.target.steps.get().id)

    def test_answering_shows_the_accepted_answers_only_afterwards(self):
        res = self.answer(self.quiz, text='drink')
        self.assertEqual(res.status_code, 201)
        self.assertEqual((res.json()['is_correct'], res.json()['accepted']), (False, ['drinks']))

    def test_a_lesson_without_steps_has_no_final_test(self):
        self.target.steps.all().delete()
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        self.assertFalse(any(q['quiz'] for q in body['questions']))


class ReviewFixesTest(PipelineBase):
    FIX = {'kind': 'CHOICE', 'prompt': 'Fixed: 2 + 2?', 'options': ['3', '4'], 'correct': 1, 'explanation': 'Four.'}

    def review(self, items):
        return {'verdict': 'BAD', 'summary': 'One key was wrong.', 'issues': ['Key'], 'questions': items}

    def test_the_reviewer_fixes_a_wrong_question(self):
        self.use(ScriptedProvider(replies={'review': [self.review([
            {'index': 0, 'verdict': 'BAD', 'note': 'The key was wrong.', 'fix': self.FIX},
        ])]}))
        self.generate()

        first = self.lesson.questions.get(order=0)
        self.assertEqual((first.prompt, first.correct, first.ai_verdict), ('Fixed: 2 + 2?', 1, 'GOOD'))
        self.assertTrue(first.ai_note.startswith('Fixed by the AI check: The key was wrong.'))
        self.assertIsNotNone(first.step)                 # it keeps its place

    def test_an_unusable_fix_leaves_the_question_marked_bad(self):
        self.use(ScriptedProvider(replies={'review': [self.review([
            {'index': 0, 'verdict': 'BAD', 'note': 'Unclear.', 'fix': {'kind': 'CHOICE', 'prompt': 'x'}},
            {'index': 1, 'verdict': 'GOOD', 'note': 'Fine.', 'fix': self.FIX},
        ])]}))
        self.generate()

        first = self.lesson.questions.get(order=0)
        self.assertEqual((first.ai_verdict, first.ai_note), ('BAD', 'Unclear.'))
        self.assertNotEqual(self.lesson.questions.get(order=1).prompt, 'Fixed: 2 + 2?')
