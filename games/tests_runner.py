"""
TOBY RUN backend (RUNNER_PLAN §B8.3, §B11): deck, finish, practice, me and the score parity fixture.

    python manage.py test games.tests_runner
"""
import json
import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from gamestats.models import Game, GameDay
from gamestats.configs import bust_config_cache
from vocabulary.models import Phrase, Review, Word

from . import runner_logic as rl
from . import runner_views
from .models import VoiceGameProgress, VoiceGameRun

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'runner-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'runner_score_cases.json')

PICS = ['apple', 'bus', 'ticket', 'tree', 'dog', 'bike', 'book', 'clock', 'bench', 'money', 'cat', 'duck']


class ScoreParityTests(TestCase):
    def test_fixture_matches_python(self):
        with open(FIXTURE, encoding='utf-8') as f:
            cases = json.load(f)['cases']
        self.assertGreaterEqual(len(cases), 20)
        for c in cases:
            r = rl.score(c['outcomes'], **c['args'])
            self.assertEqual({'points': r['points'], 'passes': r['passes'], 'score': r['score']}, c['expected'], c['name'])

    def test_hand_computed(self):
        # A1, 1000 m, one balloon at 1320 ms: W = 3.9 × 1.15 = 4.485 s → bonus 41 → (1000 + 141) × 1.1 = 1255
        r = rl.score([{'m': 'b', 'v': 'ok', 'tries': 1, 'ms': 1320, 'n': 1}], level='A1', distance_m=1000)
        self.assertEqual((r['points'], r['passes'], r['score']), (141, 1, 1255))
        self.assertEqual(rl.rnd(2.5), 3)
        self.assertEqual(rl.rnd(5.5), 6)

    def test_windows(self):
        self.assertAlmostEqual(rl.word_window(1, 'A1'), 4.485)
        self.assertAlmostEqual(rl.word_window(1, 'B1'), 3.9)
        self.assertAlmostEqual(rl.word_window(1, 'C1'), 3.51)
        self.assertEqual(rl.word_window(1, 'C1', 0.8), 3.5)


