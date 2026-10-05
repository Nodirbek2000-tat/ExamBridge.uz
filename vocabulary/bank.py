"""
The shared word bank of Word Battle and Toby Run (RUNNER_PLAN §B5, §B6, §B13).

    TOPICS, LEVELS, POS, PHRASE_KINDS, STATUSES, KIND_VOICE
    validate_word(row, defaults) / validate_phrase(row, defaults) → (clean, errors, warnings)
    import_bank(payload, dry_run=False, *, warm=True)              → report (one transaction)
    export_bank(level=, topic=, kind=, status=, tag=)              → the same JSON format (round-trips)
    distractors_for(word, n=3, ...)                                → wrong options: same level + part of speech
    warm_lines(*sources, fixed=False)                              → [[text, voice]] for the TTS warm job

The import format (one file, both games):

    {"version": 1, "source": "runner-a1a2-v1",
     "defaults": {"level": "A1", "topic": "metro", "status": "published"},
     "words":   [{"word": "ticket", "uz": "chipta", "pos": "noun", "picture": "ticket", ...}],
     "phrases": [{"kind": "echo", "text": "Excuse me, where is the exit?", "uz": "...", "voice": "grandma"}]}

Words are matched by lower(word), phrases by `key` (kind|level|normalized prompt-or-text).
A field missing from a row (and from `defaults`) is left as it is on an existing row, so a
small file can patch one field; send "" or [] to clear one. Errors and warnings are reported
per row and never stop the rest of the file. Unchanged rows are counted, not written.
"""
import os
import random
import re
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .models import PHRASE_KIND_CHOICES, POS_CHOICES, STATUS_CHOICES, VOICE_CHOICES, Phrase, Word

# ── registries ───────────────────────────────────────────────────────────────

# slug → Uzbek title (the Runner's metro map mirrors this list in runner/content.js)
TOPICS = {
    'metro': 'Metro',
    'bozor': 'Bozor',
    'park': 'Xiyobon',
    'home': 'Uy',
    'food': 'Ovqat',
    'school': 'Maktab',
    'city': 'Shahar',
    'travel': 'Sayohat',
    'health': 'Salomatlik',
    'feelings': 'His-tuyg‘u',
    'work': 'Ish',
    'nature': 'Tabiat',
    'general': 'Umumiy',
}
OTHER_TOPIC_TITLE = 'Boshqa'
LEVELS = ('A1', 'A2', 'B1', 'B2', 'C1')
POS = tuple(p for p, _ in POS_CHOICES)
PHRASE_KINDS = tuple(k for k, _ in PHRASE_KIND_CHOICES)
STATUSES = tuple(s for s, _ in STATUS_CHOICES)
VOICES = tuple(v for v, _ in VOICE_CHOICES)
# the voice a phrase gets when the file names none
KIND_VOICE = {'echo': 'narrator', 'answer': 'teacher', 'fill': 'teacher', 'twister': 'coach'}
WORD_VOICE = 'teacher'
# the legacy Word.difficulty, kept in step with the level for the old vocabulary page
LEVEL_DIFFICULTY = {'A1': 'EASY', 'A2': 'EASY', 'B1': 'MEDIUM', 'B2': 'MEDIUM', 'C1': 'HARD'}

MAX_ROWS = 5000
MAX_BYTES = 2 * 1024 * 1024
MAX_REPORTED = 500                      # errors / warnings listed in a report (the totals are always exact)
TTS_MAX_CHARS = 300                     # = games.tts_views.MAX_CHARS

