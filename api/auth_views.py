from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import User


def user_data(user):
    return {
        'id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_premium': user.is_premium,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'avatar': user.avatar.url if user.avatar else None,
        'target_band': user.target_band or '',
        'exam_date': user.exam_date.isoformat() if user.exam_date else None,
        'daily_study_minutes': user.daily_study_minutes,
        'email_newsletter': user.email_newsletter,
        'premium_until': user.premium_until.isoformat() if user.premium_until else None,
        'created_at': user.created_at.isoformat() if hasattr(user, 'created_at') and user.created_at else None,
    }


def get_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def csrf_token(request):
    return Response({'csrfToken': get_token(request)})


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    email = request.data.get('email', '').lower().strip()
    password = request.data.get('password', '')
    user = authenticate(request, username=email, password=password)
    if user is None:
        return Response({'detail': 'Invalid email or password.'}, status=status.HTTP_401_UNAUTHORIZED)
    if user.is_locked():
        return Response({'detail': 'Account locked. Try again later.'}, status=status.HTTP_403_FORBIDDEN)
    user.reset_login_attempts()
    tokens = get_tokens(user)
    return Response({'user': user_data(user), **tokens})


@api_view(['POST'])
@permission_classes([AllowAny])
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
    tokens = get_tokens(user)
    return Response({'user': user_data(user), **tokens}, status=201)


@api_view(['POST'])
@permission_classes([AllowAny])
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
        }
    )
    if created:
        user.set_unusable_password()
        user.save()

    tokens = get_tokens(user)
    return Response({'user': user_data(user), **tokens, 'created': created})


@api_view(['POST'])
@permission_classes([AllowAny])
def token_refresh_view(request):
    """Refresh JWT access token using refresh token."""
    from rest_framework_simplejwt.tokens import RefreshToken
    from rest_framework_simplejwt.exceptions import TokenError

    refresh_token = request.data.get('refresh', '')
    if not refresh_token:
        return Response({'detail': 'Refresh token required.'}, status=400)
    try:
        token = RefreshToken(refresh_token)
        return Response({'access': str(token.access_token), 'refresh': str(token)})
    except TokenError as e:
        return Response({'detail': str(e)}, status=401)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    # With JWT, client just deletes tokens. Optionally blacklist refresh token.
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
def me_view(request):
    return Response(user_data(request.user))


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
        user.exam_date = data['exam_date'] or None
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

    tokens = get_tokens(user)
    return Response({
        'access': tokens['access'],
        'refresh': tokens['refresh'],
        'user': user_data(user),
    })
