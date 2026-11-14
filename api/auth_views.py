from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.conf import settings
from django.utils.dateparse import parse_date
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from accounts import devices
from accounts.models import LOGIN_LOCK_MINUTES, User, UserSession


# Per address, so set for a classroom behind one network rather than one person.
class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class RegisterRateThrottle(AnonRateThrottle):
    scope = 'register'


def user_data(user):
    return {
        'id': user.id,
        # Telegram accounts: the address they gave us, or '' — never the placeholder.
        'email': user.public_email,
        'signup_method': user.signup_method,
        'needs_email': user.needs_contact_email,
        'has_password': user.has_usable_password(),
        'phone': user.phone,
        'full_name': user.full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_premium': user.is_premium,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        # Admin panel tier: 'super', 1, 2, 3 or null. The panel hides whole
        # menu sections based on this (see adminAccess.js).
        'admin_tier': user.admin_tier,
        'avatar': user.avatar.url if user.avatar else None,
        'target_band': user.target_band or '',
        'exam_date': user.exam_date.isoformat() if user.exam_date else None,
        'daily_study_minutes': user.daily_study_minutes,
        'email_newsletter': user.email_newsletter,
        'premium_until': user.premium_until.isoformat() if user.premium_until else None,
        'created_at': user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
    }


def get_tokens(user, request=None):
    """The JWT pair for a sign-in on the device making `request` (see accounts/devices.py)."""
    return devices.tokens_for(user, request)


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])
def csrf_token(request):
    return Response({'csrfToken': get_token(request)})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def login_view(request):
    email = request.data.get('email', '').lower().strip()
    password = request.data.get('password', '')

    # Wrong passwords count against the account, so one student who keeps
    # guessing cannot lock out everyone else on the same network.
    account = User.objects.filter(email=email).first() if email else None
    if account and account.is_locked():
        return Response(
            {'detail': f'Too many wrong passwords for this account. Try again in {LOGIN_LOCK_MINUTES} minutes.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    user = authenticate(request, username=email, password=password)
    if user is None:
        if account:
            account.record_failed_login()
        return Response({'detail': 'Invalid email or password.'}, status=status.HTTP_401_UNAUTHORIZED)
    user.reset_login_attempts()
    tokens = get_tokens(user, request)
    return Response({'user': user_data(user), **tokens})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def register_view(request):
    data = request.data
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')
    first_name = data.get('first_name', '')
    last_name = data.get('last_name', '')

    if not email or not password:
        return Response({'detail': 'Email and password required.'}, status=400)
    if User.objects.filter(email=email).exists():
        return Response({'email': ['A user with this email already exists.']}, status=400)
    if len(password) < 8:
        return Response({'password': ['Password must be at least 8 characters.']}, status=400)

    user = User.objects.create_user(
        username=email,
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
    )
    tokens = get_tokens(user, request)
    return Response({'user': user_data(user), **tokens}, status=201)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def google_login_view(request):
    """
    Verify Google ID token from frontend (@react-oauth/google),
    create or get user, return JWT tokens.
    """
    from django.conf import settings
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    credential = request.data.get('credential', '')
    if not credential:
        return Response({'detail': 'Google credential required.'}, status=400)

    try:
        id_info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except Exception as e:
        return Response({'detail': f'Invalid Google token: {e}'}, status=400)

    email = id_info.get('email', '').lower()
    if not email:
        return Response({'detail': 'Email not found in Google token.'}, status=400)

    first_name = id_info.get('given_name', '')
    last_name = id_info.get('family_name', '')

    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            'username': email,
            'first_name': first_name,
            'last_name': last_name,
            'is_active': True,
            'signup_method': User.SIGNUP_GOOGLE,
        }
    )
    if created:
        user.set_unusable_password()
        user.save()

    tokens = get_tokens(user, request)
    return Response({'user': user_data(user), **tokens, 'created': created})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([])                  # needs a valid refresh token anyway
