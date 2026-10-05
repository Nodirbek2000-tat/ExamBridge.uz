"""Scoring tests (no database): manage.py test speaking"""
from django.test import SimpleTestCase

from .scoring import score_reading, split_sentences


def heard(text, conf=-0.1):
    words, t = [], 0.0
    for w in text.split():
        words.append({'word': w, 'start': t, 'end': t + 0.4})
        t += 0.5
    return {'text': text, 'words': words, 'segments': [{'start': 0, 'end': t, 'avg_logprob': conf}]}


def statuses(r):
    return [w['status'] for w in r['words']]


class ScoringTests(SimpleTestCase):
    def test_perfect_reading(self):
        r = score_reading('I am ten years old.', heard('I am 10 years old.'))
        self.assertEqual(statuses(r), ['ok'] * 5)
        self.assertGreaterEqual(r['accuracy'], 98)

    def test_skipped_words_do_not_shift_the_rest(self):
        r = score_reading('The cat sat on the red mat.', heard('The cat on the mat.'))
        self.assertEqual(statuses(r), ['ok', 'ok', 'skip', 'ok', 'ok', 'skip', 'ok'])
        self.assertEqual(r['words'][2]['score'], 0)

    def test_near_words_need_fixing(self):
        r = score_reading('She walked to the village.', heard('She walk to the vilage.'))
        self.assertEqual(statuses(r), ['ok', 'fix', 'ok', 'ok', 'fix'])
        self.assertTrue(all(50 <= w['score'] <= 89 for w in r['words'] if w['status'] == 'fix'))

    def test_extra_words_are_ignored(self):
        r = score_reading('We play football.', heard('Well we um play the football okay.'))
        self.assertEqual(statuses(r), ['ok'] * 3)

    def test_contractions_and_spelling(self):
        r = score_reading("I'm sure my favourite colour is grey.", heard('I am sure my favorite color is gray.'))
        self.assertEqual(set(statuses(r)), {'ok'})

    def test_sentences(self):
        self.assertEqual(split_sentences('Hi. Mr. Lee is here! Ok?'), ['Hi.', 'Mr. Lee is here!', 'Ok?'])

    def test_years_read_aloud(self):
        r = score_reading('He was born in 1995.', heard('He was born in nineteen ninety-five.'))
        self.assertEqual(set(statuses(r)), {'ok'})
        r = score_reading('He was born in 1995.', heard('He was born in 1995.'))
        self.assertEqual(set(statuses(r)), {'ok'})

    def test_its_and_its(self):
        # "it's" and "its" sound the same; "its" said as "it" is a real mistake
        self.assertEqual(statuses(score_reading("It's cold.", heard('Its cold.'))), ['ok', 'ok'])
        self.assertEqual(statuses(score_reading('The dog wags its tail.', heard("The dog wags it's tail."))), ['ok'] * 5)
        self.assertEqual(statuses(score_reading('The dog wags its tail.', heard('The dog wags it tail.')))[3], 'fix')

    def test_plural_s_is_not_forgiven(self):
        r = score_reading('I have two cats.', heard('I have two cat.'))
        self.assertEqual(statuses(r)[3], 'fix')

    def test_silence_hallucination_is_not_a_reading(self):
        r = score_reading('The sky is grey and there are no birds.', heard('Thank you.'))
        self.assertTrue(r['heard_any'])
        self.assertFalse(r['read_the_text'])
        self.assertTrue(score_reading('The sky is grey.', heard('The sky.'))['read_the_text'])


class SniffTests(SimpleTestCase):
    def test_recordings_are_recognised_by_content(self):
        from .views import sniff_audio
        self.assertEqual(sniff_audio(b'\x1aE\xdf\xa3' + b'\0' * 12), ('audio/webm', '.webm'))
        self.assertEqual(sniff_audio(b'\0\0\0\x1cftypM4A ' + b'\0' * 4), ('audio/mp4', '.m4a'))
        self.assertEqual(sniff_audio(b'OggS' + b'\0' * 12), ('audio/ogg', '.ogg'))
        self.assertEqual(sniff_audio(b'RIFF\0\0\0\0WAVEfmt '), ('audio/wav', '.wav'))
        self.assertEqual(sniff_audio(b'ID3\x04' + b'\0' * 12), ('audio/mpeg', '.mp3'))
        self.assertIsNone(sniff_audio(b'<html><script>x'))
        self.assertIsNone(sniff_audio(b'\xff\xf1\x50\x80'))          # ADTS aac
        self.assertIsNone(sniff_audio(b''))

    def test_stored_name_keeps_only_audio_extensions(self):
        from .models import attempt_audio_path
        self.assertTrue(attempt_audio_path(None, 'reading.m4a').endswith('.m4a'))
        self.assertTrue(attempt_audio_path(None, 'evil.html').endswith('.webm'))
        self.assertTrue(attempt_audio_path(None, 'x.svg').endswith('.webm'))
