"""
Word-by-word scoring of a read-aloud recording. Pure functions, no Django.

    score_reading(reference_text, whisper) -> {words, accuracy, ok, fix, skip, fluency_wpm, transcript}

`whisper` is Whisper's verbose_json answer: {text, words: [{word, start, end}],
segments: [{start, end, avg_logprob}], duration}.

How a word is judged (deterministic, so a teacher can explain every number):
  1. Both texts are normalised the same way: lower case, numbers written as
     words ("10" -> "ten", "1995" -> "nineteen ninety five"), contractions opened
     ("I'm" -> "i am"), British and American spelling made equal, homophones
     Whisper swaps treated as equal.
  2. The reference words and the heard words are aligned in order (a weighted
     longest common subsequence), so one missed word does not shift the rest.
     "ice cream" may match "icecream" and the other way round.
  3. Every reference word gets a score:
       said exactly      -> 'ok',   90–100 (100 when Whisper was confident about
                                     that stretch of speech, down to 90 when unsure)
       said nearly       -> 'fix',  50–89  (spelling similarity of what was heard >= 0.6)
       not heard at all  -> 'skip', 0
  4. accuracy = mean word score; fluency = heard words per minute of speech.
"""
import math
import re

# ─── normalisation ────────────────────────────────────────────────────────────

ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
        'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
ORDINAL_WORDS = {'one': 'first', 'two': 'second', 'three': 'third', 'five': 'fifth', 'eight': 'eighth',
                 'nine': 'ninth', 'twelve': 'twelfth'}

CONTRACTIONS = {
    "i'm": 'i am', "you're": 'you are', "we're": 'we are', "they're": 'they are', "he's": 'he is', "she's": 'she is',
    "it's": 'it is', "that's": 'that is', "there's": 'there is', "here's": 'here is', "what's": 'what is',
    "who's": 'who is', "where's": 'where is', "how's": 'how is', "let's": 'let us', "i've": 'i have',
    "you've": 'you have', "we've": 'we have', "they've": 'they have', "i'll": 'i will', "you'll": 'you will',
    "we'll": 'we will', "they'll": 'they will', "he'll": 'he will', "she'll": 'she will', "it'll": 'it will',
    "that'll": 'that will', "i'd": 'i would', "you'd": 'you would', "he'd": 'he would', "she'd": 'she would',
    "we'd": 'we would', "they'd": 'they would', "don't": 'do not', "doesn't": 'does not', "didn't": 'did not',
    "can't": 'can not', 'cannot': 'can not', "won't": 'will not', "isn't": 'is not', "aren't": 'are not',
    "wasn't": 'was not', "weren't": 'were not', "haven't": 'have not', "hasn't": 'has not', "hadn't": 'had not',
    "wouldn't": 'would not', "shouldn't": 'should not', "couldn't": 'could not', "mustn't": 'must not',
    "needn't": 'need not', "y'all": 'you all', 'gonna': 'going to', 'wanna': 'want to', 'gotta': 'got to',
}
# the same written without the apostrophe — only where that is not another English word
# ("its", "lets", "well", "were", "ill", "id", "shed" … are real words, so they are left alone)
NO_APOSTROPHE = {k.replace("'", ''): v for k, v in CONTRACTIONS.items()
                 if "'" in k and k.replace("'", '') not in {
                     'its', 'lets', 'well', 'were', 'ill', 'id', 'hell', 'shell', 'wed', 'shed', 'hed', 'whos', 'hows',
                     'wheres', 'heres', 'shes', 'hes', 'yall', 'cant', 'wont'}}
