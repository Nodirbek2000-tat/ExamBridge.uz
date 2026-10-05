"""
Word Battle rules that need no database: scoring, the bot opponent, and turning bank words
into questions. services.py feeds them rows; tests call them directly.

Score of one answer (GAMES_PLAN §2):
    correct                → 100 + speed bonus (0–50, full at ≤ 1 s, nothing at the 7-s mark)
    after 3 correct in a row (the 4th, 5th … correct answer)   → × 1.5
    wrong, timeout, or faster than 400 ms (not humanly possible) → 0
"""
import math
import re

from vocabulary import bank

BASE_POINTS = 100
SPEED_MAX = 50
FULL_BONUS_MS = 1000
STREAK_AT = 3
STREAK_MULT = 1.5
TOO_FAST_MS = 400
LISTEN_EXTRA_MS = 1500        # a listen question starts with the word being played: the clock waits for it
OPTIONS = 4
UZ_OPTION_CHARS = 44          # long Uzbek meanings are cut the same way for the key and the wrong options

# the share of each question type in a 15-question round, per level
MIX = {
    'A2': {'en_uz': 4, 'uz_en': 3, 'listen': 3, 'cloze': 3, 'syn': 1, 'ant': 1},
    'B1': {'en_uz': 3, 'uz_en': 3, 'listen': 2, 'cloze': 3, 'syn': 2, 'ant': 2},
    'B2': {'en_uz': 3, 'uz_en': 2, 'listen': 2, 'cloze': 3, 'syn': 3, 'ant': 2},
    'C1': {'en_uz': 3, 'uz_en': 2, 'listen': 2, 'cloze': 3, 'syn': 3, 'ant': 2},
    'SAT': {'en_uz': 3, 'uz_en': 2, 'listen': 1, 'cloze': 4, 'syn': 3, 'ant': 2},
}
EASY_TYPES = ('en_uz', 'listen', 'uz_en')
# the rarest kinds are matched to words first, so they are not used up by the easy ones
FILL_ORDER = ('ant', 'syn', 'cloze', 'uz_en', 'listen', 'en_uz')

# the bot opponent: always labelled "Bot" on screen, with an honest one-line profile
BOT_PERSONAS = (
    {'key': 'shiddat', 'name': 'Shiddat', 'note': 'Tez javob beradi, ba’zan shoshiladi', 'acc': -0.07, 'speed': 0.72},
    {'key': 'sinchkov', 'name': 'Sinchkov', 'note': 'Shoshilmaydi, lekin aniq', 'acc': 0.08, 'speed': 1.3},
    {'key': 'barqaror', 'name': 'Barqaror', 'note': 'Bir maromda o‘ynaydi', 'acc': 0.0, 'speed': 1.0},
)
BOT_LEVEL_ACC = {'A2': 0.72, 'B1': 0.69, 'B2': 0.67, 'C1': 0.65, 'SAT': 0.64}
BOT_TYPE_ACC = {'en_uz': 0.06, 'listen': 0.02, 'uz_en': 0.0, 'cloze': -0.03, 'syn': -0.06, 'ant': -0.03}
BOT_MEDIAN_MS = 3000


def _round(x):
    """Half up (like JS Math.round) — Python's round() would send 2.5 to 2."""
    return int(math.floor(x + 0.5))


# ── scoring ──────────────────────────────────────────────────────────────────

def speed_bonus(ms, question_ms):
    if ms is None:
        return 0
    span = max(1, question_ms - FULL_BONUS_MS)
    return _round(SPEED_MAX * min(1.0, max(0.0, (question_ms - ms) / span)))


def points(correct, ms, streak_before, question_ms, *, extra_ms=0):
    """→ (points, bonus, mult). ms is the server time from serving to answering; extra_ms is the
    listen allowance (the bonus counts from the moment the word has been played)."""
    if not correct or ms is None or ms < TOO_FAST_MS:
        return 0, 0, 1.0
    bonus = speed_bonus(max(0, ms - extra_ms), question_ms)
    mult = STREAK_MULT if streak_before >= STREAK_AT else 1.0
    return _round((BASE_POINTS + bonus) * mult), bonus, mult


def extra_ms(qtype):
    return LISTEN_EXTRA_MS if qtype == 'listen' else 0


def limit_ms(qtype, question_ms, grace_ms):
    """The server deadline of one question, from the moment it was served."""
    return question_ms + grace_ms + extra_ms(qtype)


