from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Plan, Subscription
from django.utils import timezone
from datetime import timedelta


def pricing_view(request):
    plans = Plan.objects.filter(is_active=True).order_by('order')
    return render(request, 'pricing/pricing.html', {'plans': plans})


@login_required
def subscribe_view(request, plan_type):
    if request.method != 'POST':
        return redirect('pricing')

    try:
        plan = Plan.objects.get(plan_type=plan_type, is_active=True)
    except Plan.DoesNotExist:
        return redirect('pricing')

    # Demo: activate subscription immediately (real app would use payment gateway)
    expires = timezone.now() + timedelta(days=plan.duration_days)
    Subscription.objects.create(
        user=request.user,
        plan=plan,
        expires_at=expires,
        payment_ref='demo',
    )
    return redirect('profile')
