"""
TOBY RUN — the pure rules the server needs (RUNNER_PLAN §B4, §B7, §B8.3). No database access:
the views fetch the rows, these functions decide.

    LEVELS, VMAX, LEVEL_K, LEVEL_L        per-level numbers (mirrored in runner/engine/levels.js)
    word_window(n, level, ws)             the browser mic window for a word / chunk (§B3.2)
    score(outcomes, level=, distance_m=, stt=, mode=, window_scale=)  → {points, passes, score, …}
                                          the §B7 formula — parity with runner/engine/score.js is tested
                                          with games/fixtures/runner_score_cases.json
    plausibility(…)                       → [flags]  (§B8.3 finish rule 3)
    pick_prompt_type(level, item, box, …) → 'hear' | 'choice' | 'picture' | 'uz' | 'definition' | …
    build_deck(level, topic, seed, cands, reviews, now, *, twister)  → (items, token_fields)
    FIXED_VOICE_LINES                     Toby's cheers + Bekat lines for the TTS warm (vocabulary.bank.warm_lines)

Outcome (one spoken moment), as the client posts it and as score() reads it:
    {k: 'w'|'p', id, kind, pt, v: 'ok'|'close'|'miss'|'skip', tries: 1|2, ms, heard, m, n}
    m  the moment: 'b' balloon (words, fill phrases) · 's' Bekat line · 't' twister · 'r' revive
    n  words the learner had to say (the server fills it from the deck token)
"""
import math
import random

LEVEL_ORDER = ('A1', 'A2', 'B1', 'B2', 'C1')

# start → cap speed (m/s), running gap between balloons (s), Bekat every (m), chunk difficulty range
LEVELS = {
    'A1': {'start': 8.0, 'cap': 13.0, 'gap': 12.0, 'station_every': 650, 'diff': (1, 2)},
    'A2': {'start': 9.0, 'cap': 14.5, 'gap': 11.0, 'station_every': 700, 'diff': (1, 3)},
    'B1': {'start': 10.0, 'cap': 16.0, 'gap': 10.0, 'station_every': 800, 'diff': (2, 4)},
    'B2': {'start': 11.0, 'cap': 17.0, 'gap': 9.5, 'station_every': 850, 'diff': (3, 5)},
    'C1': {'start': 12.0, 'cap': 18.0, 'gap': 9.0, 'station_every': 900, 'diff': (3, 5)},
}
VMAX = {lv: LEVELS[lv]['cap'] for lv in LEVEL_ORDER}
LEVEL_K = {'A1': 1.0, 'A2': 1.1, 'B1': 1.2, 'B2': 1.35, 'C1': 1.5}
LEVEL_L = {'A1': 1.15, 'A2': 1.08, 'B1': 1.0, 'B2': 0.95, 'C1': 0.9}     # mic-window factor

DECK_SIZE = 40
DECK_WORDS = 28
DECK_PHRASES = 10
DECK_TWISTERS = 2
VERDICTS = ('ok', 'close', 'miss', 'skip')
MOMENTS = ('b', 's', 't', 'r')

# Toby's cheers after a pass (his own voice) and the lines around a Bekat — warmed with the bank
CHEERS = ('Yes!', 'Super!', 'Great!', 'Well done!', 'Wow!', 'Nice!', 'Awesome!', 'Perfect!', 'You did it!',
          'Brilliant!', 'Good job!', 'Amazing!')
BEKAT_LINES = (
    ('Hello! Can you help me?', 'grandma'),
    ('Good morning! Welcome to the station.', 'driver'),
    ("What's this?", 'teacher'),
    ('Thank you! Have a nice trip!', 'grandma'),
    ('Next stop! Get ready!', 'driver'),
)
FIXED_VOICE_LINES = [(c, 'toby') for c in CHEERS] + list(BEKAT_LINES)


def rnd(x):
    """Round half up — the same in Python and JS (Python's round() is banker's rounding)."""
    return int(math.floor(x + 0.5))


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def words_in(text):
    return len(str(text or '').split())


def word_window(n, level, ws=1.0):
    """Seconds of mic time for a word / chunk of n words (browser; server mode adds 3 s)."""
    return max(3.5, (3.4 + 0.5 * n) * LEVEL_L.get(level, 1.0) * ws)


def speed_bonus(ms, w, stt):
    """Bonus for a fast first-try pass on a balloon. Server latency is not the learner's fault: flat 25."""
    if stt == 'server':
        return 25
    if ms is None or ms < 300:
        return 0
    return rnd(50 * clamp(1 - (ms - 600) / (w * 1000 - 600), 0, 1))


