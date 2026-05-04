from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from .models import User, UserStats
from .forms import LoginForm, RegisterForm, CompleteProfileForm


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('profile')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email'].lower()
        password = form.cleaned_data['password']

        try:
            user_obj = User.objects.get(email=email)
            if user_obj.is_locked():
                messages.error(request, 'Account locked due to too many failed attempts. Try again in 30 minutes.')
                return render(request, 'accounts/login.html', {'form': form})
        except User.DoesNotExist:
            user_obj = None

        user = authenticate(request, username=email, password=password)
        if user is not None:
            user.reset_login_attempts()
            user.last_ip = get_client_ip(request)
            user.save(update_fields=['last_ip'])
            login(request, user)
            if not user.profile_completed:
                return redirect('complete_profile')
            next_url = request.GET.get('next', 'profile')
            return redirect(next_url)
        else:
            if user_obj:
                user_obj.record_failed_login()
            messages.error(request, 'Invalid email or password.')

    return render(request, 'accounts/login.html', {'form': form})


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect('profile')

    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.last_ip = get_client_ip(request)

        base = user.email.split('@')[0]
        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base}{counter}"
            counter += 1
        user.username = username
        user.save()

        UserStats.objects.create(user=user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('complete_profile')

    return render(request, 'accounts/register.html', {'form': form})


@login_required
@require_http_methods(["GET", "POST"])
def complete_profile_view(request):
    """Shown after Google OAuth or new registration."""
    form = CompleteProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.profile_completed = True
        user.save()
        UserStats.objects.get_or_create(user=user)
        messages.success(request, 'Welcome to SATBridge! Your profile is set up.')
        return redirect('profile')

    return render(request, 'accounts/complete_profile.html', {'form': form})


@login_required
def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def profile_view(request):
    stats, _ = UserStats.objects.get_or_create(user=request.user)
    recent_results = request.user.results.select_related(
        'attempt__test'
    ).order_by('-calculated_at')[:10]

    return render(request, 'accounts/profile.html', {
        'stats': stats,
        'recent_results': recent_results,
    })
