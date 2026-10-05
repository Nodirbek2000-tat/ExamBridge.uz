"""
Word bank background jobs.

rollup_item_stats (nightly, beat 03:40 Asia/Tashkent):
    SUM(seen), SUM(ok) of vocabulary.Review by (kind, item_id, skill) → Word.say_seen / say_ok /
    mean_seen / mean_ok and Phrase.say_seen / say_ok; say_heard = the top 3 `last_heard` among
    'say' rows whose last verdict was a miss (items seen ≥ 20 times); Reviews of items that no
    longer exist are deleted.

(speak_check — the TTS → Whisper speakability screen — lands in P5.)
"""
import logging
from collections import Counter, defaultdict

from celery import shared_task
from django.db.models import Count, Sum

log = logging.getLogger(__name__)

HEARD_MIN_SEEN = 20
HEARD_TOP = 3
BATCH = 1000


def _purge_orphans():
    from .models import Phrase, Review, Word
    gone = Review.objects.filter(kind='w').exclude(item_id__in=Word.objects.values('id')).delete()[0]
    gone += Review.objects.filter(kind='p').exclude(item_id__in=Phrase.objects.values('id')).delete()[0]
    return gone


def _top_heard(heavy):
    """{(kind, item_id): [heard, …]} for the items in `heavy`."""
    from .models import Review
    if not heavy:
        return {}
    counts = defaultdict(Counter)
    for kind in ('w', 'p'):
        ids = [i for k, i in heavy if k == kind]
        for start in range(0, len(ids), BATCH):
            rows = (Review.objects
                    .filter(kind=kind, skill='say', last_verdict='miss', item_id__in=ids[start:start + BATCH])
                    .exclude(last_heard='')
                    .values('item_id', 'last_heard').annotate(n=Count('id')))
            for r in rows:
                counts[(kind, r['item_id'])][r['last_heard'].strip().lower()[:80]] += r['n']
    return {key: [h for h, _ in c.most_common(HEARD_TOP)] for key, c in counts.items()}


def rollup():
    """The work itself (callable without Celery). → {'words': n_updated, 'phrases': n_updated, 'purged': n}."""
    from .models import Phrase, Review, Word

    purged = _purge_orphans()
    sums = {}
    for r in Review.objects.values('kind', 'item_id', 'skill').annotate(seen=Sum('seen'), ok=Sum('ok')).order_by():
        sums[(r['kind'], r['item_id'], r['skill'])] = (r['seen'] or 0, r['ok'] or 0)
    heavy = {(k, i) for (k, i, s), (seen, _) in sums.items() if s == 'say' and seen >= HEARD_MIN_SEEN}
    heard = _top_heard(heavy)

    updated = {'words': 0, 'phrases': 0}
    changed_words = []
    for w in Word.objects.only('id', 'say_seen', 'say_ok', 'mean_seen', 'mean_ok', 'say_heard').iterator(chunk_size=BATCH):
        say = sums.get(('w', w.id, 'say'), (0, 0))
        mean = sums.get(('w', w.id, 'mean'), (0, 0))
        h = heard.get(('w', w.id), [])
        new = (say[0], say[1], mean[0], mean[1], h)
        if (w.say_seen, w.say_ok, w.mean_seen, w.mean_ok, w.say_heard or []) != new:
            w.say_seen, w.say_ok, w.mean_seen, w.mean_ok, w.say_heard = new
            changed_words.append(w)
    for start in range(0, len(changed_words), BATCH):
        Word.objects.bulk_update(changed_words[start:start + BATCH],
                                 ['say_seen', 'say_ok', 'mean_seen', 'mean_ok', 'say_heard'])
    updated['words'] = len(changed_words)

    changed_phrases = []
    for p in Phrase.objects.only('id', 'say_seen', 'say_ok', 'say_heard').iterator(chunk_size=BATCH):
        say = sums.get(('p', p.id, 'say'), (0, 0))
        h = heard.get(('p', p.id), [])
        if (p.say_seen, p.say_ok, p.say_heard or []) != (say[0], say[1], h):
            p.say_seen, p.say_ok, p.say_heard = say[0], say[1], h
            changed_phrases.append(p)
    for start in range(0, len(changed_phrases), BATCH):
        Phrase.objects.bulk_update(changed_phrases[start:start + BATCH], ['say_seen', 'say_ok', 'say_heard'])
    updated['phrases'] = len(changed_phrases)
    return {**updated, 'purged': purged}


@shared_task(ignore_result=True)
def rollup_item_stats():
    try:
        result = rollup()
        log.info('vocabulary roll-up: %s', result)
    except Exception:
        log.exception('vocabulary roll-up failed')
