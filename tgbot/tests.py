"""
Telegram bot API, card payments and the one-time links between bot and site.
"""
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from pricing import card
from pricing.models import CardPayment, PaymentSettings, Subscription

from .models import BotChannel, TelegramAccount, TelegramLogin, TelegramToken, bot_email

User = get_user_model()

SECRET = 'test-bot-secret-0123456789'
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
BOT = {'HTTP_X_BOT_SECRET': SECRET, 'HTTP_USER_AGENT': 'Python/3.11 aiohttp/3.8.6', 'HTTP_ACCEPT': 'application/json'}
BROWSER = {'HTTP_USER_AGENT': 'Mozilla/5.0 (test)', 'HTTP_ACCEPT': 'application/json'}
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')


def receipt(name='chek.png', content=PNG):
    return SimpleUploadedFile(name, content, content_type='image/png')


@override_settings(
    CACHES=LOCMEM,
    TELEGRAM_BOT_API_SECRET=SECRET,
    TELEGRAM_BOT_TOKEN='',
    TELEGRAM_BOT_USERNAME='exambridge_test_bot',
    TELEGRAM_PAYMENTS_CHAT='@payments_test',
    FRONTEND_URL='https://exambridge.uz',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class BotTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # Receipts are written to a throwaway folder, never the real media.
        media = tempfile.mkdtemp(prefix='eb-receipts-')
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        config = PaymentSettings.load()
        config.card_number = '8600123412341234'
        config.card_holder = 'Test Owner'
        config.save()

    def bot_post(self, path, data=None, **extra):
        return self.client.post(f'/api/bot/{path}', data=data or {}, content_type='application/json', **BOT, **extra)

    def bot_get(self, path):
        return self.client.get(f'/api/bot/{path}', **BOT)

    def register(self, tid=1001, phone='+998 90 123 45 67', **profile):
        return self.bot_post('users/register/', {'telegram_id': tid, 'phone': phone, 'first_name': 'Ali', **profile})

    def pay(self, tid=1001, plan='1month', file=None):
        return self.client.post('/api/bot/payments/', {
            'telegram_id': tid, 'plan': plan, 'receipt': file or receipt(),
        }, **BOT)

    def site_user(self, email='student@mail.uz', **extra):
        return User.objects.create_user(username=email, email=email, password='pass-12345', **extra)

    def as_user(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}', **BROWSER}


class BotAuthTest(BotTestCase):
    def test_requests_without_the_secret_are_refused(self):
        self.assertEqual(self.client.get('/api/bot/plans/', **BROWSER).status_code, 403)
        wrong = {**BOT, 'HTTP_X_BOT_SECRET': 'nope'}
        self.assertEqual(self.client.get('/api/bot/plans/', **wrong).status_code, 403)

    @override_settings(TELEGRAM_BOT_API_SECRET='')
    def test_no_secret_configured_means_closed(self):
        self.assertEqual(self.client.get('/api/bot/plans/', **{**BOT, 'HTTP_X_BOT_SECRET': ''}).status_code, 403)

    def test_bot_user_agent_is_not_blocked_as_a_scraper(self):
        # aiohttp is on the scraper list for normal API paths.
        self.assertEqual(self.bot_get('plans/').status_code, 200)
        self.assertEqual(
            self.client.get('/api/payments/plans/', HTTP_USER_AGENT='Python/3.11 aiohttp/3.8.6',
                            HTTP_ACCEPT='application/json').status_code,
            403,
        )


class RegisterTest(BotTestCase):
    def test_sync_before_and_after_registration(self):
        self.assertEqual(self.bot_post('users/sync/', {'telegram_id': 1001}).json(), {'registered': False})
        res = self.register(username='ali_uz', last_name='Valiyev')
        self.assertEqual(res.status_code, 201)
        account = res.json()['account']
        self.assertEqual(account['phone'], '+998901234567')
        self.assertIsNone(account['email'])
        self.assertFalse(account['is_premium'])

        user = User.objects.get(email=bot_email(1001))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name, 'Ali')

        synced = self.bot_post('users/sync/', {'telegram_id': 1001, 'username': 'new_name'}).json()
        self.assertTrue(synced['registered'])
        self.assertEqual(TelegramAccount.objects.get(telegram_id=1001).username, 'new_name')

    def test_registering_twice_returns_the_same_account(self):
        self.register()
        res = self.register()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['created'])
        self.assertEqual(User.objects.filter(email=bot_email(1001)).count(), 1)

    def test_a_phone_number_is_required(self):
        self.assertEqual(self.register(phone='12').status_code, 400)
        self.assertEqual(self.bot_post('users/register/', {'phone': '+998901234567'}).status_code, 400)