# Recognisers cannot tell these apart, so a single word from this list is a risky speaking target.
HOMOPHONES = [
    ('to', 'too', 'two'), ('for', 'four'), ('there', 'their'), ('right', 'write'), ('buy', 'by', 'bye'),
    ('eight', 'ate'), ('know', 'no'), ('hear', 'here'), ('see', 'sea'), ('one', 'won'), ('our', 'hour'),
    ('new', 'knew'), ('wear', 'where'), ('weather', 'whether'), ('meet', 'meat'), ('week', 'weak'),
    ('tea', 'tee'), ('red', 'read'), ('son', 'sun'), ('flower', 'flour'), ('pair', 'pear'), ('sale', 'sail'),
    ('tail', 'tale'), ('mail', 'male'), ('piece', 'peace'), ('plane', 'plain'), ('blue', 'blew'),
    ('night', 'knight'), ('whole', 'hole'), ('wait', 'weight'), ('bear', 'bare'), ('dear', 'deer'),
    ('road', 'rode'), ('be', 'bee'), ('break', 'brake'), ('fair', 'fare'), ('hair', 'hare'), ('nose', 'knows'),
    ('poor', 'pour'), ('sight', 'site'), ('stair', 'stare'), ('way', 'weigh'), ('which', 'witch'),
    ('wood', 'would'), ('flu', 'flew'), ('die', 'dye'), ('great', 'grate'), ('heard', 'herd'),
    ('passed', 'past'), ('scene', 'seen'), ('some', 'sum'), ('through', 'threw'), ('waist', 'waste'),
    ('cell', 'sell'), ('made', 'maid'), ('rain', 'reign'), ('steal', 'steel'), ('aloud', 'allowed'),
    ('principal', 'principle'), ('stationary', 'stationery'), ('cereal', 'serial'), ('check', 'cheque'),
]
HOMOPHONE_OF = {w: g for g in HOMOPHONES for w in g}

_APOS = str.maketrans({'’': "'", '‘': "'", '`': "'", 'ʼ': "'"})
_TAG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,19}$')
_PICTURE_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
_TOPIC_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,29}$')
_NON_WORD = re.compile(r"[^\w\s']")


class BankError(ValueError):
    """The whole file is unusable (not a row problem)."""


# ── small cleaners ───────────────────────────────────────────────────────────

def one_line(value, *, apos=True):
    """str → trimmed, single spaces, straight apostrophes (English; apos=False keeps Uzbek o‘ g‘). Non-strings → ''."""
    if not isinstance(value, str):
        return ''
    return ' '.join((value.translate(_APOS) if apos else value).split())


def norm(text):
    """Lower case, no punctuation except apostrophes, single spaces (phrase keys)."""
    s = one_line(text).lower()
    s = _NON_WORD.sub(' ', s).replace('_', ' ')
    return ' '.join(s.split())


def phrase_key(kind, level, prompt, text):
    return f'{kind}|{level}|{norm(prompt or text)}'[:240]


def _letters_ok(s, *, digits=False):
    """Letters, spaces, - and ' only (digits too when asked); starts with a letter or digit."""
    if not s:
        return False
    for ch in s:
        if ch.isalpha() or ch in " -'" or (digits and ch.isdigit()):
            continue
        return False
    return s[0].isalpha() or (digits and s[0].isdigit())


def _case_ok(word):
    """lower case — except proper nouns ('Monday', 'T-shirt') and short acronyms ('TV')."""
    for tok in word.split(' '):
        if tok.islower() or not any(c.isalpha() for c in tok):
            continue
        if tok == tok[:1].upper() + tok[1:].lower():
            continue
        if tok.isupper() and len(tok) <= 4:
            continue
        return False
    return True


def _text(row, field, limit, errors, *, label=None, apos=True):
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f'{label or field}: matn bo‘lishi kerak')
        return None
    if '\x00' in value:
        errors.append(f'{label or field}: NUL belgisi bo‘lmasin')
        return None
    s = one_line(value, apos=apos)
    if len(s) > limit:
        errors.append(f'{label or field}: {limit} belgidan uzun ({len(s)})')
        return None
    return s


def _str_list(row, field, max_n, errors, *, max_len=40, check=None, lower=False):
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        errors.append(f'{field}: ro‘yxat bo‘lishi kerak, masalan ["..."]')
        return None
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            errors.append(f'{field}: faqat matnlar ro‘yxati')
            return None
        s = one_line(item)
        if lower:
            s = s.lower()
        if not s:
            continue
        if len(s) > max_len:
            errors.append(f'{field}: «{s[:20]}…» {max_len} belgidan uzun')
            return None
        if check and not check(s):
            errors.append(f'{field}: «{s}» — faqat harf, bo‘sh joy, - va \' bo‘lishi mumkin')
            return None
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    if len(out) > max_n:
        errors.append(f'{field}: ko‘pi bilan {max_n} ta')
        return None
    return out


