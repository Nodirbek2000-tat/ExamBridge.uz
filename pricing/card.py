"""
Card payments: a student pays to the card, sends the receipt through the
Telegram bot, an admin approves it (in the channel or in the admin panel)
and Premium opens for the bought number of days.
"""
import html
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import User
from tgbot import telegram
from tgbot.models import PaymentTarget, TelegramAccount, TelegramToken, is_bot_email

from .models import CARD_PLANS, CardPayment, PaymentSettings, Plan, Subscription

logger = logging.getLogger(__name__)

PENDING_LIMIT = 3
RECEIPT_MAX_BYTES = 10 * 1024 * 1024
RECEIPT_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}
REFUND_HOURS = 24

REJECT_REASONS = {
    'unreadable': "Chek o'qib bo'lmaydi yoki to'liq emas",
    'amount': 'Summa tarif narxiga mos emas',
    'not_received': "To'lov kartaga tushmagan",
    'duplicate': 'Bu chek avval yuborilgan',
    'other': "Ma'lumotlar mos kelmadi",
}

PLAN_TITLES = {'1month': '1 oy', '3month': '3 oy'}


class CardPaymentError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def money(amount):
    """49000 → '49 000'."""
    return f'{amount:,}'.replace(',', ' ')


def plans_payload(config=None):
    config = config or PaymentSettings.load()
    return [
        {'id': key, 'months': p['months'], 'days': p['days'], 'price': config.price(key)}
        for key, p in CARD_PLANS.items()
    ]


# ── Creating ──────────────────────────────────────────────────────────────────

def create_payment(user, plan, receipt, telegram_id=None):
    config = PaymentSettings.load()
    if not config.accepting or not config.card_number:
        raise CardPaymentError('closed', 'Payments are not being accepted right now.', 503)
    if plan not in CARD_PLANS:
        raise CardPaymentError('bad_plan', 'Unknown plan.')
    if receipt is None:
        raise CardPaymentError('no_receipt', 'Receipt file is required.')
    ext = os.path.splitext(receipt.name or '')[1].lower()
    if ext not in RECEIPT_EXTENSIONS:
        raise CardPaymentError('bad_receipt', 'The receipt must be a photo (JPG, PNG, WEBP) or a PDF.')
    if receipt.size > RECEIPT_MAX_BYTES:
        raise CardPaymentError('bad_receipt', 'The receipt file is larger than 10 MB.')
    if CardPayment.objects.filter(user=user, status=CardPayment.Status.PENDING).count() >= PENDING_LIMIT:
        raise CardPaymentError(
            'too_many_pending', 'You already have receipts waiting for a check.', 429,
        )

    payment = CardPayment.objects.create(
        user=user,
        plan=plan,
        amount=config.price(plan),
        days=CARD_PLANS[plan]['days'],
        receipt=receipt,
        telegram_id=telegram_id,
    )
    _post_to_channel(payment)
    return payment


# ── Deciding ──────────────────────────────────────────────────────────────────

def _locked_pending(payment_id):
    try:
        payment = CardPayment.objects.select_for_update().get(pk=payment_id)
    except CardPayment.DoesNotExist:
        raise CardPaymentError('not_found', 'Payment not found.', 404)
    if payment.status != CardPayment.Status.PENDING:
        raise CardPaymentError('already_decided', f'This payment is already {payment.status}.', 409)
    return payment


def approve(payment_id, decided_by):
    with transaction.atomic():
        payment = _locked_pending(payment_id)
        user = User.objects.select_for_update().get(pk=payment.user_id)
        payment.premium_until = grant_premium(user, payment)
        payment.status = CardPayment.Status.APPROVED
        payment.decided_at = timezone.now()
        payment.decided_by = decided_by[:120]
        payment.save(update_fields=['premium_until', 'status', 'decided_at', 'decided_by'])
        # Paid for: the next receipt from that Telegram goes to its own account again.
        if payment.telegram_id:
            PaymentTarget.objects.filter(telegram_id=payment.telegram_id, user=user).delete()
        transaction.on_commit(lambda: _announce_decision(payment.pk))
    return payment