def score_sequence(results, question_ms):
    """[(ok, ms, qtype)] → [[ms, ok, pts]] with the streak rule applied in order (bots)."""
    out, streak = [], 0
    for ok, ms, qtype in results:
        pts, _, _ = points(ok, ms, streak, question_ms, extra_ms=extra_ms(qtype))
        streak = streak + 1 if ok else 0
        out.append([ms, bool(ok), pts])
    return out


def too_fast(ms_list):
    """GAMES_PLAN anti-cheat: more than half of the real answers under 400 ms flags the round."""
    real = [m for m in ms_list if m is not None]
    if len(real) < 4:
        return False
    return sum(1 for m in real if m < TOO_FAST_MS) * 2 > len(real)


def compare(a, b):
    """Two results {score, correct, ms} → 1 (a wins), -1 (b wins), 0 (draw)."""
    ka = (a.get('score') or 0, a.get('correct') or 0, -(a.get('ms') or 0))
    kb = (b.get('score') or 0, b.get('correct') or 0, -(b.get('ms') or 0))
    return (ka > kb) - (ka < kb)


# ── the bot ──────────────────────────────────────────────────────────────────

def bot_profile(rng, level):
    persona = rng.choice(BOT_PERSONAS)
    acc = min(0.9, max(0.4, BOT_LEVEL_ACC.get(level, 0.66) + persona['acc'] + rng.uniform(-0.04, 0.04)))
    return persona, acc


def bot_answers(rng, level, qtypes, question_ms, persona, acc):
    """A believable run: right about `acc` of the time, answer times around 3 s (log-normal)."""
    results = []
    for t in qtypes:
        p = min(0.95, max(0.25, acc + BOT_TYPE_ACC.get(t, 0)))
        ok = rng.random() < p
        if not ok and rng.random() < 0.12:
            results.append((False, None, t))          # ran out of time
            continue
        hard = 1.15 if t in ('cloze', 'syn', 'ant') else 1.0
        ms = BOT_MEDIAN_MS * persona['speed'] * hard * math.exp(rng.gauss(0, 0.33))
        if not ok:
            ms *= 1.1
        ms = int(min(question_ms - 250, max(1150, ms))) + extra_ms(t)
        results.append((ok, ms, t))
    return score_sequence(results, question_ms)


# ── questions ────────────────────────────────────────────────────────────────

def type_slots(level, n, types):
    """n question types following the level's mix, limited to the enabled types (largest remainder)."""
    mix = {t: c for t, c in MIX.get(level, MIX['B1']).items() if t in types}
    if not mix:
        mix = {t: 1 for t in types} or {'en_uz': 1}
    total = sum(mix.values())
    raw = {t: n * c / total for t, c in mix.items()}
    out = {t: int(v) for t, v in raw.items()}
    left = n - sum(out.values())
    for t in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:left]:
        out[t] += 1
    return [t for t in FILL_ORDER for _ in range(out.get(t, 0))]


def _clean(s):
    return ' '.join(str(s or '').split())


def uz_parts(uz):
    return {p.strip().lower() for p in re.split(r'[,;/]', uz or '') if p.strip()}


def short_uz(uz):
    """The first meanings of an Uzbek gloss, cut at a comma so a long option does not stand out."""
    uz = _clean(uz)
    if len(uz) <= UZ_OPTION_CHARS:
        return uz
    out = ''
    for part in [p.strip() for p in uz.split(',') if p.strip()]:
        nxt = f'{out}, {part}' if out else part
        if len(nxt) > UZ_OPTION_CHARS:
            break
        out = nxt
    return out or uz[:UZ_OPTION_CHARS].rstrip()


def _phrases(values, max_words=3):
    out = []
    for v in values or ():
        v = _clean(v)
        if v and len(v.split()) <= max_words and len(v) <= 40:
            out.append(v)
    return out


CLOZE_MAX_CHARS = 180
BLANK = '_____'
_EDGE = r"(?<![\w'’-])"


def cloze_prompt(word, example):
    """The example with every whole-word use of `word` blanked, or None when the sentence cannot
    hide it: the word is missing, the sentence is too long, or another form of the word stays
    visible ("friendly" next to "friend" would give the answer away)."""
    ex = _clean(example)
    word = _clean(word)
    if not ex or not word or len(ex) > CLOZE_MAX_CHARS:
        return None
    pat = re.compile(_EDGE + re.escape(word) + r"(?![\w'’-])", re.I)
    if not pat.search(ex):
        return None
    out = pat.sub(BLANK, ex)
    if re.search(_EDGE + re.escape(word), out, re.I):
        return None
    return out


