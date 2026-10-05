"""
Word Battle: server-side checking (no key before the answer), timing, scoring, the streak
multiplier, ghost selection, the duel lifecycle and the spaced-repetition writes.

    python manage.py test wordbattle
"""
import json
import random
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from gamestats.models import Game
from vocabulary.models import Review, Word

from . import logic, services
from .config import DEFAULTS, validate_config
from gamestats.configs import ConfigError
from .models import Answer, Duel, QSet, Round

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'wb-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
BASE = '/api/games/word-battle'

NOUNS = ['apple', 'river', 'garden', 'window', 'teacher', 'market', 'bridge', 'castle', 'forest', 'island',
         'kitchen', 'letter', 'mirror', 'needle', 'orange', 'pencil', 'rabbit', 'saddle', 'ticket', 'violin',
         'wallet', 'basket', 'candle', 'doctor', 'engine', 'farmer', 'guitar', 'hammer', 'jacket', 'ladder']
ADJS = ['happy', 'cheap', 'bright', 'quiet', 'strong', 'early', 'heavy', 'gentle', 'honest', 'narrow']
ANTS = {'happy': 'sad', 'cheap': 'expensive', 'bright': 'dark', 'quiet': 'loud', 'strong': 'weak', 'early': 'late',
        'heavy': 'light', 'gentle': 'rough', 'honest': 'dishonest', 'narrow': 'wide'}
SYNS = {'happy': 'glad', 'cheap': 'inexpensive', 'bright': 'shiny', 'quiet': 'silent', 'strong': 'powerful',
        'early': 'premature', 'heavy': 'weighty', 'gentle': 'kind', 'honest': 'truthful', 'narrow': 'thin'}


def make_bank(level='B1', tags=None):
    rows = []
    for i, w in enumerate(NOUNS):
        rows.append(Word(word=w, uz=f'ot ma’nosi {i}', pos='noun', level=level, status='published',
                         example=f'I can see the {w} from here.', synonyms=[], antonyms=[], tags=tags or []))
    for i, w in enumerate(ADJS):
        rows.append(Word(word=w, uz=f'sifat ma’nosi {i}', pos='adj', level=level, status='published',
                         example=f'It was a very {w} day.', synonyms=[SYNS[w]], antonyms=[ANTS[w]], tags=tags or []))
    Word.objects.bulk_create(rows)


