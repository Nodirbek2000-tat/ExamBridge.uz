"""
Word Battle flow on the server. Every answer is judged here; the client gets a question's key
only in the reply to its own answer.

    start_solo(user, level)              → Round (vs a recorded real player when one exists, else a bot)
    serve_next(user, round_id)           → the next question (served_at = now), or the open one again
    submit_answer(user, round_id, idx, choice) → the verdict, the key and the points
    finish(user, round_id)               → the result (idempotent)
    create_duel / play_duel / duel_payload / my_duels
    leaderboard(user, level), home(user), admin_stats(days)

Timing is the server's: ms = answered_at − served_at. A question is open for question_ms +
grace_ms (+ the listen allowance); later answers count as a timeout. Starting a round closes
the learner's previous active one (one active round per learner, also a DB constraint).
"""
import logging
import random
import secrets
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, FloatField, Max, Min, Q, Sum
from django.db.models.functions import Cast, TruncDate
from django.utils import timezone

from gamestats.services import count_play
from vocabulary import bank, srs
from vocabulary.models import Review, Word

from . import logic
from .config import current as current_config
from .models import LEVELS, Answer, Duel, QSet, Round

log = logging.getLogger(__name__)

SLUG = 'word-battle'
GHOST_DAYS = 30
GHOST_CANDIDATES = 30
WEEK = timedelta(days=7)
ROUND_SLACK = timedelta(seconds=90)
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LEN = 6
BANK_CACHE_SECONDS = 10 * 60
LEADERBOARD_SIZE = 10
HARD_MIN_ANSWERS = 5

LEVEL_TITLES = {
    'A2': 'Elementar', 'B1': 'O‘rta', 'B2': 'O‘rtadan yuqori', 'C1': 'Yuqori', 'SAT': 'SAT lug‘ati',
}


class WBError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def public_name(user):
    from games.voice_views import _public_name
    if user is None:
        return 'O‘yinchi'
    return _public_name(user.first_name, user.last_name)


# ── the bank, per level (cached) ─────────────────────────────────────────────

WORD_FIELDS = ('id', 'word', 'uz', 'pos', 'level', 'example', 'synonyms', 'antonyms', 'distractors', 'tags')


def _level_qs(level):
    qs = Word.objects.filter(status='published').exclude(uz='')
    if level == 'SAT':
        return qs.filter(tags__contains=['sat'])
    return qs.filter(level=level)


def pool_levels(level):
    return ['B1', 'B2', 'C1'] if level == 'SAT' else bank._level_steps(level)


def level_words(level):
    key = f'wb:words:v1:{level}'
    words = cache.get(key)
    if words is None:
        words = list(_level_qs(level).only(*WORD_FIELDS).order_by('id'))
        cache.set(key, words, BANK_CACHE_SECONDS)
    return words


def level_pool(level):
    key = f'wb:pool:v1:{level}'
    pool = cache.get(key)
    if pool is None:
        pool = dict(bank.distractor_pool(pool_levels(level)))
        cache.set(key, pool, BANK_CACHE_SECONDS)
    return pool


def bank_lex():
    """{word: (uz, {synonyms}, {antonyms}, pos)} of every published word (lower case) — lets the
    question builder drop wrong options that are really a second right answer (logic.make_question)."""
    key = 'wb:lex:v2'
    lex = cache.get(key)
    if lex is None:
        lex = {}
        rows = Word.objects.filter(status='published').values_list('word', 'uz', 'synonyms', 'antonyms', 'pos')
        for word, uz, syn, ant, pos in rows.iterator(chunk_size=2000):
            lex.setdefault(word.lower(), (uz or '', frozenset(s.lower() for s in syn or ()),
                                          frozenset(a.lower() for a in ant or ()), pos or ''))
        cache.set(key, lex, BANK_CACHE_SECONDS)
    return lex


def level_counts():
    key = 'wb:counts:v1'
    out = cache.get(key)
    if out is None:
        base = Word.objects.filter(status='published').exclude(uz='')
        out = dict(base.exclude(level='').values('level').annotate(n=Count('id')).values_list('level', 'n'))
        out['SAT'] = base.filter(tags__contains=['sat']).count()
        cache.set(key, out, BANK_CACHE_SECONDS)
    return out


# ── building a round ─────────────────────────────────────────────────────────

def build_qset(user, level, cfg, rng):
    words = level_words(level)
    if len(words) < cfg['questions']:
        raise WBError('bank', 'Bu darajada so‘zlar hali yetarli emas.', 409)
    reviews = {
        r[0]: (r[1], r[2]) for r in Review.objects.filter(
            user=user, kind='w', skill='mean', item_id__in=[w.id for w in words],
        ).values_list('item_id', 'box', 'due_at')
    }
    ordered = logic.order_words(words, reviews, timezone.now(), rng)
    questions = logic.build_questions(ordered, cfg['questions'], level, cfg['types'], rng, level_pool(level),
                                      lex=bank_lex())
    if len(questions) < cfg['questions']:
        raise WBError('bank', 'Bu darajada savol tuzib bo‘lmadi — so‘zlar yetarli emas.', 409)
    return QSet.objects.create(level=level, questions=questions, n=len(questions), created_by=user)


