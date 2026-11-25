"""The bot's morning message, and signing in when the site is opened from it inside Telegram."""
import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.devices import TELEGRAM_APP, device_id
from accounts.models import UserSession

from . import daily
from .models import DailyPost, TelegramAccount
from .site_views import telegram_user

User = get_user_model()
TOKEN = '123456:TEST-token-for-the-daily-message'
JSON = 'application/json'
CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
MONDAY = date(2026, 11, 23)


def student(email, telegram_id, active=True):
    user = User.objects.create_user(username=email, email=email, password='pass-12345', is_active=active)
    TelegramAccount.objects.create(user=user, telegram_id=telegram_id)
    return user


def init_data(telegram_id, token=TOKEN, auth_date=None, **extra):
    """What Telegram puts into a Mini App opened from our bot, signed like Telegram signs it."""
    fields = {
        'query_id': 'AAHdF6IQAAAAAN0XohDhrOrc',
        'user': json.dumps({'id': telegram_id, 'first_name': 'Ali', 'language_code': 'uz'}),
        'auth_date': str(int(auth_date if auth_date is not None else time.time())),
        **extra,
    }
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class PickTest(TestCase):
    def test_each_weekday_has_its_theme_and_a_day_always_gets_the_same_message(self):
        for offset, theme in enumerate(daily.WEEK):
            self.assertTrue(daily.pick(MONDAY + timedelta(days=offset))['key'].startswith(f'{theme}:'))
        self.assertEqual(daily.pick(MONDAY), daily.pick(MONDAY))

    def test_a_message_comes_back_only_after_the_rest_of_its_theme(self):
        options = len(daily.MESSAGES['speaking'])
        keys = []
        for week in range(options):
            day = MONDAY + timedelta(weeks=week)
            message = daily.pick(day)
            DailyPost.objects.create(date=day, **message)
            keys.append(message['key'])
        self.assertEqual(len(set(keys)), options)

    def test_the_motivation_line_does_not_repeat_for_weeks(self):
        quotes = []
        for n in range(len(daily.QUOTES)):
            day = MONDAY + timedelta(days=n)
            message = daily.pick(day)
            DailyPost.objects.create(date=day, **message)
            quotes.append(message['quote'])
        self.assertEqual(len(set(quotes)), len(daily.QUOTES))

    def test_the_speaking_partner_day_has_a_name_and_opens_a_speaking_page(self):
        message = daily.pick(MONDAY + timedelta(days=3))
        self.assertNotIn('{name}', message['text'])
        self.assertTrue(any(name in message['text'] for name in daily.NAMES))
        self.assertIn(message['url'], {'https://exambridge.uz/app/ielts/skills?tab=speaking',
                                       'https://exambridge.uz/games/speaking'})

    def test_the_speaking_game_comes_up_on_speaking_partner_and_easy_days(self):
        for theme in ('speaking', 'partner', 'easy'):
            game = [m for m in daily.MESSAGES[theme] if m[2] == daily.GAME_SPEAKING]
            self.assertGreaterEqual(len(game), 2, theme)
            self.assertTrue(all(button == daily.READ for _, button, _ in game), theme)
        self.assertTrue(all('{name}' in text for text, _, page in daily.MESSAGES['partner']
                            if page == daily.GAME_SPEAKING))

    @override_settings(FRONTEND_URL='http://localhost:5173')
    def test_the_button_always_opens_an_https_page(self):
        self.assertTrue(daily.pick(MONDAY)['url'].startswith('https://exambridge.uz/'))


class FakeTelegram:
    """Stands in for requests.Session: answers like the Bot API and keeps what was sent."""

    def __init__(self, answers):
        self.answers = answers          # chat_id → list of answers, one per try
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, timeout=None):
        self.sent.append(json)
        queue = self.answers.get(json['chat_id'], [])
        answer = queue.pop(0) if queue else {'ok': True, 'result': {}}
        return mock.Mock(json=mock.Mock(return_value=answer))