class LinkAndLoginTest(BotTestCase):
    def test_site_account_links_through_the_bot(self):
        user = self.site_user()
        res = self.client.post('/api/payments/telegram/', **self.as_user(user))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['linked'])
        url = res.json()['url']
        self.assertTrue(url.startswith('https://t.me/exambridge_test_bot?start=link_'))
        token = url.split('link_', 1)[1]
        self.assertLessEqual(len('link_' + token), 64)

        linked = self.bot_post('users/link/', {'token': token, 'telegram_id': 2002, 'first_name': 'Vali'})
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.json()['account']['email'], 'student@mail.uz')
        self.assertEqual(TelegramAccount.objects.get(telegram_id=2002).user, user)

        # The token works once.
        again = self.bot_post('users/link/', {'token': token, 'telegram_id': 2002})
        self.assertEqual(again.json()['code'], 'bad_token')
        # Once linked, the site opens the bot straight on the Premium menu.
        self.assertEqual(
            self.client.post('/api/payments/telegram/', **self.as_user(user)).json()['url'],
            'https://t.me/exambridge_test_bot?start=premium',
        )

    def test_a_telegram_linked_to_someone_else_is_not_taken_over(self):
        self.register(tid=3003)
        user = self.site_user()
        token = TelegramToken.issue(user, TelegramToken.LINK).token
        res = self.bot_post('users/link/', {'token': token, 'telegram_id': 3003})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'telegram_taken')

    def test_an_expired_link_token_fails(self):
        user = self.site_user()
        row = TelegramToken.issue(user, TelegramToken.LINK)
        TelegramToken.objects.filter(pk=row.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.bot_post('users/link/', {'token': row.token, 'telegram_id': 4}).status_code, 400)

    def test_bot_sign_in_link_works_once(self):
        self.register()
        url = self.bot_post('users/login-link/', {'telegram_id': 1001}).json()['url']
        self.assertTrue(url.startswith('https://exambridge.uz/auth/telegram?token='))
        token = url.split('token=', 1)[1]

        res = self.client.post('/api/auth/telegram/', {'token': token}, content_type='application/json', **BROWSER)
        self.assertEqual(res.status_code, 200)
        self.assertIn('access', res.json())
        self.assertEqual(res.json()['user']['id'], User.objects.get(email=bot_email(1001)).id)

        again = self.client.post('/api/auth/telegram/', {'token': token}, content_type='application/json', **BROWSER)
        self.assertEqual(again.status_code, 400)

    def test_a_link_token_cannot_be_used_to_sign_in(self):
        user = self.site_user()
        token = TelegramToken.issue(user, TelegramToken.LINK).token
        res = self.client.post('/api/auth/telegram/', {'token': token}, content_type='application/json', **BROWSER)
        self.assertEqual(res.status_code, 400)

    def test_login_link_needs_registration(self):
        self.assertEqual(self.bot_post('users/login-link/', {'telegram_id': 999}).status_code, 404)


class PaymentTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.user = User.objects.get(email=bot_email(1001))

    def test_plans_show_card_and_prices(self):
        data = self.bot_get('plans/').json()
        self.assertTrue(data['accepting'])
        self.assertEqual(data['card']['number'], '8600123412341234')
        self.assertEqual({p['id']: p['price'] for p in data['plans']}, {'1month': 49000, '3month': 119000})

        public = self.client.get('/api/payments/plans/', **BROWSER).json()
        self.assertNotIn('card', public)
        self.assertEqual(public['bot_url'], 'https://t.me/exambridge_test_bot')
        self.assertEqual(public['refund_hours'], 24)

    def test_receipt_creates_a_pending_payment_and_posts_it(self):
        with mock.patch('pricing.card.telegram.send_file', return_value={'chat': {'id': -100777}, 'message_id': 55}) as send:
            res = self.pay(plan='3month')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['amount'], 119000)
        self.assertTrue(res.json()['posted'])
        payment = CardPayment.objects.get()
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.days, 90)
        self.assertEqual((payment.channel_chat_id, payment.channel_message_id), (-100777, 55))
        chat, _file, caption, buttons = send.call_args.args
        self.assertEqual(chat, '@payments_test')
        self.assertIn('119 000', caption)
        self.assertIn('+998901234567', caption)
        self.assertEqual(buttons[0][0]['callback_data'], f'pay:ok:{payment.id}')

    def test_receipt_rules(self):
        # No bot token in tests, so nothing reaches the channel: the bot must be told.
        self.assertFalse(self.pay().json()['posted'])
        CardPayment.objects.all().delete()
        self.assertEqual(self.pay(file=receipt('chek.exe')).json()['code'], 'bad_receipt')
        self.assertEqual(self.pay(plan='12month').json()['code'], 'bad_plan')
        for _ in range(card.PENDING_LIMIT):
            self.assertEqual(self.pay().status_code, 201)
        self.assertEqual(self.pay().status_code, 429)
        self.assertEqual(self.pay(tid=5555).status_code, 404)

    def test_closed_when_no_card(self):
        PaymentSettings.objects.filter(pk=1).update(card_number='')
        self.assertEqual(self.pay().json()['code'], 'closed')
        self.assertFalse(self.bot_get('plans/').json()['accepting'])

    def test_approve_grants_premium_and_tells_everyone(self):
        with mock.patch('pricing.card.telegram.send_file', return_value={'chat': {'id': -1007}, 'message_id': 9}):
            pid = self.pay(plan='1month').json()['id']
        with mock.patch('pricing.card.telegram.edit_caption') as edit, \
                mock.patch('pricing.card.telegram.send_message') as send, \
                self.captureOnCommitCallbacks(execute=True):
            res = self.bot_post(f'payments/{pid}/approve/', {'admin_telegram_id': 1, 'admin_name': '@boss'})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_premium)
        self.assertEqual(self.user.premium_source, 'card')
        self.assertAlmostEqual(
            (self.user.premium_until - timezone.now()).total_seconds(), timedelta(days=30).total_seconds(), delta=60,
        )
        self.assertTrue(Subscription.objects.filter(user=self.user, status='ACTIVE', payment_ref=f'card:{pid}').exists())

        self.assertIn('Tasdiqlandi', edit.call_args.args[2])
        chat_id, text, buttons = send.call_args.args
        self.assertEqual(chat_id, 1001)
        self.assertIn('Premium faollashtirildi', text)
        self.assertTrue(buttons[0][0]['url'].startswith('https://exambridge.uz/auth/telegram?token='))

        # A second decision is refused — two admins pressing at once grant once.
        again = self.bot_post(f'payments/{pid}/approve/', {'admin_telegram_id': 2})
        self.assertEqual(again.status_code, 409)

    def test_premium_is_extended_not_restarted(self):
        until = timezone.now() + timedelta(days=10)
        User.objects.filter(pk=self.user.pk).update(is_premium=True, premium_until=until)
        pid = self.pay(plan='3month').json()['id']
        self.bot_post(f'payments/{pid}/approve/', {})
        self.user.refresh_from_db()
        self.assertAlmostEqual(
            (self.user.premium_until - until).total_seconds(), timedelta(days=90).total_seconds(), delta=5,
        )

    def test_lifetime_premium_is_left_alone(self):
        User.objects.filter(pk=self.user.pk).update(is_premium=True, premium_until=None)
        pid = self.pay().json()['id']
        res = self.bot_post(f'payments/{pid}/approve/', {})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_premium)
        self.assertIsNone(self.user.premium_until)

    def test_reject_keeps_premium_closed_and_explains(self):
        pid = self.pay().json()['id']
        with mock.patch('pricing.card.telegram.send_message') as send, self.captureOnCommitCallbacks(execute=True):
            res = self.bot_post(f'payments/{pid}/reject/', {'reason': 'amount', 'admin_name': '@boss'})
        self.assertEqual(res.json()['reject_reason'], card.REJECT_REASONS['amount'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_premium)
        self.assertIn(card.REJECT_REASONS['amount'], send.call_args.args[1])
        self.assertEqual(self.bot_post(f'payments/{pid}/approve/', {}).status_code, 409)
        self.assertEqual(self.bot_post('payments/999/approve/', {}).status_code, 404)


class ChannelsAndStatsTest(BotTestCase):
    def test_channel_add_list_remove(self):
        res = self.bot_post('channels/', {'chat_id': -1001, 'title': 'ExamBridge', 'username': '@exambridge_uz'})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['url'], 'https://t.me/exambridge_uz')
        self.bot_post('channels/', {'chat_id': -1001, 'title': 'Renamed', 'username': 'exambridge_uz'})
        self.assertEqual(BotChannel.objects.get().title, 'Renamed')
        private = self.bot_post('channels/', {'chat_id': -1002, 'title': 'Private', 'invite_link': 'https://t.me/+abc'})
        self.assertEqual(private.json()['url'], 'https://t.me/+abc')
        self.assertEqual(len(self.bot_get('channels/').json()['channels']), 2)

        self.assertEqual(self.client.delete('/api/bot/channels/-1001/', **BOT).status_code, 204)
        self.assertEqual(self.client.delete('/api/bot/channels/-1001/', **BOT).status_code, 404)
        self.assertEqual(self.bot_post('channels/', {'chat_id': -1003, 'title': 'No link'}).status_code, 400)

    def test_stats(self):
        self.register()
        self.site_user()
        data = self.bot_get('stats/').json()
        self.assertEqual(data['users']['total'], 2)
        self.assertEqual(data['bot']['total'], 1)
        self.assertEqual(data['payments']['pending'], 0)
        self.assertIn('completed_today', data['mocks'])


class AdminPanelTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.pid = self.pay(plan='3month').json()['id']
        self.owner = self.site_user('owner@mail.uz', is_staff=True, is_superuser=True)

    def test_list_receipt_approve(self):
        auth = self.as_user(self.owner)
        data = self.client.get('/api/admin/payments/?status=pending', **auth).json()
        self.assertEqual(data['count'], 1)
        row = data['results'][0]
        self.assertEqual(row['amount'], 119000)
        self.assertEqual(row['telegram']['id'], 1001)
        self.assertIsNone(row['user']['email'])

        receipt_res = self.client.get(f'/api/admin/payments/{self.pid}/receipt/', **auth)
        self.assertEqual(receipt_res.status_code, 200)
        self.assertEqual(receipt_res['Content-Type'], 'image/png')
        self.assertEqual(b''.join(receipt_res.streaming_content), PNG)

        ok = self.client.post(f'/api/admin/payments/{self.pid}/approve/', **auth)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['status'], 'approved')
        self.assertIn('(site)', ok.json()['decided_by'])
        self.assertEqual(self.client.get('/api/admin/payments/?status=approved', **auth).json()['totals']['month_sum'], 119000)

    def test_settings(self):
        auth = self.as_user(self.owner)
        bad = self.client.put('/api/admin/payments/settings/', {'card_number': '1234', 'price_1month': 10},
                              content_type='application/json', **auth)
        self.assertEqual(set(bad.json()), {'card_number', 'price_1month'})
        ok = self.client.put('/api/admin/payments/settings/', {
            'card_number': '9860 1234 5678 9012', 'card_holder': 'N. Shukurov', 'card_label': 'Humo',
            'price_1month': 55000, 'price_3month': 129000, 'accepting': True,
        }, content_type='application/json', **auth)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['card_number'], '9860123456789012')
        self.assertEqual({p['id']: p['price'] for p in self.bot_get('plans/').json()['plans']}['3month'], 129000)

    def test_students_and_low_tier_admins_cannot_see_payments(self):
        student = User.objects.get(email=bot_email(1001))
        self.assertEqual(self.client.get('/api/admin/payments/', **self.as_user(student)).status_code, 403)
        viewer = self.site_user('viewer@mail.uz', is_staff=True, admin_level=2)
        self.assertEqual(self.client.get('/api/admin/payments/', **self.as_user(viewer)).status_code, 403)
        self.assertEqual(self.client.get(f'/api/admin/payments/{self.pid}/receipt/', **self.as_user(viewer)).status_code, 403)