def _choice(row, field, allowed, errors, *, blank_ok=False):
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        value = value.upper() if field == 'level' else value.lower()
        if blank_ok and value == '':
            return ''
    if not isinstance(value, str) or value not in allowed:
        errors.append(f'{field}: {", ".join(allowed)} dan biri bo‘lishi kerak')
        return None
    return value


def _topic(row, errors, warnings):
    value = row.get('topic')
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append('topic: matn bo‘lishi kerak')
        return None
    t = value.strip().lower()
    if t and not _TOPIC_RE.match(t):
        errors.append('topic: kichik lotin harflari, raqam va - (30 belgigacha)')
        return None
    if t and t not in TOPICS:
        warnings.append(f'noma’lum mavzu «{t}» — xaritada «{OTHER_TOPIC_TITLE}» bo‘lib chiqadi')
    return t


def _picture(row, errors):
    value = row.get('picture')
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append('picture: matn bo‘lishi kerak')
        return None
    p = value.strip().lower()
    if p and not _PICTURE_RE.match(p):
        errors.append('picture: rasm kaliti (kichik harf, raqam va -)')
        return None
    return p


def _bool(row, field, errors):
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, bool):
        errors.append(f'{field}: true yoki false bo‘lishi kerak')
        return None
    return value


def _with_defaults(row, defaults, keys):
    merged = dict(row)
    for k in keys:
        if k not in merged and k in defaults:
            merged[k] = defaults[k]
    return merged


# ── row validators ───────────────────────────────────────────────────────────

WORD_DEFAULT_KEYS = ('level', 'topic', 'status', 'pos', 'speak', 'tags', 'source')
PHRASE_DEFAULT_KEYS = ('level', 'topic', 'status', 'voice', 'source')
DEFAULT_KEYS = set(WORD_DEFAULT_KEYS) | set(PHRASE_DEFAULT_KEYS)


def validate_word(row, defaults=None):
    """One word row → (clean fields, errors, warnings). Only fields given (here or in defaults) are in `clean`."""
    errors, warnings = [], []
    if not isinstance(row, dict):
        return {}, ['qator obyekt bo‘lishi kerak: {"word": ...}'], []
    row = _with_defaults(row, defaults or {}, WORD_DEFAULT_KEYS)
    clean = {}

    word = row.get('word')
    if not isinstance(word, str) or not one_line(word):
        errors.append('word: majburiy')
    else:
        w = one_line(word)
        if len(w) > 40:
            errors.append('word: 40 belgidan uzun')
        elif any(ch.isdigit() for ch in w):
            errors.append('word: raqam bo‘lmasin — so‘z bilan yozing (seven, not 7)')
        elif not _letters_ok(w):
            errors.append('word: faqat harf, bo‘sh joy, - va \' bo‘lishi mumkin')
        elif not 1 <= len(w.split(' ')) <= 3:
            errors.append('word: 1–3 so‘z bo‘lishi kerak')
        elif not _case_ok(w):
            errors.append('word: kichik harf bilan yozing (atoqli otdan tashqari)')
        else:
            clean['word'] = w

    level = _choice(row, 'level', LEVELS, errors)
    if level is not None:
        clean['level'] = level
        clean['difficulty'] = LEVEL_DIFFICULTY[level]
    pos = _choice(row, 'pos', POS, errors, blank_ok=True)
    if pos is not None:
        clean['pos'] = pos
    status = _choice(row, 'status', STATUSES, errors)
    if status is not None:
        clean['status'] = status
    topic = _topic(row, errors, warnings)
    if topic is not None:
        clean['topic'] = topic
    picture = _picture(row, errors)
    if picture is not None:
        clean['picture'] = picture
    for field, limit in (('uz', 120), ('definition', 400), ('example', 300), ('source', 40)):
        v = _text(row, field, limit, errors, apos=field != 'uz')
        if v is not None:
            clean[field] = v

    word_like = lambda s: _letters_ok(s)                         # noqa: E731
    for field, max_n, check in (('say_also', 6, lambda s: _letters_ok(s, digits=True)),
                                ('synonyms', 8, word_like), ('antonyms', 8, word_like),
                                ('distractors', 6, word_like)):
        v = _str_list(row, field, max_n, errors, check=check)
        if v is not None:
            clean[field] = v
    tags = _str_list(row, 'tags', 8, errors, max_len=20, lower=True, check=lambda s: bool(_TAG_RE.match(s)))
    if tags is not None:
        clean['tags'] = tags
    speak = _bool(row, 'speak', errors)
    if speak is not None:
        clean['speak'] = speak

    if 'word' in clean:
        lw = clean['word'].lower()
        for field in ('synonyms', 'antonyms', 'distractors'):
            if any(x.lower() == lw for x in clean.get(field, ())):
                errors.append(f'{field}: so‘zning o‘zi bo‘lmasin')
        syn = {x.lower() for x in clean.get('synonyms', ())}
        if any(x.lower() in syn for x in clean.get('distractors', ())):
            errors.append('distractors: sinonim noto‘g‘ri variant bo‘lolmaydi')
    return clean, errors, warnings