@override_settings(CACHES=LOCMEM)
class RunnerApiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.u1 = User.objects.create(username='rn1', email='rn1@example.test', first_name='Lola', last_name='Karimova')
        cls.u2 = User.objects.create(username='rn2', email='rn2@example.test', first_name='Bek', last_name='Aliyev')
        words = []
        for i in range(36):
            lv = 'A1' if i < 24 else 'A2'
            words.append(Word(word=f'word{chr(97 + i // 26)}{chr(97 + i % 26)}', uz=f'so‘z {i}', pos='noun', level=lv,
                              topic='metro' if i % 3 == 0 else 'bozor' if i % 3 == 1 else 'park',
                              picture=PICS[i % len(PICS)] if i % 2 == 0 else '', status='published', speak=True))
        words.append(Word(word='hidden', uz='yashirin', pos='noun', level='A1', topic='metro', status='draft'))
        words.append(Word(word='mute', uz='jim', pos='noun', level='A1', topic='metro', status='published', speak=False))
        words.append(Word(word='later', uz='keyin', pos='noun', level='B1', topic='metro', status='published'))
        Word.objects.bulk_create(words)
        Word.objects.filter(word='wordaa').update(speak_risk=True)
        phrases = []
        for i in range(8):
            lv = 'A1' if i < 4 else 'A2'
            text = f'Where is the stop number {i}?'
            phrases.append(Phrase(kind='echo', text=text, uz='Bekat qayerda?', level=lv, topic='metro', voice='grandma',
                                  key=f'echo|{lv}|{text.lower()}', status='published'))
        phrases.append(Phrase(kind='answer', prompt='Where are you going?', text="I'm going to the bazaar.",
                              accept=[['going'], ['go', 'to']], min_words=3, uz='Qayerga ketyapsiz?', level='A2',
                              topic='metro', voice='driver', key='answer|A2|where are you going', status='published'))
        Phrase.objects.bulk_create(phrases)
        cls.w = {w.word: w for w in Word.objects.all()}

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def api(self, user):
        c = APIClient(**H)
        c.force_authenticate(user)
        return c

    def get_deck(self, user, level='A1', topic='all'):
        r = self.api(user).get(f'/api/games/runner/deck/?level={level}&topic={topic}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()


class DeckTests(RunnerApiBase):
    def test_deck_shape_and_filters(self):
        d = self.get_deck(self.u1, 'A1')
        self.assertEqual(d['level'], 'A1')
        self.assertTrue(d['deck'])
        self.assertIn('clips_left', d)
        self.assertEqual(d['config']['window_scale'], 1.0)
        words = [it for it in d['items'] if it['k'] == 'w']
        texts = {it['text'] for it in words}
        self.assertNotIn('hidden', texts)            # drafts are never served
        self.assertNotIn('mute', texts)              # speak=False is not a speaking target
        self.assertNotIn('later', texts)             # B1 is above A1
        self.assertTrue(all(self.w[t].level == 'A1' for t in texts))      # A1 has no level below
        self.assertEqual(len(words), 24)             # small bank: everything it has
        self.assertTrue(all(it['pt'] == 'hear' for it in words))           # all new → heard first
        phrases = [it for it in d['items'] if it['k'] == 'p']
        self.assertTrue(phrases and all(p['kind'] == 'echo' and p['level'] == 'A1' if 'level' in p else True for p in phrases))
        # A2 mixes in A1 (one level below)
        d2 = self.get_deck(self.u1, 'A2')
        lv = {self.w[it['text']].level for it in d2['items'] if it['k'] == 'w'}
        self.assertEqual(lv, {'A1', 'A2'})
        self.assertTrue(any(it['kind'] == 'answer' for it in d2['items']))

    def test_topic_first(self):
        d = self.get_deck(self.u1, 'A2', 'park')
        words = [it for it in d['items'] if it['k'] == 'w']
        first_a2 = [self.w[it['text']] for it in words if self.w[it['text']].level == 'A2'][:3]
        self.assertTrue(all(w.topic == 'park' for w in first_a2))
        self.assertEqual(self.get_deck(self.u1, 'A2', 'nope')['topic'], 'all')

    def test_bad_level(self):
        self.assertEqual(self.api(self.u1).get('/api/games/runner/deck/?level=Z9').status_code, 400)
        Game.objects.update_or_create(slug='runner', defaults={'title': 'TOBY RUN', 'config': {'levels': ['A1']}})
        bust_config_cache('runner')
        self.assertEqual(self.api(self.u1).get('/api/games/runner/deck/?level=A2').status_code, 400)

    def test_due_share_bridge_and_prompt_types(self):
        now = timezone.now()
        a1 = [w for w in self.w.values() if w.level == 'A1' and w.status == 'published' and w.speak]
        rows = []
        # 6 due say reviews (box 1 → choice at A1), 3 not due, 2 known by meaning only (bridge)
        for i, w in enumerate(a1[:6]):
            rows.append(Review(user=self.u1, kind='w', item_id=w.id, skill='say', box=1, due_at=now - timedelta(hours=i + 1)))
        for w in a1[6:9]:
            rows.append(Review(user=self.u1, kind='w', item_id=w.id, skill='say', box=3, due_at=now + timedelta(days=5)))
        for w in a1[9:11]:
            rows.append(Review(user=self.u1, kind='w', item_id=w.id, skill='mean', box=2, due_at=now + timedelta(days=2)))
        Review.objects.bulk_create(rows)
        d = self.get_deck(self.u1, 'A1')
        by_id = {it['id']: it for it in d['items'] if it['k'] == 'w'}
        due_ids = [w.id for w in a1[:6]]
        for wid in due_ids:
            self.assertIn(wid, by_id)
            self.assertIn(by_id[wid]['pt'], ('choice', 'hear'))
            self.assertEqual(by_id[wid]['box'], 1)
        choice = [it for it in by_id.values() if it['pt'] == 'choice']
        self.assertTrue(choice)
        for it in choice:
            self.assertEqual(len(it['choices']), 3)
            self.assertIn(it['text'], it['choices'])
        for w in a1[9:11]:                      # the Word Battle → Runner bridge: recall, not hear
            self.assertIn(by_id[w.id]['pt'], ('picture', 'uz'))
        # the oldest due item comes first among the words
        words = [it for it in d['items'] if it['k'] == 'w']
        self.assertEqual(words[0]['id'], due_ids[-1])

    def test_speak_risk_only_choice_or_hear(self):
        risky = self.w['wordaa']
        d = self.get_deck(self.u1, 'A1')
        it = next(i for i in d['items'] if i['k'] == 'w' and i['id'] == risky.id)
        self.assertEqual(it['pt'], 'hear')
        Review.objects.create(user=self.u1, kind='w', item_id=risky.id, skill='say', box=4, due_at=timezone.now())
        d = self.get_deck(self.u1, 'A1')
        it = next(i for i in d['items'] if i['k'] == 'w' and i['id'] == risky.id)
        self.assertEqual(it['pt'], 'choice')

    def test_prompt_type_table(self):
        w = {'id': 1, 'word': 'big', 'uz': 'katta', 'picture': 'tree', 'antonyms': ['small'], 'synonyms': ['large'],
             'definition': 'of great size'}
        self.assertEqual(rl.pick_prompt_type('A2', w, None), 'hear')
        self.assertEqual(rl.pick_prompt_type('A2', w, 0), 'choice')
        self.assertEqual(rl.pick_prompt_type('A2', w, 2), 'picture')
        self.assertEqual(rl.pick_prompt_type('A2', {**w, 'picture': ''}, 2), 'uz')
        self.assertEqual(rl.pick_prompt_type('A2', w, 5), 'uz')
        self.assertEqual(rl.pick_prompt_type('B1', w, 1), 'uz')
        self.assertIn(rl.pick_prompt_type('B2', w, 4), ('opposite', 'synonym', 'definition'))
        self.assertEqual(rl.pick_prompt_type('B1', {**w, 'antonyms': [], 'synonyms': []}, 4), 'uz')

    def test_token_round_trip(self):
        d = self.get_deck(self.u1, 'A2')
        tok = signing.loads(d['deck'], salt='runner.deck')
        self.assertEqual(tok['u'], self.u1.id)
        self.assertEqual(tok['lv'], 'A2')
        self.assertEqual(tok['s'], d['seed'])
        self.assertEqual(sorted(tok['w']), sorted(it['id'] for it in d['items'] if it['k'] == 'w'))
        self.assertEqual(len(tok['p']), len(tok['pk']))

    def test_queries_on_a_warm_cache(self):
        self.get_deck(self.u1, 'A1')                 # warms candidates + config
        c = self.api(self.u1)
        with CaptureQueriesContext(connection) as ctx:
            r = c.get('/api/games/runner/deck/?level=A1')
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 4, [q['sql'][:90] for q in ctx.captured_queries])