def _expires(now, n, cfg):
    per_q = cfg['question_ms'] + cfg['grace_ms'] + logic.LISTEN_EXTRA_MS + 4000     # + the feedback pause
    return now + timedelta(milliseconds=per_q * n) + ROUND_SLACK


def recorded_opp(rnd, *, label='Yozib olingan'):
    """A finished round as an opponent: its server-timed answers, in order."""
    rows = rnd.answers.order_by('idx').values_list('ms', 'correct', 'points', 'timeout')
    answers = [[None if to else ms, bool(ok), pts] for ms, ok, pts, to in rows]
    return {
        'name': public_name(rnd.user), 'label': label, 'note': '', 'played_at': rnd.finished_at.isoformat(),
        'answers': answers, 'score': rnd.score, 'correct': rnd.correct,
        'ms': sum(a[0] or 0 for a in answers), 'round': str(rnd.pk),
    }


def bot_opp(qset, level, cfg, rng):
    persona, acc = logic.bot_profile(rng, level)
    answers = logic.bot_answers(rng, level, [q['t'] for q in qset.questions], cfg['question_ms'], persona, acc)
    return {
        'name': persona['name'], 'label': 'Bot', 'persona': persona['key'],
        'note': f'{persona["note"]} · aniqligi ~{logic._round(acc * 100)}%',
        'answers': answers, 'score': sum(a[2] for a in answers), 'correct': sum(1 for a in answers if a[1]),
        'ms': sum(a[0] or 0 for a in answers),
    }


def pick_ghost(user, level, n, rng):
    """A recent ranked round of another learner on a set this learner has never played."""
    since = timezone.now() - timedelta(days=GHOST_DAYS)
    open_duel_sets = Duel.objects.filter(opponent__isnull=True, expires_at__gt=timezone.now()).values('qset_id')
    ids = list(
        Round.objects.filter(level=level, status=Round.FINISHED, ranked=True, finished_at__gte=since, qset__n=n)
        .exclude(user=user)
        .exclude(qset_id__in=Round.objects.filter(user=user).values('qset_id'))
        .exclude(qset_id__in=open_duel_sets)
        .order_by('-finished_at').values_list('id', flat=True)[:GHOST_CANDIDATES]
    )
    if not ids:
        return None
    return Round.objects.select_related('user', 'qset').get(pk=rng.choice(ids))


def close_active(user, now=None):
    """Close the learner's active round(s) before a new one starts."""
    now = now or timezone.now()
    for rnd in Round.objects.select_for_update().filter(user=user, status=Round.ACTIVE):
        finalize(rnd, now)


def _new_round(user, qset, level, cfg, *, mode, opp_kind, opp, ghost=None):
    now = timezone.now()
    return Round.objects.create(
        user=user, qset=qset, level=level, mode=mode, opp_kind=opp_kind, opp=opp, ghost=ghost,
        question_ms=cfg['question_ms'], grace_ms=cfg['grace_ms'], expires_at=_expires(now, qset.n, cfg),
    )


def start_solo(user, level):
    cfg = current_config()
    if level not in LEVELS or level not in cfg['levels']:
        raise WBError('level', 'Bu daraja hozir yopiq.', 400)
    rng = random.Random(secrets.randbits(32))
    try:
        with transaction.atomic():
            close_active(user)
            ghost = pick_ghost(user, level, cfg['questions'], rng) if rng.random() < cfg['ghost_share'] else None
            if ghost is not None:
                return _new_round(user, ghost.qset, level, cfg, mode='solo', opp_kind='ghost',
                                  opp=recorded_opp(ghost), ghost=ghost)
            qset = build_qset(user, level, cfg, rng)
            return _new_round(user, qset, level, cfg, mode='solo', opp_kind='bot', opp=bot_opp(qset, level, cfg, rng))
    except IntegrityError:
        raise WBError('busy', 'Boshqa raund hozirgina boshlandi — qayta urinib ko‘ring.', 409)


# ── one question at a time ───────────────────────────────────────────────────

def _lock(user, round_id):
    rnd = Round.objects.select_for_update(of=('self',)).select_related('qset').filter(pk=round_id, user=user).first()
    if rnd is None:
        raise WBError('not-found', 'Raund topilmadi.', 404)
    return rnd


def _deadline(rnd, ans):
    return ans.served_at + timedelta(milliseconds=logic.limit_ms(ans.qtype, rnd.question_ms, rnd.grace_ms))


def _timeout(rnd, ans, now, *, forced=False):
    """The learner never answered: a miss. forced = closed by the server (no review is written)."""
    ans.answered, ans.timeout, ans.correct, ans.choice = True, True, False, None
    ans.answered_at, ans.points = now, 0
    ans.ms = None if forced else int((now - ans.served_at).total_seconds() * 1000)
    rnd.streak = 0


