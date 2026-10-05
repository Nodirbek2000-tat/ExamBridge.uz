"""
Word bank (P0 contract): import / export / items / summary / warm, srs.apply, distractors, roll-up.

    python manage.py test vocabulary
"""
import copy
import json
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import bank, srs
from .models import Phrase, Review, Word
from .tasks import rollup

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'vocab-tests'}}
H = dict(HTTP_USER_AGENT='Mozilla/5.0 test', HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
IMPORT = '/api/games/words/admin/import/'
TZ = ZoneInfo('Asia/Tashkent')

SAMPLE = {
    'version': 1,
    'source': 'test-v1',
    'defaults': {'level': 'A1', 'topic': 'metro', 'status': 'published'},
    'words': [
        {'word': 'ticket', 'uz': 'chipta', 'pos': 'noun', 'picture': 'ticket',
         'definition': 'a small paper that shows you paid to travel', 'example': 'I need a ticket for the metro.',
         'say_also': ['tickets'], 'distractors': ['chair', 'window', 'coat']},
        {'word': 'expensive', 'uz': 'qimmat', 'pos': 'adj', 'level': 'A2', 'topic': 'bozor',
         'antonyms': ['cheap'], 'synonyms': ['costly'], 'example': 'This melon is very expensive.'},
        {'word': 'look for', 'uz': 'qidirmoq', 'pos': 'phrase', 'level': 'B1', 'topic': 'home',
         'example': "I'm looking for my keys.", 'say_also': ['looking for', 'looks for']},
        {'word': 'the', 'uz': '', 'pos': 'other', 'speak': False},
        {'word': 'train', 'uz': 'poyezd', 'pos': 'noun', 'status': 'draft', 'tags': ['kids']},
    ],
    'phrases': [
        {'kind': 'echo', 'text': 'Excuse me, where is the exit?', 'uz': 'Kechirasiz, chiqish qayerda?',
         'level': 'A2', 'voice': 'grandma'},
        {'kind': 'answer', 'prompt': 'Where are you going?', 'uz': 'Qayerga ketyapsiz?',
         'text': "I'm going to the bazaar.", 'accept': [['going'], ['go', 'to']], 'min_words': 3,
         'level': 'B1', 'voice': 'driver'},
        {'kind': 'fill', 'text': "I'm looking for my keys.", 'prompt': "I'm ___ my keys.", 'answer': 'looking for',
         'uz': 'Kalitlarimni qidiryapman.', 'level': 'B1', 'topic': 'home'},
        {'kind': 'twister', 'text': 'Red lorry, yellow lorry.', 'level': 'B1', 'topic': 'general'},
    ],
}


def no_cache_check(lines):
    return lines                      # the disk TTS cache is not consulted in tests


@override_settings(CACHES=LOCMEM)
class BankTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create(username='bank_staff', email='bank_staff@example.test', is_staff=True)
        cls.learner = User.objects.create(username='bank_learner', email='bank_learner@example.test')

    def setUp(self):
        self.api = APIClient(**H)
        self.api.force_authenticate(self.staff)
        p1 = mock.patch('vocabulary.bank.uncached', side_effect=no_cache_check)
        p2 = mock.patch('gamestats.tasks.start_warm', return_value={'state': 'queued'})
        p1.start()
        self.start_warm = p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def post_import(self, payload, dry_run=False):
        url = IMPORT + ('?dry_run=1' if dry_run else '')
        return self.api.post(url, payload, format='json')


class ImportTests(BankTestCase):
    def test_dry_run_writes_nothing(self):
        r = self.post_import(SAMPLE, dry_run=True)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertTrue(d['dry_run'])
        self.assertEqual(d['words'], {'created': 5, 'updated': 0, 'unchanged': 0})
        self.assertEqual(d['phrases'], {'created': 4, 'updated': 0, 'unchanged': 0})
        self.assertEqual(Word.objects.count(), 0)
        self.assertEqual(Phrase.objects.count(), 0)
        self.assertFalse(d['warm']['started'])
        self.assertGreater(d['warm']['lines'], 0)
        self.start_warm.assert_not_called()

    def test_upsert_is_idempotent(self):
        first = self.post_import(SAMPLE).json()
        self.assertEqual(first['words']['created'], 5)
        self.assertEqual(first['phrases']['created'], 4)
        self.assertEqual(first['error_count'], 0)
        second = self.post_import(SAMPLE).json()
        self.assertEqual(second['words'], {'created': 0, 'updated': 0, 'unchanged': 5})
        self.assertEqual(second['phrases'], {'created': 0, 'updated': 0, 'unchanged': 4})
        self.assertEqual(Word.objects.count(), 5)
        self.assertEqual(Phrase.objects.count(), 4)
        ticket = Word.objects.get(word='ticket')
        self.assertEqual((ticket.level, ticket.topic, ticket.source, ticket.difficulty), ('A1', 'metro', 'test-v1', 'EASY'))
        self.assertEqual(ticket.say_also, ['tickets'])
        echo = Phrase.objects.get(kind='echo')
        self.assertEqual(echo.voice, 'grandma')
        self.assertEqual(echo.key, 'echo|A2|excuse me where is the exit')
        self.assertEqual(Phrase.objects.get(kind='twister').voice, 'coach')       # the kind's default voice
        self.assertEqual(Phrase.objects.get(kind='fill').voice, 'teacher')

    def test_changed_rows_are_updated_others_unchanged(self):
        self.post_import(SAMPLE)
        changed = copy.deepcopy(SAMPLE)
        changed['words'][0]['uz'] = 'yo‘l chiptasi'
        changed['phrases'][0]['uz'] = 'Kechirasiz, chiqish eshigi qayerda?'
        d = self.post_import(changed).json()
        self.assertEqual(d['words'], {'created': 0, 'updated': 1, 'unchanged': 4})
        self.assertEqual(d['phrases'], {'created': 0, 'updated': 1, 'unchanged': 3})
        self.assertEqual(Word.objects.get(word='ticket').uz, 'yo‘l chiptasi')

    def test_missing_fields_are_left_alone(self):
        self.post_import(SAMPLE)
        d = self.post_import({'version': 1, 'words': [{'word': 'Ticket', 'uz': 'chipta'}]}).json()
        self.assertEqual(d['words']['unchanged'], 0)        # only the case of the word changed
        w = Word.objects.get(word__iexact='ticket')
        self.assertEqual(w.word, 'Ticket')
        self.assertEqual(w.definition, 'a small paper that shows you paid to travel')
        self.assertEqual(w.source, 'test-v1')

    def test_errors_are_per_row(self):
        payload = {
            'version': 1, 'defaults': {'level': 'A1'},
            'words': [
                {'word': 'apple', 'uz': 'olma', 'picture': 'apple'},
                {'word': 'ice cream!', 'uz': 'muzqaymoq'},
                {'word': '7 days', 'uz': 'yetti kun'},
                {'word': 'a very long chunk here', 'uz': 'x'},
                {'word': 'BANANA', 'uz': 'banan'},
                {'word': 'pear', 'uz': 'nok', 'level': 'Z9'},
                {'word': 'milk', 'uz': 'sut', 'pos': 'thing'},
                {'word': 'bread', 'uz': 'non', 'say_also': 'breads'},
                'not an object',
            ],
            'phrases': [
                {'kind': 'echo', 'text': ' '.join(['word'] * 15), 'uz': 'x'},
                {'kind': 'answer', 'prompt': 'How are you?', 'accept': [], 'uz': 'x'},
                {'kind': 'fill', 'text': 'I like tea.', 'prompt': 'I like tea.', 'answer': 'coffee', 'uz': 'x'},
                {'kind': 'echo', 'text': 'Good morning!', 'uz': 'Xayrli tong!', 'voice': 'robot'},
                {'kind': 'chant', 'text': 'Hi', 'uz': 'x'},
                {'kind': 'echo', 'text': 'See you later!', 'uz': 'Ko‘rishguncha!'},
            ],
        }
        r = self.post_import(payload)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['words']['created'], 1)
        self.assertEqual(d['phrases']['created'], 1)
        by = {(e['kind'], e['index']): e for e in d['errors']}
        self.assertEqual(set(by), {('word', i) for i in range(1, 9)} | {('phrase', i) for i in range(5)})
        self.assertIn('faqat harf', by[('word', 1)]['error'])
        self.assertEqual(by[('word', 1)]['key'], 'ice cream!')
        self.assertIn('raqam', by[('word', 2)]['error'])
        self.assertIn('1–3', by[('word', 3)]['error'])
        self.assertIn('kichik harf', by[('word', 4)]['error'])
        self.assertIn('level', by[('word', 5)]['error'])
        self.assertIn('pos', by[('word', 6)]['error'])
        self.assertIn('say_also', by[('word', 7)]['error'])
        self.assertIn('14', by[('phrase', 0)]['error'])
        self.assertIn('accept', by[('phrase', 1)]['error'])
        self.assertIn('___', by[('phrase', 2)]['error'])
        self.assertIn('answer', by[('phrase', 2)]['error'])
        self.assertIn('voice', by[('phrase', 3)]['error'])
        self.assertIn('kind', by[('phrase', 4)]['error'])
        self.assertEqual(d['error_count'], 13)
        self.assertTrue(Word.objects.filter(word='apple').exists())

    def test_new_word_needs_a_level(self):
        d = self.post_import({'version': 1, 'words': [{'word': 'kite', 'uz': 'varrak'}]}).json()
        self.assertEqual(d['words']['created'], 0)
        self.assertIn('level', d['errors'][0]['error'])

    def test_warnings(self):
        payload = {
            'version': 1, 'defaults': {'level': 'A1'},
            'words': [
                {'word': 'bus', 'uz': 'avtobus'},                        # short, no picture
                {'word': 'bus', 'uz': 'avtobus'},                        # duplicate in the file
                {'word': 'tram', 'uz': '', 'topic': 'space'},            # empty uz, unknown topic
                {'word': 'right', 'uz': 'o‘ng', 'picture': 'hand'},     # homophone
                {'word': 'cat', 'uz': 'mushuk', 'picture': 'cat'},       # short but has a picture → fine
            ],
            'phrases': [
                {'kind': 'echo', 'text': 'one two three four five six seven eight nine ten eleven twelve thirteen',
                 'uz': 'sanash'},
            ],
        }
        d = self.post_import(payload).json()
        self.assertEqual(d['error_count'], 0, d['errors'])
        msgs = [(w['kind'], w['index'], w['warning']) for w in d['warnings']]
        text = json.dumps(msgs, ensure_ascii=False)
        self.assertIn('4 harfdan qisqa', text)
        self.assertIn('faylda takror', text)
        self.assertIn('noma’lum mavzu', text)
        self.assertIn('uz bo‘sh', text)
        self.assertIn('gomofon', text)
        self.assertIn('echo uzun', text)
        self.assertFalse([m for m in msgs if m[1] == 4], 'a short word with a picture is fine')
        self.assertEqual(Word.objects.get(word='tram').topic, 'space')   # saved anyway
        self.assertEqual(d['words']['created'], 4)

    def test_limits(self):
        many = {'version': 1, 'defaults': {'level': 'A1'},
                'words': [{'word': f'w{"a" * (i % 5)}'} for i in range(5001)]}
        r = self.post_import(many)
        self.assertEqual(r.status_code, 400)
        self.assertIn('5000', r.json()['error'])
        big = json.dumps({'version': 1, 'words': [{'word': 'apple', 'definition': 'x' * 300}] * 7500})
        self.assertGreater(len(big), bank.MAX_BYTES)
        r = self.api.post(IMPORT, big, content_type='application/json')
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.post_import([1, 2]).status_code, 400)
        self.assertEqual(self.post_import({'version': 2, 'words': [{'word': 'a'}]}).status_code, 400)
        self.assertEqual(self.post_import({'version': 1}).status_code, 400)

    def test_auto_warm_after_a_real_import(self):
        self.post_import(SAMPLE, dry_run=True)
        self.start_warm.assert_not_called()
        d = self.post_import(SAMPLE).json()
        self.assertTrue(d['warm']['started'])
        self.start_warm.assert_called_once()
        lines = self.start_warm.call_args[0][0]
        self.assertIn(['ticket', 'teacher'], lines)
        self.assertIn(['Excuse me, where is the exit?', 'grandma'], lines)
        self.assertIn(['Where are you going?', 'driver'], lines)
        self.assertIn(["I'm going to the bazaar.", 'driver'], lines)
        self.assertIn(['Red lorry, yellow lorry.', 'coach'], lines)
        self.assertNotIn(['train', 'teacher'], lines)                       # a draft is not warmed
        self.start_warm.reset_mock()
        d = self.post_import(SAMPLE).json()                                  # nothing new
        self.assertFalse(d['warm']['started'])
        self.start_warm.assert_not_called()
        with mock.patch('gamestats.tasks.warm_running', return_value=True):
            changed = copy.deepcopy(SAMPLE)
            changed['words'][0]['word'] = 'tickets office'
            d = self.post_import(changed).json()
        self.assertTrue(d['warm']['running'])
        self.start_warm.assert_not_called()

    def test_staff_only(self):
        api = APIClient(**H)
        api.force_authenticate(self.learner)
        self.assertEqual(api.post(IMPORT, SAMPLE, format='json').status_code, 403)
        self.assertEqual(api.get('/api/games/words/admin/items/').status_code, 403)
        self.assertEqual(APIClient(**H).get('/api/games/words/admin/summary/').status_code, 401)