class FinishTests(RunnerApiBase):
    def body(self, d, outcomes, **kw):
        b = {'deck': d['deck'], 'ref': kw.pop('ref', 'r' + str(len(outcomes)) + 'x' * 8), 'mode': 'voice', 'stt': 'browser',
             'distance_m': 1500, 'duration_s': 180, 'coins': 200, 'revives': 0, 'stations': 2, 'clips': 0,
             'outcomes': outcomes}
        b.update(kw)
        return b

    def outcomes_for(self, d, n=6, v='ok', ms=1200):
        words = [it for it in d['items'] if it['k'] == 'w'][:n]
        return [{'k': 'w', 'id': it['id'], 'kind': 'word', 'pt': it['pt'], 'v': v, 'tries': 1, 'ms': ms + 37 * i,
                 'heard': it['text'], 'm': 'b'} for i, it in enumerate(words)]

    def post(self, user, body):
        return self.api(user).post('/api/games/runner/finish/', body, format='json')

    def test_finish_saves_run_reviews_and_counts_play(self):
        d = self.get_deck(self.u1, 'A1')
        outs = self.outcomes_for(d, 6)
        expected = rl.score([{**o, 'n': 1} for o in outs], level='A1', distance_m=1500)['score']
        with self.captureOnCommitCallbacks(execute=True):
            r = self.post(self.u1, self.body(d, outs, score_client=expected))
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j['score'], expected)
        self.assertTrue(j['ranked'])
        self.assertEqual(j['flags'], [])
        self.assertEqual(j['srs']['new'], 6)
        self.assertEqual(j['srs']['strengthened'], 0)        # a 'hear' pass never promotes
        run = VoiceGameRun.objects.get(user=self.u1, slug='runner')
        self.assertEqual((run.score, run.lines_said, run.level, run.duration_sec, run.ranked), (expected, 6, 'A1', 180, True))
        self.assertEqual(run.stars, min(3, 6 // 5))
        self.assertEqual(run.accuracy, 1.0)
        self.assertEqual(run.meta['mode'], 'voice')
        self.assertEqual(run.meta['said'], 6)
        self.assertEqual(run.meta['resp']['score'], expected)
        self.assertEqual(Review.objects.filter(user=self.u1, skill='say').count(), 6)
        p = VoiceGameProgress.objects.get(user=self.u1, slug='runner')
        self.assertEqual((p.plays, p.best_score), (1, expected))
        self.assertEqual(GameDay.objects.get(user=self.u1, slug='runner').plays, 1)

    def test_bad_tokens(self):
        d = self.get_deck(self.u1, 'A1')
        outs = self.outcomes_for(d, 2)
        self.assertEqual(self.post(self.u1, self.body({'deck': 'nonsense'}, outs)).status_code, 400)
        self.assertEqual(self.post(self.u2, self.body(d, outs)).status_code, 400)            # someone else's deck
        with mock.patch('django.core.signing.time.time', return_value=signing.time.time() + 4 * 3600):
            self.assertEqual(self.post(self.u1, self.body(d, outs)).status_code, 400)        # expired
        self.assertEqual(self.post(self.u1, self.body(d, outs, duration_s=5)).status_code, 400)
        self.assertEqual(self.post(self.u1, self.body(d, outs, duration_s=4000)).status_code, 400)
        self.assertEqual(self.post(self.u1, self.body(d, outs * 61)).status_code, 400)       # > 120 outcomes
        big = self.body(d, outs, pad='x' * (25 * 1024))
        self.assertEqual(self.post(self.u1, big).status_code, 400)
        self.assertFalse(VoiceGameRun.objects.exists())

    def test_foreign_and_local_items_dropped(self):
        d = self.get_deck(self.u1, 'A1')
        outs = self.outcomes_for(d, 2)
        outs += [{'k': 'w', 'id': self.w['later'].id, 'v': 'ok', 'tries': 1, 'ms': 900, 'm': 'b'},
                 {'k': 'local', 'id': 3, 'v': 'ok', 'tries': 1},
                 {'k': 'w', 'id': 'x', 'v': 'ok'}, 'junk']
        r = self.post(self.u1, self.body(d, outs))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(VoiceGameRun.objects.get().meta['said'], 2)
        self.assertFalse(Review.objects.filter(item_id=self.w['later'].id).exists())

    def test_repeated_ref_is_idempotent(self):
        d = self.get_deck(self.u1, 'A1')
        body = self.body(d, self.outcomes_for(d, 4), ref='3f9c0d6a5b2e4c1f8a7d6e5f4c3b2a10')
        r1 = self.post(self.u1, body)
        r2 = self.post(self.u1, body)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(VoiceGameRun.objects.filter(user=self.u1).count(), 1)
        self.assertEqual(VoiceGameProgress.objects.get(user=self.u1, slug='runner').plays, 1)
        self.assertEqual(Review.objects.get(user=self.u1, item_id=body['outcomes'][0]['id']).seen, 1)

    def assert_flag(self, flag, body):
        before = Review.objects.count()
        r = self.post(self.u1, body)
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertIn(flag, j['flags'])
        self.assertFalse(j['ranked'])
        self.assertEqual(Review.objects.count(), before)          # no review update
        run = VoiceGameRun.objects.get(ref=body['ref'])
        self.assertFalse(run.ranked)
        self.assertIn(flag, run.meta['flags'])

    def test_plausibility_flags(self):
        d = self.get_deck(self.u1, 'A1')
        outs = self.outcomes_for(d, 6)
        self.assert_flag('distance', self.body(d, outs, ref='dist1', distance_m=13 * 60 * 1.05 + 31, duration_s=60))
        many = self.outcomes_for(d, 20) + self.outcomes_for(d, 20)
        self.assert_flag('too-many', self.body(d, many, ref='many1', duration_s=40, distance_m=300))
        fast = self.outcomes_for(d, 6, ms=200)
        self.assert_flag('too-fast', self.body(d, fast, ref='fast1'))
        self.assert_flag('score-mismatch', self.body(d, outs, ref='mism1', score_client=10 ** 6))

    def test_listen_and_card_are_unranked(self):
        d = self.get_deck(self.u1, 'A1')
        outs = self.outcomes_for(d, 3)
        r = self.post(self.u1, self.body(d, outs, ref='listen1', mode='listen'))
        self.assertFalse(r.json()['ranked'])
        self.assertEqual(r.json()['flags'], [])
        self.assertEqual(Review.objects.filter(user=self.u1, skill='mean').count(), 3)       # listen → meaning
        self.assertFalse(Review.objects.filter(user=self.u1, skill='say').exists())
        r = self.post(self.u1, self.body(d, outs, ref='card1', mode='card'))
        self.assertFalse(r.json()['ranked'])
        self.assertEqual(Review.objects.filter(user=self.u1, skill='say').count(), 3)        # card → full say credit
        board = self.api(self.u1).get('/api/games/voice/runner/leaderboard/').json()
        self.assertEqual(board['top'], [])                       # unranked runs stay off the board
        self.post(self.u1, self.body(d, outs, ref='voice1'))
        board = self.api(self.u1).get('/api/games/voice/runner/leaderboard/').json()
        self.assertEqual(len(board['top']), 1)

    def test_srs_promotion_and_weak(self):
        d = self.get_deck(self.u1, 'A1')
        words = [it for it in d['items'] if it['k'] == 'w'][:3]
        now = timezone.now()
        Review.objects.bulk_create([Review(user=self.u1, kind='w', item_id=it['id'], skill='say', box=2,
                                           due_at=now - timedelta(days=1)) for it in words])
        outs = [{'k': 'w', 'id': words[0]['id'], 'pt': 'picture', 'v': 'ok', 'tries': 1, 'ms': 1500, 'm': 'b'},
                {'k': 'w', 'id': words[1]['id'], 'pt': 'picture', 'v': 'miss', 'tries': 1, 'heard': 'wery', 'm': 'b'},
                {'k': 'w', 'id': words[1]['id'], 'pt': 'picture', 'v': 'ok', 'tries': 2, 'm': 'b'},
                {'k': 'w', 'id': words[2]['id'], 'pt': 'picture', 'v': 'skip', 'tries': 1, 'm': 'b'}]
        j = self.post(self.u1, self.body(d, outs, ref='srs1')).json()
        self.assertEqual(j['srs']['strengthened'], 1)
        r0 = Review.objects.get(user=self.u1, item_id=words[0]['id'])
        r1 = Review.objects.get(user=self.u1, item_id=words[1]['id'])
        r2 = Review.objects.get(user=self.u1, item_id=words[2]['id'])
        self.assertEqual((r0.box, r0.first_ok, r0.best_ms), (3, 1, 1500))
        self.assertEqual(r1.box, 0)
        self.assertEqual(r1.last_heard, 'wery')
        self.assertEqual((r2.box, r2.seen), (2, 0))             # skip changes nothing

    def test_query_budget(self):
        d = self.get_deck(self.u1, 'A1')
        self.post(self.u1, self.body(d, self.outcomes_for(d, 2), ref='warm1'))       # progress row exists now
        body = self.body(d, self.outcomes_for(d, 8), ref='budget1')
        c = self.api(self.u1)
        with CaptureQueriesContext(connection) as ctx:
            r = c.post('/api/games/runner/finish/', body, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 10, [q['sql'][:80] for q in ctx.captured_queries])

    def test_count_play_once(self):
        d = self.get_deck(self.u1, 'A1')
        body = self.body(d, self.outcomes_for(d, 2), ref='once1')
        with mock.patch.object(runner_views, 'count_play') as cp:
            with self.captureOnCommitCallbacks(execute=True):
                self.post(self.u1, body)
            with self.captureOnCommitCallbacks(execute=True):
                self.post(self.u1, body)                          # a retry of the same run
        self.assertEqual(cp.call_count, 1)

    def test_bekat_and_revive_moments(self):
        d = self.get_deck(self.u1, 'A2')
        echo = next(it for it in d['items'] if it['k'] == 'p' and it['kind'] == 'echo')
        word = next(it for it in d['items'] if it['k'] == 'w')
        outs = [{'k': 'p', 'id': echo['id'], 'v': 'ok', 'tries': 1, 'm': 's', 'score': 0.95},
                {'k': 'w', 'id': word['id'], 'v': 'ok', 'tries': 1, 'ms': 900, 'm': 'r'},
                {'k': 'p', 'id': echo['id'], 'v': 'ok', 'tries': 1, 'm': 'b'}]          # bad moment → Bekat
        j = self.post(self.u1, self.body(d, outs, ref='bekat1', distance_m=1000)).json()
        self.assertEqual(j['points'], 300)
        self.assertEqual(j['passes'], 2)


class PracticeAndMeTests(RunnerApiBase):
    def test_practice_moves_due_not_box(self):
        d = self.get_deck(self.u1, 'A1')
        it = next(i for i in d['items'] if i['k'] == 'w')
        now = timezone.now()
        Review.objects.create(user=self.u1, kind='w', item_id=it['id'], skill='say', box=2, due_at=now + timedelta(minutes=10))
        r = self.api(self.u1).post('/api/games/runner/practice/', {
            'deck': d['deck'], 'outcomes': [{'k': 'w', 'id': it['id'], 'v': 'ok', 'heard': it['text'], 'ms': 800},
                                            {'k': 'w', 'id': 999999, 'v': 'ok'}]}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['fixed'], 1)
        rv = Review.objects.get(user=self.u1, item_id=it['id'])
        self.assertEqual(rv.box, 2)
        self.assertGreater(rv.due_at, now + timedelta(hours=23))
        self.assertEqual(self.api(self.u2).post('/api/games/runner/practice/', {'deck': d['deck'], 'outcomes': []},
                                                format='json').status_code, 400)

    def test_me(self):
        d = self.get_deck(self.u1, 'A1')
        words = [i for i in d['items'] if i['k'] == 'w'][:4]
        Review.objects.bulk_create([Review(user=self.u1, kind='w', item_id=w['id'], skill='say', box=3 if n < 2 else 1,
                                           seen=2, due_at=timezone.now()) for n, w in enumerate(words)])
        c = self.api(self.u1)
        r = c.get('/api/games/runner/me/?level=A1')
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual(j['level'], 'A1')
        self.assertEqual(sum(t['total'] for t in j['topics']), 24 + 4)
        self.assertEqual(sum(t['said'] for t in j['topics']), 4)
        self.assertEqual(sum(t['mastered'] for t in j['topics']), 2)
        self.assertEqual(j['week']['mastered_total'], 2)
        with CaptureQueriesContext(connection) as ctx:
            c.get('/api/games/runner/me/?level=A1')
        self.assertEqual(len(ctx.captured_queries), 0)            # cached
        self.assertEqual(c.get('/api/games/runner/me/?level=Q').status_code, 400)


class BoardTests(RunnerApiBase):
    """§B7 Leaderboard: this week's ranked voice runs; by=score (best) and by=said (sum of lines)."""

    def mkrun(self, user, score, lines, ranked=True, days_ago=0, **kw):
        r = VoiceGameRun.objects.create(user=user, slug='runner', score=score, lines_said=lines, ranked=ranked,
                                        duration_sec=120, level='A1', meta=kw.get('meta', {}))
        if days_ago:
            VoiceGameRun.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return r

    def test_score_and_said_boards(self):
        self.mkrun(self.u1, 900, 10)
        self.mkrun(self.u1, 1200, 4)
        self.mkrun(self.u2, 1000, 30)
        self.mkrun(self.u2, 5000, 99, ranked=False)              # a listen / flagged run: never on the board
        self.mkrun(self.u1, 9000, 99, days_ago=9)                 # last week
        j = self.api(self.u2).get('/api/games/runner/board/').json()
        self.assertEqual(j['by'], 'score')
        self.assertEqual([(r['rank'], r['name'], r['value'], r['is_me']) for r in j['top']],
                         [(1, 'Lola K.', 1200, False), (2, 'Bek A.', 1000, True)])
        self.assertEqual(j['me'], {'value': 1000, 'rank': 2})
        self.assertIsNone(j['players'])                         # shown only from 30 players up
        j = self.api(self.u1).get('/api/games/runner/board/?by=said').json()
        self.assertEqual([(r['rank'], r['value']) for r in j['top']], [(1, 30), (2, 14)])
        self.assertEqual(j['me'], {'value': 14, 'rank': 2})

    def test_ties_share_a_rank_and_no_runs(self):
        self.mkrun(self.u1, 700, 5)
        self.mkrun(self.u2, 700, 5)
        j = self.api(self.u1).get('/api/games/runner/board/').json()
        self.assertEqual([r['rank'] for r in j['top']], [1, 1])
        User = get_user_model()
        u3 = User.objects.create(username='rn3', email='rn3@example.test', first_name='Ali')
        self.assertEqual(self.api(u3).get('/api/games/runner/board/').json()['me'], {'value': 0, 'rank': None})


class AdminStatsTests(RunnerApiBase):
    """§B8.6 / §B8.3: GET admin/stats/ for the Games → Runner admin page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.staff = User.objects.create(username='rnstaff', email='rnstaff@example.test', is_staff=True)

    def test_staff_only(self):
        self.assertEqual(self.api(self.u1).get('/api/games/runner/admin/stats/').status_code, 403)

    def test_numbers(self):
        mk = VoiceGameRun.objects.create
        mk(user=self.u1, slug='runner', score=900, lines_said=12, ranked=True, duration_sec=180, level='A1',
           meta={'mode': 'voice', 'stt': 'browser', 'said': 16, 'first': 10, 'by_kind': {'word': [12, 9], 'echo': [4, 1]},
                 'ms_med': 1200, 'flags': []})
        mk(user=self.u2, slug='runner', score=300, lines_said=6, ranked=False, duration_sec=120, level='A2',
           meta={'mode': 'voice', 'stt': 'server', 'said': 8, 'first': 2, 'by_kind': {'word': [8, 2]}, 'ms_med': 3100,
                 'flags': ['too-fast']})
        mk(user=self.u2, slug='runner', score=100, lines_said=4, ranked=False, duration_sec=60, level='A2',
           meta={'mode': 'listen', 'stt': 'browser', 'said': 4, 'first': 0, 'flags': []})
        Word.objects.filter(word='wordab').update(say_seen=40, say_ok=10, say_heard=['wordap', 'word'])
        Word.objects.filter(word='wordac').update(say_seen=25, say_ok=20)
        Word.objects.filter(word='wordad').update(say_seen=5, say_ok=0)          # too few to judge
        GameDay.objects.create(user=self.u1, slug='runner', date=timezone.localdate() - timedelta(days=2), plays=1)
        GameDay.objects.create(user=self.u1, slug='runner', date=timezone.localdate() - timedelta(days=1), plays=1)
        GameDay.objects.create(user=self.u2, slug='runner', date=timezone.localdate() - timedelta(days=3), plays=1)
        r = self.api(self.staff).get('/api/games/runner/admin/stats/?days=7')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        t = j['totals']
        self.assertEqual((t['runs'], t['players'], t['ranked'], t['unranked']), (3, 2, 1, 2))
        self.assertEqual(t['avg_duration_s'], 120)
        self.assertEqual(t['utterances_per_run'], round(28 / 3, 1))
        self.assertEqual(t['first_try_rate'], round(12 / 28, 3))
        self.assertEqual(len(j['series']), 7)
        self.assertEqual(j['series'][-1]['runs'], 3)
        self.assertEqual(j['by_kind']['word'], {'said': 20, 'first': 11, 'rate': 0.55})
        self.assertEqual(j['by_level']['A2']['runs'], 2)
        self.assertEqual(j['share'], {'stt': {'browser': 2, 'server': 1}, 'mode': {'voice': 2, 'listen': 1, 'card': 0}})
        self.assertEqual(j['median_ms'], {'browser': 1200, 'server': 3100})
        self.assertEqual(j['unranked_reasons'], {'too-fast': 1, 'listen': 1})
        self.assertEqual([h['text'] for h in j['hardest']], ['wordab', 'wordac'])
        self.assertEqual(j['hardest'][0]['heard'], ['wordap', 'word'])
        self.assertEqual(j['hardest'][0]['rate'], 0.25)
        self.assertEqual(j['retention'], {'cohort': 2, 'd1': 0.5, 'd7': 0.5})
        self.assertIn('speed_scale', j['config'])
        # cached for 10 minutes; ?fresh=1 recomputes
        VoiceGameRun.objects.filter(slug='runner').delete()
        self.assertEqual(self.api(self.staff).get('/api/games/runner/admin/stats/?days=7').json()['totals']['runs'], 3)
        self.assertEqual(self.api(self.staff).get('/api/games/runner/admin/stats/?days=7&fresh=1').json()['totals']['runs'], 0)

    def test_finish_stores_the_median_ms(self):
        d = self.get_deck(self.u1, 'A1')
        words = [it for it in d['items'] if it['k'] == 'w'][:3]
        outs = [{'k': 'w', 'id': it['id'], 'kind': 'word', 'pt': it['pt'], 'v': 'ok', 'tries': 1, 'ms': ms, 'heard': it['text'], 'm': 'b'}
                for it, ms in zip(words, (900, 1500, 2400))]
        r = self.api(self.u1).post('/api/games/runner/finish/', {
            'deck': d['deck'], 'ref': 'msmedianref01', 'mode': 'voice', 'stt': 'browser', 'distance_m': 600,
            'duration_s': 90, 'coins': 30, 'revives': 0, 'stations': 0, 'clips': 0, 'outcomes': outs}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(VoiceGameRun.objects.get(ref='msmedianref01').meta['ms_med'], 1500)