@override_settings(CACHES=LOCMEM)
class WBTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.a = User.objects.create(username='wb_a', email='wb_a@example.test', first_name='Anvar', last_name='Karimov')
        cls.b = User.objects.create(username='wb_b', email='wb_b@example.test', first_name='Bonu', last_name='Aliyeva')
        cls.c = User.objects.create(username='wb_c', email='wb_c@example.test', first_name='Sardor')
        cls.staff = User.objects.create(username='wb_staff', email='wb_staff@example.test', is_staff=True)
        make_bank('B1')
        Game.objects.update_or_create(slug='word-battle', defaults={'title': 'WORD BATTLE', 'status': 'soon', 'order': 40})

    def setUp(self):
        cache.clear()
        self.cp = mock.patch('wordbattle.services.count_play')
        self.count_play = self.cp.start()
        self.addCleanup(self.cp.stop)

    def client_for(self, user):
        api = APIClient(**H)
        api.force_authenticate(user)
        return api

    def set_config(self, **cfg):
        Game.objects.filter(slug='word-battle').update(config=cfg)
        cache.clear()

    def start(self, user, level='B1'):
        r = self.client_for(user).post(f'{BASE}/rounds/', {'level': level}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def key_of(self, round_id, idx):
        rnd = Round.objects.select_related('qset').get(pk=round_id)
        return rnd.qset.questions[idx]['k']

    def play_all(self, user, round_id, *, right=True, back_ms=2000):
        """Answer every question (right or wrong), each `back_ms` after it was served."""
        api = self.client_for(user)
        while True:
            r = api.post(f'{BASE}/rounds/{round_id}/next/', format='json')
            if r.status_code == 409:
                break
            self.assertEqual(r.status_code, 200, r.content)
            q = r.json()
            Answer.objects.filter(round_id=round_id, idx=q['idx']).update(
                served_at=timezone.now() - timedelta(milliseconds=back_ms))
            k = self.key_of(round_id, q['idx'])
            choice = k if right else (k + 1) % 4
            a = api.post(f'{BASE}/rounds/{round_id}/answer/', {'idx': q['idx'], 'choice': choice}, format='json')
            self.assertEqual(a.status_code, 200, a.content)
        f = api.post(f'{BASE}/rounds/{round_id}/finish/', format='json')
        self.assertEqual(f.status_code, 200, f.content)
        return f.json()


# ── pure rules ───────────────────────────────────────────────────────────────

class ScoringTests(TestCase):
    def test_speed_bonus_and_points(self):
        self.assertEqual(logic.points(True, 900, 0, 7000), (150, 50, 1.0))
        self.assertEqual(logic.points(True, 1000, 0, 7000), (150, 50, 1.0))
        self.assertEqual(logic.points(True, 4000, 0, 7000), (125, 25, 1.0))
        self.assertEqual(logic.points(True, 7000, 0, 7000), (100, 0, 1.0))
        self.assertEqual(logic.points(True, 8400, 0, 7000), (100, 0, 1.0))      # inside the grace: no bonus
        self.assertEqual(logic.points(False, 900, 5, 7000), (0, 0, 1.0))
        self.assertEqual(logic.points(True, 399, 0, 7000), (0, 0, 1.0))          # faster than a human
        self.assertEqual(logic.points(True, 2500, 0, 7000, extra_ms=1500), (150, 50, 1.0))   # listen allowance

    def test_streak_multiplier_after_three_in_a_row(self):
        self.assertEqual(logic.points(True, 1000, 2, 7000)[0], 150)
        self.assertEqual(logic.points(True, 1000, 3, 7000), (225, 50, 1.5))
        self.assertEqual(logic.points(True, 7000, 4, 7000)[0], 150)
        seq = logic.score_sequence([(True, 1000, 'en_uz')] * 4 + [(False, 3000, 'syn'), (True, 1000, 'syn')], 7000)
        self.assertEqual([p for _, _, p in seq], [150, 150, 150, 225, 0, 150])

    def test_too_fast_flag(self):
        self.assertTrue(logic.too_fast([100, 200, 300, 5000, 300]))
        self.assertFalse(logic.too_fast([100, 200, 3000, 5000]))
        self.assertFalse(logic.too_fast([100, 100, 100]))                # too few answers to judge

    def test_compare(self):
        self.assertEqual(logic.compare({'score': 10, 'correct': 1, 'ms': 5}, {'score': 9, 'correct': 9, 'ms': 1}), 1)
        self.assertEqual(logic.compare({'score': 10, 'correct': 2, 'ms': 5}, {'score': 10, 'correct': 2, 'ms': 4}), -1)
        self.assertEqual(logic.compare({'score': 10, 'correct': 2, 'ms': 5}, {'score': 10, 'correct': 2, 'ms': 5}), 0)

    def test_type_slots(self):
        slots = logic.type_slots('B1', 15, list(logic.MIX['B1']))
        self.assertEqual(len(slots), 15)
        self.assertEqual(slots.count('cloze'), 3)
        only = logic.type_slots('B2', 15, ['en_uz', 'syn'])
        self.assertEqual(len(only), 15)
        self.assertEqual(set(only), {'en_uz', 'syn'})
        self.assertEqual(len(logic.type_slots('A2', 12, list(logic.MIX['A2']))), 12)

    def test_bot_is_believable(self):
        rng = random.Random(3)
        persona, acc = logic.bot_profile(rng, 'B1')
        ans = logic.bot_answers(rng, 'B1', ['en_uz'] * 15, 7000, persona, acc)
        self.assertEqual(len(ans), 15)
        for ms, ok, pts in ans:
            if ms is not None:
                self.assertGreaterEqual(ms, 1150)
                self.assertLessEqual(ms, 7000)
            if not ok:
                self.assertEqual(pts, 0)


@override_settings(CACHES=LOCMEM)
class QuestionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_bank('B1')

    def setUp(self):
        cache.clear()

    def test_every_type_has_four_distinct_options_and_the_key(self):
        pool = services.level_pool('B1')
        rng = random.Random(1)
        happy = Word.objects.get(word='happy')
        apple = Word.objects.get(word='apple')
        for w, t in [(apple, 'en_uz'), (apple, 'uz_en'), (apple, 'listen'), (apple, 'cloze'),
                     (happy, 'syn'), (happy, 'ant'), (happy, 'cloze')]:
            q = logic.make_question(w, t, pool, rng)
            self.assertIsNotNone(q, (w.word, t))
            self.assertEqual(len(q['o']), 4)
            self.assertEqual(len({o.lower() for o in q['o']}), 4, q)
            answer = q['o'][q['k']]
            expected = {'en_uz': w.uz, 'listen': w.uz, 'uz_en': w.word, 'cloze': w.word, 'syn': 'glad', 'ant': 'sad'}[t]
            self.assertEqual(answer, expected, (t, q))
        cloze = logic.make_question(happy, 'cloze', pool, rng)
        self.assertIn('_____', cloze['p'])
        self.assertNotIn('happy', cloze['p'].lower())
        self.assertNotIn('sad', [o.lower() for o in cloze['o']])          # an antonym would fit the blank too

    def test_opposite_never_offers_a_second_opposite(self):
        pool = services.level_pool('B1')
        w = Word.objects.get(word='narrow')
        w.antonyms = ['wide', 'broad']
        for seed in range(20):
            q = logic.make_question(w, 'ant', pool, random.Random(seed))
            wrong = [o for i, o in enumerate(q['o']) if i != q['k']]
            self.assertFalse({'wide', 'broad'} & set(wrong), q)

    def test_cloze_hides_every_use_of_the_word(self):
        self.assertEqual(logic.cloze_prompt('friend', 'A friend in need is a friend indeed.'),
                         'A _____ in need is a _____ indeed.')
        self.assertIsNone(logic.cloze_prompt('friend', 'My friend is very friendly.'))     # "friendly" gives it away
        self.assertIsNone(logic.cloze_prompt('apple', 'I like pears.'))
        self.assertEqual(logic.cloze_prompt('look after', 'Please Look after the kids.'), 'Please _____ the kids.')
        w = Word.objects.get(word='window')
        w.example = 'The window was open, so I closed the window.'
        q = logic.make_question(w, 'cloze', services.level_pool('B1'), random.Random(3), services.bank_lex())
        self.assertEqual(q['p'], 'The _____ was open, so I closed the _____.')

    def test_wrong_options_are_never_a_second_right_answer(self):
        # quiet: synonym 'silent', antonym 'loud'
        Word.objects.filter(word='strong').update(uz='kuchli, sifat ma’nosi 3')    # shares a meaning with quiet
        Word.objects.filter(word='heavy').update(synonyms=['weighty', 'silent'])   # a synonym of the key
        Word.objects.filter(word='bright').update(antonyms=['dark', 'quiet'])      # calls quiet its opposite
        Word.objects.filter(word='gentle').update(synonyms=['kind', 'loud'])       # a synonym of quiet's opposite
        cache.clear()
        quiet = Word.objects.get(word='quiet')
        pool, lex = services.level_pool('B1'), services.bank_lex()
        banned = {'syn': {'strong', 'heavy'}, 'ant': {'bright', 'gentle'}, 'cloze': {'strong', 'bright'},
                  'uz_en': {'strong'}}
        for seed in range(25):
            for t, bad in banned.items():
                q = logic.make_question(quiet, t, pool, random.Random(seed), lex)
                self.assertIsNotNone(q, (t, seed))
                wrong = {o.lower() for i, o in enumerate(q['o']) if i != q['k']}
                self.assertFalse(wrong & bad, (t, q))

    def test_wrong_options_keep_the_part_of_speech(self):
        apple = Word.objects.get(word='apple')
        apple.distractors = ['happy', 'cheap']            # curated, but adjectives: only a last resort for a noun
        pool, lex = services.level_pool('B1'), services.bank_lex()
        for seed in range(10):
            for t in ('uz_en', 'cloze'):
                q = logic.make_question(apple, t, pool, random.Random(seed), lex)
                self.assertFalse({o.lower() for o in q['o']} & set(ADJS), (t, q))

    def test_build_questions_uses_each_word_once(self):
        words = services.level_words('B1')
        rng = random.Random(5)
        qs = logic.build_questions(logic.order_words(words, {}, timezone.now(), rng), 15, 'B1',
                                   list(logic.MIX['B1']), rng, services.level_pool('B1'))
        self.assertEqual(len(qs), 15)
        self.assertEqual(len({q['w'] for q in qs}), 15)
        self.assertIn(qs[0]['t'], logic.EASY_TYPES)

    def test_due_reviews_come_first(self):
        words = services.level_words('B1')
        now = timezone.now()
        due = {words[5].id: (2, now - timedelta(days=1)), words[9].id: (1, now - timedelta(hours=1)),
               words[3].id: (3, now + timedelta(days=3))}
        order = logic.order_words(words, due, now, random.Random(1))
        self.assertEqual([order[0].id, order[1].id], [words[5].id, words[9].id])
        self.assertEqual(order[-1].id, words[3].id)


class ConfigTests(TestCase):
    def test_validator(self):
        self.assertEqual(validate_config({'question_ms': 8000, 'ghost_share': 0.5}), {'question_ms': 8000, 'ghost_share': 0.5})
        self.assertEqual(validate_config({'levels': ['SAT', 'B1']}), {'levels': ['B1', 'SAT']})
        self.assertEqual(validate_config({'questions': None}), {'questions': None})
        for bad in ({'nope': 1}, {'question_ms': 100}, {'questions': 15.5}, {'levels': ['A1']}, {'types': []},
                    {'leaderboard': 'yes'}, {'ghost_share': 2}, []):
            with self.assertRaises(ConfigError, msg=bad):
                validate_config(bad)
        self.assertEqual(set(DEFAULTS), {'question_ms', 'grace_ms', 'questions', 'levels', 'types', 'ghost_share',
                                         'duel_hours', 'leaderboard'})


# ── the API ──────────────────────────────────────────────────────────────────

class RoundTests(WBTestCase):
    def test_no_key_leaves_the_server_before_the_answer(self):
        rnd = self.start(self.a)
        self.assertNotIn('answers', json.dumps(rnd['opponent']))
        self.assertNotIn('score', rnd['opponent'])
        api = self.client_for(self.a)
        r = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json').json()
        self.assertEqual(set(r), {'idx', 'n', 't', 'prompt', 'options', 'pos', 'question_ms', 'extra_ms', 'left_ms',
                                  'resumed', 'opp', 'score', 'streak', 'correct'})
        self.assertNotIn('"k"', json.dumps(r))
        self.assertEqual(len(r['options']), 4)
        detail = api.get(f'{BASE}/rounds/{rnd["id"]}/').json()      # the active round: no questions at all
        self.assertNotIn('items', detail)
        self.assertNotIn('options', json.dumps(detail))

    def test_answers_are_judged_on_the_server(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        q = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json').json()
        k = self.key_of(rnd['id'], q['idx'])
        Answer.objects.filter(round_id=rnd['id'], idx=0).update(served_at=timezone.now() - timedelta(seconds=2))
        wrong = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 0, 'choice': (k + 1) % 4}, format='json').json()
        self.assertFalse(wrong['correct'])
        self.assertEqual(wrong['key'], k)
        self.assertEqual(wrong['points'], 0)
        again = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 0, 'choice': k}, format='json').json()
        self.assertFalse(again['correct'])                          # a second try changes nothing
        api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        k1 = self.key_of(rnd['id'], 1)
        Answer.objects.filter(round_id=rnd['id'], idx=1).update(served_at=timezone.now() - timedelta(seconds=1))
        right = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 1, 'choice': k1}, format='json').json()
        self.assertTrue(right['correct'])
        self.assertGreaterEqual(right['points'], 140)
        self.assertEqual(right['score'], right['points'])
        bad = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 1, 'choice': 7}, format='json')
        self.assertEqual(bad.status_code, 400)
        unserved = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 5, 'choice': 0}, format='json')
        self.assertEqual(unserved.status_code, 409)
        other = self.client_for(self.b).post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        self.assertEqual(other.status_code, 404)

    def test_server_timing(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        rid = rnd['id']

        def serve_and_answer(idx, after_ms):
            """after_ms is counted from the end of the listen allowance, like the learner's visible clock."""
            q = api.post(f'{BASE}/rounds/{rid}/next/', format='json').json()
            self.assertEqual(q['idx'], idx)
            back = after_ms + q['extra_ms']
            Answer.objects.filter(round_id=rid, idx=idx).update(served_at=timezone.now() - timedelta(milliseconds=back))
            return api.post(f'{BASE}/rounds/{rid}/answer/', {'idx': idx, 'choice': self.key_of(rid, idx)},
                            format='json').json()

        a = serve_and_answer(0, 300)
        self.assertTrue(a['correct'])
        self.assertEqual(a['points'] if a['ms'] < 400 else 0, 0)  # under 400 ms (a listen one is never that fast)
        a = serve_and_answer(1, 8000)
        self.assertTrue(a['correct'])                             # inside the 1.5-s grace
        self.assertFalse(a['timeout'])
        self.assertEqual(a['bonus'], 0)
        a = serve_and_answer(2, 8600)
        self.assertTrue(a['timeout'])
        self.assertFalse(a['correct'])
        self.assertEqual(a['points'], 0)
        self.assertEqual(a['streak'], 0)

    def test_listen_questions_get_the_listening_allowance(self):
        rnd = self.start(self.a)
        rid = rnd['id']
        qset = Round.objects.get(pk=rid).qset
        idx = next(i for i, q in enumerate(qset.questions) if q['t'] == 'listen')
        api = self.client_for(self.a)
        for i in range(idx + 1):
            q = api.post(f'{BASE}/rounds/{rid}/next/', format='json').json()
            if i < idx:
                api.post(f'{BASE}/rounds/{rid}/answer/', {'idx': i, 'choice': None}, format='json')
        self.assertEqual(q['t'], 'listen')
        self.assertEqual(q['extra_ms'], 1500)
        Answer.objects.filter(round_id=rid, idx=idx).update(served_at=timezone.now() - timedelta(milliseconds=9800))
        a = api.post(f'{BASE}/rounds/{rid}/answer/', {'idx': idx, 'choice': self.key_of(rid, idx)}, format='json').json()
        self.assertTrue(a['correct'])                             # 9.8 s < 7 + 1.5 + 1.5

    def test_open_question_is_served_again_with_its_clock_running(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        q1 = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json').json()
        Answer.objects.filter(round_id=rnd['id'], idx=0).update(served_at=timezone.now() - timedelta(seconds=3))
        q2 = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json').json()
        self.assertEqual(q2['idx'], 0)
        self.assertTrue(q2['resumed'])
        self.assertLess(q2['left_ms'], q1['left_ms'] - 2500)
        Answer.objects.filter(round_id=rnd['id'], idx=0).update(served_at=timezone.now() - timedelta(seconds=12))
        q3 = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json').json()
        self.assertEqual(q3['idx'], 1)                            # the old one expired: a timeout
        self.assertTrue(Answer.objects.get(round_id=rnd['id'], idx=0).timeout)

    def test_resume_and_expiry(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        Answer.objects.filter(round_id=rnd['id'], idx=0).update(served_at=timezone.now() - timedelta(seconds=2))
        api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 0, 'choice': self.key_of(rnd['id'], 0)}, format='json')
        state = api.get(f'{BASE}/rounds/{rnd["id"]}/').json()       # a reload mid-round
        self.assertEqual((state['status'], state['cursor'], state['marks']), ('active', 1, [True]))
        self.assertEqual(state['opp_score'], Round.objects.get(pk=rnd['id']).opp['answers'][0][2])
        Round.objects.filter(pk=rnd['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        r = api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        self.assertEqual(r.status_code, 410)
        self.assertEqual(Round.objects.get(pk=rnd['id']).status, Round.ABANDONED)   # closed, and it stays closed
        self.assertEqual(Review.objects.filter(user=self.a).count(), 1)

    def test_question_and_answer_stay_cheap(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 0, 'choice': 0}, format='json')
        with CaptureQueriesContext(connection) as nq:
            api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
        with CaptureQueriesContext(connection) as aq:
            api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': 1, 'choice': 0}, format='json')
        self.assertLessEqual(len(nq), 6, [q['sql'][:80] for q in nq.captured_queries])
        self.assertLessEqual(len(aq), 6, [q['sql'][:80] for q in aq.captured_queries])

    def test_streak_multiplier_through_the_api(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        mults = []
        for i in range(5):
            api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
            Answer.objects.filter(round_id=rnd['id'], idx=i).update(served_at=timezone.now() - timedelta(milliseconds=900))
            a = api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': i, 'choice': self.key_of(rnd['id'], i)}, format='json').json()
            mults.append((a['mult'], a['streak']))
        self.assertEqual(mults, [(1.0, 1), (1.0, 2), (1.0, 3), (1.5, 4), (1.5, 5)])
        self.assertEqual(Round.objects.get(pk=rnd['id']).score, 150 * 3 + 225 * 2)

    def test_finish_writes_reviews_counts_once_and_is_idempotent(self):
        rnd = self.start(self.a)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.play_all(self.a, rnd['id'], right=True)
        self.assertEqual(res['status'], 'finished')
        self.assertTrue(res['complete'])
        self.assertTrue(res['ranked'])
        self.assertEqual(res['correct'], 15)
        self.assertEqual(len(res['items']), 15)
        self.assertTrue(all('example' in it and 'uz' in it for it in res['items']))
        self.assertEqual(res['srs'], {'strengthened': 15, 'new': 15, 'weak': 0})
        self.assertEqual(Review.objects.filter(user=self.a, skill='mean', kind='w').count(), 15)
        self.assertTrue(all(r.box == 1 for r in Review.objects.filter(user=self.a)))
        self.assertEqual(self.count_play.call_count, 1)
        self.count_play.assert_called_with(self.a, 'word-battle')
        again = self.client_for(self.a).post(f'{BASE}/rounds/{rnd["id"]}/finish/', format='json').json()
        self.assertEqual(again['score'], res['score'])
        self.assertEqual(self.count_play.call_count, 1)
        self.assertEqual(QSet.objects.get(pk=Round.objects.get(pk=rnd['id']).qset_id).plays, 1)
        self.assertIn(res['outcome'], ('win', 'lose', 'draw'))
        self.assertEqual(res['rank'], 1)
        self.assertTrue(res['new_best'])

    def test_misses_are_demoted_reviews(self):
        rnd = self.start(self.a)
        res = self.play_all(self.a, rnd['id'], right=False)
        self.assertEqual(res['correct'], 0)
        self.assertEqual(res['srs']['weak'], 15)
        r = Review.objects.filter(user=self.a).first()
        self.assertEqual((r.box, r.miss, r.last_verdict), (0, 1, 'miss'))
        self.assertLess(r.due_at, timezone.now() + timedelta(minutes=11))

    def test_one_active_round_and_abandon(self):
        first = self.start(self.a)
        api = self.client_for(self.a)
        api.post(f'{BASE}/rounds/{first["id"]}/next/', format='json')
        k = self.key_of(first['id'], 0)
        api.post(f'{BASE}/rounds/{first["id"]}/answer/', {'idx': 0, 'choice': k}, format='json')
        api.post(f'{BASE}/rounds/{first["id"]}/next/', format='json')     # served, never answered
        second = self.start(self.a)
        old = Round.objects.get(pk=first['id'])
        self.assertEqual(old.status, Round.ABANDONED)
        self.assertFalse(old.ranked)
        self.assertEqual(Review.objects.filter(user=self.a).count(), 1)      # only the answered one
        self.assertEqual(str(Round.objects.get(user=self.a, status=Round.ACTIVE).pk), second['id'])
        self.count_play.assert_not_called()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Round.objects.create(user=self.a, qset=old.qset, level='B1', expires_at=timezone.now())

    def test_a_stopped_round_compares_the_same_questions(self):
        rnd = self.start(self.a)
        api = self.client_for(self.a)
        for i in range(2):
            api.post(f'{BASE}/rounds/{rnd["id"]}/next/', format='json')
            Answer.objects.filter(round_id=rnd['id'], idx=i).update(served_at=timezone.now() - timedelta(seconds=2))
            api.post(f'{BASE}/rounds/{rnd["id"]}/answer/', {'idx': i, 'choice': self.key_of(rnd['id'], i)}, format='json')
        res = api.post(f'{BASE}/rounds/{rnd["id"]}/finish/', format='json').json()
        self.assertEqual(res['status'], 'abandoned')
        self.assertIsNone(res['outcome'])
        opp = Round.objects.get(pk=rnd['id']).opp['answers'][:2]
        self.assertEqual(res['opponent']['score'], sum(a[2] for a in opp))      # not the bot's full 15-question run

    def test_too_fast_rounds_are_flagged_and_unranked(self):
        rnd = self.start(self.a)
        res = self.play_all(self.a, rnd['id'], right=True, back_ms=150)
        self.assertEqual(res['flags'], ['too-fast'])
        self.assertFalse(res['ranked'])
        self.assertEqual(res['score'], 0)

    def test_leaderboard_is_weekly_per_level_and_ranked_only(self):
        self.play_all(self.a, self.start(self.a)['id'], right=True)
        self.play_all(self.b, self.start(self.b)['id'], right=False)
        lb = self.client_for(self.b).get(f'{BASE}/leaderboard/?level=B1').json()
        self.assertEqual([t['name'] for t in lb['top']], ['Anvar K.', 'Bonu A.'])
        self.assertEqual(lb['me']['rank'], 2)
        self.assertTrue(lb['top'][1]['is_me'])
        Round.objects.filter(user=self.a).update(finished_at=timezone.now() - timedelta(days=8))
        lb = self.client_for(self.b).get(f'{BASE}/leaderboard/?level=B1').json()
        self.assertEqual(lb['me']['rank'], 1)
        self.assertEqual(self.client_for(self.b).get(f'{BASE}/leaderboard/?level=C1').json()['top'], [])

    def test_home(self):
        d = self.client_for(self.a).get(f'{BASE}/home/').json()
        lv = {x['level']: x for x in d['levels']}
        self.assertEqual(lv['B1']['words'], 40)
        self.assertTrue(lv['B1']['open'])
        self.assertFalse(lv['C1']['open'])
        self.assertIsNone(d['active'])
        rnd = self.start(self.a)
        self.assertEqual(self.client_for(self.a).get(f'{BASE}/home/').json()['active']['id'], rnd['id'])
        bad = self.client_for(self.a).post(f'{BASE}/rounds/', {'level': 'C1'}, format='json')
        self.assertEqual(bad.status_code, 409)                    # no C1 words in this bank


class GhostTests(WBTestCase):
    def test_a_recorded_real_player_is_replayed_on_the_same_set(self):
        self.set_config(ghost_share=1)
        a_round = self.start(self.a)
        a_res = self.play_all(self.a, a_round['id'], right=True)
        self.assertEqual(a_round['opponent']['kind'], 'bot')     # nobody to replay yet
        self.assertEqual(a_round['opponent']['label'], 'Bot')
        b_round = self.start(self.b)
        self.assertEqual(b_round['opponent']['kind'], 'ghost')
        self.assertEqual(b_round['opponent']['label'], 'Yozib olingan')
        self.assertEqual(b_round['opponent']['name'], 'Anvar K.')
        rb = Round.objects.get(pk=b_round['id'])
        ra = Round.objects.get(pk=a_round['id'])
        self.assertEqual(rb.qset_id, ra.qset_id)
        self.assertEqual(rb.ghost_id, ra.pk)
        q = self.client_for(self.b).post(f'{BASE}/rounds/{b_round["id"]}/next/', format='json').json()
        first = ra.answers.get(idx=0)
        self.assertEqual(q['opp'], {'ms': first.ms, 'ok': True, 'pts': first.points})
        b_res = self.play_all(self.b, b_round['id'], right=False)
        self.assertEqual(b_res['opponent']['score'], a_res['score'])
        self.assertEqual(b_res['outcome'], 'lose')
        # A never meets their own set, and an unranked round is never replayed
        self.assertEqual(self.start(self.a)['opponent']['kind'], 'bot')

    def test_unranked_rounds_are_not_ghosts(self):
        self.set_config(ghost_share=1)
        self.play_all(self.a, self.start(self.a)['id'], right=True, back_ms=100)    # flagged too-fast
        self.assertEqual(self.start(self.b)['opponent']['kind'], 'bot')

    def test_ghost_share_zero_always_plays_a_bot(self):
        self.play_all(self.a, self.start(self.a)['id'], right=True)
        self.set_config(ghost_share=0)
        rnd = self.start(self.b)
        self.assertEqual(rnd['opponent']['kind'], 'bot')
        self.assertIn('aniqligi', rnd['opponent']['note'])


class DuelTests(WBTestCase):
    def test_lifecycle_create_accept_compare(self):
        api_a, api_b = self.client_for(self.a), self.client_for(self.b)
        d = api_a.post(f'{BASE}/duels/', {'level': 'B1'}, format='json')
        self.assertEqual(d.status_code, 201, d.content)
        d = d.json()
        code = d['code']
        self.assertEqual((d['status'], d['role'], d['can_play']), ('open', 'creator', True))
        guest = APIClient(**H).get(f'{BASE}/duels/{code}/').json()          # a guest sees status and names only
        self.assertEqual((guest['role'], guest['creator']['name']), ('guest', 'Anvar K.'))
        seen_b = api_b.get(f'{BASE}/duels/{code}/').json()
        self.assertEqual((seen_b['role'], seen_b['can_play']), ('guest', True))

        rb = api_b.post(f'{BASE}/duels/{code}/accept/', format='json')
        self.assertEqual(rb.status_code, 201, rb.content)
        rb = rb.json()
        self.assertEqual((rb['mode'], rb['opponent']['kind'], rb['duel']), ('duel', 'wait', code))
        taken = self.client_for(self.c).post(f'{BASE}/duels/{code}/accept/', format='json')
        self.assertEqual(taken.status_code, 409)
        resume = api_b.post(f'{BASE}/duels/{code}/accept/', format='json').json()
        self.assertEqual(resume['id'], rb['id'])                  # the open round, not a new one
        b_res = self.play_all(self.b, rb['id'], right=False)
        self.assertEqual(b_res['outcome'], 'pending')
        mid = api_a.get(f'{BASE}/duels/{code}/').json()
        self.assertEqual(mid['status'], 'playing')
        self.assertNotIn('score', mid['opponent'])                 # hidden until both have played

        ra = api_a.post(f'{BASE}/duels/{code}/accept/', format='json').json()
        self.assertEqual(ra['opponent']['kind'], 'duel')          # B's recorded run
        self.assertEqual(Round.objects.get(pk=ra['id']).qset_id, Round.objects.get(pk=rb['id']).qset_id)
        a_res = self.play_all(self.a, ra['id'], right=True)
        self.assertEqual(a_res['outcome'], 'win')
        done_a = api_a.get(f'{BASE}/duels/{code}/').json()
        done_b = api_b.get(f'{BASE}/duels/{code}/').json()
        self.assertEqual((done_a['status'], done_a['result'], done_a['winner']), ('done', 'me', 'creator'))
        self.assertEqual(done_b['result'], 'them')
        self.assertEqual(done_b['creator']['score'], a_res['score'])
        self.assertEqual(len(done_b['compare']), 15)
        later_b = api_b.get(f'{BASE}/rounds/{rb["id"]}/').json()  # B's result now knows the outcome
        self.assertEqual(later_b['outcome'], 'lose')
        self.assertEqual(api_a.post(f'{BASE}/duels/{code}/accept/', format='json').status_code, 409)   # played
        # B finished first and is on the weekly board; A finished second (B's result shows every key): not ranked
        self.assertTrue(Round.objects.get(pk=rb['id']).ranked)
        self.assertFalse(Round.objects.get(pk=ra['id']).ranked)
        self.assertFalse(a_res['ranked'])

    def test_my_duels_cost_the_same_for_two_or_five(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        api_a, api_b = self.client_for(self.a), self.client_for(self.b)
        LAST = []

        def add(n):
            for _ in range(n):
                code = api_a.post(f'{BASE}/duels/', {'level': 'B1'}, format='json').json()['code']
                self.assertEqual(api_b.post(f'{BASE}/duels/{code}/accept/', format='json').status_code, 201)

        def home_queries():
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(api_b.get(f'{BASE}/home/').status_code, 200)
            LAST[:] = [x["sql"][:150] for x in q.captured_queries]
            return len(q)

        add(2)
        home_queries()                                            # warm the per-level caches
        two = home_queries()
        add(3)
        self.assertEqual(len(api_b.get(f'{BASE}/home/').json()['duels']), 5)
        self.assertEqual(home_queries(), two, LAST)

    def test_expiry(self):
        api_a = self.client_for(self.a)
        code = api_a.post(f'{BASE}/duels/', {'level': 'B1'}, format='json').json()['code']
        Duel.objects.filter(code=code).update(expires_at=timezone.now() - timedelta(minutes=1))
        r = self.client_for(self.b).post(f'{BASE}/duels/{code}/accept/', format='json')
        self.assertEqual(r.status_code, 410)
        d = api_a.get(f'{BASE}/duels/{code}/').json()
        self.assertEqual((d['status'], d['can_play']), ('expired', False))

    def test_challenge_with_the_set_just_played(self):
        api_a = self.client_for(self.a)
        rnd = self.start(self.a)
        early = api_a.post(f'{BASE}/duels/', {'round': rnd['id']}, format='json')
        self.assertEqual(early.status_code, 409)                  # not finished yet
        self.play_all(self.a, rnd['id'], right=True)
        d = api_a.post(f'{BASE}/duels/', {'round': rnd['id']}, format='json').json()
        same = api_a.post(f'{BASE}/duels/', {'round': rnd['id']}, format='json').json()
        self.assertEqual(d['code'], same['code'])                 # one open link per round
        self.assertTrue(d['creator']['played'])
        self.assertEqual(api_a.post(f'{BASE}/duels/{d["code"]}/accept/', format='json').status_code, 409)
        rb = self.client_for(self.b).post(f'{BASE}/duels/{d["code"]}/accept/', format='json').json()
        self.assertEqual(rb['opponent']['kind'], 'duel')
        self.assertEqual(rb['opponent']['name'], 'Anvar K.')

    def test_unknown_code(self):
        self.assertEqual(self.client_for(self.a).get(f'{BASE}/duels/NOPE42/').status_code, 404)
        self.assertEqual(self.client_for(self.a).post(f'{BASE}/duels/NOPE42/accept/').status_code, 404)


class AdminTests(WBTestCase):
    def test_stats_and_config(self):
        self.play_all(self.a, self.start(self.a)['id'], right=True)
        self.assertEqual(self.client_for(self.a).get(f'{BASE}/admin/stats/').status_code, 403)
        api = self.client_for(self.staff)
        s = api.get(f'{BASE}/admin/stats/?days=7').json()
        self.assertEqual(s['totals']['rounds'], 1)
        self.assertEqual(s['totals']['accuracy'], 100)
        self.assertEqual(len(s['series']), 7)
        self.assertEqual(s['series'][-1]['rounds'], 1)
        self.assertEqual(sum(t['answers'] for t in s['types']), 15)
        cfg = api.patch('/api/games/stats/admin/games/word-battle/', {'config': {'question_ms': 9000}}, format='json')
        self.assertEqual(cfg.status_code, 200, cfg.content)
        self.assertEqual(cfg.json()['effective']['question_ms'], 9000)
        bad = api.patch('/api/games/stats/admin/games/word-battle/', {'config': {'question_ms': 1}}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.start(self.b)['question_ms'], 9000)