def word_warnings(state):
    """Warnings on a word's final state (row merged over the saved word)."""
    out = []
    word = state.get('word') or ''
    speak = state.get('speak', True)
    if speak and not (state.get('uz') or '').strip():
        out.append('uz bo‘sh — o‘zbekcha ma’nosini yozing')
    letters = sum(ch.isalpha() for ch in word)
    if speak and ' ' not in word and letters < 4 and not state.get('picture'):
        out.append('4 harfdan qisqa va rasmsiz — tanib olish qiyin, chunk tavsiya')
    group = HOMOPHONE_OF.get(word.lower())
    if speak and group:
        out.append(f'gomofon xavfi ({"/".join(group)}) — rasm yoki chunk bilan bering')
    example = (state.get('example') or '').lower()
    if example and word and word.lower().split(' ')[0][:4] not in example:
        out.append('misol gapda so‘zning o‘zi yo‘q (cloze uchun kerak)')
    return out


def validate_phrase(row, defaults=None):
    """One phrase row → (clean fields incl. key, errors, warnings). Phrases are always whole rows."""
    errors, warnings = [], []
    if not isinstance(row, dict):
        return {}, ['qator obyekt bo‘lishi kerak: {"kind": ...}'], []
    row = _with_defaults(row, defaults or {}, PHRASE_DEFAULT_KEYS)
    clean = {}

    kind = _choice(row, 'kind', PHRASE_KINDS, errors)
    if row.get('kind') is None:
        errors.append('kind: majburiy (echo, answer, fill, twister)')
    level = _choice(row, 'level', LEVELS, errors)
    if row.get('level') is None:
        errors.append('level: majburiy (A1–C1)')
    text = _text(row, 'text', 200, errors) or ''
    prompt = _text(row, 'prompt', 200, errors) or ''
    answer = _text(row, 'answer', 80, errors) or ''
    uz = _text(row, 'uz', 240, errors, apos=False)
    source = _text(row, 'source', 40, errors)
    status = _choice(row, 'status', STATUSES, errors)
    voice = _choice(row, 'voice', VOICES, errors, blank_ok=True)
    topic = _topic(row, errors, warnings)
    picture = _picture(row, errors)

    min_words = row.get('min_words', 0)
    if isinstance(min_words, bool) or not isinstance(min_words, int) or not 0 <= min_words <= 30:
        errors.append('min_words: 0–30 butun son')
        min_words = 0

    accept = row.get('accept', [])
    words_in = lambda s: len(s.split())                          # noqa: E731

    if kind in ('echo', 'twister'):
        if not text:
            errors.append('text: majburiy')
        elif words_in(text) > 14:
            errors.append(f'text: ko‘pi bilan 14 so‘z ({words_in(text)})')
        elif kind == 'echo' and words_in(text) > 12:
            warnings.append(f'echo uzun ({words_in(text)} so‘z) — 12 tagacha yaxshi')
        if accept not in ([], None):
            errors.append(f'accept: {kind} uchun kerak emas')
        accept = []
    elif kind == 'answer':
        if not prompt:
            errors.append('prompt: majburiy (savol)')
        elif words_in(prompt) > 20:
            errors.append(f'prompt: ko‘pi bilan 20 so‘z ({words_in(prompt)})')
        ok = isinstance(accept, list) and accept and all(
            isinstance(g, list) and g and all(isinstance(x, str) and one_line(x) for x in g) for g in accept)
        if not ok:
            errors.append('accept: kalit so‘z guruhlari kerak, masalan [["going"], ["go", "to"]]')
            accept = []
        else:
            accept = [[one_line(x).lower() for x in g][:8] for g in accept][:8]
    elif kind == 'fill':
        if not text:
            errors.append('text: majburiy (to‘liq to‘g‘ri gap)')
        if '___' not in prompt:
            errors.append('prompt: ___ bo‘lishi kerak, masalan "I\'m ___ my keys."')
        if not answer:
            errors.append('answer: majburiy (tushib qolgan qism)')
        elif text and answer.lower() not in text.lower():
            errors.append('answer: text ichida bo‘lishi kerak')
        elif '___' in prompt and norm(prompt.replace('___', answer, 1)) != norm(text):
            warnings.append('prompt ichidagi ___ o‘rniga answer qo‘yilsa, text chiqmaydi')
        if not isinstance(accept, list):
            errors.append('accept: ro‘yxat bo‘lishi kerak')
            accept = []
        else:
            chunks = []
            for a in accept:
                s = one_line(' '.join(a) if isinstance(a, list) and all(isinstance(x, str) for x in a) else a)
                if not s:
                    errors.append('accept: fill uchun qo‘shimcha javoblar matni, masalan ["searching for"]')
                    break
                chunks.append(s)
            accept = chunks[:8]

    if kind in ('echo', 'answer', 'fill') and uz is not None and not uz:
        warnings.append('uz bo‘sh — o‘zbekcha tarjimasini yozing')
    if kind in ('echo', 'answer', 'fill') and uz is None:
        warnings.append('uz yo‘q — o‘zbekcha tarjimasini yozing')

    if errors:
        return {}, errors, warnings

    clean.update(kind=kind, level=level, text=text, prompt=prompt, answer=answer if kind == 'fill' else '',
                 accept=accept, min_words=min_words if kind == 'answer' else 0,
                 voice=voice or KIND_VOICE[kind], key=phrase_key(kind, level, prompt, text))
    if uz is not None:
        clean['uz'] = uz
    if source is not None:
        clean['source'] = source
    if status is not None:
        clean['status'] = status
    if topic is not None:
        clean['topic'] = topic
    if picture is not None:
        clean['picture'] = picture
    return clean, errors, warnings


