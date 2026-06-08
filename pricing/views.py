"""
Stripe payment integration.
Endpoints:
  POST /api/payments/checkout/      → create Stripe Checkout Session
  POST /api/payments/webhook/       → Stripe webhook handler (no auth)
  GET  /api/payments/status/        → current user subscription info
  POST /api/payments/cancel/        → cancel active subscription
  GET  /api/payments/stats/         → admin revenue stats
"""
import stripe
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from accounts.models import User
from .models import Plan, Subscription

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

# ─── Plan config ──────────────────────────────────────────────────────────────
PLANS = {
    '1month': {
        'label': '1 Month Premium',
        'days': 30,
        'amount': settings.STRIPE_PRICE_1MONTH,   # USD cents
        'interval': 'month',
        'interval_count': 1,
        'plan_type': 'PREMIUM',
    },
    '3month': {
        'label': '3 Months Pro',
        'days': 90,
        'amount': settings.STRIPE_PRICE_3MONTH,   # USD cents
        'interval': 'month',
        'interval_count': 3,
        'plan_type': 'PRO',
    },
    '6month': {
        'label': '6 Months Pro',
        'days': 180,
        'amount': settings.STRIPE_PRICE_6MONTH,   # USD cents
        'interval': 'month',
        'interval_count': 6,
        'plan_type': 'PRO',
    },
}


def _get_or_create_stripe_customer(user):
    """Return existing Stripe customer or create a new one."""
    if user.stripe_customer_id:
        try:
            customer = stripe.Customer.retrieve(user.stripe_customer_id)
            if getattr(customer, 'deleted', False):
                raise Exception('deleted')
            return customer
        except Exception:
            pass  # create a fresh one

    customer = stripe.Customer.create(
        email=user.email,
        name=user.full_name,
        metadata={'user_id': str(user.id)},
    )
    user.stripe_customer_id = customer.id
    user.save(update_fields=['stripe_customer_id'])
    return customer


def _activate_premium(user, days, plan_type, amount, plan_label,
                      payment_ref='', stripe_sub_id=''):
    """Grant premium to user and record Subscription."""
    now = timezone.now()

    # Extend existing expiry if still active
    base = user.premium_until if (user.is_premium and user.premium_until and user.premium_until > now) else now
    expires_at = base + timedelta(days=days)

    # Cancel old active subscriptions for this user
    Subscription.objects.filter(user=user, status='ACTIVE').update(status='CANCELLED')

    # Get or create plan record
    plan_obj, _ = Plan.objects.get_or_create(
        plan_type=plan_type,
        defaults={
            'name': PLANS.get(plan_label, {}).get('label', plan_type),
            'duration_days': days,
            'is_active': True,
            'price': amount / 100,
        },
    )

    Subscription.objects.create(
        user=user,
        plan=plan_obj,
        status='ACTIVE',
        expires_at=expires_at,
        payment_ref=payment_ref,
        stripe_subscription_id=stripe_sub_id,
        amount_paid=amount,
        plan_label=plan_label,
    )

    user.is_premium = True
    user.premium_until = expires_at
    if stripe_sub_id:
        user.stripe_subscription_id = stripe_sub_id
    user.save(update_fields=['is_premium', 'premium_until', 'stripe_subscription_id'])

    logger.info('Premium activated: user=%s plan=%s expires=%s', user.email, plan_label, expires_at)


# ─── Create checkout session ──────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_checkout(request):
    plan_key = request.data.get('plan', '').strip()
    if plan_key not in PLANS:
        return Response({'error': 'Invalid plan. Use "1month", "3month" or "6month".'}, status=400)

    p = PLANS[plan_key]
    user = request.user

    try:
        customer = _get_or_create_stripe_customer(user)

        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=['card'],
            mode='subscription',
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"SAT+ {p['label']}",
                        'description': 'Unlimited access to SAT mocks, IELTS Speaking, Writing & more.',
                    },
                    'unit_amount': p['amount'],
                    'recurring': {
                        'interval': p['interval'],
                        'interval_count': p['interval_count'],
                    },
                },
                'quantity': 1,
            }],
            metadata={
                'user_id': str(user.id),
                'plan': plan_key,
                'days': str(p['days']),
                'amount': str(p['amount']),
            },
            success_url=f"{settings.FRONTEND_URL}/app/subscription?success=1&plan={plan_key}",
            cancel_url=f"{settings.FRONTEND_URL}/app/subscription?cancelled=1",
            allow_promotion_codes=True,
        )
        return Response({'url': session.url})

    except stripe.error.StripeError as e:
        logger.error('Stripe error creating checkout: %s', e)
        return Response({'error': str(e)}, status=400)


