"""Free accounts get FREE_MESSAGES AI Tutor messages in total, across all their chats.
Premium accounts and staff are not limited. The AI video lessons are not counted here."""
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import AIChatSent

FREE_MESSAGES = 5


def unlimited(user):
    if user.is_staff:
        return True
    try:
        return bool(user.check_premium())
    except Exception:
        return bool(getattr(user, 'is_premium', False))


def status(user):
    if unlimited(user):
        return {'unlimited': True, 'limit': None, 'used': None, 'left': None}
    used = AIChatSent.objects.filter(user=user).count()
    return {'unlimited': False, 'limit': FREE_MESSAGES, 'used': used, 'left': max(0, FREE_MESSAGES - used)}


def take(user):
    """Count one message. Returns the sent record (None when unlimited), or False when the free
    messages are used up. The user row is locked so two quick sends cannot both get the last one."""
    if unlimited(user):
        return None
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if AIChatSent.objects.filter(user=user).count() >= FREE_MESSAGES:
            return False
        return AIChatSent.objects.create(user=user)


def give_back(sent):
    """The AI failed to answer: the message should not count."""
    if sent:
        AIChatSent.objects.filter(pk=sent.pk).delete()
