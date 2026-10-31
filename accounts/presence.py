"""Who is on the site right now, and who came by on which day.

Every authenticated API request refreshes User.last_seen_at, so the web app and
the mobile app are both covered without either having to do anything special.
An open tab with no requests (someone reading a long passage) keeps itself
online through GET /api/auth/ping/.

The write is limited to once a minute per user: the cache key decides, so a
busy page costs one cache call per request and no database writes.
"""

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from .models import User, UserDailyVisit

logger = logging.getLogger(__name__)

WRITE_EVERY_SECONDS = 60
# Longer than the 60 s heartbeat, so one lost ping does not read as "left".
ONLINE_WINDOW = timedelta(minutes=3)


def online_since():
    """Anyone seen after this moment counts as online."""
    return timezone.now() - ONLINE_WINDOW


def touch(user):
    """Record that this user is here. Never lets a failure reach the request."""
    try:
        if not cache.add(f'presence:{user.pk}', 1, WRITE_EVERY_SECONDS):
            return
        now = timezone.now()
        today = timezone.localdate(now)
        previous = user.last_seen_at
        # .update() on purpose: it leaves User.updated_at alone.
        User.objects.filter(pk=user.pk).update(last_seen_at=now)
        if previous is None or timezone.localdate(previous) != today:
            UserDailyVisit.objects.bulk_create(
                [UserDailyVisit(user=user, date=today)], ignore_conflicts=True,
            )
    except Exception:
        logger.exception('Could not record last_seen for user %s', user.pk)


class LastSeenMiddleware:
    """Runs after the view: JWT users only exist once DRF has authenticated them."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/api/'):
            user = getattr(request, 'user', None)
            if getattr(user, 'is_authenticated', False):
                touch(user)
        return response