class ExportTests(BankTestCase):
    def test_export_round_trips(self):
        self.post_import(SAMPLE)
        r = self.api.get('/api/games/words/admin/export/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r['Content-Disposition'])
        data = json.loads(r.content)
        self.assertEqual(len(data['words']), 5)
        self.assertEqual(len(data['phrases']), 4)
        again = self.post_import(data).json()
        self.assertEqual(again['words'], {'created': 0, 'updated': 0, 'unchanged': 5})
        self.assertEqual(again['phrases'], {'created': 0, 'updated': 0, 'unchanged': 4})
        self.assertEqual(again['error_count'], 0)
        # into an empty bank it recreates the same rows
        before = {w.word: bank.word_row(w, full=True) for w in Word.objects.all()}
        pbefore = {p.key: bank.phrase_row(p, full=True) for p in Phrase.objects.all()}
        Word.objects.all().delete()
        Phrase.objects.all().delete()
        self.post_import(data)
        self.assertEqual({w.word: bank.word_row(w, full=True) for w in Word.objects.all()}, before)
        self.assertEqual({p.key: bank.phrase_row(p, full=True) for p in Phrase.objects.all()}, pbefore)

    def test_export_filters(self):
        self.post_import(SAMPLE)
        d = json.loads(self.api.get('/api/games/words/admin/export/?level=B1&kind=p').content)
        self.assertEqual(d['words'], [])
        self.assertEqual({p['kind'] for p in d['phrases']}, {'answer', 'fill', 'twister'})
        d = json.loads(self.api.get('/api/games/words/admin/export/?topic=metro&kind=w').content)
        self.assertEqual({w['word'] for w in d['words']}, {'ticket', 'the', 'train'})
        d = json.loads(self.api.get('/api/games/words/admin/export/?status=draft').content)
        self.assertEqual([w['word'] for w in d['words']], ['train'])


class ItemsTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.post_import(SAMPLE)

    def test_list_filters_and_pages(self):
        d = self.api.get('/api/games/words/admin/items/?kind=w').json()
        self.assertEqual(d['count'], 5)
        self.assertEqual(d['pages'], 1)
        self.assertEqual(d['items'][0]['k'], 'w')
        self.assertIn('say_seen', d['items'][0])
        d = self.api.get('/api/games/words/admin/items/?kind=w&level=A1&status=published').json()
        self.assertEqual({i['word'] for i in d['items']}, {'ticket', 'the'})
        d = self.api.get('/api/games/words/admin/items/?kind=w&q=chip').json()
        self.assertEqual([i['word'] for i in d['items']], ['ticket'])
        d = self.api.get('/api/games/words/admin/items/?kind=w&tag=kids').json()
        self.assertEqual([i['word'] for i in d['items']], ['train'])
        d = self.api.get('/api/games/words/admin/items/?kind=p&level=B1').json()
        self.assertEqual(d['count'], 3)
        self.assertEqual(d['items'][0]['k'], 'p')
        Word.objects.bulk_create([Word(word=f'filler{chr(97 + i // 26)}{chr(97 + i % 26)}', level='C1') for i in range(60)])
        d = self.api.get('/api/games/words/admin/items/?kind=w&level=C1&page=2').json()
        self.assertEqual((d['count'], d['pages'], d['page'], len(d['items'])), (60, 2, 2, 10))

    def test_patch_word(self):
        w = Word.objects.get(word='ticket')
        url = f'/api/games/words/admin/items/w/{w.id}/'
        r = self.api.patch(url, {'status': 'draft', 'level': 'A2', 'topic': 'bozor', 'picture': 'coin'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(sorted(r.json()['changed']), ['level', 'picture', 'status', 'topic'])   # A1→A2: still EASY
        w.refresh_from_db()
        self.assertEqual((w.status, w.level, w.topic, w.picture), ('draft', 'A2', 'bozor', 'coin'))
        self.assertEqual(w.uz, 'chipta')
        r = self.api.patch(url, {'say_also': ['tickets', 'a ticket'], 'speak': False}, format='json')
        self.assertEqual(r.status_code, 200)
        w.refresh_from_db()
        self.assertEqual((w.say_also, w.speak), (['tickets', 'a ticket'], False))
        self.assertEqual(self.api.patch(url, {'level': 'X1'}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(url, {'say_also': 'tickets'}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(url, {'say_seen': 5}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(url, {'word': 'Expensive'}, format='json').status_code, 400)   # taken
        self.assertEqual(self.api.patch('/api/games/words/admin/items/w/999999/', {'uz': 'x'}, format='json').status_code, 404)

    def test_patch_phrase(self):
        p = Phrase.objects.get(kind='fill')
        url = f'/api/games/words/admin/items/p/{p.id}/'
        r = self.api.patch(url, {'uz': 'Kalitimni qidiryapman.', 'status': 'reviewed'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual((p.uz, p.status), ('Kalitimni qidiryapman.', 'reviewed'))
        self.assertEqual(self.api.patch(url, {'answer': 'nothing'}, format='json').status_code, 400)
        r = self.api.patch(url, {'level': 'B2'}, format='json')
        self.assertEqual(r.status_code, 200)
        p.refresh_from_db()
        self.assertTrue(p.key.startswith('fill|B2|'))
        e = Phrase.objects.get(kind='echo')
        r = self.api.patch(f'/api/games/words/admin/items/p/{e.id}/', {'voice': ''}, format='json')
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.voice, 'narrator')


class SummaryWarmTests(BankTestCase):
    def test_summary(self):
        self.post_import(SAMPLE)
        d = self.api.get('/api/games/words/admin/summary/').json()
        self.assertEqual(d['words']['total'], 5)
        self.assertEqual(d['words']['by_status'], {'published': 4, 'draft': 1})
        self.assertIn({'level': 'A1', 'topic': 'metro', 'status': 'published', 'n': 2}, d['words']['grid'])
        self.assertEqual(d['phrases']['total'], 4)
        self.assertIn({'level': 'B1', 'kind': 'answer', 'status': 'published', 'n': 1}, d['phrases']['by_kind'])
        self.assertEqual(d['tags'], {'kids': 1})
        # 4 published words + echo + answer×2 + fill + twister, plus the Runner's fixed cheers / Bekat lines
        from games.runner_logic import FIXED_VOICE_LINES
        expected = 9 + len(set(FIXED_VOICE_LINES))
        self.assertEqual(d['warm']['lines'], expected)
        self.assertEqual(d['warm']['unwarmed'], expected)
        self.assertEqual(len(d['topics']), len(bank.TOPICS))

    def test_warm(self):
        self.post_import(SAMPLE)
        self.start_warm.reset_mock()
        r = self.api.post('/api/games/words/admin/warm/', {'level': 'B1'}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        lines = self.start_warm.call_args[0][0]
        self.assertIn(['look for', 'teacher'], lines)
        self.assertNotIn(['ticket', 'teacher'], lines)
        with mock.patch('gamestats.tasks.warm_running', return_value=True):
            self.assertEqual(self.api.post('/api/games/words/admin/warm/', {}, format='json').status_code, 409)
        with mock.patch('vocabulary.bank.uncached', return_value=[]):
            r = self.api.post('/api/games/words/admin/warm/', {}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['nothing'])
        self.assertEqual(self.api.get('/api/games/words/admin/warm/').status_code, 200)


class SrsTests(TestCase):
    NOW = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
    TODAY = date(2026, 10, 4)

    def rv(self, box=0, promoted_on=None):
        return Review(box=box, due_at=self.NOW, promoted_on=promoted_on)

    def test_intervals(self):
        self.assertEqual([srs.interval(b) for b in range(7)], [0, 1, 3, 7, 14, 30, 60])

    def test_table(self):
        now, day = self.NOW, timedelta(days=1)
        # (box, promoted_on, verdict, tries, pt, practice) → (effect, box, due, promoted_on)
        cases = [
            ((0, None, 'ok', 1, 'uz', False), ('promoted', 1, now + day, self.TODAY)),
            ((3, None, 'ok', 1, 'picture', False), ('promoted', 4, now + 14 * day, self.TODAY)),
            ((6, None, 'ok', 1, 'uz', False), ('promoted', 6, now + 60 * day, self.TODAY)),
            ((2, self.TODAY, 'ok', 1, 'uz', False), ('held', 2, now + 3 * day, self.TODAY)),           # once a day
            ((2, self.TODAY - day, 'ok', 1, 'uz', False), ('promoted', 3, now + 7 * day, self.TODAY)),
            ((0, None, 'ok', 1, 'hear', False), ('held', 0, now, None)),                                 # hear never promotes
            ((2, None, 'ok', 1, 'hear', False), ('held', 2, now + day, None)),
            ((2, None, 'ok', 2, 'uz', False), ('held', 2, now + day, None)),                             # second try
            ((3, None, 'close', 1, 'uz', False), ('held', 3, now + day, None)),
            ((0, None, 'close', 1, 'uz', False), ('held', 0, now, None)),
            ((5, None, 'miss', 1, 'uz', False), ('demoted', 3, now + timedelta(minutes=10), None)),
            ((1, None, 'miss', 1, 'uz', False), ('demoted', 0, now + timedelta(minutes=10), None)),
            ((4, None, 'skip', 1, 'uz', False), ('skipped', 4, now, None)),
            ((4, None, 'ok', 1, 'uz', True), ('fixed', 4, now + day, None)),                             # practice
            ((4, None, 'close', 1, 'uz', True), ('fixed', 4, now + day, None)),
            ((4, None, 'miss', 1, 'uz', True), ('skipped', 4, now, None)),
        ]
        for (box, promoted, verdict, tries, pt, practice), (effect, nbox, due, npromoted) in cases:
            with self.subTest(box=box, verdict=verdict, tries=tries, pt=pt, practice=practice, promoted=promoted):
                r = self.rv(box, promoted)
                got = srs.apply(r, verdict, now, tries=tries, pt=pt, practice=practice)
                self.assertEqual((got, r.box, r.due_at, r.promoted_on), (effect, nbox, due, npromoted))

    def test_counters(self):
        r = self.rv()
        srs.apply(r, 'ok', self.NOW, tries=1, pt='uz')
        srs.apply(r, 'ok', self.NOW, tries=2, pt='uz')
        srs.apply(r, 'close', self.NOW)
        srs.apply(r, 'miss', self.NOW)
        srs.apply(r, 'skip', self.NOW)
        srs.apply(r, 'ok', self.NOW, practice=True)
        self.assertEqual((r.seen, r.ok, r.first_ok, r.miss, r.last_verdict), (4, 2, 1, 1, 'miss'))
        with self.assertRaises(ValueError):
            srs.apply(r, 'great', self.NOW)

    def test_save_reviews_upserts(self):
        user = get_user_model().objects.create(username='srs_u', email='srs_u@example.test')
        r = srs.new_review(user.id, 'w', 5, 'say', self.NOW)
        srs.apply(r, 'ok', self.NOW, pt='uz')
        srs.save_reviews([r])
        r2 = srs.new_review(user.id, 'w', 5, 'say', self.NOW)
        r2.box, r2.seen = 4, 9
        srs.save_reviews([r2, srs.new_review(user.id, 'w', 5, 'mean', self.NOW)])
        self.assertEqual(Review.objects.filter(user=user).count(), 2)
        self.assertEqual(Review.objects.get(user=user, skill='say').box, 4)


class DistractorTests(TestCase):
    def setUp(self):
        Word.objects.bulk_create([
            Word(word='happy', uz='baxtli', level='A2', pos='adj', synonyms=['glad']),
            Word(word='glad', uz='xursand', level='A2', pos='adj'),
            Word(word='sad', uz='xafa', level='A2', pos='adj', picture='heart'),
            Word(word='tall', uz='baland', level='A2', pos='adj'),
            Word(word='cheerful', uz='quvnoq', level='A2', pos='adj', synonyms=['happy']),
            Word(word='angry', uz='jahldor', level='A2', pos='adj', status='draft'),
            Word(word='quick', uz='tez', level='B1', pos='adj'),
            Word(word='table', uz='stol', level='A2', pos='noun'),
        ])

    def test_same_level_and_pos_never_a_synonym(self):
        happy = Word.objects.get(word='happy')
        for _ in range(10):
            got = bank.distractors_for(happy, 3)
            self.assertEqual(len(got), 3)
            self.assertNotIn('glad', got)            # its synonym
            self.assertNotIn('cheerful', got)        # lists happy as a synonym
            self.assertNotIn('angry', got)           # draft
            self.assertNotIn('happy', got)
            self.assertTrue({'sad', 'tall'} <= set(got))
            self.assertIn(got[2], {'quick', 'table'})
        self.assertEqual(set(bank.distractors_for(happy, 2, field='uz')), {'xafa', 'baland'})
        self.assertEqual(bank.distractors_for(happy, 3, with_picture=True), ['sad'])

    def test_curated_first_and_shared_pool(self):
        happy = Word.objects.get(word='happy')
        happy.distractors = ['upset', 'glad']
        pool = bank.distractor_pool(['A2', 'B1'])
        with self.assertNumQueries(0):
            got = bank.distractors_for(happy, 3, pool=pool)
        self.assertEqual(got[0], 'upset')
        self.assertNotIn('glad', got)


class RollupTests(TestCase):
    def test_rollup(self):
        User = get_user_model()
        users = [User.objects.create(username=f'ru{i}', email=f'ru{i}@example.test') for i in range(22)]
        w = Word.objects.create(word='very', level='A1')
        p = Phrase.objects.create(kind='echo', text='Hello there', level='A1', key='echo|A1|hello there')
        rows = []
        for i, u in enumerate(users):
            heard = 'wery' if i < 12 else ('berry' if i < 16 else '')
            rows.append(Review(user=u, kind='w', item_id=w.id, skill='say', seen=1, ok=0 if heard else 1,
                               last_heard=heard, last_verdict='miss' if heard else 'ok'))
        rows.append(Review(user=users[0], kind='w', item_id=w.id, skill='mean', seen=3, ok=2))
        rows.append(Review(user=users[0], kind='p', item_id=p.id, skill='say', seen=2, ok=1))
        rows.append(Review(user=users[0], kind='w', item_id=987654, skill='say', seen=1))       # orphan
        Review.objects.bulk_create(rows)
        out = rollup()
        self.assertEqual(out['purged'], 1)
        w.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual((w.say_seen, w.say_ok, w.mean_seen, w.mean_ok), (22, 6, 3, 2))
        self.assertEqual(w.say_heard, ['wery', 'berry'])
        self.assertEqual((p.say_seen, p.say_ok, p.say_heard), (2, 1, []))
        self.assertEqual(rollup(), {'words': 0, 'phrases': 0, 'purged': 0})