def _opp_for(rnd, idx):
    answers = (rnd.opp or {}).get('answers') or []
    if idx < len(answers):
        ms, ok, pts = answers[idx]
        return {'ms': ms, 'ok': ok, 'pts': pts}
    return None


def question_payload(rnd, q, idx, served_at, now):
    """What the learner sees — never the key. left_ms: visible time left (listen allowance included)."""
    elapsed = int((now - served_at).total_seconds() * 1000)
    return {
        'idx': idx, 'n': rnd.qset.n, 't': q['t'],
        'prompt': q['p'], 'options': list(q['o']), 'pos': q.get('pos', ''),
        'question_ms': rnd.question_ms, 'extra_ms': logic.extra_ms(q['t']),
        'left_ms': max(0, rnd.question_ms + logic.extra_ms(q['t']) - elapsed),
        'resumed': served_at != now,
        'opp': _opp_for(rnd, idx),
        'score': rnd.score, 'streak': rnd.streak, 'correct': rnd.correct,
    }


def serve_next(user, round_id):
    """The next question, or the open one again. Errors are raised after the commit, so a round
    closed on the way (expired, a missed last question) stays closed."""
    with transaction.atomic():
        rnd = _lock(user, round_id)
        if rnd.status != Round.ACTIVE:
            raise WBError('finished', 'Raund tugagan.', 409)
        now = timezone.now()
        if now > rnd.expires_at:
            finalize(rnd, now)
            error = WBError('expired', 'Raund vaqti tugadi.', 410)
        else:
            questions = rnd.qset.questions
            last = rnd.answers.filter(idx=rnd.cursor - 1).first() if rnd.cursor else None
            if last is not None and not last.answered:
                if now <= _deadline(rnd, last):          # the open question again — its clock keeps running
                    return question_payload(rnd, questions[last.idx], last.idx, last.served_at, now)
                _timeout(rnd, last, now)
                last.save()
            if rnd.cursor < len(questions):
                idx = rnd.cursor
                q = questions[idx]
                Answer.objects.create(round=rnd, idx=idx, word_id=q['w'], qtype=q['t'], served_at=now)
                rnd.cursor = idx + 1
                rnd.save(update_fields=['cursor', 'streak'])
                return question_payload(rnd, q, idx, now, now)
            rnd.save(update_fields=['streak'])
            error = WBError('done', 'Savollar tugadi.', 409)
    raise error


def answer_payload(rnd, ans, q, mult=1.0, bonus=0):
    return {
        'idx': ans.idx, 'correct': ans.correct, 'timeout': ans.timeout, 'choice': ans.choice,
        'key': q['k'], 'answer': q['o'][q['k']], 'word': q.get('en', ''), 'uz': q.get('uz', ''),
        'ms': ans.ms, 'points': ans.points, 'bonus': bonus, 'mult': mult,
        'score': rnd.score, 'streak': rnd.streak, 'correct_count': rnd.correct,
        'last': ans.idx >= rnd.qset.n - 1,
    }


def submit_answer(user, round_id, idx, choice):
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        raise WBError('bad-idx', 'idx noto‘g‘ri.', 400)
    if choice is not None and (not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < logic.OPTIONS):
        raise WBError('bad-choice', 'choice 0–3 yoki null bo‘lishi kerak.', 400)
    with transaction.atomic():
        rnd = _lock(user, round_id)
        ans = Answer.objects.select_for_update().filter(round=rnd, idx=idx).first()
        if ans is None:
            raise WBError('not-served', 'Bu savol hali berilmagan.', 409)
        q = rnd.qset.questions[idx]
        if ans.answered:                              # a retry of the same answer: the same verdict
            return answer_payload(rnd, ans, q)
        if rnd.status != Round.ACTIVE:
            raise WBError('finished', 'Raund tugagan.', 409)
        now = timezone.now()
        ms = int((now - ans.served_at).total_seconds() * 1000)
        late = ms > logic.limit_ms(ans.qtype, rnd.question_ms, rnd.grace_ms)
        if choice is None or late:
            _timeout(rnd, ans, now)
            ans.ms = ms
            pts, bonus, mult = 0, 0, 1.0
        else:
            correct = choice == q['k']
            pts, bonus, mult = logic.points(correct, ms, rnd.streak, rnd.question_ms, extra_ms=logic.extra_ms(ans.qtype))
            ans.answered, ans.choice, ans.correct, ans.answered_at, ans.ms, ans.points = True, choice, correct, now, ms, pts
            if correct:
                rnd.streak += 1
                rnd.correct += 1
                rnd.best_streak = max(rnd.best_streak, rnd.streak)
            else:
                rnd.streak = 0
            rnd.score += pts
        ans.save()
        rnd.save(update_fields=['score', 'correct', 'streak', 'best_streak'])
        return answer_payload(rnd, ans, q, mult, bonus)


# ── finishing ────────────────────────────────────────────────────────────────