def _short(s, n=60):
    s = s if isinstance(s, str) else ''
    return s if len(s) <= n else s[:n - 1] + '…'


# ── import ───────────────────────────────────────────────────────────────────

def _changed(obj, clean):
    return [f for f, v in clean.items() if getattr(obj, f) != v]


def import_bank(payload, dry_run=False, *, warm=True):
    """Validate and upsert one import file in one transaction (rolled back on a dry run).

    Returns {dry_run, words: {created, updated, unchanged}, phrases: {...}, errors, warnings,
    error_count, warning_count, warm: {lines, started, running}}. Raises BankError for a file
    that cannot be read at all.
    """
    if not isinstance(payload, dict):
        raise BankError('Fayl obyekt bo‘lishi kerak: {"version": 1, "words": [...], "phrases": [...]}')
    version = payload.get('version', 1)
    if version != 1:
        raise BankError('version: faqat 1')
    words_in = payload.get('words', [])
    phrases_in = payload.get('phrases', [])
    if not isinstance(words_in, list) or not isinstance(phrases_in, list):
        raise BankError('words va phrases ro‘yxat bo‘lishi kerak')
    if not words_in and not phrases_in:
        raise BankError('Faylda so‘z ham, ibora ham yo‘q')
    if len(words_in) + len(phrases_in) > MAX_ROWS:
        raise BankError(f'Bitta faylda ko‘pi bilan {MAX_ROWS} qator ({len(words_in) + len(phrases_in)} ta keldi)')
    defaults = payload.get('defaults') or {}
    if not isinstance(defaults, dict):
        raise BankError('defaults obyekt bo‘lishi kerak')

    errors, warnings = [], []

    def err(i, kind, key, msgs):
        errors.append({'index': i, 'kind': kind, 'key': _short(key), 'error': '; '.join(msgs)})

    def warn(i, kind, key, msg):
        warnings.append({'index': i, 'kind': kind, 'key': _short(key), 'warning': msg})

    for k in defaults:
        if k not in DEFAULT_KEYS:
            warn(None, 'file', k, f'defaults: «{k}» ishlatilmaydi')
    src = payload.get('source')
    if src is not None:
        if not isinstance(src, str) or len(one_line(src)) > 40:
            raise BankError('source: 40 belgigacha matn')
        defaults = {**defaults, 'source': one_line(src)}

    # ── words ──
    word_rows = []                                   # (index, clean)
    seen_words = {}
    for i, row in enumerate(words_in):
        clean, errs, warns = validate_word(row, defaults)
        key = row.get('word') if isinstance(row, dict) else ''
        if errs:
            err(i, 'word', key, errs)
            continue
        for w in warns:
            warn(i, 'word', key, w)
        lw = clean['word'].lower()
        if lw in seen_words:
            warn(i, 'word', key, f'faylda takror (words[{seen_words[lw]}]) — birinchisi olindi')
            continue
        seen_words[lw] = i
        word_rows.append((i, clean))

    # ── phrases ──
    phrase_rows = []
    seen_keys = {}
    for i, row in enumerate(phrases_in):
        clean, errs, warns = validate_phrase(row, defaults)
        key = (row.get('prompt') or row.get('text')) if isinstance(row, dict) else ''
        if errs:
            err(i, 'phrase', key, errs)
            continue
        for w in warns:
            warn(i, 'phrase', key, w)
        if clean['key'] in seen_keys:
            warn(i, 'phrase', key, f'faylda takror (phrases[{seen_keys[clean["key"]]}]) — birinchisi olindi')
            continue
        seen_keys[clean['key']] = i
        phrase_rows.append((i, clean))

    counts = {'words': {'created': 0, 'updated': 0, 'unchanged': 0},
              'phrases': {'created': 0, 'updated': 0, 'unchanged': 0}}
    touched_words, touched_phrases = [], []
    now = timezone.now()

    with transaction.atomic():
        existing = {}
        lowers = [c['word'].lower() for _, c in word_rows]
        for start in range(0, len(lowers), 1000):
            chunk = lowers[start:start + 1000]
            for w in Word.objects.annotate(lw=Lower('word')).filter(lw__in=chunk):
                existing[w.lw] = w

        new_words, upd_words, upd_fields = [], [], set()
        for i, clean in word_rows:
            obj = existing.get(clean['word'].lower())
            state = {f: getattr(obj, f) for f in ('word', 'uz', 'speak', 'picture', 'example')} if obj else {}
            state.update(clean)
            for w in word_warnings(state):
                warn(i, 'word', clean['word'], w)
            if obj is None:
                if 'level' not in clean:
                    err(i, 'word', clean['word'], ['level: yangi so‘z uchun majburiy (A1–C1)'])
                    continue
                new_words.append(Word(**clean))
                counts['words']['created'] += 1
                continue
            changed = _changed(obj, clean)
            if not changed:
                counts['words']['unchanged'] += 1
                continue
            for f in changed:
                setattr(obj, f, clean[f])
            obj.updated_at = now
            upd_fields.update(changed)
            upd_words.append(obj)
            counts['words']['updated'] += 1
        if new_words:
            Word.objects.bulk_create(new_words, batch_size=500)
        if upd_words:
            Word.objects.bulk_update(upd_words, sorted(upd_fields | {'updated_at'}), batch_size=200)
        touched_words = new_words + upd_words

        keys = [c['key'] for _, c in phrase_rows]
        by_key = {}
        for start in range(0, len(keys), 1000):
            for p in Phrase.objects.filter(key__in=keys[start:start + 1000]):
                by_key[p.key] = p
        new_phrases, upd_phrases, upd_pfields = [], [], set()
        for _, clean in phrase_rows:
            obj = by_key.get(clean['key'])
            if obj is None:
                new_phrases.append(Phrase(**clean))
                counts['phrases']['created'] += 1
                continue
            changed = _changed(obj, clean)
            if not changed:
                counts['phrases']['unchanged'] += 1
                continue
            for f in changed:
                setattr(obj, f, clean[f])
            obj.updated_at = now
            upd_pfields.update(changed)
            upd_phrases.append(obj)
            counts['phrases']['updated'] += 1
        if new_phrases:
            Phrase.objects.bulk_create(new_phrases, batch_size=500)
        if upd_phrases:
            Phrase.objects.bulk_update(upd_phrases, sorted(upd_pfields | {'updated_at'}), batch_size=200)
        touched_phrases = new_phrases + upd_phrases

        if dry_run:
            transaction.set_rollback(True)

    # the new and changed lines learners will hear (published only — drafts cost nothing until reviewed)
    lines = warm_lines([w for w in touched_words if w.status == 'published'],
                       [p for p in touched_phrases if p.status == 'published'])
    lines = uncached(lines)
    warm_report = {'lines': len(lines), 'started': False, 'running': False}
    if lines and warm and not dry_run:
        from gamestats.tasks import WARM_MAX_LINES, start_warm, warm_running
        if warm_running():
            warm_report['running'] = True
        else:
            start_warm(lines[:WARM_MAX_LINES])
            warm_report['started'] = True

    return {
        'dry_run': bool(dry_run),
        **counts,
        'errors': errors[:MAX_REPORTED],
        'warnings': warnings[:MAX_REPORTED],
        'error_count': len(errors),
        'warning_count': len(warnings),
        'warm': warm_report,
    }