# ─── Webhook ──────────────────────────────────────────────────────────────────
@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning('Stripe webhook: invalid payload')
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.warning('Stripe webhook: invalid signature')
        return HttpResponse(status=400)

    etype = event['type']
    data = event['data']['object']

    # ── Payment succeeded (subscription created / renewed) ────────────────────
    if etype == 'checkout.session.completed':
        meta = data.get('metadata', {})
        user_id = meta.get('user_id')
        plan_key = meta.get('plan', '1month')
        days = int(meta.get('days', 30))
        amount = int(meta.get('amount', 999))
        stripe_sub_id = data.get('subscription', '')
        payment_ref = data.get('id', '')

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.error('Stripe webhook: user %s not found', user_id)
            return HttpResponse(status=200)

        p = PLANS.get(plan_key, PLANS['1month'])
        _activate_premium(
            user=user,
            days=days,
            plan_type=p['plan_type'],
            amount=amount,
            plan_label=plan_key,
            payment_ref=payment_ref,
            stripe_sub_id=stripe_sub_id,
        )

    # ── Recurring invoice paid (auto-renewal) ─────────────────────────────────
    elif etype == 'invoice.payment_succeeded':
        stripe_sub_id = data.get('subscription')
        if not stripe_sub_id:
            return HttpResponse(status=200)

        # Birinchi to'lovni (subscription_create) o'tkazib yuboramiz —
        # uni checkout.session.completed allaqachon faollashtirgan.
        # Faqat avto-yangilanish (subscription_cycle) shu yerda ishlansin,
        # aks holda premium ikki marta qo'shilib (60 kun) va revenue 2x sanaladi.
        billing_reason = data.get('billing_reason', '')
        if billing_reason == 'subscription_create':
            return HttpResponse(status=200)

        # Find user by stripe_subscription_id
        user = User.objects.filter(stripe_subscription_id=stripe_sub_id).first()
        if not user:
            return HttpResponse(status=200)

        # Find plan from subscription
        sub = Subscription.objects.filter(
            user=user, stripe_subscription_id=stripe_sub_id
        ).order_by('-started_at').first()

        plan_key = sub.plan_label if sub else '1month'
        p = PLANS.get(plan_key, PLANS['1month'])
        amount = data.get('amount_paid', p['amount'])

        _activate_premium(
            user=user,
            days=p['days'],
            plan_type=p['plan_type'],
            amount=amount,
            plan_label=plan_key,
            payment_ref=data.get('id', ''),
            stripe_sub_id=stripe_sub_id,
        )

    # ── Subscription cancelled / failed ───────────────────────────────────────
    elif etype in ('customer.subscription.deleted', 'invoice.payment_failed'):
        stripe_sub_id = data.get('id') if etype == 'customer.subscription.deleted' else data.get('subscription')
        if stripe_sub_id:
            Subscription.objects.filter(stripe_subscription_id=stripe_sub_id).update(status='CANCELLED')
            user = User.objects.filter(stripe_subscription_id=stripe_sub_id).first()
            if user:
                user.is_premium = False
                user.premium_until = None
                user.stripe_subscription_id = ''
                user.save(update_fields=['is_premium', 'premium_until', 'stripe_subscription_id'])
                logger.info('Premium revoked: user=%s reason=%s', user.email, etype)

    return HttpResponse(status=200)


# ─── Subscription status ──────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_status(request):
    user = request.user
    user.check_premium()  # auto-expire if needed

    active_sub = Subscription.objects.filter(
        user=user, status='ACTIVE'
    ).order_by('-started_at').first()

    return Response({
        'is_premium': user.is_premium,
        'premium_until': user.premium_until,
        'plan': active_sub.plan_label if active_sub else None,
        'stripe_subscription_id': user.stripe_subscription_id,
        'publishable_key': settings.STRIPE_PUBLISHABLE_KEY,
    })


# ─── Cancel subscription ──────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_subscription(request):
    user = request.user
    stripe_sub_id = user.stripe_subscription_id

    if not stripe_sub_id:
        return Response({'error': 'No active subscription found.'}, status=400)

    try:
        # Cancel at period end (user keeps access until billing period ends)
        stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=True)
        return Response({'message': 'Subscription will cancel at end of billing period.'})
    except stripe.error.StripeError as e:
        return Response({'error': str(e)}, status=400)


# ─── Admin revenue stats ──────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
def payment_stats(request):
    from django.db.models import Sum, Count
    from accounts.models import User as UserModel

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_revenue = Subscription.objects.aggregate(
        total=Sum('amount_paid')
    )['total'] or 0

    monthly_revenue = Subscription.objects.filter(
        started_at__gte=month_start
    ).aggregate(total=Sum('amount_paid'))['total'] or 0

    total_premium = UserModel.objects.filter(is_premium=True).count()
    total_users = UserModel.objects.count()

    plan_breakdown = Subscription.objects.filter(status='ACTIVE').values(
        'plan_label'
    ).annotate(count=Count('id'), revenue=Sum('amount_paid'))

    return Response({
        'total_revenue_usd': round(total_revenue / 100, 2),
        'monthly_revenue_usd': round(monthly_revenue / 100, 2),
        'total_premium': total_premium,
        'total_users': total_users,
        'plan_breakdown': list(plan_breakdown),
    })