def finalize(rnd, now=None):
    """Close an active round: open questions become timeouts, reviews are written, the play is
    counted. A solo round that did not reach the end is 'abandoned' (never ranked)."""
    now = now or timezone.now()
    answers = list(rnd.answers.order_by('idx'))
    forced = set()
    for a in answers:
        if not a.answered:
            _timeout(rnd, a, now, forced=True)
            forced.add(a.idx)
    if forced:
        Answer.objects.bulk_update([a for a in answers if a.idx in forced],
                                   ['answered', 'timeout', 'correct', 'choice', 'answered_at', 'ms', 'points'])
    n = rnd.qset.n
    rnd.complete = len(answers) >= n and not forced
    rnd.status = Round.FINISHED if (rnd.complete or rnd.mode == 'duel') else Round.ABANDONED
    flags = ['too-fast'] if logic.too_fast([a.ms for a in answers if not a.timeout]) else []
    rnd.flags = flags
    rnd.ranked = rnd.complete and not flags
    if rnd.ranked and rnd.mode == 'duel' and _other_side_finished(rnd):
        # the side that finishes second could have been told every key (a finished round shows them):
        # its duel still counts, the weekly board does not
        rnd.ranked = False
    rnd.finished_at = now

    # spaced repetition: skill 'mean', one outcome per question the learner really saw and answered
    judged = [a for a in answers if a.idx not in forced]
    existing = {r.item_id: r for r in Review.objects.filter(
        user_id=rnd.user_id, kind='w', skill='mean', item_id__in=[a.word_id for a in judged])}
    rows, summary = [], {'strengthened': 0, 'new': 0, 'weak': 0}
    for a in judged:
        rev = existing.get(a.word_id)
        if rev is None:
            rev = srs.new_review(rnd.user_id, 'w', a.word_id, 'mean', now)
            existing[a.word_id] = rev
            summary['new'] += 1
        effect = srs.apply(rev, 'ok' if a.correct else 'miss', now, tries=1, pt=a.qtype)
        if a.ms is not None and a.correct:
            rev.best_ms = a.ms if rev.best_ms is None else min(rev.best_ms, a.ms)
        if effect == 'promoted':
            summary['strengthened'] += 1
        elif effect == 'demoted':
            summary['weak'] += 1
        rows.append(rev)
    srs.save_reviews(list({id(r): r for r in rows}.values()))
    rnd.srs = summary
    rnd.save(update_fields=['status', 'complete', 'ranked', 'flags', 'finished_at', 'srs', 'streak'])
    if rnd.complete:
        QSet.objects.filter(pk=rnd.qset_id).update(plays=F('plays') + 1)
    if rnd.status == Round.FINISHED:
        user = rnd.user
        transaction.on_commit(lambda: count_play(user, SLUG))
    return rnd


def _other_side_finished(rnd):
    return Duel.objects.filter(
        Q(creator_round=rnd, opponent_round__status=Round.FINISHED)
        | Q(opponent_round=rnd, creator_round__status=Round.FINISHED)
    ).exists()


def close_if_stale(rnd, now=None):
    now = now or timezone.now()
    if rnd is not None and rnd.status == Round.ACTIVE and now > rnd.expires_at:
        with transaction.atomic():
            locked = Round.objects.select_for_update().get(pk=rnd.pk)
            if locked.status == Round.ACTIVE:
                finalize(locked, now)
            return locked
    return rnd


def finish(user, round_id):
    with transaction.atomic():
        rnd = _lock(user, round_id)
        if rnd.status == Round.ACTIVE:
            finalize(rnd)
    return result_payload(rnd, user)


# ── payloads ─────────────────────────────────────────────────────────────────

def opponent_public(rnd):
    opp = rnd.opp or {}
    return {
        'kind': rnd.opp_kind, 'name': opp.get('name') or ('Raqib' if rnd.opp_kind == 'wait' else 'Bot'),
        'label': opp.get('label', ''), 'note': opp.get('note', ''), 'persona': opp.get('persona', ''),
        'played_at': opp.get('played_at'),
    }


def _so_far(rnd):
    """A resumed round: my own verdicts so far, and the opponent's points on those same questions."""
    if not rnd.cursor or rnd.status != Round.ACTIVE:
        return [], 0
    marks = list(rnd.answers.filter(answered=True).order_by('idx').values_list('correct', flat=True))
    return marks, sum(a[2] for a in ((rnd.opp or {}).get('answers') or [])[:len(marks)])


def round_payload(rnd):
    marks, opp_score = _so_far(rnd)
    return {
        'id': str(rnd.pk), 'level': rnd.level, 'mode': rnd.mode, 'status': rnd.status, 'n': rnd.qset.n,
        'cursor': rnd.cursor, 'score': rnd.score, 'correct': rnd.correct, 'streak': rnd.streak,
        'marks': marks, 'opp_score': opp_score,
        'question_ms': rnd.question_ms, 'grace_ms': rnd.grace_ms, 'listen_ms': logic.LISTEN_EXTRA_MS,
        'opponent': opponent_public(rnd), 'expires_at': rnd.expires_at.isoformat(),
        'duel': rnd_duel_code(rnd),
    }