def reject(payment_id, decided_by, reason):
    reason = REJECT_REASONS.get(reason, reason or REJECT_REASONS['other'])
    with transaction.atomic():
        payment = _locked_pending(payment_id)
        payment.status = CardPayment.Status.REJECTED
        payment.decided_at = timezone.now()
        payment.decided_by = decided_by[:120]
        payment.reject_reason = reason[:200]
        payment.save(update_fields=['status', 'decided_at', 'decided_by', 'reject_reason'])
        transaction.on_commit(lambda: _announce_decision(payment.pk))
    return payment


def grant_premium(user, payment):
    """Add the bought days to the user's Premium. Returns the new end (None = no end)."""
    now = timezone.now()
    if user.is_premium and user.premium_until is None:
        return None      # already Premium for ever (granted by an admin): nothing to add

    still_running = user.is_premium and user.premium_until and user.premium_until > now
    expires_at = (user.premium_until if still_running else now) + timedelta(days=payment.days)

    plan_type = Plan.PlanType.PREMIUM if payment.plan == '1month' else Plan.PlanType.PRO
    plan, _ = Plan.objects.get_or_create(
        plan_type=plan_type,
        defaults={'name': plan_type.label, 'duration_days': payment.days, 'is_active': True},
    )
    Subscription.objects.filter(user=user, status=Subscription.Status.ACTIVE).update(
        status=Subscription.Status.CANCELLED,
    )
    # amount_paid stays 0: that column is Stripe's USD cents. The so'm amount
    # lives on the CardPayment the payment_ref points to.
    Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE, expires_at=expires_at,
        payment_ref=f'card:{payment.pk}', plan_label=payment.plan, amount_paid=0,
    )
    user.is_premium = True
    user.premium_until = expires_at
    user.premium_source = 'card'
    user.save(update_fields=['is_premium', 'premium_until', 'premium_source'])
    return expires_at


# ── Telegram messages ─────────────────────────────────────────────────────────

def _local(dt):
    return timezone.localtime(dt).strftime('%d.%m.%Y %H:%M')


def _date(dt):
    return timezone.localtime(dt).strftime('%d.%m.%Y') if dt else 'muddatsiz'


def account_label(user):
    """'Ali Valiyev · ali@gmail.com' — the account a payment is for, as people know it."""
    tg = TelegramAccount.objects.filter(user=user).first()
    name = user.full_name if not is_bot_email(user.email) else (tg.display_name if tg else '')
    return ' · '.join(x for x in (name, user.public_email) if x) or f'#{user.pk}'


def _receipt_caption(payment):
    user = payment.user
    # Who sent it: the Telegram the receipt came from, which may belong to another account.
    tg = (
        TelegramAccount.objects.filter(telegram_id=payment.telegram_id).first() if payment.telegram_id else None
    ) or TelegramAccount.objects.filter(user=user).first()
    e = html.escape
    lines = [f"🧾 <b>Yangi to'lov #{payment.pk}</b>", '']
    lines.append(f'👤 {e(user.full_name if not is_bot_email(user.email) else (tg.display_name if tg else "—"))}')
    if tg and tg.phone:
        lines.append(f'📱 {e(tg.phone)}')
    if tg:
        handle = f'@{e(tg.username)} · ' if tg.username else ''
        lines.append(f'✈️ {handle}ID <code>{tg.telegram_id}</code>')
    if user.public_email:
        lines.append(f'📧 {e(user.public_email)}')
    if tg and tg.user_id != user.id:
        lines.append(f'ℹ️ Boshqa Telegram akkauntdan to\'langan — Premium shu akkauntga (ID {user.pk}) ochiladi')
    lines.append(f'💎 Tarif: <b>{PLAN_TITLES[payment.plan]}</b> — <b>{money(payment.amount)} so\'m</b>')
    lines.append(f'🕒 {_local(payment.created_at)}')
    return '\n'.join(lines)


