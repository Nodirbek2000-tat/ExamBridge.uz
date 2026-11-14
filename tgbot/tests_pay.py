""""Pay via Telegram": the Premium bought always goes to the account it was bought for."""
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from pricing.models import CardPayment

from .models import PaymentTarget, TelegramAccount, TelegramToken
from .tests import BotTestCase, User


class PayForAnAccountTest(BotTestCase):
    def open_bot(self, user):
        res = self.client.post('/api/payments/telegram/', **self.as_user(user))
        self.assertEqual(res.status_code, 200)
        url = res.json()['url']
        self.assertTrue(url.startswith('https://t.me/exambridge_test_bot?start=pay_'))
        token = url.split('pay_', 1)[1]
        self.assertLessEqual(len('pay_' + token), 64)
        return token

    def check(self, token, tid):
        return self.bot_post('users/pay-check/', {'token': token, 'telegram_id': tid})

    def confirm(self, token, tid):
        return self.bot_post('users/pay-confirm/', {'token': token, 'telegram_id': tid, 'first_name': 'Vali'})

    def approve(self, payment_id):
        with mock.patch('pricing.card.telegram.send_message') as send, \
                mock.patch('pricing.card.telegram.edit_caption'), \
                self.captureOnCommitCallbacks(execute=True):
            res = self.bot_post(f'payments/{payment_id}/approve/', {'admin_telegram_id': 1, 'admin_name': 'Boss'})
        self.assertEqual(res.status_code, 200)
        return send

    def test_a_google_account_pays_from_a_telegram_that_has_its_own_bot_account(self):
        self.register(tid=5005)                                 # this Telegram signed up in the bot before
        own = TelegramAccount.objects.get(telegram_id=5005).user
        gmail = self.site_user('student@gmail.com', first_name='Ali', last_name='Valiyev')
        token = self.open_bot(gmail)

        seen = self.check(token, 5005).json()
        self.assertEqual((seen['relation'], seen['account']['email']), ('other', 'student@gmail.com'))
        self.assertEqual(seen['own_account']['user_id'], own.id)
        self.assertTrue(TelegramToken.peek(token, TelegramToken.PAY))       # looking does not use it up

        res = self.confirm(token, 5005)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['linked'])
        self.assertEqual(TelegramAccount.objects.get(telegram_id=5005).user, own)   # never merged
        self.assertEqual(self.bot_get('plans/?telegram_id=5005').json()['for_account']['email'], 'student@gmail.com')

        with mock.patch('pricing.card.telegram.send_file', return_value=None):
            paid = self.pay(tid=5005)
        self.assertEqual(paid.status_code, 201)
        self.assertEqual(paid.json()['account']['user_id'], gmail.id)
        payment = CardPayment.objects.get(pk=paid.json()['id'])
        self.assertEqual((payment.user, payment.telegram_id), (gmail, 5005))

        send = self.approve(payment.id)
        gmail.refresh_from_db()
        own.refresh_from_db()
        self.assertTrue(gmail.is_premium)
        self.assertFalse(own.is_premium)
        chat_id, text = send.call_args[0][:2]
        self.assertEqual(chat_id, 5005)                          # told in the chat that paid
        self.assertIn('student@gmail.com', text)
        self.assertFalse(PaymentTarget.objects.exists())         # the next receipt is its own again
        self.assertEqual(self.bot_get('plans/?telegram_id=5005').json()['for_account']['user_id'], own.id)

    def test_a_telegram_without_an_account_becomes_part_of_the_site_account(self):
        gmail = self.site_user('student@gmail.com')
        token = self.open_bot(gmail)
        self.assertEqual(self.check(token, 6006).json()['relation'], 'unlinked')

        res = self.confirm(token, 6006)
        self.assertTrue(res.json()['linked'])
        self.assertEqual(TelegramAccount.objects.get(telegram_id=6006).user, gmail)

        with mock.patch('pricing.card.telegram.send_file', return_value=None):
            paid = self.pay(tid=6006)
        self.assertEqual(CardPayment.objects.get(pk=paid.json()['id']).user, gmail)

    def test_an_account_already_linked_to_another_telegram_keeps_that_link(self):
        gmail = self.site_user('student@gmail.com')
        TelegramAccount.objects.create(user=gmail, telegram_id=7007)
        token = self.open_bot(gmail)

        res = self.confirm(token, 8008)                          # a second phone, never in the bot
        self.assertEqual((res.json()['relation'], res.json()['linked']), ('unlinked', False))
        self.assertEqual(TelegramAccount.objects.get(user=gmail).telegram_id, 7007)

        with mock.patch('pricing.card.telegram.send_file', return_value=None):
            paid = self.pay(tid=8008)
        self.assertEqual(paid.status_code, 201)
        self.assertEqual(CardPayment.objects.get(pk=paid.json()['id']).user, gmail)

    def test_the_same_account_just_pays(self):
        self.register(tid=9009)
        user = TelegramAccount.objects.get(telegram_id=9009).user
        token = self.open_bot(user)
        self.assertEqual(self.check(token, 9009).json()['relation'], 'same')
        self.assertEqual(self.confirm(token, 9009).json()['relation'], 'same')

    def test_a_pay_link_works_once_and_not_after_a_day(self):
        gmail = self.site_user('student@gmail.com')
        token = self.open_bot(gmail)
        self.confirm(token, 6006)
        self.assertEqual(self.confirm(token, 6006).json()['code'], 'bad_token')
        self.assertEqual(self.check(token, 6006).json()['code'], 'bad_token')

        late = self.open_bot(gmail)
        TelegramToken.objects.filter(token=late).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.check(late, 6006).status_code, 400)
        self.assertEqual(self.check('nope', 6006).json()['code'], 'bad_token')

    def test_a_choice_runs_out_after_two_hours(self):
        self.register(tid=5005)
        gmail = self.site_user('student@gmail.com')
        self.confirm(self.open_bot(gmail), 5005)
        PaymentTarget.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        own = TelegramAccount.objects.get(telegram_id=5005).user
        self.assertEqual(self.bot_get('plans/?telegram_id=5005').json()['for_account']['user_id'], own.id)

    def test_a_disabled_account_cannot_be_paid_for(self):
        gmail = self.site_user('student@gmail.com')
        token = self.open_bot(gmail)
        User.objects.filter(pk=gmail.pk).update(is_active=False)
        self.assertEqual(self.check(token, 6006).json()['code'], 'bad_token')