def rnd_duel_code(rnd):
    if rnd.mode != 'duel':
        return None
    return Duel.objects.filter(Q(creator_round=rnd) | Q(opponent_round=rnd)).values_list('code', flat=True).first()


def _best(user, level, exclude=None):
    qs = Round.objects.filter(user=user, level=level, status=Round.FINISHED, ranked=True)
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return qs.aggregate(b=Max('score'))['b']


def result_payload(rnd, user):
    questions = rnd.qset.questions
    answers = {a.idx: a for a in rnd.answers.all()}
    words = {w.id: w for w in Word.objects.filter(id__in=[q['w'] for q in questions]).only('id', 'word', 'uz', 'example', 'definition')}
    opp = rnd.opp or {}
    opp_answers = opp.get('answers') or []
    items = []
    for i, q in enumerate(questions):
        a = answers.get(i)
        w = words.get(q['w'])
        oa = opp_answers[i] if i < len(opp_answers) else None
        items.append({
            'idx': i, 't': q['t'], 'word': q.get('en') or (w.word if w else ''), 'uz': (w.uz if w else '') or q.get('uz', ''),
            'pos': q.get('pos', ''), 'example': w.example if w else '', 'definition': w.definition if w else '',
            'prompt': q['p'], 'options': q['o'], 'key': q['k'],
            'served': a is not None, 'choice': a.choice if a else None, 'correct': bool(a and a.correct),
            'timeout': bool(a and a.timeout), 'ms': a.ms if a else None, 'points': a.points if a else 0,
            'opp_ok': oa[1] if oa else None,
        })
    my_ms = sum(a.ms or 0 for a in answers.values() if not a.timeout)
    me = {'score': rnd.score, 'correct': rnd.correct, 'ms': my_ms}
    duel = None
    if rnd.mode == 'duel':
        d = (Duel.objects.select_related('creator', 'opponent', 'creator_round', 'opponent_round', 'qset')
             .prefetch_related(*DUEL_ANSWERS).filter(Q(creator_round=rnd) | Q(opponent_round=rnd)).first())
        duel = duel_payload(d, user) if d else None
    opponent = {**opponent_public(rnd)}
    outcome = None
    if rnd.opp_kind in ('ghost', 'bot', 'duel'):
        opponent.update({'score': opp.get('score', 0), 'correct': opp.get('correct', 0)})
        if rnd.status != Round.FINISHED:          # stopped early: the opponent up to the same question
            seen = opp_answers[:len(answers)]
            opponent.update({'score': sum(a[2] for a in seen), 'correct': sum(1 for a in seen if a[1])})
        outcome = {1: 'win', -1: 'lose', 0: 'draw'}[logic.compare(me, opp)]
    elif duel and duel['status'] == 'done':
        other = duel['opponent'] if duel['role'] == 'creator' else duel['creator']
        opponent.update({'kind': 'duel', 'name': other['name'], 'label': 'Duel',
                         'score': other.get('score', 0), 'correct': other.get('correct', 0)})
        outcome = {'me': 'win', 'them': 'lose', 'draw': 'draw'}.get(duel.get('result'))
    elif rnd.opp_kind == 'wait':
        outcome = 'pending'
    if rnd.status != Round.FINISHED:
        outcome = None
    prev = _best(user, rnd.level, exclude=rnd.pk)
    lb = leaderboard(user, rnd.level, size=0) if rnd.ranked else None
    return {
        **round_payload(rnd), 'complete': rnd.complete, 'ranked': rnd.ranked, 'flags': rnd.flags,
        'best_streak': rnd.best_streak, 'avg_ms': int(my_ms / max(1, len([a for a in answers.values() if not a.timeout]))),
        'best': max(prev or 0, rnd.score if rnd.ranked else 0), 'new_best': bool(rnd.ranked and rnd.score > (prev or 0)),
        'rank': lb['me']['rank'] if lb else None,
        'opponent': opponent, 'outcome': outcome, 'items': items, 'srs': rnd.srs or {},
        'duel_info': duel,
    }


# ── leaderboard and home ─────────────────────────────────────────────────────

def leaderboard(user, level, size=LEADERBOARD_SIZE):
    since = timezone.now() - WEEK
    week = Round.objects.filter(level=level, status=Round.FINISHED, ranked=True, finished_at__gte=since)
    top = []
    if size:
        rows = (week.values('user', 'user__first_name', 'user__last_name')
                .annotate(best=Max('score'), first_at=Min('finished_at'))
                .order_by('-best', 'first_at', 'user')[:size])
        from games.voice_views import _public_name
        top = [{'name': _public_name(r['user__first_name'], r['user__last_name']), 'score': r['best'],
                'is_me': r['user'] == user.id} for r in rows]
    my_best = week.filter(user=user).aggregate(b=Max('score'))['b']
    rank = None
    if my_best is not None:
        rank = week.values('user').annotate(best=Max('score')).filter(best__gt=my_best).count() + 1
    return {'level': level, 'top': top, 'me': {'best': my_best or 0, 'rank': rank}}


