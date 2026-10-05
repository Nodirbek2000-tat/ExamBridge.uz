"""
Spaced repetition for the word bank — one rule for Toby Run (skill 'say') and Word Battle
(skill 'mean'). RUNNER_PLAN §B4.3.

Leitner boxes 0–6. A box's interval: 1 → 1 day, 2 → 3, 3 → 7, 4 → 14, 5 → 30, 6 → 60 days.

    apply(review, verdict, now, *, tries=1, pt='', practice=False) → effect

    verdict   'ok' | 'close' | 'miss' | 'skip'
    tries     1 = the first try of this moment (an echo retry is 2)
    pt        the prompt type that was shown ('hear', 'choice', 'picture', 'uz', 'fill', …);
              a 'hear' pass is only an echo of the model voice, so it never promotes
    practice  results-screen retries / the repair drill: never moves the box

| Event                                   | Effect                                                   | → effect     |
|-----------------------------------------|----------------------------------------------------------|--------------|
| ok on the first try, pt != 'hear'       | box + 1 (max 6), at most one promotion per calendar day   | 'promoted'   |
|                                         | (promoted_on, Asia/Tashkent); due = now + interval(box)  |  or 'held'   |
| 'hear' ok, a second-try ok, or close    | no promotion; due = now + 1 day if box ≥ 1, else now      | 'held'       |
| miss                                    | box = max(0, box − 2); due = now + 10 min                 | 'demoted'    |
| skip                                    | nothing changes                                           | 'skipped'    |
| practice pass (ok / close)              | no box change; due = now + 1 day                          | 'fixed'      |
| practice miss / skip                    | nothing changes                                           | 'skipped'    |

A first-try ok on a day the item was already promoted keeps its box and is scheduled by it
('held'). Outside practice, apply() also keeps the counters: seen (not on skip), ok (verdict
ok), miss, first_ok (ok on the first try) and last_verdict. The caller sets last_heard / best_ms.

"Mastered" = box ≥ 3 (recalled on at least 3 separate days).

Save many reviews at once with save_reviews(rows) — one INSERT … ON CONFLICT UPDATE.
"""
from datetime import timedelta

from django.utils import timezone

INTERVAL_DAYS = (1, 3, 7, 14, 30, 60)
MAX_BOX = 6
MASTERED_BOX = 3
MISS_DELAY = timedelta(minutes=10)
VERDICTS = ('ok', 'close', 'miss', 'skip')

# the fields save_reviews() rewrites on a conflict (everything but the identity)
UPDATE_FIELDS = ['box', 'due_at', 'seen', 'ok', 'miss', 'first_ok', 'best_ms', 'last_heard', 'last_verdict',
                 'promoted_on', 'updated_at']


def interval(box):
    """Days until the next review for a box (box 0 → 0: it comes back in the next session)."""
    if box <= 0:
        return 0
    return INTERVAL_DAYS[min(box, MAX_BOX) - 1]


def _local_day(now):
    return timezone.localdate(now) if timezone.is_aware(now) else now.date()


def apply(review, verdict, now, *, tries=1, pt='', practice=False):
    """Update one Review (in memory) for one outcome. Returns the effect (see the module table)."""
    if verdict not in VERDICTS:
        raise ValueError(f'unknown verdict {verdict!r}')
    box = review.box or 0

    if practice:
        if verdict in ('ok', 'close'):
            review.due_at = now + timedelta(days=1)
            return 'fixed'
        return 'skipped'

    if verdict == 'skip':
        return 'skipped'

    review.seen = (review.seen or 0) + 1
    review.last_verdict = verdict

    if verdict == 'miss':
        review.miss = (review.miss or 0) + 1
        review.box = max(0, box - 2)
        review.due_at = now + MISS_DELAY
        return 'demoted'

    if verdict == 'ok':
        review.ok = (review.ok or 0) + 1
        if tries <= 1:
            review.first_ok = (review.first_ok or 0) + 1

    if verdict == 'ok' and tries <= 1 and pt != 'hear':
        today = _local_day(now)
        if review.promoted_on != today:
            review.box = min(MAX_BOX, box + 1)
            review.promoted_on = today
            review.due_at = now + timedelta(days=interval(review.box))
            return 'promoted'
        review.box = box
        review.due_at = now + timedelta(days=interval(box)) if box >= 1 else now
        return 'held'

    # a 'hear' pass, a second-try pass or a close one: keep the box, see it again soon
    review.box = max(box, 0)
    review.due_at = now + timedelta(days=1) if review.box >= 1 else now
    return 'held'


def new_review(user_id, kind, item_id, skill, now=None):
    """An unsaved Review for an item the learner has never met (box 0, due now)."""
    from .models import Review
    return Review(user_id=user_id, kind=kind, item_id=item_id, skill=skill, box=0, due_at=now or timezone.now())


def save_reviews(rows):
    """Insert or update many Review rows in one statement (unique: user, kind, item_id, skill)."""
    from .models import Review
    if not rows:
        return []
    now = timezone.now()
    for r in rows:              # bulk_create sets auto_now on insert only; an update needs it too
        r.updated_at = now
    return Review.objects.bulk_create(
        rows, update_conflicts=True, unique_fields=['user', 'kind', 'item_id', 'skill'], update_fields=UPDATE_FIELDS,
    )
