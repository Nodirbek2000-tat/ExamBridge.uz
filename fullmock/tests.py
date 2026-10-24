"""Full mock exams: building one, taking it in exam order, the final result."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cefr.models import (
    CEFRAttempt, CEFRListeningSection, CEFRReadingPassage, CEFRSpeakingResponse, CEFRSpeakingTest, CEFRTest,
    CEFRWritingResponse, CEFRWritingTest,
)
from ielts.models import (
    IELTSAttempt, IELTSTest, ListeningAnswer, ListeningQuestion, ListeningSection, ReadingAnswer,
    ReadingPassage, ReadingQuestion, SpeakingResponse, SpeakingTask, WritingResponse, WritingTask,
)

from .models import FullMock, FullMockAttempt
from .sections import ielts_round

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


class RoundingTest(TestCase):
    def test_ielts_overall_rounding(self):
        self.assertEqual(ielts_round(6.125), 6.0)
        self.assertEqual(ielts_round(6.25), 6.5)
        self.assertEqual(ielts_round(6.75), 7.0)
        self.assertEqual(ielts_round(6.6), 6.5)


class IELTSFullMockTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='a@t.uz', email='a@t.uz', password='x', is_staff=True)
        self.student = User.objects.create_user(username='s@t.uz', email='s@t.uz', password='x')
        self.other = User.objects.create_user(username='o@t.uz', email='o@t.uz', password='x')

        self.listening = IELTSTest.objects.create(title='L test', audio_url='https://cdn.example.com/a.mp3')
        self.l_qs = []
        for n in (1, 2):
            sec = ListeningSection.objects.create(test=self.listening, section_number=n, title=f'S{n}')
            self.l_qs.append(ListeningQuestion.objects.create(section=sec, number=n, question_type='GAP',
                                                              content='q', correct_answer='a'))
        self.reading = IELTSTest.objects.create(title='R test')
        self.r_qs = []
        for n in (1, 2, 3):
            p = ReadingPassage.objects.create(test=self.reading, title=f'P{n}', content='x', passage_number=n)
            self.r_qs.append(ReadingQuestion.objects.create(passage=p, number=n, question_type='GAP',
                                                            content='q', correct_answer='a'))
        self.w1 = WritingTask.objects.create(title='Chart', task_type=1, test_type='GENERAL', prompt='p')
        self.w2 = WritingTask.objects.create(title='Essay', task_type=2, prompt='p')
        self.sp = SpeakingTask.objects.create(title='Mock', test_type='MOCK', source='IELTS')

    def admin_post(self, url, data, method='post'):
        self.client.force_login(self.admin)
        return getattr(self.client, method)(url, data=data, content_type='application/json', **HEADERS)

    def build(self, publish=True):
        res = self.admin_post('/api/admin/ielts/full-mocks/', {'title': 'Real Mock 1', 'sections': {
            'listening': self.listening.id, 'reading': self.reading.id, 'writing1': self.w1.id,
            'writing2': self.w2.id, 'speaking': self.sp.id}})
        self.assertEqual(res.status_code, 201, res.content)
        mock_id = res.json()['id']
        if publish:
            res = self.admin_post(f'/api/admin/ielts/full-mocks/{mock_id}/', {'is_published': True}, 'patch')
            self.assertEqual(res.status_code, 200, res.content)
        return mock_id

    def link(self, attempt_id, step, ref_id, user=None):
        self.client.force_login(user or self.student)
        return self.client.post(f'/api/full-mocks/attempts/{attempt_id}/link/', data={'step': step, 'ref_id': ref_id},
                                content_type='application/json', **HEADERS)

    def detail(self, attempt_id):
        self.client.force_login(self.student)
        return self.client.get(f'/api/full-mocks/attempts/{attempt_id}/', **HEADERS).json()

    def test_cannot_publish_incomplete(self):
        res = self.admin_post('/api/admin/ielts/full-mocks/', {'title': 'Half', 'sections': {
            'listening': self.listening.id}})
        mock_id = res.json()['id']
        res = self.admin_post(f'/api/admin/ielts/full-mocks/{mock_id}/', {'is_published': True}, 'patch')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(any('Reading' in p for p in res.json()['problems']))

    def test_listening_without_audio_blocks_publishing(self):
        self.listening.audio_url = ''
        self.listening.save()
        mock_id = self.build(publish=False)
        res = self.admin_post(f'/api/admin/ielts/full-mocks/{mock_id}/', {'is_published': True}, 'patch')
        self.assertEqual(res.status_code, 400)

    def test_wrong_kind_of_test_refused(self):
        res = self.admin_post('/api/admin/ielts/full-mocks/', {'title': 'X', 'sections': {'writing1': self.w2.id}})
        self.assertEqual(res.status_code, 400)

    def test_students_only_see_published(self):
        self.build(publish=False)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/api/ielts/full-mocks/', **HEADERS).json(), [])
        self.build()
        self.assertEqual(len(self.client.get('/api/ielts/full-mocks/', **HEADERS).json()), 1)

    def test_full_run_in_exam_order(self):
        mock_id = self.build()
        self.client.force_login(self.student)
        fm = self.client.post(f'/api/ielts/full-mocks/{mock_id}/start/', **HEADERS).json()['attempt_id']
        # resuming gives the same rehearsal
        self.assertEqual(self.client.post(f'/api/ielts/full-mocks/{mock_id}/start/', **HEADERS).json()['attempt_id'], fm)

        # Reading cannot start before Listening is finished
        r_att = IELTSAttempt.objects.create(user=self.student, test=self.reading)
        self.assertEqual(self.link(fm, 'reading', r_att.id).status_code, 400)

        # Someone else's attempt is refused
        foreign = IELTSAttempt.objects.create(user=self.other, test=self.listening)
        self.assertEqual(self.link(fm, 'listening', foreign.id).status_code, 400)

        l_att = IELTSAttempt.objects.create(user=self.student, test=self.listening)
        self.assertEqual(self.link(fm, 'listening', l_att.id).status_code, 200)
        self.assertEqual(self.detail(fm)['current'], 'listening')
        for q in self.l_qs:
            ListeningAnswer.objects.create(attempt=l_att, question=q, answer='a', is_correct=True)
        l_att.listening_band = 7.0
        l_att.save()
        l_att.complete()
        self.assertEqual(self.detail(fm)['current'], 'reading')

        self.assertEqual(self.link(fm, 'reading', r_att.id).status_code, 200)
        for q in self.r_qs:
            ReadingAnswer.objects.create(attempt=r_att, question=q, answer='a', is_correct=True)
        r_att.reading_band = 6.5
        r_att.save()
        r_att.complete()

        w1 = IELTSAttempt.objects.create(user=self.student)
        self.assertEqual(self.link(fm, 'writing1', w1.id).status_code, 200)
        WritingResponse.objects.create(attempt=w1, task=self.w1, response_text='w ' * 160, ai_band=6.0)
        w2 = IELTSAttempt.objects.create(user=self.student)
        self.assertEqual(self.link(fm, 'writing2', w2.id).status_code, 200)
        WritingResponse.objects.create(attempt=w2, task=self.w2, response_text='w ' * 260, ai_band=7.0)

        sp = IELTSAttempt.objects.create(user=self.student)
        self.assertEqual(self.link(fm, 'speaking', sp.id).status_code, 200)
        resp = SpeakingResponse.objects.create(attempt=sp, task=self.sp)
        data = self.detail(fm)
        self.assertEqual(data['status'], 'COMPLETED')          # every section done…
        self.assertFalse(data['result']['complete'])           # …but speaking is still being scored
        self.assertEqual(data['steps'][-1]['response_id'], resp.id)

        resp.ai_band = 6.0
        resp.save()
        data = self.detail(fm)
        # writing = (6 + 2*7) / 3 = 6.67 → 6.5 ; overall = (7 + 6.5 + 6.5 + 6) / 4 = 6.5
        self.assertEqual(data['result']['sections']['writing'], 6.5)
        self.assertEqual(data['result']['overall'], 6.5)
        self.assertEqual(FullMockAttempt.objects.get(id=fm).result['overall'], 6.5)

        # a finished rehearsal cannot be changed
        self.assertEqual(self.link(fm, 'speaking', sp.id).status_code, 400)

    def test_old_practice_answer_does_not_count(self):
        # IELTS writing "start" can hand back an older practice attempt that
        # already has an essay — that essay must not finish the mock's section.
        from datetime import timedelta
        from .sections import step_by_key, step_state
        mock = FullMock.objects.get(id=self.build())
        old = IELTSAttempt.objects.create(user=self.student)
        WritingResponse.objects.create(attempt=old, task=self.w1, response_text='old essay', ai_band=5.0)
        step = step_by_key('ielts', 'writing1')
        later = timezone.now() + timedelta(seconds=1)
        self.assertEqual(step_state(mock, step, self.student, old.id, since=later)['state'], 'in_progress')
        self.assertEqual(step_state(mock, step, self.student, old.id)['state'], 'done')

    def test_premium_mock_needs_premium(self):
        mock_id = self.build()
        FullMock.objects.filter(id=mock_id).update(is_premium=True)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(f'/api/ielts/full-mocks/{mock_id}/start/', **HEADERS).status_code, 403)

    def test_student_cannot_use_admin_api(self):
        self.client.force_login(self.student)
        res = self.client.post('/api/admin/ielts/full-mocks/', data={'title': 'x'},
                               content_type='application/json', **HEADERS)
        self.assertEqual(res.status_code, 403)


class CEFRFullMockTest(TestCase):
    def test_overall_score_and_level(self):
        admin = User.objects.create_user(username='a@t.uz', email='a@t.uz', password='x', is_staff=True)
        student = User.objects.create_user(username='s@t.uz', email='s@t.uz', password='x')
        lt = CEFRTest.objects.create(title='L', level='B2', test_type='LISTENING', audio_url='https://x.uz/a.mp3')
        CEFRListeningSection.objects.create(test=lt, title='p1')
        rt = CEFRTest.objects.create(title='R', level='B2', test_type='READING')
        CEFRReadingPassage.objects.create(test=rt, title='p1', content='x')
        wt = CEFRWritingTest.objects.create(title='W', kind='FULL', task11='a', task12='b', task2='c')
        st = CEFRSpeakingTest.objects.create(title='S', part11=['q'], part2={'prompt': 'p'},
                                             part3={'topic': 't'}, part12=[])
        self.client.force_login(admin)
        mock = self.client.post('/api/admin/cefr/full-mocks/', data={'title': 'CEFR 1', 'sections': {
            'listening': lt.id, 'reading': rt.id, 'writing': wt.id, 'speaking': st.id}},
            content_type='application/json', **HEADERS).json()
        FullMock.objects.filter(id=mock['id']).update(is_published=True)

        self.client.force_login(student)
        fm = self.client.post(f"/api/cefr/full-mocks/{mock['id']}/start/", **HEADERS).json()['attempt_id']
        la = CEFRAttempt.objects.create(user=student, test=lt, attempt_type='LISTENING', correct_count=26,
                                        total_count=35, status='COMPLETED', finished_at=timezone.now())
        ra = CEFRAttempt.objects.create(user=student, test=rt, attempt_type='READING', correct_count=26,
                                        total_count=35, status='COMPLETED', finished_at=timezone.now())
        wr = CEFRWritingResponse.objects.create(user=student, test=wt, status='READY', score=60,
                                                submitted_at=timezone.now())
        sr = CEFRSpeakingResponse.objects.create(user=student, test=st, status='READY', score=66,
                                                 submitted_at=timezone.now())
        for step, ref in (('listening', la.id), ('reading', ra.id), ('writing', wr.id), ('speaking', sr.id)):
            res = self.client.post(f'/api/full-mocks/attempts/{fm}/link/', data={'step': step, 'ref_id': ref},
                                   content_type='application/json', **HEADERS)
            self.assertEqual(res.status_code, 200, (step, res.content))
        result = res.json()['result']
        # listening 26 → 65, reading 26 → 65, writing 60, speaking 66 → 64 → B2
        self.assertEqual(result['sections'], {'listening': 65, 'reading': 65, 'writing': 60, 'speaking': 66})
        self.assertEqual(result['overall'], 64)
        self.assertEqual(result['level'], 'B2')