def item_points(o, *, level, stt, mode, ws):
    """Points for one outcome (§B7)."""
    v = o.get('v')
    if v not in ('ok', 'close'):
        return 0
    m = o.get('m') or 'b'
    first = int(o.get('tries') or 1) <= 1
    if m == 'b':
        if not first:
            return 50
        if v == 'close':
            return 60
        bonus = speed_bonus(o.get('ms'), word_window(int(o.get('n') or 1), level, ws), stt) if mode in ('voice', 'card') else 0
        return 100 + bonus
    if m == 's':
        return 150 if v == 'ok' else 100
    if m == 't':
        return 200 if v == 'ok' else 120
    return 0


def score(outcomes, *, level, distance_m, stt='browser', mode='voice', window_scale=1.0):
    """The §B7 formula. → {points, passes, mult, score}"""
    points = 0
    passes = 0
    for o in outcomes:
        points += item_points(o, level=level, stt=stt, mode=mode, ws=window_scale)
        if o.get('v') in ('ok', 'close') and int(o.get('tries') or 1) <= 1 and (o.get('m') or 'b') in ('b', 's', 't'):
            passes += 1
    mult = 1 + 0.1 * min(passes, 20)
    total = rnd((max(0, int(distance_m)) + points) * mult * LEVEL_K.get(level, 1.0))
    return {'points': points, 'passes': passes, 'mult': round(mult, 2), 'score': total}


def plausibility(*, level, distance_m, duration_s, outcomes, score_client, server_score, speed_scale=1.0):
    """Finish rule 3: anything impossible → a flag (the run is kept, unranked, and reviews are not touched)."""
    flags = []
    vmax = VMAX.get(level, 18.0) * float(speed_scale or 1.0)
    if distance_m > duration_s * vmax * 1.05 + 30:
        flags.append('distance')
    spoken = sum(1 for o in outcomes if o.get('v') != 'skip')
    if spoken > duration_s / 2.5 + 3:
        flags.append('too-many')
    firsts = [o for o in outcomes if o.get('v') in ('ok', 'close') and int(o.get('tries') or 1) <= 1
              and (o.get('m') or 'b') in ('b', 's', 't')]
    timed = [o for o in firsts if isinstance(o.get('ms'), int)]
    if timed and sum(1 for o in timed if o['ms'] < 400) * 2 > len(firsts):
        flags.append('too-fast')
    if score_client is not None and score_client > 1.25 * server_score:
        flags.append('score-mismatch')
    return flags


# ── the deck (§B4.1, §B4.2) ──────────────────────────────────────────────────

def level_below(level):
    i = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 0
    return LEVEL_ORDER[i - 1] if i > 0 else None


def pick_prompt_type(level, w, box, *, bridge=False):
    """The prompt a word gets from its `say` box (None = never said). w: a candidate word dict."""
    young = level in ('A1', 'A2')
    pic = bool(w.get('picture'))
    uz = bool(w.get('uz'))
    if w.get('speak_risk'):                       # recogniser risk: only with the answer in view or in the ear
        return 'hear' if box is None else 'choice'
    if bridge:                                    # meaning known from Word Battle — now say it
        return 'picture' if (young and pic) else 'uz' if uz else 'hear'
    if box is None:
        return 'hear'
    if young:
        if box <= 1:
            return 'choice'
        if box <= 3:
            return 'picture' if pic else 'uz' if uz else 'choice'
        return 'uz' if uz else 'picture' if pic else 'choice'
    if box <= 3:
        return 'uz' if uz else 'picture' if pic else 'hear'
    opts = []
    if w.get('antonyms'):
        opts.append('opposite')
    if w.get('synonyms'):
        opts.append('synonym')
    if w.get('definition') and level in ('B2', 'C1'):
        opts.append('definition')
    if opts:
        return opts[(w['id'] + box) % len(opts)]
    return 'uz' if uz else 'picture' if pic else 'hear'


def _choice_pool(cands):
    """vocabulary.bank.distractor_pool format, built from the cached candidates (no query)."""
    pool = {}
    for w in cands:
        pool.setdefault((w['level'], w['pos']), []).append(
            (w['id'], w['word'], w['uz'], w['picture'], {s.lower() for s in w.get('synonyms') or ()}))
    return pool


class _W:                                     # what bank.distractors_for() reads from a Word
    __slots__ = ('word', 'level', 'pos', 'uz', 'synonyms', 'distractors')

    def __init__(self, w):
        self.word = w['word']
        self.level = w['level']
        self.pos = w['pos']
        self.uz = w['uz']
        self.synonyms = w.get('synonyms') or []
        self.distractors = w.get('distractors') or []


