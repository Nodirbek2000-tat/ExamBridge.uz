"""
Hidden daily limit for IELTS + CEFR speaking tests (owner, 2026-10-08).

    3 speaking tests per rolling 24 hours, one allowance shared by
      - IELTS speaking (api/ielts_views.py — legacy CEFR practice tasks run on the same IELTS attempts)
      - CEFR multilevel speaking (api/cefr_speaking.py)
    The Speaking game (speaking/ app) is not part of it.
    The 3rd test locks the user for 12 hours from that moment; when the lock ends the count
    starts again from zero. Nothing shows the limit before it is hit: the only trace is the 429.
    It applies to everyone — premium, staff and superusers too (no similar limit in the project
    exempts anyone: speaking/views.py, games/stt_views.py, api/analytics_ai.py).

What counts (one `ielts.SpeakingUse` row each):
  IELTS  the first successful submission of an attempt. A practice attempt (no IELTSTest) is
         counted per task: /ielts/attempt/start/ hands the same IN_PROGRESS attempt to every
         practice task opened before the first submit (two tabs), so each task on it is its
         own test. A full-test attempt counts once however many tasks it submits. Submitting
         the same thing again within RESUBMIT_GRACE (a network retry) is free and never
         counted again; reused later than that (an old ?attempt= link) it is a new test.
  CEFR   the first successful submission of a response. A response is submitted once
         (the status leaves IN_PROGRESS), so a repeated submit is free forever.

While locked:
  - starting a speaking test (IELTS or CEFR), and submitting an attempt that was not counted
    yet, answer 429 {code, detail, retry_after_seconds, unlock_at} + a Retry-After header
  - grace: ONE test that was already running when the limit was reached (started before the
    3rd use) may still be submitted, so nobody loses a recording they were making in a second
    tab. That use is recorded but does not lock again, and it does not count after the unlock.

Storage and concurrency: database rows, no cache (a Redis restart changes nothing). Each
decision runs in a short transaction that locks the user's row (SELECT ... FOR UPDATE), so
simultaneous submits of one user are decided one after another and never exceed the limit;
the only extra test is the explicit grace above. The use is written BEFORE the upload / Whisper
work and removed again when that work fails, so a refused race never pays for Whisper and a
failed submission does not cost the student anything.
"""
import math
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from ielts.models import SpeakingUse

LIMIT = 3
WINDOW = timedelta(hours=24)
LOCK = timedelta(hours=12)
RESUBMIT_GRACE = timedelta(hours=2)
CODE = 'speaking_daily_limit'

IELTS = SpeakingUse.Kind.IELTS
CEFR = SpeakingUse.Kind.CEFR


def _last_lock(user):
    """The use that set the user's most recent lock (it may have ended), or None."""
    return (SpeakingUse.objects.filter(user=user, locked_until__isnull=False)
            .order_by('-locked_until').only('id', 'used_at', 'locked_until').first())


def _used(user, now, last_lock):
    """Tests in the rolling window that started after the last lock ended."""
    qs = SpeakingUse.objects.filter(user=user, used_at__gt=now - WINDOW)
    if last_lock is not None:
        qs = qs.filter(used_at__gte=last_lock.locked_until)
    return qs.count()


def locked_until(user, now=None):
    """When the user's lock ends, or None when they may take a speaking test."""
    now = now or timezone.now()
    lock = _last_lock(user)
    return lock.locked_until if lock is not None and lock.locked_until > now else None


def _ceil_second(moment):
    """The moment rounded UP to a whole second, so a client that waits until unlock_at is never early."""
    return moment if not moment.microsecond else moment.replace(microsecond=0) + timedelta(seconds=1)


def refusal(until, now=None):
    """The 429 every client shows. N hours rounded up; under one hour, M minutes (at least 1)."""
    now = now or timezone.now()
    seconds = max(1, math.ceil((until - now).total_seconds()))
    if seconds >= 3600:
        wait = f'{math.ceil(seconds / 3600)} soatdan'
    else:
        wait = f'{max(1, math.ceil(seconds / 60))} daqiqadan'
    resp = Response({
        'code': CODE,
        'detail': f'Kunlik limitingiz tugadi. Limitingiz {wait} keyin ochiladi.',
        'retry_after_seconds': seconds,
        'unlock_at': timezone.localtime(_ceil_second(until)).isoformat(timespec='seconds'),
    }, status=429)
    resp['Retry-After'] = str(seconds)
    return resp


def refuse_if_locked(user):
    """429 Response while the user is locked (start of a new test), else None."""
    now = timezone.now()
    until = locked_until(user, now)
    return refusal(until, now) if until else None


def claim(user, kind, ref_id, started_at, task_ref=None, free_for=RESUBMIT_GRACE):
    """
    Count one submission. Call it once the request is valid and BEFORE any upload or AI work.
    task_ref: the SpeakingTask of an IELTS practice attempt (each task on it is its own test);
              None for a full-test attempt and for CEFR.

    Returns (refusal, use_id):
      (429 Response, None)  locked — do nothing else
      (None, None)          already counted (a retry): go ahead, nothing to undo
      (None, id)            counted now: go ahead, and release(id) if the work fails
    free_for: how long after its use the same ref may be submitted again for free
              (None = forever).
    """
    with transaction.atomic():
        # one decision at a time per user: a parallel submit waits here for a few milliseconds
        get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
        now = timezone.now()            # after the wait, so the uses stay in decision order

        same = SpeakingUse.objects.filter(kind=kind, ref_id=ref_id, task_ref=task_ref)
        counted = same if free_for is None else same.filter(used_at__gt=now - free_for)
        if counted.exists():
            return None, None
        # a ref counted earlier (outside free_for) is a reused attempt: a new test, never "already running"
        fresh = not same.exists()

        lock = _last_lock(user)
        if lock is not None and lock.locked_until > now:
            already_running = fresh and started_at is not None and started_at < lock.used_at
            grace_used = SpeakingUse.objects.filter(user=user, used_at__gte=lock.used_at).exclude(pk=lock.pk).exists()
            if not already_running or grace_used:
                return refusal(lock.locked_until, now), None
            use = SpeakingUse.objects.create(user=user, kind=kind, ref_id=ref_id, task_ref=task_ref, used_at=now)
            return None, use.pk

        reached = _used(user, now, lock) + 1 >= LIMIT
        use = SpeakingUse.objects.create(user=user, kind=kind, ref_id=ref_id, task_ref=task_ref, used_at=now,
                                         locked_until=now + LOCK if reached else None)
        return None, use.pk


def release(use_id):
    """Undo a use whose submission failed, so the student keeps the allowance."""
    if use_id:
        SpeakingUse.objects.filter(pk=use_id).delete()