@override_settings(TELEGRAM_BOT_TOKEN=TOKEN)
class SendTest(TestCase):
    def setUp(self):
        student('a@mail.uz', 111)
        student('b@mail.uz', 222)
        student('c@mail.uz', 333)
        student('gone@mail.uz', 444, active=False)          # deactivated accounts get nothing
        self.telegram = FakeTelegram({
            222: [{'ok': False, 'error_code': 403, 'description': 'Forbidden: bot was blocked by the user'}],
            333: [{'ok': False, 'error_code': 429, 'parameters': {'retry_after': 1}}, {'ok': True}],
        })
        for patcher in (mock.patch('tgbot.daily.requests.Session', return_value=self.telegram),
                        mock.patch('tgbot.daily.time.sleep')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_student_gets_it_once_with_a_button_that_opens_the_site_in_telegram(self):
        post = daily.send_today(MONDAY)
        self.assertEqual((post.total, post.sent, post.gone, post.failed), (3, 2, 1, 0))
        self.assertIsNotNone(post.finished_at)
        self.assertEqual(sorted({m['chat_id'] for m in self.telegram.sent}), [111, 222, 333])
        button = self.telegram.sent[0]['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['web_app']['url'], post.url)
        self.assertEqual(self.telegram.sent[0]['parse_mode'], 'HTML')

        self.assertIsNone(daily.send_today(MONDAY))          # the second run that day sends nothing
        self.assertEqual(len(self.telegram.sent), 4)          # 3 students, one of them asked twice (429)
        self.assertEqual(DailyPost.objects.count(), 1)

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_without_a_bot_token_nothing_happens(self):
        self.assertIsNone(daily.send_today(MONDAY))
        self.assertFalse(DailyPost.objects.exists())

    def test_the_scheduled_task_sends_todays(self):
        from .tasks import send_daily_message
        send_daily_message()
        self.assertEqual(DailyPost.objects.get().sent, 2)


class InitDataTest(TestCase):
    def test_telegrams_signature_is_checked(self):
        self.assertEqual(telegram_user(init_data(111), TOKEN)['id'], 111)
        self.assertIsNone(telegram_user(init_data(111, token='999:other-bot'), TOKEN))
        self.assertIsNone(telegram_user(init_data(111).replace('111', '112', 1), TOKEN))
        self.assertIsNone(telegram_user('', TOKEN))
        self.assertIsNone(telegram_user('not a query string', TOKEN))

    def test_an_old_one_is_refused(self):
        self.assertIsNone(telegram_user(init_data(111, auth_date=time.time() - 2 * 86400), TOKEN))

    def test_newer_fields_are_part_of_the_signature(self):
        self.assertEqual(telegram_user(init_data(111, signature='abc', chat_type='sender'), TOKEN)['id'], 111)


@override_settings(TELEGRAM_BOT_TOKEN=TOKEN)
class MiniAppSignInTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = student('s@mail.uz', 555)

    def open(self, data):
        return self.client.post('/api/auth/telegram/webapp/', {'init_data': data}, content_type=JSON,
                                HTTP_USER_AGENT=CHROME, HTTP_X_DEVICE_ID='device-telegram-view', HTTP_ACCEPT=JSON)

    def login(self, device):
        res = self.client.post('/api/auth/login/', {'email': 's@mail.uz', 'password': 'pass-12345'}, content_type=JSON,
                               HTTP_USER_AGENT=CHROME, HTTP_X_DEVICE_ID=device, HTTP_ACCEPT=JSON)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def me(self, tokens):
        return self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f"Bearer {tokens['access']}",
                               HTTP_USER_AGENT=CHROME, HTTP_ACCEPT=JSON)

    def test_the_students_own_telegram_signs_them_in(self):
        res = self.open(init_data(555))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['user']['email'], 's@mail.uz')
        self.assertEqual(self.me(res.json()).status_code, 200)
        self.assertEqual(UserSession.objects.get(user=self.user).device_id, TELEGRAM_APP)

    def test_it_takes_no_device_place_and_signs_no_device_out(self):
        laptop, phone = self.login('device-laptop'), self.login('device-phone')
        first = self.open(init_data(555)).json()
        again = self.open(init_data(555)).json()            # opened again tomorrow: replaces its own session
        self.assertEqual(self.me(laptop).status_code, 200)
        self.assertEqual(self.me(phone).status_code, 200)
        self.assertEqual(self.me(again).status_code, 200)
        self.assertEqual(self.me(first).status_code, 401)
        live = UserSession.objects.filter(user=self.user, revoked_at__isnull=True)
        self.assertEqual(live.count(), 3)
        # and a third real device still signs the oldest real one out, not counting Telegram
        self.login('device-tablet')
        self.assertEqual(live.exclude(device_id=TELEGRAM_APP).count(), 2)
        self.assertEqual(self.me(again).status_code, 200)

    def test_no_browser_can_pretend_to_be_the_mini_app(self):
        request = mock.Mock(META={'HTTP_X_DEVICE_ID': TELEGRAM_APP, 'HTTP_USER_AGENT': CHROME})
        self.assertNotEqual(device_id(request), TELEGRAM_APP)

    def test_a_forged_or_unknown_telegram_is_refused(self):
        self.assertEqual(self.open(init_data(555, token='999:other-bot')).json()['code'], 'bad_init_data')
        res = self.open(init_data(777))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['code'], 'not_linked')
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.open(init_data(555)).status_code, 404)