# two reference words that one heard word stands for, beyond the plain join ("ice cream" / "icecream"):
# Whisper writes "it's" and "its" (both sound the same), "let's" and "lets"
JOINED_ALIAS = {'itis': 'its', 'letus': 'lets'}
# British -> one spelling (Whisper writes American more often)
SPELLING = {
    'grey': 'gray', 'programme': 'program', 'programmes': 'programs', 'tyre': 'tire', 'tyres': 'tires',
    'pyjamas': 'pajamas', 'aeroplane': 'airplane', 'aeroplanes': 'airplanes', 'cheque': 'check',
    'jewellery': 'jewelry', 'mould': 'mold', 'plough': 'plow', 'practise': 'practice', 'practised': 'practiced',
    'practising': 'practicing', 'licence': 'license', 'defence': 'defense', 'offence': 'offense',
    'catalogue': 'catalog', 'dialogue': 'dialog', 'ageing': 'aging', 'judgement': 'judgment', 'enrol': 'enroll',
    'mum': 'mom', 'mummy': 'mommy', 'maths': 'math', 'okay': 'ok', 'mr': 'mister', 'mrs': 'missus',
    'learnt': 'learned', 'dreamt': 'dreamed', 'spelt': 'spelled', 'burnt': 'burned', 'whilst': 'while',
    'towards': 'toward', 'amongst': 'among', 'travelled': 'traveled', 'travelling': 'traveling',
    'traveller': 'traveler', 'travellers': 'travelers', 'cancelled': 'canceled', 'cancelling': 'canceling',
    'labelled': 'labeled', 'modelling': 'modeling', 'fuelled': 'fueled', 'levelled': 'leveled',
    'signalled': 'signaled', 'counselling': 'counseling', 'marvellous': 'marvelous', 'quarrelled': 'quarreled',
}
# words that sound the same — Whisper picks one spelling, the learner said it right
HOMOPHONES = [
    ['to', 'too', 'two'], ['for', 'four'], ['there', 'their', "they're", 'theyre'], ['right', 'write'],
    ['buy', 'by', 'bye'], ['eight', 'ate'], ['know', 'no'], ['hear', 'here'], ['see', 'sea'], ['one', 'won'],
    ['our', 'hour'], ['new', 'knew'], ['wear', 'where'], ['weather', 'whether'], ['meet', 'meat'],
    ['week', 'weak'], ['tea', 'tee'], ['red', 'read'], ['sun', 'son'], ['flour', 'flower'], ['pair', 'pear'],
    ['piece', 'peace'], ['sale', 'sail'], ['tail', 'tale'], ['road', 'rode'], ['whole', 'hole'], ['plane', 'plain'],
    ['break', 'brake'], ['steal', 'steel'], ['site', 'sight'], ['allowed', 'aloud'], ['would', 'wood'],
]
# ("it's" / "its" is handled by JOINED_ALIAS: "its" vs "it" must stay a mistake — the s was not said)
_HOMO = {}
for _i, _group in enumerate(HOMOPHONES):
    for _w in _group:
        _HOMO.setdefault(re.sub(r"[^a-z0-9]", '', _w), _i)

_OUR = re.compile(r'^(\w{3,})our(s|ed|ing|ite|ites|able|ful|less)?$')
_ISE = re.compile(r'^(\w{2,})(is|ys)(e|es|ed|ing|ation|ations|er|ers)$')
_TRE = re.compile(r'^(\w{2,})tre(s)?$')


def canon(tok):
    """One spelling for British / American variants (applied to both sides, so only equality matters)."""
    tok = SPELLING.get(tok, tok)
    m = _OUR.match(tok)
    if m and len(tok) >= 6:
        tok = f'{m.group(1)}or{m.group(2) or ""}'
    m = _ISE.match(tok)
    if m:
        tok = f'{m.group(1)}{"iz" if m.group(2) == "is" else "yz"}{m.group(3)}'
    m = _TRE.match(tok)
    if m and len(tok) >= 5:
        tok = f'{m.group(1)}ter{m.group(2) or ""}'
    return tok