def word_item(level, w, box, pt, rng, pool):
    item = {'k': 'w', 'id': w['id'], 'kind': 'word', 'pt': pt, 'text': w['word'], 'uz': w['uz'] or '',
            'picture': w['picture'] or '', 'say_also': list(w.get('say_also') or []), 'voice': 'teacher', 'box': box}
    if pt == 'choice':
        from vocabulary.bank import distractors_for
        wrong = distractors_for(_W(w), 2, pool=pool, rng=rng)
        if len(wrong) < 2:
            item['pt'] = 'hear' if box is None else ('picture' if w['picture'] else 'uz' if w['uz'] else 'hear')
        else:
            choices = [w['word']] + wrong
            rng.shuffle(choices)
            item['choices'] = choices
    elif pt == 'opposite':
        item['prompt'] = (w.get('antonyms') or [''])[0]
        item['say_also'] = item['say_also'] + [s for s in (w.get('synonyms') or []) if s][:4]
    elif pt == 'synonym':
        syns = [s for s in (w.get('synonyms') or []) if s]
        item['prompt'] = syns[0]
        item['say_also'] = item['say_also'] + syns[1:5]
    elif pt == 'definition':
        item['prompt'] = w.get('definition') or ''
    return item


def phrase_item(p, box):
    item = {'k': 'p', 'id': p['id'], 'kind': p['kind'], 'text': p['text'] or '', 'uz': p['uz'] or '',
            'voice': p['voice'] or {'echo': 'narrator', 'answer': 'teacher', 'fill': 'teacher', 'twister': 'coach'}.get(p['kind'], 'narrator'),
            'box': box}
    if p.get('picture'):
        item['picture'] = p['picture']
    if p['kind'] == 'answer':
        item['prompt'] = p['prompt'] or ''
        item['accept'] = p.get('accept') or []
        item['min_words'] = p.get('min_words') or 0
    elif p['kind'] == 'fill':
        item['pt'] = 'fill'
        item['prompt'] = p['prompt'] or ''
        item['answer'] = p['answer'] or ''
        item['accept'] = p.get('accept') or []
    return item


def build_deck(level, topic, seed, cands, reviews, now, *, twister=True):
    """
    cands:   {'w': [word dicts], 'p': [phrase dicts]} — published, level L and one below, any topic
    reviews: {(kind, item_id): {'say': (box, due_at), 'mean': (box, due_at)}}
    → (items, token) ; token = {'w': [ids], 'wn': [words to say], 'p': [ids], 'pk': 'eaft…', 'pn': [...]}
    """
    rng = random.Random(seed)
    below = level_below(level)
    top_first = (lambda it: 0 if it.get('topic') == topic else 1) if topic and topic != 'all' else (lambda it: 0)

    def say_of(kind, iid):
        r = reviews.get((kind, iid))
        return r.get('say') if r else None

    def mean_of(kind, iid):
        r = reviews.get((kind, iid))
        return r.get('mean') if r else None

    words = [w for w in cands.get('w', ()) if w['level'] in (level, below)]
    phrases = [p for p in cands.get('p', ()) if p['level'] in (level, below)]
    rng.shuffle(words)
    rng.shuffle(phrases)

    # ── words: due · bridge · new below · new at L · then anything already seen (soonest due first)
    due, bridge, new_low, new_l, later = [], [], [], [], []
    for w in words:
        s = say_of('w', w['id'])
        if s is None:
            m = mean_of('w', w['id'])
            if m is not None and m[0] >= 2 and not w.get('speak_risk'):
                bridge.append(w)
            elif w['level'] == level:
                new_l.append(w)
            else:
                new_low.append(w)
        elif s[1] <= now:
            due.append(w)
        else:
            later.append(w)
    due.sort(key=lambda w: (say_of('w', w['id'])[1], say_of('w', w['id'])[0]))
    later.sort(key=lambda w: (say_of('w', w['id'])[1], say_of('w', w['id'])[0]))
    new_l.sort(key=top_first)            # stable: the seeded shuffle stays inside each group
    new_low.sort(key=top_first)
    bridge.sort(key=top_first)

    picked, taken = [], set()

    def take(src, n, how):
        for w in src:
            if n <= 0:
                break
            if w['id'] in taken:
                continue
            taken.add(w['id'])
            picked.append((w, how))
            n -= 1

    nw = DECK_WORDS
    take(due, int(nw * 0.4), 'due')
    take(bridge, max(1, int(nw * 0.1)), 'bridge')
    take(new_low, int(nw * 0.15), 'new')
    take(new_l, nw - len(picked), 'new')
    take(new_low, nw - len(picked), 'new')
    take(due, nw - len(picked), 'due')
    take(bridge, nw - len(picked), 'bridge')
    take(later, nw - len(picked), 'due')

    pool = _choice_pool(words)
    items = []
    for w, how in picked:
        s = say_of('w', w['id'])
        box = s[0] if s else None
        pt = pick_prompt_type(level, w, box, bridge=(how == 'bridge'))
        items.append(word_item(level, w, box, pt, rng, pool))

    # ── phrases: Bekat lines (echo, answer), fill balloons (B1+), twisters (B1+, optional)
    bekat = [p for p in phrases if p['kind'] in ('echo', 'answer')]
    fills = [p for p in phrases if p['kind'] == 'fill']
    twists = [p for p in phrases if p['kind'] == 'twister'] if twister and level not in ('A1', 'A2') else []

    def order(src):
        d, n_l, n_low, lat = [], [], [], []
        for p in src:
            s = say_of('p', p['id'])
            if s is None:
                (n_l if p['level'] == level else n_low).append(p)
            elif s[1] <= now:
                d.append(p)
            else:
                lat.append(p)
        d.sort(key=lambda p: (say_of('p', p['id'])[1], say_of('p', p['id'])[0]))
        lat.sort(key=lambda p: say_of('p', p['id'])[1])
        n_l.sort(key=top_first)
        n_low.sort(key=top_first)
        return d, n_l, n_low, lat

    def pick_phr(src, n):
        if n <= 0 or not src:
            return []
        d, n_l, n_low, lat = order(src)
        out, seen = [], set()
        for group, cap in ((d, int(n * 0.4) or 1), (n_l, n), (n_low, n), (d, n), (lat, n)):
            for p in group:
                if len(out) >= n or cap <= 0:
                    break
                if p['id'] in seen:
                    continue
                seen.add(p['id'])
                out.append(p)
                cap -= 1
        return out

    n_fill = min(len(fills), 4) if level not in ('A1', 'A2') else 0
    chosen = pick_phr(bekat, DECK_PHRASES - n_fill) + pick_phr(fills, n_fill) + pick_phr(twists, DECK_TWISTERS)
    for p in chosen:
        s = say_of('p', p['id'])
        items.append(phrase_item(p, s[0] if s else None))

    token = {
        'w': [it['id'] for it in items if it['k'] == 'w'],
        'wn': [words_in(it['text']) for it in items if it['k'] == 'w'],
        'p': [it['id'] for it in items if it['k'] == 'p'],
        'pk': ''.join({'echo': 'e', 'answer': 'a', 'fill': 'f', 'twister': 't'}[it['kind']] for it in items if it['k'] == 'p'),
        'pn': [words_in(it.get('answer') or it['text']) for it in items if it['k'] == 'p'],
    }
    return items, token


