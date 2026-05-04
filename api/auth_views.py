from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
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
