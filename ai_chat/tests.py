"""AI Tutor: free accounts get FREE_MESSAGES messages in total, with short answers."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from .models import AIChatSent, AIConversation, AIMessage
from .quota import FREE_MESSAGES

User = get_user_model()
HEADERS = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
CHAT = '/api/ai/chat/'
QUOTA = '/api/ai/quota/'


def chunk(text):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeOpenAI:
    """Stands in for the OpenAI client and keeps the arguments of every call."""
    calls = []
    fail = False

    def __init__(self, api_key=None):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        FakeOpenAI.calls.append(kwargs)
        if FakeOpenAI.fail:
            raise RuntimeError('provider down')
        return iter([chunk('Short '), chunk('answer.')])


class ChatLimitTest(TestCase):
    def setUp(self):
        FakeOpenAI.calls = []
        FakeOpenAI.fail = False
        patcher = patch('api.ai_views.OpenAI', FakeOpenAI)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = self.make('free@test.uz')

    def make(self, email, **extra):
        return User.objects.create_user(username=email, email=email, password='x', **extra)

    def h(self, user=None):
        return {**HEADERS, 'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user or self.user)}'}

    def send(self, text='Explain the present perfect', user=None, **data):
        response = self.client.post(CHAT, {'subject': 'IELTS', 'message': text, **data}, **self.h(user))
        if response.status_code != 200:
            return response, []
        events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().split('\n')
                  if line.startswith('data: ')]
        return response, events

    def quota(self, user=None):
        return self.client.get(QUOTA, **self.h(user)).json()

    def test_a_free_account_gets_five_messages_in_total_across_chats(self):
        self.assertEqual(self.quota(), {'unlimited': False, 'limit': FREE_MESSAGES, 'used': 0, 'left': FREE_MESSAGES})
        response, events = self.send()
        self.assertEqual(response.status_code, 200)
        conv = events[0]['id']
        self.assertEqual(next(e for e in events if e['type'] == 'quota')['left'], FREE_MESSAGES - 1)
        self.assertEqual(events[-1]['type'], 'done')
        self.send(conversation_id=conv)
        for _ in range(FREE_MESSAGES - 2):           # every new chat counts against the same five
            self.send()
        self.assertEqual(self.quota()['left'], 0)

        response, _ = self.send()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'premium_required')
        self.assertEqual(len(FakeOpenAI.calls), FREE_MESSAGES)      # the sixth never reached the AI
        self.assertEqual(AIMessage.objects.filter(role='user', conversation__user=self.user).count(), FREE_MESSAGES)

    def test_deleting_chats_does_not_give_messages_back(self):
        for _ in range(FREE_MESSAGES):
            self.send()
        for conv in AIConversation.objects.filter(user=self.user):
            self.client.delete(f'/api/ai/conversations/{conv.id}/', **self.h())
        self.assertEqual(self.quota()['left'], 0)
        self.assertEqual(self.send()[0].status_code, 403)

    def test_free_answers_are_short(self):
        self.send()
        call = FakeOpenAI.calls[-1]
        self.assertEqual(call['max_tokens'], 600)
        self.assertIn('free plan', call['messages'][0]['content'])

    def test_premium_and_staff_are_not_limited_and_get_full_answers(self):
        premium = self.make('premium@test.uz', is_premium=True,
                            premium_until=timezone.now() + timedelta(days=30))
        staff = self.make('staff@test.uz', is_staff=True)
        for user in (premium, staff):
            self.assertTrue(self.quota(user)['unlimited'])
            for _ in range(FREE_MESSAGES + 1):
                self.assertEqual(self.send(user=user)[0].status_code, 200)
        call = FakeOpenAI.calls[-1]
        self.assertEqual(call['max_tokens'], 2048)
        self.assertNotIn('free plan', call['messages'][0]['content'])
        self.assertFalse(AIChatSent.objects.filter(user__in=[premium, staff]).exists())

    def test_an_expired_premium_is_limited_again(self):
        user = self.make('expired@test.uz', is_premium=True, premium_until=timezone.now() - timedelta(days=1))
        self.assertFalse(self.quota(user)['unlimited'])

    def test_a_failed_answer_does_not_count(self):
        FakeOpenAI.fail = True
        _, events = self.send()
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(self.quota()['left'], FREE_MESSAGES)

    def test_a_missing_chat_does_not_count(self):
        response, _ = self.send(conversation_id=999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.quota()['left'], FREE_MESSAGES)