class ContinueWithTelegramTest(BotTestCase):
    def start(self):
        res = self.client.post('/api/auth/telegram/start/', **BROWSER)
        self.assertEqual(res.status_code, 200)
        return res.json()

    def poll(self, token):
        return self.client.post('/api/auth/telegram/poll/', {'token': token},
                                content_type='application/json', **BROWSER).json()

    def test_new_student_signs_up_in_the_bot_and_lands_signed_in(self):
        req = self.start()
        self.assertTrue(req['url'].startswith('https://t.me/exambridge_test_bot?start=login_'))
        self.assertLessEqual(len(req['url'].split('start=')[1]), 64)
        self.assertEqual(self.poll(req['token'])['status'], 'pending')

        self.register(tid=7007)
        ok = self.bot_post('users/login-confirm/', {'telegram_id': 7007, 'token': req['token'], 'signed_up': True})
        self.assertEqual(ok.status_code, 200)

        res = self.poll(req['token'])
        self.assertEqual(res['status'], 'ok')
        self.assertIn('access', res)
        self.assertTrue(res['created'])
        self.assertEqual(res['user']['id'], User.objects.get(email=bot_email(7007)).id)
        # The answer is handed out once.
        self.assertEqual(self.poll(req['token'])['status'], 'expired')

    def test_existing_account_is_not_sent_to_onboarding(self):
        self.register(tid=8008)
        req = self.start()
        self.bot_post('users/login-confirm/', {'telegram_id': 8008, 'token': req['token']})
        self.assertFalse(self.poll(req['token'])['created'])

    def test_nothing_happens_without_the_bot(self):
        self.assertEqual(self.poll('made-up-token')['status'], 'expired')
        req = self.start()
        for _ in range(3):
            self.assertEqual(self.poll(req['token'])['status'], 'pending')

    def test_a_request_is_confirmed_once_and_not_after_it_expires(self):
        self.register(tid=1)
        self.register(tid=2)
        req = self.start()
        self.bot_post('users/login-confirm/', {'telegram_id': 1, 'token': req['token']})
        second = self.bot_post('users/login-confirm/', {'telegram_id': 2, 'token': req['token']})
        self.assertEqual(second.json()['code'], 'bad_token')
        self.assertEqual(self.poll(req['token'])['user']['id'], User.objects.get(email=bot_email(1)).id)

        late = self.start()
        TelegramLogin.objects.filter(token=late['token']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.bot_post('users/login-confirm/', {'telegram_id': 1, 'token': late['token']}).status_code, 400)
        self.assertEqual(self.poll(late['token'])['status'], 'expired')

    def test_a_confirmation_left_unused_runs_out(self):
        self.register(tid=3)
        req = self.start()
        self.bot_post('users/login-confirm/', {'telegram_id': 3, 'token': req['token']})
        TelegramLogin.objects.filter(token=req['token']).update(
            confirmed_at=timezone.now() - TelegramLogin.PICKUP - timedelta(seconds=1),
        )
        self.assertEqual(self.poll(req['token'])['status'], 'expired')

    def test_confirm_needs_a_registered_telegram(self):
        req = self.start()
        self.assertEqual(self.bot_post('users/login-confirm/', {'telegram_id': 404, 'token': req['token']}).status_code, 404)

    @override_settings(TELEGRAM_BOT_USERNAME='')
    def test_off_when_the_bot_is_not_set_up(self):
        self.assertEqual(self.client.post('/api/auth/telegram/start/', **BROWSER).status_code, 503)