# ── export ───────────────────────────────────────────────────────────────────

WORD_EXPORT_FIELDS = ('word', 'uz', 'pos', 'level', 'topic', 'picture', 'definition', 'example', 'say_also',
                      'synonyms', 'antonyms', 'distractors', 'tags')
PHRASE_EXPORT_FIELDS = ('kind', 'text', 'prompt', 'answer', 'accept', 'min_words', 'uz', 'level', 'topic', 'picture',
                        'voice')


def word_row(w, *, full=False):
    """A Word as an import row. Empty fields are left out unless full=True (round-trips either way)."""
    row = {}
    for f in WORD_EXPORT_FIELDS:
        v = getattr(w, f)
        if full or v not in ('', [], None):
            row[f] = v
    if full or not w.speak:
        row['speak'] = w.speak
    row['status'] = w.status
    if full or w.source:
        row['source'] = w.source
    return row


def phrase_row(p, *, full=False):
    row = {}
    for f in PHRASE_EXPORT_FIELDS:
        v = getattr(p, f)
        if full or v not in ('', [], None) and not (f == 'min_words' and v == 0):
            row[f] = v
    row['status'] = p.status
    if full or p.source:
        row['source'] = p.source
    return row


def filter_words(level='', topic='', status='', tag='', q=''):
    qs = Word.objects.all()
    if level:
        qs = qs.filter(level=level)
    if topic:
        qs = qs.filter(topic=topic)
    if status:
        qs = qs.filter(status=status)
    if tag:
        qs = qs.filter(tags__contains=[tag])
    if q:
        qs = qs.filter(Q(word__icontains=q) | Q(uz__icontains=q) | Q(definition__icontains=q))
    return qs