def number_to_words(n):
    if n < 0 or n > 999_999_999:
        return str(n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else '')
    if n < 1000:
        return ONES[n // 100] + ' hundred' + (' ' + number_to_words(n % 100) if n % 100 else '')
    if n < 1_000_000:
        return number_to_words(n // 1000) + ' thousand' + (' ' + number_to_words(n % 1000) if n % 1000 else '')
    return number_to_words(n // 1_000_000) + ' million' + (' ' + number_to_words(n % 1_000_000) if n % 1_000_000 else '')


def spoken_number(digits):
    """A written number as it is read aloud: years 1100–1999 the way people say them
    ("1995" -> "nineteen ninety five", "1900" -> "nineteen hundred"), everything else plainly."""
    n = int(digits)
    if len(digits) == 4 and 1100 <= n <= 1999:
        hi, lo = divmod(n, 100)
        if lo == 0:
            return number_to_words(hi) + ' hundred'
        return number_to_words(hi) + (' oh ' + ONES[lo] if lo < 10 else ' ' + number_to_words(lo))
    return number_to_words(n)


def _ordinal(n):
    words = number_to_words(n).split()
    last = words[-1]
    if last in ORDINAL_WORDS:
        last = ORDINAL_WORDS[last]
    elif last.endswith('y'):
        last = last[:-1] + 'ieth'
    else:
        last = last + 'th'
    return ' '.join(words[:-1] + [last])


def _expand(raw):
    """One whitespace-separated chunk → its spoken tokens."""
    s = raw.lower().replace('’', "'").replace('‘', "'").replace('`', "'")
    s = re.sub(r'(\d),(\d{3})', r'\1\2', s)                         # 1,200 -> 1200
    s = re.sub(r'(\d+):(\d{2})', lambda m: f' {m.group(1)} ' + (
        'oclock' if m.group(2) == '00' else (f'oh {int(m.group(2))}' if int(m.group(2)) < 10 else m.group(2))) + ' ', s)
    s = re.sub(r"\bo'?clock\b", ' oclock ', s)
    s = re.sub(r'\$(\d+)', r' \1 dollars ', s)
    s = re.sub(r'£(\d+)', r' \1 pounds ', s)
    s = re.sub(r'(\d+)%', r' \1 percent ', s)
    s = re.sub(r'(\d+)\.(\d+)', lambda m: f' {m.group(1)} point ' + ' '.join(m.group(2)) + ' ', s)
    s = re.sub(r'[-–—/]', ' ', s)                                   # well-known -> well known
    out = []
    for part in s.split():
        part = re.sub(r"^[^a-z0-9']+|[^a-z0-9']+$", '', part).strip("'")
        if not part:
            continue
        if part in CONTRACTIONS:
            out.extend(CONTRACTIONS[part].split())
            continue
        if part in NO_APOSTROPHE:
            out.extend(NO_APOSTROPHE[part].split())
            continue
        m = re.fullmatch(r'(\d+)(st|nd|rd|th)', part)
        if m:
            out.extend(_ordinal(int(m.group(1))).split())
            continue
        if part.isdigit():
            out.extend(spoken_number(part).split())
            continue
        m = re.fullmatch(r'(\d+)s', part)                           # 1990s
        if m:
            words = spoken_number(m.group(1)).split()
            out.extend(words[:-1] + [words[-1][:-1] + 'ies' if words[-1].endswith('y') else words[-1] + 's'])
            continue
        part = re.sub(r"'s$", '', part).replace("'", '')
        part = re.sub(r'[^a-z0-9]', '', part)
        if part:
            out.append(part)
    return out


def tokens(text):
    return [t for chunk in str(text or '').split() for t in _expand(chunk)]


def say_text(word):
    """The word as the teacher voice says it: no surrounding punctuation."""
    return re.sub(r'^[^A-Za-z0-9]+|[^A-Za-z0-9]+$', '', str(word or '').replace('’', "'"))


_SENTENCE_END = re.compile(
    r'(?:(?<=[.!?…])|(?<=[.!?…]["”’)]))(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)'
    r'\s+(?=["“‘(]?[A-Z0-9])')


def split_sentences(text):
    """One sentence per line, for the read screen."""
    out = []
    for line in str(text or '').replace('\r', '').split('\n'):
        line = ' '.join(line.split())
        if not line:
            continue
        out.extend(s.strip() for s in _SENTENCE_END.split(line) if s.strip())
    return out


def reference_words(text):
    """The words the learner sees and is judged on (lone punctuation like "—" is not judged)."""
    return [w for w in str(text or '').split() if tokens(w)]


# ─── similarity ───────────────────────────────────────────────────────────────

def _levenshtein(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


NEAR_AT = 0.6


def similarity(a, b, _cache={}):  # noqa: B006 — a tiny memo shared by every call
    """1.0 = the same word (after normalisation), 0.6–0.99 = nearly, 0 = different."""
    key = (a, b)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    ca, cb = canon(a), canon(b)
    if ca == cb or (ca in _HOMO and _HOMO.get(ca) == _HOMO.get(cb)) or (a in _HOMO and _HOMO.get(a) == _HOMO.get(b)):
        sim = 1.0
    else:
        longest = max(len(ca), len(cb)) or 1
        if abs(len(ca) - len(cb)) / longest > 1 - NEAR_AT:
            sim = 0.0
        else:
            sim = 1 - _levenshtein(ca, cb) / longest
            if sim < NEAR_AT:
                sim = 0.0
    if len(_cache) > 50_000:
        _cache.clear()
    _cache[key] = sim
    return sim


# ─── alignment ────────────────────────────────────────────────────────────────

def align(ref, heard):
    """
    ref, heard: token lists. → per ref token: (sim, [heard indexes]); sim 0 = not heard.
    Weighted LCS: an exact pair is worth 1, a near pair 0.8 × similarity, so exact
    matches are preferred; two ref tokens may match one heard token ("ice cream" /
    "icecream") and the other way round.
    """
    n, m = len(ref), len(heard)
    W = [[0.0] * (m + 1) for _ in range(n + 2)]
    def joined(a, b):
        t = canon(a + b)
        return JOINED_ALIAS.get(t, t)

    joined_ref = [joined(ref[i], ref[i + 1]) if i + 1 < n else None for i in range(n)]
    joined_heard = [joined(heard[j], heard[j + 1]) if j + 1 < m else None for j in range(m)]
    canon_ref = [canon(t) for t in ref]
    canon_heard = [canon(t) for t in heard]

    def gain(i, j):
        s = similarity(ref[i], heard[j])
        return 1.0 if s == 1.0 else (0.8 * s if s else 0.0)

    for i in range(n - 1, -1, -1):
        row, nxt = W[i], W[i + 1]
        for j in range(m - 1, -1, -1):
            best = max(nxt[j], row[j + 1])
            g = gain(i, j)
            if g and g + nxt[j + 1] > best:
                best = g + nxt[j + 1]
            if joined_ref[i] is not None and joined_ref[i] == canon_heard[j] and 2.0 + W[i + 2][j + 1] > best:
                best = 2.0 + W[i + 2][j + 1]
            if joined_heard[j] is not None and canon_ref[i] == joined_heard[j] and 1.0 + nxt[j + 2] > best:
                best = 1.0 + nxt[j + 2]
            row[j] = best

    out = [(0.0, []) for _ in range(n)]
    i = j = 0
    eps = 1e-9
    while i < n and j < m:
        cur = W[i][j]
        g = gain(i, j)
        if g and abs(cur - (g + W[i + 1][j + 1])) < eps:
            out[i] = (similarity(ref[i], heard[j]), [j])
            i, j = i + 1, j + 1
        elif joined_ref[i] is not None and joined_ref[i] == canon_heard[j] and abs(cur - (2.0 + W[i + 2][j + 1])) < eps:
            out[i] = (1.0, [j])
            out[i + 1] = (1.0, [j])
            i, j = i + 2, j + 1
        elif joined_heard[j] is not None and canon_ref[i] == joined_heard[j] and abs(cur - (1.0 + W[i + 1][j + 2])) < eps:
            out[i] = (1.0, [j, j + 1])
            i, j = i + 1, j + 2
        elif abs(cur - W[i + 1][j]) < eps:
            i += 1
        else:
            j += 1
    return out


# ─── scoring ──────────────────────────────────────────────────────────────────

def _confidence_at(t, segments):
    """Whisper's confidence (0–1) for the stretch of speech around time t."""
    for s in segments or []:
        try:
            if float(s.get('start', 0)) - 0.05 <= t <= float(s.get('end', 0)) + 0.05:
                return max(0.0, min(1.0, math.exp(float(s.get('avg_logprob', 0)))))
        except (TypeError, ValueError):
            continue
    return 1.0


def ok_score(conf):
    """An exactly said word: 100 when Whisper was sure (≥ 0.9), down to 90 when it was not (≤ 0.55)."""
    return 90 + round(10 * max(0.0, min(1.0, (conf - 0.55) / 0.35)))


def near_score(sim):
    """A nearly said word: similarity 0.6 → 50 … 0.99 → 89."""
    return max(50, min(89, 50 + round((sim - NEAR_AT) / (1 - NEAR_AT) * 39)))


def score_reading(reference_text, whisper):
    whisper = whisper or {}
    shown = reference_words(reference_text)
    ref, owner = [], []
    for wi, w in enumerate(shown):
        for t in tokens(w):
            ref.append(t)
            owner.append(wi)

    hwords = []
    for w in whisper.get('words') or []:
        text = str(w.get('word') or '').strip()
        if not text:
            continue
        try:
            start, end = float(w.get('start') or 0), float(w.get('end') or 0)
        except (TypeError, ValueError):
            start = end = 0.0
        hwords.append({'word': text, 'start': start, 'end': max(start, end)})
    heard, howner = [], []
    for hi, w in enumerate(hwords):
        for t in tokens(w['word']):
            heard.append(t)
            howner.append(hi)
    # a transcript without word times (should not happen with timestamp_granularities=word)
    if not hwords and whisper.get('text'):
        heard = tokens(whisper['text'])
        howner = [None] * len(heard)

    aligned = align(ref, heard)
    segments = whisper.get('segments') or []

    per_word = [[] for _ in shown]           # (score, status, heard word indexes)
    for k, (sim, hidx) in enumerate(aligned):
        hw = sorted({howner[j] for j in hidx if howner[j] is not None})
        if not sim:
            per_word[owner[k]].append((0, 'skip', hw))
        elif sim == 1.0:
            mid = (hwords[hw[0]]['start'] + hwords[hw[-1]]['end']) / 2 if hw else 0
            per_word[owner[k]].append((ok_score(_confidence_at(mid, segments)) if hw else 100, 'ok', hw))
        else:
            per_word[owner[k]].append((near_score(sim), 'fix', hw))

    words = []
    for wi, w in enumerate(shown):
        parts = per_word[wi]
        statuses = {p[1] for p in parts}
        score = round(sum(p[0] for p in parts) / len(parts)) if parts else 0
        status = 'ok' if statuses == {'ok'} else 'skip' if statuses == {'skip'} else 'fix'
        hw = sorted({h for p in parts for h in p[2]})
        item = {'i': wi, 'word': w, 'say': say_text(w), 'score': score, 'status': status,
                'heard': ' '.join(say_text(hwords[h]['word']) for h in hw)[:60] if hw else '',
                'start': round(hwords[hw[0]]['start'], 2) if hw else None,
                'end': round(hwords[hw[-1]]['end'], 2) if hw else None}
        words.append(item)

    n = len(words)
    accuracy = round(sum(w['score'] for w in words) / n) if n else 0
    fluency = 0
    if len(hwords) >= 3:
        span = hwords[-1]['end'] - hwords[0]['start']
        if span >= 2:
            fluency = min(400, round(len(hwords) / span * 60))
    return {
        'words': words,
        'accuracy': accuracy,
        'ok': sum(w['status'] == 'ok' for w in words),
        'fix': sum(w['status'] == 'fix' for w in words),
        'skip': sum(w['status'] == 'skip' for w in words),
        'fluency_wpm': fluency,
        'transcript': str(whisper.get('text') or ' '.join(w['word'] for w in hwords)).strip(),
        'heard_any': bool(heard),
        # not one word of the text was recognised: silence that Whisper filled with "Thank you." /
        # "you", or something else entirely was read — the learner is asked to read again
        'read_the_text': any(w['status'] != 'skip' for w in words),
    }
