"""Hints, the teacher's spoken feedback on a wrong answer, praise for a right one, and lesson XP."""
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import override_settings

from . import praise, xp
from .board import replace_steps
from .models import AILesson, LessonAnswer, LessonProgress, LessonQuestion
from .questions import QuestionError, clean_content
from .tests import BASE, STUDENT, StudentBase, make_user
from .tests_pipeline import PipelineBase, ScriptedProvider


class HintAndFeedbackContentTest(StudentBase):
    def test_hint_and_teacher_line_are_kept_and_limited(self):
        content = clean_content({**self.choice(), 'hint': ' Think of 2 apples ', 'say_if_wrong': 'Not quite! It is four.'})
        self.assertEqual((content['hint'], content['wrong_say']), ('Think of 2 apples', 'Not quite! It is four.'))
        with self.assertRaises(QuestionError):
            clean_content({**self.choice(), 'hint': 'x' * 301})
        with self.assertRaises(QuestionError):
            clean_content({**self.choice(), 'wrong_say': 'x' * 701})

    @staticmethod
    def choice():
        return {'kind': 'CHOICE', 'prompt': '2 + 2?', 'options': ['3', '4'], 'correct': 1}


MEDIA = tempfile.mkdtemp(prefix='ai_lessons_feedback')


@override_settings(MEDIA_ROOT=MEDIA)
class AnswerFeedbackTest(StudentBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        LessonQuestion.objects.filter(pk=self.inside.pk).update(
            hint='Count on your fingers.', wrong_say='Not quite! Two and two make four.',
        )
        self.inside.refresh_from_db()
        self.inside.wrong_audio.save('voice.mp3', ContentFile(b'ID3 test'), save=True)

    def test_the_hint_is_on_the_page_before_answering(self):
        body = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        question = next(q for q in body['questions'] if q['id'] == self.inside.id)
        self.assertEqual(question['hint'], 'Count on your fingers.')
        self.assertNotIn('Two and two', str(body))           # what the teacher says comes with the answer only

    def test_a_wrong_answer_gets_the_teachers_words_and_voice(self):
        res = self.answer(self.inside, choice=0).json()
        self.assertEqual(res['wrong_say'], 'Not quite! Two and two make four.')
        self.assertTrue(res['wrong_audio'].startswith('http://testserver/media/ai_lessons/audio/'))
        self.assertEqual(res['xp'], 0)

    def test_a_right_answer_gets_xp_and_no_correction(self):
        res = self.answer(self.inside, choice=1).json()
        self.assertEqual((res['xp'], res['wrong_say'], res['wrong_audio']), (10, '', None))

    def test_a_hint_halves_the_xp(self):
        res = self.answer(self.inside, choice=1, hint_used=True).json()
        self.assertEqual((res['hint_used'], res['xp']), (True, 5))

    def test_there_is_no_hint_to_use_on_a_question_without_one(self):
        res = self.answer(self.homework_after_finishing(), choice=1, hint_used=True).json()
        self.assertEqual((res['hint_used'], res['xp']), (False, 10))

    def homework_after_finishing(self):
        self.finish()
        return self.homework


class LessonXPTest(StudentBase):
    def test_xp_adds_up_answers_and_finishing(self):
        LessonAnswer.objects.create(user=self.student, question=self.inside, choice=1, is_correct=True)
        LessonAnswer.objects.create(user=self.student, question=self.written, text='x', verdict='partly')
        self.finish()
        LessonAnswer.objects.create(user=self.student, question=self.homework, choice=1, is_correct=True, hint_used=True)

        self.assertEqual(xp.by_lesson(self.student), {self.target.id: 10 + 5 + 5 + xp.FINISH})
        self.assertEqual(xp.by_user()[self.student.id], 70)

        listed = self.client.get(STUDENT, **self.s).json()
        self.assertEqual(listed[0]['xp'], 70)
        detail = self.client.get(f'{STUDENT}{self.target.id}/', **self.s).json()
        self.assertEqual((detail['xp'], detail['xp_for_finishing']), (70, xp.FINISH))

    def test_another_students_answers_do_not_count(self):
        other = make_user('other@test.uz')
        LessonAnswer.objects.create(user=other, question=self.inside, choice=1, is_correct=True)
        LessonProgress.objects.create(user=other, lesson=self.target, completed_at='2026-01-01T00:00Z')
        self.assertEqual(xp.by_lesson(self.student), {})

    def test_the_leaderboard_counts_lesson_xp(self):
        LessonAnswer.objects.create(user=self.student, question=self.inside, choice=1, is_correct=True)
        rows = self.client.get('/api/admin/leaderboard/', **self.h).json()['leaderboard']
        mine = next(r for r in rows if r['user_id'] == self.student.id)
        self.assertEqual((mine['ai_lessons_xp'], mine['xp']), (10, 10))


@override_settings(MEDIA_ROOT=MEDIA)
class AdminFeedbackTest(StudentBase):
    def test_new_words_drop_the_old_recording(self):
        question = self.inside
        question.wrong_say = 'Old words.'
        question.wrong_audio.save('voice.mp3', ContentFile(b'ID3 test'), save=True)
        body = {'kind': 'CHOICE', 'prompt': '2 + 2?', 'options': ['3', '4'], 'correct': 1, 'step': self.step.id,
                'wrong_say': 'New words.', 'hint': 'Fingers.'}
        res = self.client.put(f'{BASE}questions/{question.id}/', data=body, content_type='application/json', **self.h)
        self.assertEqual(res.status_code, 200)
        question.refresh_from_db()
        self.assertEqual((question.wrong_say, question.hint, bool(question.wrong_audio)), ('New words.', 'Fingers.', False))
        row = next(q for q in res.json()['questions'] if q['id'] == question.id)
        self.assertEqual((row['wrong_say'], row['wrong_voiced']), ('New words.', False))

    def test_the_preview_marks_with_the_hint_too(self):
        LessonQuestion.objects.filter(pk=self.inside.pk).update(hint='Fingers.')
        res = self.client.post(f'{BASE}questions/{self.inside.id}/try/', data={'choice': 1, 'hint_used': True},
                               content_type='application/json', **self.h)
        self.assertEqual(res.json()['xp'], 5)


class VoicedFeedbackTest(PipelineBase):
    def setUp(self):
        super().setUp()
        self.generate()

    def test_the_voice_job_records_the_teachers_lines_and_the_praise(self):
        provider = self.use(ScriptedProvider())
        left = praise.missing(self.lesson.language, self.lesson.voice)
        self.approve()

        spoken = [text for text, _ in provider.spoken]
        question = self.lesson.questions.get(is_homework=False, kind='CHOICE', step__isnull=False)
        self.assertIn(question.wrong_say, spoken)
        self.assertTrue(question.wrong_audio.name.endswith('.mp3'))
        self.assertGreater(question.wrong_audio_seconds, 0)
        for _, line in left:
            self.assertIn(line, spoken)
        # homework is not spoken to
        homework = self.lesson.questions.filter(is_homework=True).exclude(wrong_say='')
        self.assertFalse(any(q.wrong_audio for q in homework))

        play = self.client.get(f'{BASE}{self.lesson.id}/play/', **self.h).json()
        self.assertEqual(len(play['praise']), len(praise.lines(self.lesson.language)))

    def test_praise_already_recorded_for_this_voice_is_not_paid_for_again(self):
        self.approve()                                      # records whatever praise was missing
        AILesson.objects.filter(pk=self.lesson.pk).update(status='REVIEW')
        self.lesson.steps.update(audio='')
        provider = self.use(ScriptedProvider())
        self.approve()
        spoken = [text for text, _ in provider.spoken]
        self.assertFalse(set(praise.lines(self.lesson.language)) & set(spoken))