def filter_phrases(level='', topic='', status='', q='', kind=''):
    qs = Phrase.objects.all()
    if level:
        qs = qs.filter(level=level)
    if topic:
        qs = qs.filter(topic=topic)
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(prompt__icontains=q) | Q(uz__icontains=q))
    return qs


def export_bank(level='', topic='', kind='', status='', tag=''):
    """The §B6 format. kind: 'w' words only, 'p' phrases only, '' both. Importing it back changes nothing."""
    out = {'version': 1, 'words': [], 'phrases': []}
    if kind in ('', 'w'):
        qs = filter_words(level, topic, status, tag).order_by('level', 'topic', Lower('word'))
        out['words'] = [word_row(w) for w in qs]
    if kind in ('', 'p') and not tag:
        qs = filter_phrases(level, topic, status).order_by('level', 'topic', 'kind', 'id')
        out['phrases'] = [phrase_row(p) for p in qs]
    return out


# ── distractors ──────────────────────────────────────────────────────────────

def distractor_pool(levels=None, *, status='published'):
    """{(level, pos): [(id, word, uz, picture, {synonyms…})]} — load once, reuse for many distractors_for()."""
    qs = Word.objects.filter(status=status)
    if levels:
        qs = qs.filter(level__in=list(levels))
    pool = defaultdict(list)
    for wid, word, uz, pic, lvl, pos, syn in qs.values_list('id', 'word', 'uz', 'picture', 'level', 'pos', 'synonyms'):
        pool[(lvl, pos)].append((wid, word, uz, pic, {s.lower() for s in syn or ()}))
    return pool


