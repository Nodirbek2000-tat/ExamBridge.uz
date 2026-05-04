from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.shortcuts import redirect


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom adapter for email/password registration."""

    def get_login_redirect_url(self, request):
        user = request.user
        if not user.profile_completed:
            return '/auth/complete-profile/'
        return '/dashboard/'

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        # Generate username from email
        user.username = user.email.split('@')[0]
        if commit:
            user.save()
        return user


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom adapter for Google OAuth."""

    def pre_social_login(self, request, sociallogin):
        """
        Called after successful Google auth.
        If email already exists, connect accounts.
        """
        from django.contrib.auth import get_user_model
        User = get_user_model()

        if sociallogin.is_existing:
            return

        email = sociallogin.account.extra_data.get('email', '').lower()
        if not email:
            return

        try:
            user = User.objects.get(email=email)
            sociallogin.connect(request, user)
        except User.DoesNotExist:
            pass

    def populate_user(self, request, sociallogin, data):
        """Fill user data from Google profile."""
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data

        if not user.first_name:
            user.first_name = extra.get('given_name', '')
        if not user.last_name:
            user.last_name = extra.get('family_name', '')

        # username from email
        email = extra.get('email', '')
        if email:
            base = email.split('@')[0]
            from django.contrib.auth import get_user_model
            User = get_user_model()
            username = base
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base}{counter}"
                counter += 1
            user.username = username

        # Profile not yet complete — needs country & phone
        user.profile_completed = False
        return user

    def get_connect_redirect_url(self, request, socialaccount):
        return '/auth/complete-profile/'