def can_do(w, t):
    if t in ('en_uz', 'uz_en', 'listen'):
        return bool(_clean(w.uz))
    if t == 'syn':
        return bool([s for s in _phrases(w.synonyms) if s.lower() != w.word.lower()])
    if t == 'ant':
        return bool([a for a in _phrases(w.antonyms) if a.lower() != w.word.lower()])
    if t == 'cloze':
        return cloze_prompt(w.word, w.example) is not None
    return False


_NO_LEX = ('', frozenset(), frozenset(), '')


def lex_of(pool):
    """{word: (uz, {synonyms}, {antonyms}, pos)} (lower case) from a distractor pool. The pool has no
    antonyms; services.bank_lex() gives the full map."""
    out = {}
    for (_lvl, pos), group in pool.items():
        for _id, word, uz, _pic, syn in group:
            out.setdefault(word.lower(), (uz or '', frozenset(syn or ()), frozenset(), pos or ''))
    return out


def _pick(cands, n, *, avoid, check=None):
    """The first n candidates whose lower-case form is not in `avoid` (which grows as they are taken)."""
    out = []
    for c in cands:
        c = _clean(c)
        key = c.lower()
        if not c or key in avoid or (check and not check(c)):
            continue
        avoid.add(key)
        out.append(c)
        if len(out) >= n:
            break
    return out


def make_question(w, t, pool, rng, lex=None):
    """One question {t, w, en, uz, pos, p, o, k} for word `w` (a vocabulary.Word), or None when the
    bank cannot give it 3 clean wrong options.

    A wrong option must never be a second right answer. Besides the word's own synonyms / antonyms,
    `lex` ({word: (uz, synonyms, antonyms, pos)}) lets us drop options that share an Uzbek meaning
    with the word, list the word (or the key) as their synonym, or list the word as their antonym.
    The curated `distractors` of the word go through the same checks, and bank words of another part
    of speech (the Runner's easy picture distractors, the bank's last resort) only fill what is left."""
    lex = lex if lex is not None else lex_of(pool)

    def info(c):
        return lex.get(c.lower(), _NO_LEX)

    word = _clean(w.word)
    low = word.lower()
    syns = [s for s in _phrases(w.synonyms) if s.lower() != low]
    ants = [a for a in _phrases(w.antonyms) if a.lower() != low]
    syn_low = {s.lower() for s in syns}
    ant_low = {a.lower() for a in ants}
    my_uz = uz_parts(w.uz)
    need = OPTIONS - 1
    prompt = word

    def same_pos_first(cands):
        return sorted(cands, key=lambda c: 0 if (info(c)[3] or w.pos or '') == (w.pos or '') else 1)

    def same_meaning(c):
        """c means what the word means: a shared Uzbek gloss, or the word is among c's synonyms."""
        uz, c_syn, _, _ = info(c)
        return bool(uz_parts(uz) & my_uz) or low in c_syn or bool(c_syn & syn_low)

    if t in ('en_uz', 'listen'):
        correct = short_uz(w.uz)
        if not correct:
            return None
        taken = []

        def ok_uz(c):
            parts = uz_parts(c)
            return not (parts & my_uz) and all(not (parts & uz_parts(x)) for x in taken)

        avoid = {correct.lower()}
        for c in bank.distractors_for(w, 10, pool=pool, rng=rng, field='uz'):
            got = _pick([short_uz(c)], 1, avoid=avoid, check=ok_uz)
            taken += got
            if len(taken) >= need:
                break
        wrong = taken
    else:
        if t == 'uz_en':
            correct, prompt = word, short_uz(w.uz)
            cands = same_pos_first(bank.distractors_for(w, 10, pool=pool, rng=rng))

            def check(c):
                return not same_meaning(c)
        elif t == 'syn':
            if not syns:
                return None
            correct = rng.choice(syns)
            key_syn = info(correct)[1]
            cands = rng.sample(ants, min(1, len(ants))) + same_pos_first(bank.distractors_for(w, 10, pool=pool, rng=rng))

            def check(c):
                c_low = c.lower()
                if c_low in ant_low:                       # an opposite is a fair wrong option here…
                    return low not in info(c)[1]           # …unless the bank also calls it a synonym
                return not same_meaning(c) and c_low not in key_syn and correct.lower() not in info(c)[1]
        elif t == 'ant':
            if not ants:
                return None
            correct = rng.choice(ants)
            key_syn = info(correct)[1]
            curated = {_clean(d).lower() for d in w.distractors or ()}
            from_bank = [c for c in bank.distractors_for(w, 12, pool=pool, rng=rng) if _clean(c).lower() not in curated]
            cands = rng.sample(syns, min(2, len(syns))) + same_pos_first(from_bank)

            def check(c):
                c_low = c.lower()
                _, c_syn, c_ant, _ = info(c)
                # not an opposite of the word: listed, a synonym of a listed one, or c lists the word as its opposite
                return (c_low not in ant_low and c_low not in key_syn and not (c_syn & ant_low)
                        and low not in c_ant)
        elif t == 'cloze':
            prompt = cloze_prompt(w.word, w.example)
            if prompt is None:
                return None
            correct = word
            cands = same_pos_first(bank.distractors_for(w, 10, pool=pool, rng=rng))

            def check(c):
                c_low = c.lower()
                return c_low not in ant_low and c_low not in syn_low and not same_meaning(c) and low not in info(c)[2]
        else:
            return None
        # every other right answer is kept out: synonyms of the word (uz_en, syn, cloze), its antonyms (ant, cloze)
        avoid = {low, correct.lower()} | (ant_low if t == 'ant' else syn_low)
        if t == 'cloze':
            avoid |= ant_low
        wrong = _pick(cands, need, avoid=avoid, check=check)

    if len(wrong) < need:
        return None
    options = [correct] + wrong[:need]
    rng.shuffle(options)
    return {
        't': t, 'w': w.id, 'en': word, 'uz': short_uz(w.uz), 'pos': w.pos or '',
        'p': prompt, 'o': options, 'k': options.index(correct),
    }