def _level_steps(level):
    if level not in LEVELS:
        return [level]
    i = LEVELS.index(level)
    out = [level]
    for d in (1, 2):
        for j in (i - d, i + d):
            if 0 <= j < len(LEVELS):
                out.append(LEVELS[j])
    return out


def distractors_for(word, n=3, *, pool=None, rng=None, field='word', with_picture=False):
    """`n` wrong options for `word`: same level and part of speech first (then nearby levels), never the
    word itself or a synonym of it. The word's own curated `distractors` come first.

    field='word' → English words · field='uz' → their Uzbek meanings (EN→UZ questions).
    with_picture=True → only words that have a picture (the Runner's picture choices).
    Pass `pool=distractor_pool(...)` when building many questions (no query per call).
    """
    rng = rng or random
    if pool is None:
        pool = distractor_pool(_level_steps(word.level))
    target = word.word.lower()
    banned = {target} | {s.lower() for s in (word.synonyms or ())}
    banned_uz = {(word.uz or '').lower()} if field == 'uz' else set()
    picked, picked_keys = [], set()

    def take(value, key):
        if not value or key in picked_keys or len(picked) >= n:
            return
        picked.append(value)
        picked_keys.add(key)

    if field == 'word' and not with_picture:
        for d in word.distractors or ():
            if d.lower() not in banned:
                take(d, d.lower())

    def from_group(level, pos):
        cands = [c for c in pool.get((level, pos), ())
                 if c[1].lower() not in banned and target not in c[4] and (not with_picture or c[3])]
        rng.shuffle(cands)
        for _, w, uz, _pic, _ in cands:
            if field == 'uz':
                if uz and uz.lower() not in banned_uz:
                    take(uz, uz.lower())
            else:
                take(w, w.lower())
            if len(picked) >= n:
                return

    for lvl in _level_steps(word.level):
        from_group(lvl, word.pos)
        if len(picked) >= n:
            return picked
    for (lvl, pos) in list(pool):                    # last resort: the same level, any part of speech
        if lvl == word.level and pos != word.pos:
            from_group(lvl, pos)
            if len(picked) >= n:
                break
    return picked[:n]


# ── TTS warm ─────────────────────────────────────────────────────────────────

def tts_clean(text):
    """The same clean-up POST /api/games/voice/tts/ does, so the warmed clip is the one the game asks for."""
    return ' '.join(str(text or '').split())[:TTS_MAX_CHARS]


def item_lines(item):
    """[(text, voice)] a learner can hear for one Word or Phrase."""
    if isinstance(item, Word):
        return [(item.word, WORD_VOICE)]
    voice = item.voice or KIND_VOICE.get(item.kind, 'narrator')
    if item.kind == 'echo':
        return [(item.text, voice)]
    if item.kind == 'answer':
        return [(item.prompt, voice), (item.text, voice)]
    if item.kind == 'fill':
        return [(item.text, voice)]
    if item.kind == 'twister':
        return [(item.text, voice)]
    return []


def warm_lines(*sources, fixed=False):
    """[[text, voice]] for Words / Phrases (querysets or lists), deduplicated, in order.

    fixed=True adds the Runner's own fixed lines when games.runner_logic defines
    FIXED_VOICE_LINES = [(text, voice), …] (Toby's cheers, Bekat greetings).
    """
    out, seen = [], set()

    def add(text, voice):
        t = tts_clean(text)
        if t and voice in VOICES and (t, voice) not in seen:
            seen.add((t, voice))
            out.append([t, voice])

    for src in sources:
        for item in src:
            for text, voice in item_lines(item):
                add(text, voice)
    if fixed:
        try:
            from games.runner_logic import FIXED_VOICE_LINES
        except ImportError:
            FIXED_VOICE_LINES = ()
        for text, voice in FIXED_VOICE_LINES:
            add(text, voice)
    return out


def uncached(lines):
    """Only the lines with no clip on disk yet."""
    from games.tts_views import _cache_path
    return [line for line in lines if not os.path.exists(_cache_path(line[1], line[0]))]