# ── finish: outcomes from the client → clean outcomes (§B8.3 rule 2) ─────────

KIND_LETTER = {'e': 'echo', 'a': 'answer', 'f': 'fill', 't': 'twister'}


def token_index(tok):
    """{('w'|'p', id): (kind, n)} from a deck token."""
    idx = {}
    for i, wid in enumerate(tok.get('w') or ()):
        wn = tok.get('wn') or ()
        idx[('w', wid)] = ('word', wn[i] if i < len(wn) else 1)
    pk = tok.get('pk') or ''
    pn = tok.get('pn') or ()
    for i, pid in enumerate(tok.get('p') or ()):
        idx[('p', pid)] = (KIND_LETTER.get(pk[i] if i < len(pk) else 'e', 'echo'), pn[i] if i < len(pn) else 1)
    return idx


def _moment(kind, m):
    allowed = {'word': ('b', 's', 'r'), 'fill': ('b', 'r'), 'echo': ('s', 'r'), 'answer': ('s', 'r'), 'twister': ('t',)}
    ok = allowed.get(kind, ('b',))
    return m if m in ok else ok[0]


def clean_outcomes(raw, idx):
    """Keep the outcomes of items in the deck (local items and unknown ids are dropped silently)."""
    out = []
    for o in raw if isinstance(raw, list) else ():
        if not isinstance(o, dict):
            continue
        k = o.get('k')
        try:
            iid = int(o.get('id'))
        except (TypeError, ValueError):
            continue
        if k not in ('w', 'p') or (k, iid) not in idx:
            continue
        v = o.get('v')
        if v not in VERDICTS:
            continue
        kind, n = idx[(k, iid)]
        m = _moment(kind, o.get('m'))
        try:
            tries = int(o.get('tries') or 1)
        except (TypeError, ValueError):
            tries = 1
        tries = clamp(tries, 1, 3)
        if m == 'r':
            tries = max(2, tries)               # a revive is a second chance: never a first-try pass
        ms = o.get('ms')
        ms = int(ms) if isinstance(ms, (int, float)) and not isinstance(ms, bool) and math.isfinite(ms) and 0 <= ms <= 60000 else None
        heard = o.get('heard') if isinstance(o.get('heard'), str) else ''
        heard = ' '.join(heard.replace('\x00', '').split())[:80]
        pt = o.get('pt') if isinstance(o.get('pt'), str) else ''
        out.append({'k': k, 'id': iid, 'kind': kind, 'pt': pt[:12], 'v': v, 'tries': tries, 'ms': ms,
                    'heard': heard, 'm': m, 'n': n})
    return out