def _decision_line(payment):
    if payment.status == CardPayment.Status.APPROVED:
        return (
            f'\n\n✅ <b>Tasdiqlandi</b> — {html.escape(payment.decided_by)} · {_local(payment.decided_at)}'
            f'\n⭐ Premium: {_date(payment.premium_until)} gacha'
        )
    return (
        f'\n\n❌ <b>Rad etildi</b> — {html.escape(payment.decided_by)} · {_local(payment.decided_at)}'
        f'\nSabab: {html.escape(payment.reject_reason)}'
    )


def decision_buttons(payment_id):
    return [[
        {'text': '✅ Tasdiqlash', 'callback_data': f'pay:ok:{payment_id}'},
        {'text': '❌ Rad etish', 'callback_data': f'pay:no:{payment_id}'},
    ]]


def _post_to_channel(payment):
    chat = settings.TELEGRAM_PAYMENTS_CHAT
    if not chat:
        return
    message = telegram.send_file(chat, payment.receipt, _receipt_caption(payment), decision_buttons(payment.pk))
    if message:
        payment.channel_chat_id = message['chat']['id']
        payment.channel_message_id = message['message_id']
        payment.save(update_fields=['channel_chat_id', 'channel_message_id'])


def site_login_url(user):
    token = TelegramToken.issue(user, TelegramToken.LOGIN)
    return f"{settings.FRONTEND_URL.rstrip('/')}/auth/telegram?token={token.token}"


def _announce_decision(payment_id):
    """Update the channel post and tell the student. Runs after the decision is saved."""
    try:
        payment = CardPayment.objects.select_related('user').get(pk=payment_id)
        if payment.channel_chat_id and payment.channel_message_id:
            telegram.edit_caption(
                payment.channel_chat_id, payment.channel_message_id,
                _receipt_caption(payment) + _decision_line(payment),
            )

        # The answer goes to whoever sent the receipt, which may not be the account's own Telegram.
        tg = TelegramAccount.objects.filter(user=payment.user).first()
        chat_id = payment.telegram_id or (tg.telegram_id if tg else None)
        if not chat_id:
            return
        if payment.status == CardPayment.Status.APPROVED:
            text = (
                "🎉 <b>To'lovingiz tasdiqlandi!</b>\n\n"
                f"💎 Premium faollashtirildi — <b>{_date(payment.premium_until)}</b> gacha.\n"
                f"👤 Akkaunt: <b>{html.escape(account_label(payment.user))}</b>\n"
                "Barcha premium mock testlar va AI imkoniyatlari shu akkauntda ochildi."
            )
            buttons = [[{'text': '🌐 Saytga kirish', 'url': site_login_url(payment.user)}]]
        else:
            support = settings.TELEGRAM_SUPPORT_USERNAME
            text = (
                "❌ <b>To'lovingiz tasdiqlanmadi</b>\n\n"
                f"Sabab: {html.escape(payment.reject_reason)}\n\n"
                "Xatolik bo'lsa, chekni qaytadan yuboring yoki yordamga yozing"
                + (f': @{support}' if support else '.')
            )
            buttons = None
        telegram.send_message(chat_id, text, buttons)
    except Exception:
        logger.exception('Could not announce the decision on payment %s', payment_id)


# ── Numbers ───────────────────────────────────────────────────────────────────

def payment_totals():
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)
    approved = Q(status=CardPayment.Status.APPROVED)
    agg = CardPayment.objects.aggregate(
        pending=Count('id', filter=Q(status=CardPayment.Status.PENDING)),
        approved=Count('id', filter=approved),
        rejected=Count('id', filter=Q(status=CardPayment.Status.REJECTED)),
        today_count=Count('id', filter=approved & Q(decided_at__gte=today_start)),
        today_sum=Sum('amount', filter=approved & Q(decided_at__gte=today_start)),
        month_count=Count('id', filter=approved & Q(decided_at__gte=month_start)),
        month_sum=Sum('amount', filter=approved & Q(decided_at__gte=month_start)),
        total_sum=Sum('amount', filter=approved),
    )
    return {k: v or 0 for k, v in agg.items()}