def order_words(words, reviews, now, rng, *, due_max=5):
    """Learner-first order: up to `due_max` due meaning reviews (oldest first), then unseen words
    (shuffled), then the rest (lowest box first). reviews: {word_id: (box, due_at)}."""
    due, new, rest = [], [], []
    for w in words:
        r = reviews.get(w.id)
        if r is None:
            new.append(w)
        elif r[1] <= now:
            due.append((r[1], w))
        else:
            rest.append((r[0], rng.random(), w))
    due.sort(key=lambda x: x[0])
    rng.shuffle(new)
    rest.sort(key=lambda x: (x[0], x[1]))
    head = [w for _, w in due[:due_max]]
    return head + new + [w for _, w in due[due_max:]] + [w for _, _, w in rest]


def build_questions(ordered, n, level, types, rng, pool, *, window=None, lex=None):
    """n questions from the words in priority order. Each word is used once; a type no word can
    take falls back to an easy one. lex: see make_question (default: built from the pool)."""
    window = window or max(n * 2, n + 10)
    cands = list(ordered[:window])
    extra = list(ordered[window:])
    lex = lex if lex is not None else lex_of(pool)
    used = set()
    out = []

    def take(t):
        for src in (cands, extra):
            for w in src:
                if w.id in used or not can_do(w, t):
                    continue
                q = make_question(w, t, pool, rng, lex)
                if q:
                    used.add(w.id)
                    return q
        return None

    for t in type_slots(level, n, types):
        q = take(t)
        if q is None:
            for alt in [x for x in EASY_TYPES if x in types] or ['en_uz']:
                q = take(alt)
                if q:
                    break
        if q:
            out.append(q)
    return arrange(out, rng)


def arrange(questions, rng):
    """Shuffle, open with an easy type, and never three of a kind in a row."""
    qs = list(questions)
    rng.shuffle(qs)
    for i, q in enumerate(qs):
        if q['t'] in EASY_TYPES:
            qs[0], qs[i] = qs[i], qs[0]
            break
    for i in range(2, len(qs)):
        if qs[i]['t'] == qs[i - 1]['t'] == qs[i - 2]['t']:
            for j in range(i + 1, len(qs)):
                if qs[j]['t'] != qs[i]['t']:
                    qs[i], qs[j] = qs[j], qs[i]
                    break
    return qs