class TelegramAccountOnSiteTest(BotTestCase):
    """A Telegram account never shows its placeholder e-mail and can add a real one."""

    def test_profile_hides_the_placeholder_and_asks_for_an_email(self):
        self.register(tid=5005)
        user = User.objects.get(email=bot_email(5005))
        self.assertEqual(user.signup_method, User.SIGNUP_TELEGRAM)

        me = self.client.get('/api/auth/me/', **self.as_user(user)).json()
        self.assertEqual(me['email'], '')
        self.assertTrue(me['needs_email'])
        self.assertFalse(me['has_password'])
        self.assertEqual(me['phone'], '+998901234567')
        self.assertNotIn('tg.exambridge.uz', str(me))

        bad = self.client.patch('/api/auth/profile/', {'contact_email': 'not-an-email'},
                                content_type='application/json', **self.as_user(user))
        self.assertEqual(bad.status_code, 400)
        ok = self.client.patch('/api/auth/profile/', {'contact_email': ' Ali@Gmail.com '},
                               content_type='application/json', **self.as_user(user))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['email'], 'ali@gmail.com')
        self.assertFalse(ok.json()['needs_email'])

        user.refresh_from_db()
        # Contact only: the sign-in address stays the internal one.
        self.assertEqual(user.email, bot_email(5005))
        self.assertEqual(user.contact_email, 'ali@gmail.com')

    def test_an_email_account_cannot_change_its_sign_in_address_this_way(self):
        user = self.site_user('real@mail.uz')
        res = self.client.patch('/api/auth/profile/', {'contact_email': 'other@mail.uz'},
                                content_type='application/json', **self.as_user(user))
        self.assertEqual(res.status_code, 400)
        me = self.client.get('/api/auth/me/', **self.as_user(user)).json()
        self.assertEqual(me['email'], 'real@mail.uz')
        self.assertFalse(me['needs_email'])

    def test_admin_users_filter_by_how_they_signed_up(self):
        self.register(tid=6006, username='tg_student')
        self.site_user('site@mail.uz')
        google = self.site_user('g@gmail.com')
        User.objects.filter(pk=google.pk).update(signup_method=User.SIGNUP_GOOGLE)
        owner = self.site_user('boss@mail.uz', is_staff=True, is_superuser=True)
        auth = self.as_user(owner)

        data = self.client.get('/api/admin/users/', **auth).json()
        self.assertEqual(data['counts']['sources'], {'email': 2, 'google': 1, 'telegram': 1, 'testmakon': 0})

        tg = self.client.get('/api/admin/users/?source=telegram', **auth).json()
        self.assertEqual(tg['count'], 1)
        row = tg['results'][0]
        self.assertEqual(row['signup_method'], 'telegram')
        self.assertEqual(row['email'], '')
        self.assertEqual(row['telegram_username'], 'tg_student')
        self.assertEqual(self.client.get('/api/admin/users/?source=google', **auth).json()['count'], 1)
        # Search finds Telegram students by @username and phone.
        self.assertEqual(self.client.get('/api/admin/users/?q=@tg_student', **auth).json()['count'], 1)
        self.assertEqual(self.client.get('/api/admin/users/?q=901234567', **auth).json()['count'], 1)