def token_refresh_view(request):
    """A fresh access token, while the device's session is still live."""
    from rest_framework_simplejwt.tokens import RefreshToken
    from rest_framework_simplejwt.exceptions import TokenError

    refresh_token = request.data.get('refresh', '')
    if not refresh_token:
        return Response({'detail': 'Refresh token required.'}, status=400)
    try:
        token = RefreshToken(refresh_token)
    except TokenError as e:
        return Response({'detail': str(e)}, status=401)

    key = token.get('sid')
    if key:
        if not devices.is_live(key):
            return Response(devices.signed_out_error(key), status=401)
        devices.seen(key)
        return Response({'access': str(token.access_token), 'refresh': str(token)})

    # Signed in before devices were counted: this device gets its session now.
    user = User.objects.filter(pk=token.get('user_id'), is_active=True).first()
    if user is None:
        return Response({'detail': 'User not found.'}, status=401)
    if not devices.has_sessions(user):
        return Response({'access': str(token.access_token), 'refresh': str(token)})
    return Response(get_tokens(user, request))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    # This device stops counting towards the limit at once.
    key = request.auth.get('sid') if request.auth is not None and hasattr(request.auth, 'get') else None
    if key:
        devices.revoke([key], UserSession.Reason.LOGOUT)
    refresh_token = request.data.get('refresh', '')
    if refresh_token:
        try:
            from rest_framework_simplejwt.tokens import RefreshToken
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            pass
    return Response({'detail': 'Logged out.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def devices_view(request):
    """This account's signed-in devices, the one asking first."""
    current = request.auth.get('sid') if request.auth is not None and hasattr(request.auth, 'get') else None
    rows = sorted(
        devices.live_sessions(request.user).order_by('-last_seen_at'),
        key=lambda s: s.key != current,
    )
    return Response({
        'limit': devices.max_devices() if devices.is_limited(request.user) else None,
        'devices': [
            {
                'id': s.id,
                'name': s.device,
                'mobile': devices.is_mobile(s.user_agent),
                'ip': s.ip,
                'signed_in_at': s.created_at,
                'last_seen_at': s.last_seen_at,
                'current': s.key == current,
            }
            for s in rows
        ],
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def device_sign_out_view(request, pk):
    """Sign one of this account's devices out."""
    session = devices.live_sessions(request.user).filter(pk=pk).first()
    if session is None:
        return Response({'detail': 'Device not found.'}, status=404)
    devices.revoke([session.key], UserSession.Reason.USER)
    return Response(status=204)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(user_data(request.user))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([])
def ping_view(request):
    """Heartbeat from an open tab; LastSeenMiddleware does the recording."""
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_profile_view(request):
    user = request.user
    data = request.data
    fields = []
    if 'first_name' in data:
        user.first_name = data['first_name'].strip()
        fields.append('first_name')
    if 'last_name' in data:
        user.last_name = data['last_name'].strip()
        fields.append('last_name')
    if 'target_band' in data:
        user.target_band = str(data['target_band']).strip()
        fields.append('target_band')
    if 'exam_date' in data:
        # parse here: a raw string on the model was saved fine, but user_data() then
        # called .isoformat() on it (500), and a bad string made save() itself fail
        raw = data['exam_date']
        text = str(raw).strip() if raw else ''
        if text:
            try:
                exam_date = parse_date(text)
            except ValueError:                  # well formed but impossible, e.g. 2026-02-30
                exam_date = None
            if exam_date is None:
                return Response({'detail': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)
        else:
            exam_date = None
        user.exam_date = exam_date
        fields.append('exam_date')
    if 'daily_study_minutes' in data:
        try:
            user.daily_study_minutes = max(1, int(data['daily_study_minutes']))
            fields.append('daily_study_minutes')
        except (ValueError, TypeError):
            pass
    if 'email_newsletter' in data:
        user.email_newsletter = bool(data['email_newsletter'])
        fields.append('email_newsletter')
    if 'contact_email' in data:
        # Telegram accounts only: a contact address, not a login — nothing proves
        # the student owns it, so it must never become `email`.
        if user.signup_method != User.SIGNUP_TELEGRAM:
            return Response({'detail': 'Your e-mail is your sign-in and cannot be changed here.'}, status=400)
        address = str(data['contact_email'] or '').strip().lower()
        try:
            validate_email(address)
        except ValidationError:
            return Response({'contact_email': ['Enter a valid e-mail address.']}, status=400)
        user.contact_email = address
        fields.append('contact_email')
    if fields:
        user.save(update_fields=fields)
    return Response(user_data(user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    user = request.user
    old_password = request.data.get('old_password', '')
    new_password = request.data.get('new_password', '')
    if not old_password or not new_password:
        return Response({'detail': 'Both old and new passwords are required.'}, status=400)
    if not user.check_password(old_password):
        return Response({'detail': 'Current password is incorrect.'}, status=400)
    if len(new_password) < 8:
        return Response({'detail': 'New password must be at least 8 characters.'}, status=400)
    user.set_password(new_password)
    user.save(update_fields=['password'])
    return Response({'detail': 'Password changed successfully.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def platform_bridge_auth(request):
    """
    TestMakon.uz calls this endpoint with a shared API key to get JWT tokens
    for its users. SAT+ creates or retrieves the user by platform_uid.

    POST body: { api_key, platform_uid, phone, full_name }
    """
    api_key = request.data.get('api_key', '')
    expected_key = getattr(settings, 'TESTMAKON_API_KEY', '')
    if not expected_key or api_key != expected_key:
        return Response({'detail': 'Invalid API key.'}, status=status.HTTP_403_FORBIDDEN)

    platform_uid = request.data.get('platform_uid', '').strip()
    if not platform_uid:
        return Response({'detail': 'platform_uid required.'}, status=status.HTTP_400_BAD_REQUEST)

    phone = request.data.get('phone', '').strip()
    full_name = request.data.get('full_name', '').strip()
    is_premium = bool(request.data.get('is_premium', False))
    premium_until = request.data.get('premium_until')  # ISO string or None

    # Find or create the SAT+ user for this testmakon user
    user = User.objects.filter(platform='testmakon', platform_uid=platform_uid).first()
    if not user:
        # Build a unique email from platform_uid so Django's email unique constraint holds
        fake_email = f"tm_{platform_uid}@testmakon.internal"
        user, created = User.objects.get_or_create(
            email=fake_email,
            defaults={
                'username': f'tm_{platform_uid}',
                'platform': 'testmakon',
                'signup_method': User.SIGNUP_TESTMAKON,
                'platform_uid': platform_uid,
                'phone': phone,
                'is_active': True,
                'profile_completed': True,
            }
        )
        if created and full_name:
            parts = full_name.split(' ', 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ''
            user.save(update_fields=['first_name', 'last_name'])

    # Sync premium status from testmakon
    from django.utils import timezone as tz
    from django.utils.dateparse import parse_datetime
    changed = []
    if user.is_premium != is_premium:
        user.is_premium = is_premium
        changed.append('is_premium')
    if premium_until:
        parsed = parse_datetime(premium_until)
        if parsed and user.premium_until != parsed:
            user.premium_until = parsed
            changed.append('premium_until')
    elif not is_premium and user.premium_until:
        user.premium_until = None
        changed.append('premium_until')
    if changed:
        user.save(update_fields=changed)

    tokens = get_tokens(user, request)
    return Response({
        'access': tokens['access'],
        'refresh': tokens['refresh'],
        'user': user_data(user),
    })
