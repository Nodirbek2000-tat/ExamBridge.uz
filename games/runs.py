"""
Saving one finished run of a voice game — shared by POST /api/games/voice/<slug>/runs/
and the Runner's POST /api/games/runner/finish/.

    record_run(user, slug, *, score, stars, accuracy, level, duration_sec, lines_said,
               ref='', ranked=True, meta=None) → (run, best)

Values must already be clean (the views clamp what the client sent). Creates the
VoiceGameRun and bumps the learner's VoiceGameProgress totals with one F() UPDATE, so two
runs finishing at once never lose a play or stars. Runs inside the caller's transaction
(it opens a savepoint of its own).

`ref` (≤ 32 chars) makes a run idempotent: a second run with the same (user, ref) raises
IntegrityError from the unique constraint — the caller rolls back its own work and answers
with the stored run (find_run). `ranked=False` keeps the run out of the weekly leaderboard.
count_play() is NOT called here: the caller does it after the commit.
"""
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from .models import VoiceGameProgress, VoiceGameRun


def record_run(user, slug, *, score, stars, accuracy, level, duration_sec, lines_said,
               ref='', ranked=True, meta=None):
    with transaction.atomic():
        run = VoiceGameRun.objects.create(
            user=user, slug=slug, score=score, stars=stars, accuracy=accuracy,
            level=level, duration_sec=duration_sec, lines_said=lines_said,
            ref=(ref or '')[:32], ranked=bool(ranked), meta=meta if isinstance(meta, dict) else {},
        )
        progress, _ = VoiceGameProgress.objects.get_or_create(user=user, slug=slug)
        VoiceGameProgress.objects.filter(pk=progress.pk).update(
            plays=F('plays') + 1,
            stars_total=F('stars_total') + stars,
            best_score=Greatest(F('best_score'), Value(score)),
            updated_at=timezone.now(),
        )
        best = VoiceGameProgress.objects.filter(pk=progress.pk).values_list('best_score', flat=True).first()
    return run, best or 0


def find_run(user, slug, ref):
    """The run already saved under this ref (for an idempotent retry), or None."""
    if not ref:
        return None
    return VoiceGameRun.objects.filter(user=user, slug=slug, ref=ref[:32]).first()
