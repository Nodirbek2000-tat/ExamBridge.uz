"""
Site side of the Telegram bot: open the bot linked to your account, and sign
in with the one-time link the bot gives you.
"""
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from api.auth_views import LoginRateThrottle, get_tokens, user_data

from .models import TelegramAccount, TelegramToken


def bot_url(start=None):
    name = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
    if not name:
        return None
    return f'https://t.me/{name}' + (f'?start={start}' if start else '')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def telegram_open(request):
    """The bot link for this account: links Telegram first if it is not linked yet."""
    if not bot_url():
        return Response({'detail': 'Telegram payments are not configured yet.'}, status=503)
    linked = TelegramAccount.objects.filter(user=request.user).exists()
    if linked:
        return Response({'url': bot_url('premium'), 'linked': True})
    token = TelegramToken.issue(request.user, TelegramToken.LINK)
    return Response({'url': bot_url(f'link_{token.token}'), 'linked': False})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def telegram_login(request):
    """Exchange the bot's one-time sign-in link for the usual JWT pair."""
    user = TelegramToken.redeem(str(request.data.get('token') or '').strip(), TelegramToken.LOGIN)
    if user is None:
        return Response(
            {'detail': 'This sign-in link has expired or was already used. Open the bot and tap “Saytga kirish” again.'},
            status=400,
        )
    return Response({'user': user_data(user), **get_tokens(user)})
