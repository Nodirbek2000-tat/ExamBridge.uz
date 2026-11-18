"""How many times each mock and part was done, in the admin reading/listening lists."""
from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}


class AdminUsageCountsTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='a@t.uz', email='a@t.uz', password='x',
                                              is_staff=True, is_superuser=True)
        self.ali = User.objects.create_user(username='ali@t.uz', email='ali@t.uz', password='x')
        self.vali = User.objects.create_user(username='vali@t.uz', email='vali@t.uz', password='x')
        self.client.force_login(self.admin)

    def get(self, url):
        res = self.client.get(url, **HEADERS)
        self.assertEqual(res.status_code, 200, res.content)
        return {row['id']: row for row in res.json()}

    def test_ielts_reading_counts_finished_attempts_and_students(self):
        from ielts.models import IELTSAttempt, IELTSTest, ReadingPassage
        test = IELTSTest.objects.create(title='Cambridge 9 Test 4')
        in_mock = ReadingPassage.objects.create(test=test, title='P1', content='x', passage_number=1)
        practice = ReadingPassage.objects.create(title='Practice', content='x', is_standalone=True, passage_number=2)
        done = IELTSAttempt.Status.COMPLETED
        IELTSAttempt.objects.create(user=self.ali, test=test, status=done, reading_band=6.5)
        IELTSAttempt.objects.create(user=self.ali, test=test, status=done, reading_band=7.0)
        IELTSAttempt.objects.create(user=self.vali, test=test, status=done, reading_band=5.5)
        IELTSAttempt.objects.create(user=self.vali, test=test, status=done, listening_band=6.0)      # listening, not reading
        IELTSAttempt.objects.create(user=self.vali, test=test)                                        # not finished
        IELTSAttempt.objects.create(user=self.ali, reading_passage=practice, status=done)

        rows = self.get('/api/admin/ielts/reading/')
        self.assertEqual(rows[in_mock.id]['test_done'], {'times': 3, 'students': 2})
        self.assertEqual(rows[practice.id]['done'], {'times': 1, 'students': 1})
        self.assertEqual(rows[practice.id]['test_done'], {'times': 0, 'students': 0})

        listening = self.get('/api/admin/ielts/listening/')
        self.assertEqual(listening, {})                      # no listening sections: nothing to list

    def test_cefr_listening_separates_mock_and_practice(self):
        from cefr.models import CEFRAttempt, CEFRListeningSection, CEFRTest
        test = CEFRTest.objects.create(title='Listening Mock 1', level='B2', test_type='LISTENING')
        part = CEFRListeningSection.objects.create(test=test, title='p1', section_number=1)
        practice = CEFRListeningSection.objects.create(title='Practice p3', section_number=3, is_standalone=True)
        done = CEFRAttempt.Status.COMPLETED
        CEFRAttempt.objects.create(user=self.ali, test=test, attempt_type='LISTENING', status=done)
        CEFRAttempt.objects.create(user=self.ali, test=test, attempt_type='READING', status=done)      # another skill
        CEFRAttempt.objects.create(user=self.vali, listening_section=practice, attempt_type='LISTENING', status=done)
        CEFRAttempt.objects.create(user=self.vali, listening_section=practice, attempt_type='LISTENING', status=done)

        rows = self.get('/api/admin/cefr/listening/')
        self.assertEqual(rows[part.id]['test_done'], {'times': 1, 'students': 1})
        self.assertEqual(rows[practice.id]['done'], {'times': 2, 'students': 1})

    def test_ielts_writing_counts_attempts_that_answered_the_task(self):
        from ielts.models import IELTSAttempt, WritingResponse, WritingTask
        task = WritingTask.objects.create(title='Task 2: cities', prompt='Discuss.', task_type=2)
        first = IELTSAttempt.objects.create(user=self.ali)
        WritingResponse.objects.create(attempt=first, task=task, response_text='a b c')
        WritingResponse.objects.create(attempt=IELTSAttempt.objects.create(user=self.ali), task=task, response_text='d')
        rows = self.get('/api/admin/ielts/writing/')
        self.assertEqual(rows[task.id]['done'], {'times': 2, 'students': 1})
