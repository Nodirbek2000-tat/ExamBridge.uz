"""
Card payments on the site: public plan prices, and the admin panel's
Payments section (settings, receipts, approve / reject).
"""
import mimetypes

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from tgbot.models import TelegramAccount, is_bot_email
from tgbot.site_views import bot_url

from . import card
from .models import CardPayment, PaymentSettings

PAGE_SIZE = 30


@api_view(['GET'])
@permission_classes([AllowAny])
def public_plans(request):
    """Prices for the pricing page. The card number stays in the bot."""
    config = PaymentSettings.load()
    return Response({
        'accepting': bool(config.accepting and config.card_number),
        'plans': card.plans_payload(config),
        'bot_url': bot_url(),
        'refund_hours': card.REFUND_HOURS,
    })


# ── Admin panel ───────────────────────────────────────────────────────────────

def _settings_payload(config):
    return {
        'card_number': config.card_number,
        'card_holder': config.card_holder,
        'card_label': config.card_label,
        'price_1month': config.price_1month,
        'price_3month': config.price_3month,
        'accepting': config.accepting,
        'updated_at': config.updated_at.isoformat() if config.updated_at else None,
    }


@api_view(['GET', 'PUT'])
@permission_classes([IsAdminUser])
def admin_settings(request):
    config = PaymentSettings.load()
    if request.method == 'GET':
        return Response(_settings_payload(config))

    data = request.data
    errors = {}
    card_number = ''.join(ch for ch in str(data.get('card_number', config.card_number)) if ch.isdigit())
    if card_number and not 16 <= len(card_number) <= 19:
        errors['card_number'] = 'A card number has 16 digits.'
    prices = {}
    for field in ('price_1month', 'price_3month'):
        try:
            prices[field] = int(data.get(field, getattr(config, field)))
        except (TypeError, ValueError):
            prices[field] = -1
        if not 1000 <= prices[field] <= 10_000_000:
            errors[field] = "Enter a price between 1 000 and 10 000 000 so'm."
    if errors:
        return Response(errors, status=400)

    config.card_number = card_number
    config.card_holder = str(data.get('card_holder', config.card_holder)).strip()[:100]
    config.card_label = str(data.get('card_label', config.card_label)).strip()[:60]
    config.price_1month = prices['price_1month']
    config.price_3month = prices['price_3month']
    config.accepting = bool(data.get('accepting', config.accepting))
    config.save()
    return Response(_settings_payload(config))


def _payment_payload(p, accounts):
    tg = accounts.get(p.user_id)
    user = p.user
    return {
        'id': p.id,
        'status': p.status,
        'plan': p.plan,
        'months': p.months,
        'amount': p.amount,
        'created_at': p.created_at.isoformat(),
        'decided_at': p.decided_at.isoformat() if p.decided_at else None,
        'decided_by': p.decided_by,
        'reject_reason': p.reject_reason,
        'premium_until': p.premium_until.isoformat() if p.premium_until else None,
        'receipt_type': 'pdf' if p.receipt.name.lower().endswith('.pdf') else 'image',
        'user': {
            'id': user.id,
            'name': (tg.display_name if tg and is_bot_email(user.email) else user.full_name),
            'email': None if is_bot_email(user.email) else user.email,
            'phone': (tg.phone if tg else '') or user.phone,
            'is_premium': user.is_premium,
        },
        'telegram': {'id': tg.telegram_id, 'username': tg.username} if tg else None,
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_payments(request):
    status = request.query_params.get('status', 'pending')
    qs = CardPayment.objects.select_related('user')
    if status in CardPayment.Status.values:
        qs = qs.filter(status=status)
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except ValueError:
        page = 1
    total = qs.count()
    rows = list(qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])
    accounts = {a.user_id: a for a in TelegramAccount.objects.filter(user_id__in=[p.user_id for p in rows])}
    return Response({
        'results': [_payment_payload(p, accounts) for p in rows],
        'count': total,
        'page': page,
        'pages': max(1, -(-total // PAGE_SIZE)),
        'totals': card.payment_totals(),
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_receipt(request, payment_id):
    payment = CardPayment.objects.filter(pk=payment_id).first()
    if payment is None or not payment.receipt:
        raise Http404
    try:
        handle = payment.receipt.open('rb')
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(payment.receipt.name)[0] or 'application/octet-stream'
    response = FileResponse(handle, content_type=content_type)
    response['Cache-Control'] = 'private, no-store'
    return response


def _admin_label(user):
    return f'{user.full_name} (site)'


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_approve(request, payment_id):
    try:
        payment = card.approve(payment_id, _admin_label(request.user))
    except card.CardPaymentError as e:
        return Response({'code': e.code, 'detail': e.message}, status=e.status)
    accounts = {a.user_id: a for a in TelegramAccount.objects.filter(user_id=payment.user_id)}
    return Response(_payment_payload(payment, accounts))


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_reject(request, payment_id):
    try:
        payment = card.reject(payment_id, _admin_label(request.user), str(request.data.get('reason') or ''))
    except card.CardPaymentError as e:
        return Response({'code': e.code, 'detail': e.message}, status=e.status)
    accounts = {a.user_id: a for a in TelegramAccount.objects.filter(user_id=payment.user_id)}
    return Response(_payment_payload(payment, accounts))


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_reject_reasons(request):
    return Response({'reasons': [{'id': k, 'label': v} for k, v in card.REJECT_REASONS.items()]})