def home(user):
    cfg = current_config()
    counts = level_counts()
    best = dict(Round.objects.filter(user=user, status=Round.FINISHED, ranked=True)
                .values('level').annotate(b=Max('score')).values_list('level', 'b'))
    now = timezone.now()
    active = close_if_stale(Round.objects.select_related('qset').filter(user=user, status=Round.ACTIVE).first(), now)
    if active is not None and active.status != Round.ACTIVE:
        active = None
    week = Round.objects.filter(user=user, status=Round.FINISHED, finished_at__gte=now - WEEK)
    return {
        'config': {k: cfg[k] for k in ('levels', 'questions', 'question_ms', 'grace_ms', 'leaderboard', 'duel_hours')},
        'levels': [{
            'level': lv, 'title': LEVEL_TITLES[lv], 'words': counts.get(lv, 0), 'best': best.get(lv) or 0,
            'open': lv in cfg['levels'] and counts.get(lv, 0) >= cfg['questions'],
        } for lv in LEVELS],
        'active': ({'id': str(active.pk), 'level': active.level, 'cursor': active.cursor, 'n': active.qset.n,
                    'mode': active.mode} if active else None),
        'week': {'rounds': week.count(), 'correct': week.aggregate(s=Sum('correct'))['s'] or 0},
        'duels': my_duels(user, limit=6),
    }


# ── duels ────────────────────────────────────────────────────────────────────

def _new_code():
    for _ in range(8):
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if not Duel.objects.filter(code=code).exists():
            return code
    raise WBError('busy', 'Kod yaratib bo‘lmadi — qayta urinib ko‘ring.', 503)


def create_duel(user, *, level=None, round_id=None):
    cfg = current_config()
    now = timezone.now()
    expires = now + timedelta(hours=cfg['duel_hours'])
    if round_id:
        rnd = Round.objects.select_related('qset').filter(pk=round_id, user=user).first()
        if rnd is None:
            raise WBError('not-found', 'Raund topilmadi.', 404)
        if rnd.status != Round.FINISHED or not rnd.complete:
            raise WBError('not-finished', 'Avval raundni oxirigacha o‘ynang.', 409)
        if rnd.mode == 'duel':
            raise WBError('duel-round', 'Bu raund allaqachon duel edi — yangi jangdan chaqiring.', 409)
        open_one = Duel.objects.filter(creator=user, creator_round=rnd, opponent__isnull=True, expires_at__gt=now).first()
        if open_one:
            return open_one
        return Duel.objects.create(code=_new_code(), level=rnd.level, qset=rnd.qset, creator=user,
                                   creator_round=rnd, expires_at=expires)
    if level not in LEVELS or level not in cfg['levels']:
        raise WBError('level', 'Bu daraja hozir yopiq.', 400)
    rng = random.Random(secrets.randbits(32))
    with transaction.atomic():
        qset = build_qset(user, level, cfg, rng)
        return Duel.objects.create(code=_new_code(), level=level, qset=qset, creator=user, expires_at=expires)


def get_duel(code):
    code = (code or '').strip().upper()[:10]
    d = (Duel.objects.select_related('creator', 'opponent', 'creator_round', 'opponent_round', 'qset')
         .prefetch_related(*DUEL_ANSWERS).filter(code=code).first())
    if d is None:
        raise WBError('not-found', 'Duel topilmadi.', 404)
    return d


def play_duel(user, code):
    """Start (or resume) the viewer's side of a duel. The first other learner to accept takes the slot."""
    cfg = current_config()
    try:
        with transaction.atomic():
            d = Duel.objects.select_for_update().filter(code=(code or '').strip().upper()[:10]).first()
            if d is None:
                raise WBError('not-found', 'Duel topilmadi.', 404)
            now = timezone.now()
            creator = d.creator_id == user.id
            if not creator and d.opponent_id and d.opponent_id != user.id:
                raise WBError('taken', 'Bu duelni boshqa o‘yinchi qabul qilgan.', 409)
            mine_id = d.creator_round_id if creator else d.opponent_round_id
            if mine_id:
                mine = Round.objects.select_related('qset').get(pk=mine_id)
                if mine.status == Round.ACTIVE and now <= mine.expires_at:
                    return mine
                raise WBError('played', 'Siz bu duelni o‘ynab bo‘lgansiz.', 409)
            if now > d.expires_at:
                raise WBError('expired', 'Duel muddati tugagan.', 410)
            if not creator and Round.objects.filter(user=user, qset_id=d.qset_id).exists():
                raise WBError('seen', 'Bu savollarni allaqachon o‘ynagansiz — yangi duel yarating.', 409)
            close_active(user, now)
            other_id = d.opponent_round_id if creator else d.creator_round_id
            other = Round.objects.select_related('user').filter(pk=other_id, status=Round.FINISHED).first() if other_id else None
            if other is not None:
                opp_kind, opp = 'duel', recorded_opp(other, label='Duel')
            else:
                waiting_for = d.opponent if creator else d.creator
                opp_kind = 'wait'
                opp = {'name': public_name(waiting_for) if waiting_for else 'Do‘stingiz', 'label': 'Duel',
                       'note': 'Hali o‘ynamagan — natijalar ikkalangiz tugatgach solishtiriladi.'}
            rnd = _new_round(user, d.qset, d.level, cfg, mode='duel', opp_kind=opp_kind, opp=opp)
            if creator:
                d.creator_round = rnd
            else:
                d.opponent, d.opponent_round = user, rnd
                d.accepted_at = d.accepted_at or now
            d.save(update_fields=['creator_round', 'opponent', 'opponent_round', 'accepted_at'])
            return rnd
    except IntegrityError:
        raise WBError('busy', 'Boshqa raund hozirgina boshlandi — qayta urinib ko‘ring.', 409)


