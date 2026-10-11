"""MHEAD (headings dragged into [[N]] slots of the passage): import checks and scoring."""

import copy
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from ielts.models import ReadingQuestion

HEADINGS = [{'option': 'A', 'text': 'First heading'}, {'option': 'B', 'text': 'Second heading'},
            {'option': 'C', 'text': 'Unused heading'}]

PASSAGE = {
    'title': 'Slots', 'passage_number': 2,
    'content': 'Intro\n\n[[14]]\nParagraph one.\n\n[[15]]\nParagraph two.',
    'questions': [
        {'number': 14, 'question_type': 'MHEAD', 'correct_answer': 'B',
         'group_instruction': 'Questions 14–15\nChoose the correct heading.', 'choices': HEADINGS},
        {'number': 15, 'question_type': 'heading_drag', 'correct_answer': 'A'},
    ],
}


class HeadingDragImportTest(TestCase):
    HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='mhead_admin', email='mhead_admin@exambridge.test', password='x')
        self.client.force_login(self.admin)

    def _post(self, payload):
        return self.client.post('/api/import/ielts/reading/', data=json.dumps(payload),
                                content_type='application/json', **self.HEADERS)

    def test_single_passage_imports_and_scores(self):
        res = self._post(PASSAGE)
        self.assertEqual(res.status_code, 200, res.content)
        qs = {q.number: q for q in ReadingQuestion.objects.filter(passage_id=res.json()['id'])}
        self.assertEqual({q.question_type for q in qs.values()}, {'MHEAD'})  # alias normalised
        self.assertEqual(qs[14].choices.count(), 3)

        start = self.client.post(f"/api/ielts/reading/{res.json()['id']}/start/", **self.HEADERS).json()
        detail = self.client.get(f"/api/ielts/reading/{res.json()['id']}/", **self.HEADERS).json()
        self.assertIn('[[14]]', detail['content'])
        answers = {str(qs[14].id): 'b', str(qs[15].id): 'C'}
        sub = self.client.post(f"/api/ielts/reading/{res.json()['id']}/submit/",
                               data=json.dumps({'attempt_id': start['attempt_id'], 'answers': answers}),
                               content_type='application/json', **self.HEADERS)
        self.assertEqual(sub.status_code, 200, sub.content)
        self.assertEqual(sub.json()['correct'], 1)

    def test_full_mock_imports(self):
        mock = {'title': 'Mock', 'parts': [dict(copy.deepcopy(PASSAGE), passage_number=2)]}
        res = self._post(mock)
        self.assertEqual(res.status_code, 200, res.content)

    def test_missing_slot_is_rejected(self):
        bad = copy.deepcopy(PASSAGE)
        bad['content'] = 'Intro\n\n[[14]]\nOnly one slot.'
        res = self._post(bad)
        self.assertEqual(res.status_code, 400)
        self.assertIn('[[15]]', res.json()['error'])

    def test_slot_without_question_is_rejected_in_mock(self):
        bad = copy.deepcopy(PASSAGE)
        bad['content'] += '\n\n[[16]]\nExtra.'
        res = self._post({'title': 'Mock', 'parts': [bad]})
        self.assertEqual(res.status_code, 400)
        self.assertIn('[[16]]', res.json()['error'])

    def test_headings_list_is_required(self):
        bad = copy.deepcopy(PASSAGE)
        del bad['questions'][0]['choices']
        res = self._post(bad)
        self.assertEqual(res.status_code, 400)
        self.assertIn('choices', res.json()['error'])