DUEL_ANSWERS = ('creator_round__answers', 'opponent_round__answers')     # prefetched: no query per duel


def _answers(rnd):
    return sorted(rnd.answers.all(), key=lambda a: a.idx)


def _side(rnd):
    if rnd is None:
        return {'played': False, 'playing': False}
    done = rnd.status == Round.FINISHED
    return {'played': done, 'playing': rnd.status == Round.ACTIVE, 'score': rnd.score, 'correct': rnd.correct,
            'ms': sum(a.ms or 0 for a in _answers(rnd) if not a.timeout) if done else 0}


def duel_payload(d, viewer):
    now = timezone.now()
    cr = close_if_stale(d.creator_round, now)
    orr = close_if_stale(d.opponent_round, now)
    viewer_id = getattr(viewer, 'id', None) if getattr(viewer, 'is_authenticated', False) else None
    role = 'creator' if viewer_id == d.creator_id else 'opponent' if viewer_id and viewer_id == d.opponent_id else 'guest'
    c, o = _side(cr), _side(orr)
    both = c['played'] and o['played']
    expired = now > d.expires_at
    if both:
        status = 'done'
    elif expired and not (c['playing'] or o['playing']):
        status = 'expired'
    elif not d.opponent_id:
        status = 'open'
    else:
        status = 'playing'
    winner = result = None
    if both:
        cmp = logic.compare(c, o)
        winner = 'draw' if cmp == 0 else 'creator' if cmp > 0 else 'opponent'
        if role != 'guest':
            result = 'draw' if winner == 'draw' else 'me' if winner == role else 'them'

    def side(user, s, mine):
        out = {'name': public_name(user) if user else None, 'played': s['played'], 'playing': s['playing']}
        if s['played'] and (both or mine):
            out.update({'score': s['score'], 'correct': s['correct']})
        return out

    my_round = cr if role == 'creator' else orr if role == 'opponent' else None
    can_play = bool(viewer_id) and not expired and (
        (role == 'creator' and (cr is None or cr.status == Round.ACTIVE))
        or (role == 'opponent' and (orr is None or orr.status == Round.ACTIVE))
        or (role == 'guest' and not d.opponent_id)
    )
    compare = None
    if both:
        ca = [a.correct for a in _answers(cr)]
        oa = [a.correct for a in _answers(orr)]
        compare = [[ca[i] if i < len(ca) else False, oa[i] if i < len(oa) else False] for i in range(d.qset.n)]
    return {
        'code': d.code, 'level': d.level, 'status': status, 'role': role, 'result': result, 'winner': winner,
        'created_at': d.created_at.isoformat(), 'expires_at': d.expires_at.isoformat(),
        'left_s': max(0, int((d.expires_at - now).total_seconds())), 'n': d.qset.n,
        'question_ms': current_config()['question_ms'],
        'creator': side(d.creator, c, role == 'creator'),
        'opponent': side(d.opponent, o, role == 'opponent') if d.opponent_id else None,
        'my_round': str(my_round.pk) if my_round else None,
        'my_round_active': bool(my_round and my_round.status == Round.ACTIVE),
        'can_play': can_play, 'compare': compare,
    }


def my_duels(user, limit=10):
    qs = (Duel.objects.filter(Q(creator=user) | Q(opponent=user))
          .select_related('creator', 'opponent', 'creator_round', 'opponent_round', 'qset')
          .prefetch_related(*DUEL_ANSWERS)
          .order_by('-created_at')[:limit])
    return [duel_payload(d, user) for d in qs]


# ── admin ────────────────────────────────────────────────────────────────────

def admin_stats(days):
    key = f'wb:admin:v1:{days}'
    hit = cache.get(key)
    if hit is not None:
        return hit
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start_day = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start_day, time.min), tz)

    started = Round.objects.filter(started_at__gte=since)
    finished = Round.objects.filter(status=Round.FINISHED, finished_at__gte=since)
    per_day = {r['d']: r for r in finished.annotate(d=TruncDate('finished_at', tzinfo=tz)).values('d').annotate(
        n=Count('id'), players=Count('user', distinct=True), avg=Avg('score'))}
    series = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        r = per_day.get(d)
        series.append({'date': d.isoformat(), 'rounds': r['n'] if r else 0, 'players': r['players'] if r else 0,
                       'avg_score': round(r['avg']) if r and r['avg'] is not None else None})

    agg = finished.aggregate(n=Count('id'), players=Count('user', distinct=True), avg=Avg('score'),
                             ranked=Count('id', filter=Q(ranked=True)), flagged=Count('id', filter=~Q(flags=[])))
    answered = Answer.objects.filter(answered_at__gte=since, answered=True)
    acc = answered.aggregate(n=Count('id'), ok=Count('id', filter=Q(correct=True)),
                             fast=Avg('ms', filter=Q(correct=True, timeout=False)))
    n_started = started.count()
    n_abandoned = started.filter(status=Round.ABANDONED).count()

    by_level = {r['level']: r for r in finished.values('level').annotate(
        n=Count('id'), players=Count('user', distinct=True), avg=Avg('score'), correct=Avg('correct'))}
    by_type = {r['qtype']: r for r in answered.values('qtype').annotate(n=Count('id'), ok=Count('id', filter=Q(correct=True)))}
    opp = dict(finished.values('opp_kind').annotate(n=Count('id')).values_list('opp_kind', 'n'))

    duels = Duel.objects.filter(created_at__gte=since)
    d_agg = duels.aggregate(n=Count('id'), accepted=Count('id', filter=Q(opponent__isnull=False)))
    d_done = duels.filter(creator_round__status=Round.FINISHED, opponent_round__status=Round.FINISHED).count()
    d_expired = duels.filter(expires_at__lt=timezone.now()).exclude(
        creator_round__status=Round.FINISHED, opponent_round__status=Round.FINISHED).count()

    hard_rows = list(answered.values('word_id').annotate(n=Count('id'), ok=Count('id', filter=Q(correct=True)))
                     .filter(n__gte=HARD_MIN_ANSWERS)
                     .annotate(rate=Cast('ok', FloatField()) / Cast('n', FloatField()))
                     .order_by('rate', '-n')[:20])
    words = {w.id: w for w in Word.objects.filter(id__in=[r['word_id'] for r in hard_rows]).only('id', 'word', 'uz', 'level', 'tags')}
    wrong_types = {}
    for r in answered.filter(word_id__in=list(words), correct=False).values('word_id', 'qtype').annotate(n=Count('id')):
        wrong_types.setdefault(r['word_id'], {})[r['qtype']] = r['n']
    hardest = [{
        'id': r['word_id'], 'word': words[r['word_id']].word if r['word_id'] in words else '?',
        'uz': words[r['word_id']].uz if r['word_id'] in words else '', 'level': words[r['word_id']].level if r['word_id'] in words else '',
        'sat': 'sat' in (words[r['word_id']].tags or []) if r['word_id'] in words else False,
        'answers': r['n'], 'accuracy': round(100 * r['ok'] / r['n']) if r['n'] else 0,
        'miss_types': wrong_types.get(r['word_id'], {}),
    } for r in hard_rows]

    counts = level_counts()
    out = {
        'days': days, 'start': start_day.isoformat(), 'end': today.isoformat(),
        'totals': {
            'started': n_started, 'rounds': agg['n'], 'abandoned': n_abandoned, 'players': agg['players'],
            'avg_score': round(agg['avg']) if agg['avg'] is not None else None,
            'accuracy': round(100 * acc['ok'] / acc['n']) if acc['n'] else None,
            'answers': acc['n'], 'avg_ms': round(acc['fast']) if acc['fast'] is not None else None,
            'completion': round(100 * agg['n'] / n_started) if n_started else None,
            'ranked': agg['ranked'], 'flagged': agg['flagged'],
        },
        'series': series,
        'levels': [{
            'level': lv, 'title': LEVEL_TITLES[lv], 'words': counts.get(lv, 0),
            'rounds': (by_level.get(lv) or {}).get('n', 0), 'players': (by_level.get(lv) or {}).get('players', 0),
            'avg_score': round(by_level[lv]['avg']) if lv in by_level and by_level[lv]['avg'] is not None else None,
            'avg_correct': round(by_level[lv]['correct'], 1) if lv in by_level and by_level[lv]['correct'] is not None else None,
        } for lv in LEVELS],
        'types': [{
            'type': t, 'answers': (by_type.get(t) or {}).get('n', 0),
            'accuracy': round(100 * by_type[t]['ok'] / by_type[t]['n']) if t in by_type and by_type[t]['n'] else None,
        } for t in ('en_uz', 'uz_en', 'syn', 'ant', 'cloze', 'listen')],
        'opponents': {k: opp.get(k, 0) for k in ('ghost', 'bot', 'duel', 'wait')},
        'duels': {'created': d_agg['n'], 'accepted': d_agg['accepted'], 'completed': d_done, 'expired': d_expired},
        'hardest': hardest, 'hard_min': HARD_MIN_ANSWERS,
    }
    cache.set(key, out, 120)
    return out
