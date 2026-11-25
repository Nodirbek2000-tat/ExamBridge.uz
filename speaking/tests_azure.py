"""
Azure pronunciation scoring (speaking/azure.py) and the Azure → Whisper fallback in
tasks.score_attempt. No network: the SDK and the recognizer are fakes that replay JSON shaped
like Azure's (NBest[0].Words[].PronunciationAssessment, Phonemes, Offset / Duration in ticks).

    manage.py test speaking --noinput --keepdb
"""
import io
import json
import logging
import os
import shutil
import tempfile
import wave
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from ai_chat.models import AIUsage
from ai_chat.usage import cost_of

from . import azure
from .models import SpeakingAttempt, SpeakingLesson
from .tasks import score_attempt

TICK = 10_000_000
LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'speaking-azure-tests'}}

# IPA as Azure returns it with phonemeAlphabet = IPA
IPA = {
    'the': ['ð', 'ə'], 'cat': ['k', 'æ', 't'], 'sat': ['s', 'æ', 't'], 'on': ['ɑ', 'n'], 'red': ['r', 'ɛ', 'd'],
    'mat': ['m', 'æ', 't'], 'i': ['aɪ'], 'think': ['θ', 'ɪ', 'ŋ', 'k'], 'this': ['ð', 'ɪ', 's'], 'is': ['ɪ', 'z'],
    'a': ['ə'], 'good': ['ɡ', 'ʊ', 'd'], 'idea': ['aɪ', 'd', 'i', 'ə'], "i'm": ['aɪ', 'm'], 'ten': ['t', 'ɛ', 'n'],
    'years': ['j', 'ɪ', 'r', 'z'], 'old': ['oʊ', 'l', 'd'], 'he': ['h', 'i'], 'was': ['w', 'ʌ', 'z'],
    'born': ['b', 'ɔ', 'r', 'n'], 'in': ['ɪ', 'n'], 'nineteen': ['n', 'aɪ', 'n', 't', 'i', 'n'],
    'ninety': ['n', 'aɪ', 'n', 't', 'i'], 'five': ['f', 'aɪ', 'v'], 'she': ['ʃ', 'i'], 'walk': ['w', 'ɔ', 'k'],
    'to': ['t', 'u'], 'village': ['v', 'ɪ', 'l', 'ɪ', 'dʒ'], 'we': ['w', 'i'], 'play': ['p', 'l', 'eɪ'],
    'football': ['f', 'ʊ', 't', 'b', 'ɔ', 'l'], 'every': ['ɛ', 'v', 'r', 'i'], 'day': ['d', 'eɪ'],
    'after': ['æ', 'f', 't', 'ɚ'], 'school': ['s', 'k', 'u', 'l'], 'my': ['m', 'aɪ'], 'friends': ['f', 'r', 'ɛ', 'n', 'd', 'z'],
    'and': ['æ', 'n', 'd'], 'come': ['k', 'ʌ', 'm'], 'too': ['t', 'u'], 'it': ['ɪ', 't'], 'fun': ['f', 'ʌ', 'n'],
    'hello': ['h', 'ɛ', 'l', 'oʊ'], 'how': ['h', 'aʊ'], 'are': ['ɑ', 'r'], 'you': ['j', 'u'], 'doing': ['d', 'u', 'ɪ', 'ŋ'],
    'today': ['t', 'ə', 'd', 'eɪ'],
}


def prosody(unexpected=0.0, missing=0.0, monotone=False):
    return {'Prosody': {
        'Break': {'ErrorTypes': ['None'], 'UnexpectedBreak': {'Confidence': unexpected},
                  'MissingBreak': {'Confidence': missing}, 'BreakLength': 0},
        'Intonation': {'ErrorTypes': ['Monotone'] if monotone else [],
                       'Monotone': {'SyllablePitchDeltaConfidence': 0.2 if monotone else 0.9}}}}


def word(text, start, score=97.0, phonemes=None, error=None, length=0.4, feedback=None):
    """One entry of NBest[0].Words, as Azure sends it (start / length in seconds here, ticks in the JSON)."""
    names = IPA.get(text.lower(), list(text.lower()))
    scores = phonemes or {}
    offset, duration = int(start * TICK), int(length * TICK)
    step = duration // max(1, len(names))
    return {
        'Word': text, 'Offset': offset, 'Duration': duration,
        'PronunciationAssessment': {
            'AccuracyScore': float(score),
            'ErrorType': error or ('Mispronunciation' if score < 60 else 'None'),
            'Feedback': feedback or prosody(),
        },
        'Syllables': [{'Syllable': ''.join(names), 'PronunciationAssessment': {'AccuracyScore': float(score)},
                       'Offset': offset, 'Duration': duration}],
        'Phonemes': [{'Phoneme': p, 'PronunciationAssessment': {'AccuracyScore': float(scores.get(p, min(100, score + 1)))},
                      'Offset': offset + k * step, 'Duration': step} for k, p in enumerate(names)],
    }


def said(text, start=0.5, gap=0.1, **scores):
    """Words read one after another: said('the cat', cat=70) → two words, 'cat' scored 70."""
    out, t = [], start
    for w in text.split():
        out.append(word(w, t, scores.get(w.replace("'", ''), 97.0)))
        t += 0.4 + gap
    return out


def segment(words, fluency=95.0, prosody_score=90.0, display=None):
    """One recognised segment: SpeechServiceResponse_JsonResult of a `recognized` event."""
    start = words[0]['Offset'] if words else 0
    end = (words[-1]['Offset'] + words[-1]['Duration']) if words else 0
    lexical = ' '.join(w['Word'] for w in words)
    acc = round(sum(w['PronunciationAssessment']['AccuracyScore'] for w in words) / max(1, len(words)), 1)
    pa = {'AccuracyScore': acc, 'FluencyScore': fluency, 'CompletenessScore': 100.0, 'PronScore': acc}
    if prosody_score is not None:
        pa['ProsodyScore'] = prosody_score
    return {
        'Id': 'f3b6c1e0a4d24c2f9c7a5b8e0d1f2a3b', 'RecognitionStatus': 'Success',
        'Offset': start, 'Duration': end - start, 'Channel': 0,
        'DisplayText': display if display is not None else lexical.capitalize() + '.', 'SNR': 31.5,
        'NBest': [{'Confidence': 0.93, 'Lexical': lexical, 'ITN': lexical, 'MaskedITN': lexical,
                   'Display': lexical.capitalize() + '.', 'PronunciationAssessment': pa, 'Words': words}],
    }


def statuses(r):
    return [w['status'] for w in r['words']]


# A real continuous-mode answer (a synthesised voice reading the text below, 7.5 s, one segment):
# Azure heard an extra "i'm" in the pause, wrote "10" as digits, sent MissingBreak confidence 1.0
# on almost every word and tagged the whole flat utterance Monotone.
# (word, Offset, Duration, AccuracyScore, UnexpectedBreak conf., MissingBreak conf., BreakLength, phonemes)
REAL_TEXT = "I'm 10 years old. My friends and I play football every day after school."
REAL_WORDS = [
    ("i'm", 600000, 3100000, 91, None, None, 0, [('aɪ', 18), ('m', 67)]),
    ("10", 3800000, 3300000, 94, 3.42e-08, 1, 0, [('t', 73), ('ɛ', 65), ('n', 71)]),
    ("years", 7200000, 3900000, 100, 3.42e-08, 1, 0, [('j', 100), ('ɪɹ', 100), ('z', 100)]),
    ("old", 11200000, 5500000, 97, 3.42e-08, 1, 0, [('oʊ', 100), ('l', 100), ('d', 81)]),
    ("i'm", 24200000, 3500000, 94, 2.53, 0, 7400000, [('aɪ', 33), ('m', 91)]),
    ("my", 27800000, 2100000, 91, 3.42e-08, 1, 0, [('m', 64), ('aɪ', 81)]),
    ("friends", 30000000, 6500000, 88, 3.42e-08, 1, 0, [('f', 100), ('ɹ', 100), ('ɛ', 100), ('n', 100), ('d', 100), ('z', 73)]),
    ("and", 36600000, 3100000, 94, 3.42e-08, 1, 0, [('ə', 91), ('n', 45), ('d', 34)]),
    ("i", 39800000, 1300000, 97, 3.42e-08, 1, 0, [('aɪ', 97)]),
    ("play", 41200000, 4100000, 63, 3.42e-08, 1, 0, [('p', 100), ('l', 52), ('eɪ', 53)]),
    ("football", 45400000, 5700000, 94, 3.42e-08, 1, 0, [('f', 94), ('ʊ', 94), ('t', 94), ('b', 94), ('ɔ', 94), ('l', 94)]),
    ("every", 51200000, 3300000, 94, 3.42e-08, 1, 0, [('ɛ', 94), ('v', 94), ('ɹ', 94), ('i', 94)]),
    ("day", 54600000, 3100000, 97, 3.42e-08, 1, 0, [('d', 97), ('eɪ', 97)]),
    ("after", 57800000, 4600000, 94, 3.42e-08, 1, 0, [('æ', 78), ('f', 100), ('t', 100), ('ɚ', 100)]),
    ("school", 62500000, 5200000, 85, 3.42e-08, 1, 0, [('s', 100), ('k', 100), ('u', 100), ('l', 71)]),
]


def real_segment():
    words = []
    for text, offset, duration, score, unexpected, missing, length, phonemes in REAL_WORDS:
        brk = {'ErrorTypes': ['None'], 'BreakLength': length}
        if unexpected is not None:
            brk.update({'UnexpectedBreak': {'Confidence': unexpected}, 'MissingBreak': {'Confidence': missing}})
        step = duration // len(phonemes)
        words.append({
            'Word': text, 'Offset': offset, 'Duration': duration,
            'PronunciationAssessment': {'AccuracyScore': float(score), 'ErrorType': 'None', 'Feedback': {'Prosody': {
                'Break': brk,
                'Intonation': {'ErrorTypes': ['Monotone'], 'Monotone': {'SyllablePitchDeltaConfidence': 0.21659842}}}}},
            'Syllables': [{'Syllable': ''.join(p for p, _ in phonemes), 'Grapheme': text,
                           'PronunciationAssessment': {'AccuracyScore': float(score)}, 'Offset': offset, 'Duration': duration}],
            'Phonemes': [{'Phoneme': p, 'PronunciationAssessment': {'AccuracyScore': float(s)},
                          'Offset': offset + k * step, 'Duration': step} for k, (p, s) in enumerate(phonemes)],
        })
    lexical = "i'm 10 years old i'm my friends and i play football every day after school"
    display = "I'm 10 years old, I'm my friends and I play football every day after school."
    return {
        'Id': '606a6f37b2b24a2baf3625a4f45e89f8', 'RecognitionStatus': 'Success', 'Offset': 600000,
        'Duration': 67100000, 'Channel': 0, 'DisplayText': display, 'SNR': 38.27846,
        'NBest': [{'Confidence': 0.9633742, 'Lexical': lexical, 'ITN': lexical, 'MaskedITN': lexical, 'Display': display,
                   'PronunciationAssessment': {'AccuracyScore': 92.0, 'FluencyScore': 84.0, 'ProsodyScore': 74.7,
                                               'CompletenessScore': 100.0, 'PronScore': 85.1},
                   'Words': words}],
    }


# ─── scoring: Azure JSON → word records ───────────────────────────────────────

class ScoreSegmentsTests(SimpleTestCase):
    def test_good_reading(self):
        r = azure.score_segments('The cat sat on the red mat.', [segment(said('the cat sat on the red mat'))])
        self.assertEqual(statuses(r), ['ok'] * 7)
        w = r['words'][1]
        self.assertEqual((w['word'], w['say'], w['heard'], w['score'], w['error'], w['tip']), ('cat', 'cat', 'cat', 97, '', ''))
        self.assertEqual([p['p'] for p in w['phonemes']], ['k', 'æ', 't'])
        self.assertEqual(r['words'][0]['start'], 0.5)
        self.assertEqual(r['words'][0]['end'], 0.9)
        self.assertEqual(r['words'][-1]['word'], 'mat.')
        self.assertEqual((r['accuracy'], r['completeness_score'], r['fluency_score'], r['prosody_score']), (97, 100, 95, 90))
        # sorted [90, 95, 97, 100] → 0.4·90 + 0.2·(95 + 97 + 100)
        self.assertEqual(r['pron_score'], 94)
        self.assertEqual((r['ok'], r['fix'], r['bad'], r['skip']), (7, 0, 0, 0))
        self.assertTrue(r['heard_any'] and r['read_the_text'])
        self.assertEqual(r['transcript'], 'The cat sat on the red mat.')

    def test_a_real_azure_answer(self):
        r = azure.score_segments(REAL_TEXT, [real_segment()])
        self.assertEqual(len(r['words']), 14)                          # the extra "i'm" is not a word of the text
        self.assertEqual(r['skip'], 0)
        self.assertEqual([w['heard'] for w in r['words'][:5]], ["i'm", '10', 'years', 'old', 'my'])
        self.assertEqual(r['words'][4]['start'], 2.78)                 # "My": its own time, not the inserted word's
        play = r['words'][8]
        self.assertEqual((play['word'], play['status'], play['score'], play['tip']), ('play', 'fix', 63, 'l'))
        self.assertEqual(play['phonemes'], [{'p': 'p', 'score': 100}, {'p': 'l', 'score': 52}, {'p': 'eɪ', 'score': 53}])
        # a real 1.1 s pause after "old." — the MissingBreak confidence of 1.0 is not believed
        self.assertEqual({w['error'] for w in r['words']}, {'Monotone'})
        self.assertEqual((r['accuracy'], r['fluency_score'], r['prosody_score'], r['completeness_score']), (91, 84, 75, 100))
        self.assertEqual(r['pron_score'], 85)                           # Azure's own PronScore was 85.1
        self.assertEqual((r['ok'], r['fix'], r['bad']), (13, 1, 0))

    def test_mispronounced_word_names_the_sound(self):
        words = said('i think this is a good idea', think=48, good=72)
        words[1]['Phonemes'][0]['PronunciationAssessment']['AccuracyScore'] = 22.0     # θ said as s / t
        words[5]['Phonemes'][1]['PronunciationAssessment']['AccuracyScore'] = 55.0     # ʊ
        r = azure.score_segments('I think this is a good idea.', [segment(words)])
        think, good = r['words'][1], r['words'][5]
        self.assertEqual((think['status'], think['score'], think['error'], think['tip']), ('bad', 48, 'Mispronunciation', 'θ'))
        self.assertEqual(think['phonemes'][0], {'p': 'θ', 'score': 22})
        self.assertEqual((good['status'], good['score'], good['tip']), ('fix', 72, 'ʊ'))
        self.assertEqual(r['words'][0]['tip'], '')
        self.assertEqual((r['ok'], r['fix'], r['bad'], r['skip']), (5, 1, 1, 0))

    def test_omitted_word_is_grey_and_does_not_shift_the_rest(self):
        r = azure.score_segments('The cat sat on the red mat.', [segment(said('the cat on the mat'))])
        self.assertEqual(statuses(r), ['ok', 'ok', 'skip', 'ok', 'ok', 'skip', 'ok'])
        sat = r['words'][2]
        self.assertEqual((sat['score'], sat['error'], sat['phonemes'], sat['tip'], sat['start']), (0, 'Omission', [], '', None))
        self.assertEqual(r['words'][3]['heard'], 'on')
        self.assertEqual(r['completeness_score'], 71)                  # 5 of 7 words said
        self.assertEqual(r['accuracy'], round(97 * 5 / 7))              # an omitted word counts 0

    def test_numbers_and_contractions_line_up(self):
        words = said("i'm ten years old he was born in nineteen ninety five", five=30)
        r = azure.score_segments("I'm 10 years old. He was born in 1995.", [segment(words)])
        self.assertEqual([w['word'] for w in r['words']], ["I'm", '10', 'years', 'old.', 'He', 'was', 'born', 'in', '1995.'])
        self.assertEqual(statuses(r)[:8], ['ok'] * 8)
        self.assertEqual(r['words'][0]['heard'], "i'm")
        self.assertEqual(r['words'][1]['heard'], 'ten')
        year = r['words'][8]
        self.assertEqual(year['heard'], 'nineteen ninety five')
        self.assertEqual(year['score'], round((97 + 97 + 30) / 3))
        self.assertEqual(year['status'], 'fix')
        self.assertEqual(len(year['phonemes']), 6 + 5 + 3)                # all three spoken words
        self.assertEqual(year['tip'], 'f')
        self.assertEqual(year['start'], words[8]['Offset'] / TICK)
        self.assertEqual(year['end'], round((words[10]['Offset'] + words[10]['Duration']) / TICK, 2))
        # Azure may also write the digits back
        digits = azure.score_segments('I am 10 years old.', [segment(said('i am 10 years old'))])
        self.assertEqual(statuses(digits), ['ok'] * 5)

    def test_long_reading_in_segments(self):
        text = ('We play football every day after school. My friends come too, and it is fun. '
                'I think this is a good idea.')
        one = said('we play football every day after school', start=0.4)
        two = said('my friends come too and it is fun', start=6.0, come=40)
        three = said('i think this is a good idea', start=12.5)
        segs = [segment(one, fluency=100, prosody_score=90), segment(two, fluency=60, prosody_score=70),
                segment(three, fluency=90, prosody_score=80)]
        # a miscue view of segment 1 alone: "my" looks omitted there but is read in segment 2
        segs[0]['NBest'][0]['Words'].append(
            {'Word': 'my', 'PronunciationAssessment': {'AccuracyScore': 0, 'ErrorType': 'Omission'}})
        r = azure.score_segments(text, segs)
        self.assertEqual(r['skip'], 0)
        self.assertEqual(r['words'][7]['word'], 'My')
        self.assertEqual(r['words'][7]['start'], 6.0)                  # absolute time, from segment 2
        self.assertEqual(r['words'][9]['status'], 'bad')               # "come"
        self.assertEqual(r['words'][-1]['start'], round(three[-1]['Offset'] / TICK, 2))
        d = [s['Duration'] / TICK for s in segs]                       # 3.4 s, 3.9 s, 3.4 s of speech
        # pauses of 2.2 s and 2.6 s between the segments: 1.2 s + 1.6 s beyond the 1 s allowance,
        # out of 15.5 s from the first word to the last
        kept = (15.5 - 2.8) / 15.5
        self.assertEqual(r['fluency_score'], round((100 * d[0] + 60 * d[1] + 90 * d[2]) / sum(d) * kept))
        self.assertEqual(r['prosody_score'], round((90 * d[0] + 70 * d[1] + 80 * d[2]) / sum(d)))
        # one segment: Azure's own FluencyScore as it is
        self.assertEqual(azure.score_segments(text, segs[:1])['fluency_score'], 100)
        self.assertEqual(r['completeness_score'], 100)
        self.assertEqual(r['transcript'].count('.'), 3)
        self.assertGreater(r['fluency_wpm'], 0)

    def test_prosody_breaks_are_read_with_the_punctuation_and_the_timing(self):
        words = [
            word('my', 0.5), word('friends', 1.0),
            word('come', 2.0, feedback=prosody(unexpected=0.92)),   # 0.6 s pause inside the clause
            word('too', 2.5),
            word('and', 3.0, feedback=prosody(missing=0.95)),       # no pause after "too,"
            word('it', 3.5, feedback=prosody(unexpected=0.9)),      # flagged, but there was no pause
            word('is', 4.0, feedback=prosody(missing=1.0)),         # mid-clause: Azure sends 1.0 on most words
            word('fun', 4.5),
            word('we', 6.0, feedback=prosody(missing=1.0)),         # after "fun." with a real pause
            word('play', 6.5),
        ]
        r = azure.score_segments('My friends come too, and it is fun. We play.', [segment(words)])
        self.assertEqual([w['error'] for w in r['words']],
                         ['', '', 'UnexpectedBreak', '', 'MissingBreak', '', '', '', '', ''])
        self.assertEqual(set(statuses(r)), {'ok'})                     # breaks never change the colour
        mono = said('we play football')
        for w in mono:
            w['PronunciationAssessment']['Feedback'] = prosody(monotone=True)
        self.assertEqual({w['error'] for w in azure.score_segments('We play football.', [segment(mono)])['words']},
                         {'Monotone'})

    def test_similar_word_is_never_green(self):
        r = azure.score_segments('She walked to the village.', [segment(said('she walk to the village'))])
        walked = r['words'][1]
        self.assertEqual((walked['status'], walked['error'], walked['heard']), ('fix', 'Mispronunciation', 'walk'))
        self.assertLessEqual(walked['score'], 79)

    def test_no_speech(self):
        r = azure.score_segments('The cat sat on the red mat.', [])
        self.assertFalse(r['heard_any'])
        self.assertEqual(set(statuses(r)), {'skip'})
        self.assertIsNone(r['fluency_score'])
        other = azure.score_segments('The cat sat on the red mat.', [segment(said('hello how are you doing today'))])
        self.assertTrue(other['heard_any'])
        self.assertFalse(other['read_the_text'])

    def test_without_prosody(self):
        r = azure.score_segments('We play football.', [segment(said('we play football'), fluency=80, prosody_score=None)])
        self.assertIsNone(r['prosody_score'])
        self.assertEqual(r['pron_score'], azure.pron_score(97, 80, 100, None))

    def test_pron_score_weights(self):
        self.assertEqual(azure.pron_score(90, 80, 100, 70), 82)       # 0.4·70 + 0.2·(80 + 90 + 100)
        self.assertEqual(azure.pron_score(90, 80, 100, None), 86)     # 0.6·80 + 0.2·(90 + 100)
        self.assertEqual(azure.pron_score(90, None, 50, None), 66)    # 0.6·50 + 0.4·90


# ─── the SDK call (fake recognizer) ───────────────────────────────────────────

class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, cb):
        self.callbacks.append(cb)

    def disconnect_all(self):
        self.callbacks = []

    def fire(self, evt):
        for cb in list(self.callbacks):
            cb(evt)


def fake_sdk(events):
    """An SDK module stand-in: the recognizer replays `events` when recognition starts.
    events: ('json', segment) | ('nomatch',) | ('error', code name, details) | ('eos',) | ('stop',)"""
    Enum = lambda *names: SimpleNamespace(**{n: SimpleNamespace(name=n) for n in names})  # noqa: E731
    sdk = SimpleNamespace(
        ResultReason=Enum('RecognizedSpeech', 'NoMatch'),
        CancellationReason=Enum('Error', 'EndOfStream'),
        CancellationErrorCode=Enum('TooManyRequests', 'Forbidden', 'AuthenticationFailure', 'BadRequest',
                                   'ServiceError', 'ConnectionFailure'),
        PropertyId=SimpleNamespace(Speech_SegmentationSilenceTimeoutMs='seg-ms', SpeechServiceResponse_JsonResult='json'),
        PronunciationAssessmentGradingSystem=SimpleNamespace(HundredMark='HundredMark'),
        PronunciationAssessmentGranularity=SimpleNamespace(Phoneme='Phoneme'),
        made={},
    )

    class SpeechConfig:
        def __init__(self, subscription=None, region=None):
            self.region, self.props = region, {}
            sdk.made['speech_config'] = self

        def set_property(self, key, value):
            self.props[key] = value

    class AudioConfig:
        def __init__(self, filename=None):
            sdk.made['audio_file'] = filename

    class PronConfig:
        def __init__(self, reference_text=None, grading_system=None, granularity=None, enable_miscue=False):
            self.args = (reference_text, grading_system, granularity, enable_miscue)
            self.phoneme_alphabet, self.prosody, self.applied = None, False, None
            sdk.made['pron'] = self

        def enable_prosody_assessment(self):
            self.prosody = True

        def apply_to(self, recognizer):
            self.applied = recognizer

    class Recognizer:
        def __init__(self, speech_config=None, audio_config=None, language=None):
            self.language, self.stopped = language, False
            self.recognized, self.canceled, self.session_stopped = FakeSignal(), FakeSignal(), FakeSignal()
            sdk.made['recognizer'] = self

        def start_continuous_recognition(self):
            for e in events:
                if e[0] == 'json':
                    self.recognized.fire(SimpleNamespace(result=SimpleNamespace(
                        reason=sdk.ResultReason.RecognizedSpeech, properties={'json': json.dumps(e[1])})))
                elif e[0] == 'nomatch':
                    self.recognized.fire(SimpleNamespace(result=SimpleNamespace(reason=sdk.ResultReason.NoMatch,
                                                                                properties={})))
                elif e[0] == 'error':
                    self.canceled.fire(SimpleNamespace(cancellation_details=SimpleNamespace(
                        reason=sdk.CancellationReason.Error, code=getattr(sdk.CancellationErrorCode, e[1]),
                        error_details=e[2])))
                elif e[0] == 'eos':
                    self.canceled.fire(SimpleNamespace(cancellation_details=SimpleNamespace(
                        reason=sdk.CancellationReason.EndOfStream, code=None, error_details='')))
                elif e[0] == 'stop':
                    self.session_stopped.fire(SimpleNamespace(session_id='s'))

        def stop_continuous_recognition(self):
            self.stopped = True

    sdk.SpeechConfig, sdk.PronunciationAssessmentConfig, sdk.SpeechRecognizer = SpeechConfig, PronConfig, Recognizer
    sdk.audio = SimpleNamespace(AudioConfig=AudioConfig)
    return sdk


@override_settings(AZURE_SPEECH_KEY='test-key', AZURE_SPEECH_REGION='northeurope')
class RecognizeTests(SimpleTestCase):
    def run_with(self, events, text='The cat sat.'):
        sdk = fake_sdk(events)
        with mock.patch.object(azure, '_sdk', return_value=sdk):
            return azure.recognize('reading.wav', text, 12.0), sdk

    def test_collects_every_segment_of_a_continuous_session(self):
        a, b = segment(said('the cat')), segment(said('sat', start=3))
        out, sdk = self.run_with([('json', a), ('nomatch',), ('json', b), ('eos',), ('stop',)],
                                 text='The cat\nsat on the red mat.')
        self.assertEqual(out, [a, b])
        made = sdk.made
        self.assertEqual(made['audio_file'], 'reading.wav')
        self.assertEqual(made['speech_config'].region, 'northeurope')
        self.assertEqual(made['speech_config'].props, {'seg-ms': '1500'})
        self.assertEqual(made['recognizer'].language, 'en-US')
        self.assertEqual(made['pron'].args, ('The cat sat on the red mat.', 'HundredMark', 'Phoneme', True))
        self.assertEqual(made['pron'].phoneme_alphabet, 'IPA')
        self.assertTrue(made['pron'].prosody)
        self.assertIs(made['pron'].applied, made['recognizer'])
        self.assertTrue(made['recognizer'].stopped)

    def test_cancellations_say_what_to_do(self):
        cases = [('TooManyRequests', 'WebSocket upgrade failed: Too many requests (429).', azure.AzureBusy),
                 ('Forbidden', 'WebSocket upgrade failed: Forbidden (403).', azure.AzureOff),
                 ('AuthenticationFailure', 'WebSocket upgrade failed: Authentication error (401).', azure.AzureOff),
                 ('BadRequest', 'Quota exceeded for this subscription.', azure.AzureOff),
                 ('ServiceError', 'Internal service error.', azure.AzureError),
                 ('ConnectionFailure', 'Connection was closed by the remote host.', azure.AzureError)]
        for code, details, expected in cases:
            with self.subTest(code=code):
                with self.assertRaises(expected) as caught:
                    self.run_with([('json', segment(said('the cat'))), ('error', code, details), ('stop',)])
                self.assertIs(type(caught.exception), expected)
                self.assertIn(code, str(caught.exception))

    def test_a_session_that_never_ends_times_out(self):
        with mock.patch.object(azure, '_wait_seconds', return_value=0.05):
            with self.assertRaises(azure.AzureError):
                self.run_with([('json', segment(said('the cat')))])

    @override_settings(AZURE_SPEECH_KEY='')
    def test_not_configured(self):
        with self.assertRaises(azure.AzureError):
            azure.recognize('reading.wav', 'The cat sat.')

    def test_real_sdk_config(self):
        try:
            sdk = azure._sdk()
        except azure.AzureError:
            self.skipTest('azure-cognitiveservices-speech is not installed')
        cfg = json.loads(azure.pron_config("I'm 10 years old.\nHe was born in 1995.", sdk).to_json())
        self.assertEqual(cfg['referenceText'], "I'm 10 years old. He was born in 1995.")
        self.assertEqual((cfg['gradingSystem'], cfg['granularity'], cfg['phonemeAlphabet']), ('HundredMark', 'Phoneme', 'IPA'))
        self.assertTrue(cfg['enableMiscue'])
        self.assertTrue(cfg['enableProsodyAssessment'])


# ─── audio conversion ─────────────────────────────────────────────────────────

def stereo_wav(seconds=1.0, rate=44100):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x10\x00\xf0\xff' * int(seconds * rate))
    return buf.getvalue()


class WavFileTests(SimpleTestCase):
    def folders(self):
        made, real = [], tempfile.mkdtemp

        def spy(*a, **kw):
            made.append(real(*a, **kw))
            return made[-1]
        return made, mock.patch.object(azure.tempfile, 'mkdtemp', side_effect=spy)

    @skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not on PATH')
    def test_converts_to_16k_mono_pcm_and_cleans_up(self):
        made, spy = self.folders()
        with spy, azure.wav_file(stereo_wav(1.0)) as (path, seconds):
            with wave.open(path, 'rb') as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth()), (1, 16000, 2))
            self.assertAlmostEqual(seconds, 1.0, delta=0.05)
            with open(path, 'rb') as f:
                self.assertEqual(f.read(44)[36:40], b'data')            # a plain 44-byte header
        self.assertFalse(any(os.path.exists(d) for d in made))

    @skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not on PATH')
    def test_unreadable_upload_fails_and_cleans_up(self):
        made, spy = self.folders()
        with spy, self.assertRaises(azure.AzureError):
            with azure.wav_file(b'<html>not a recording</html>' * 100):
                pass
        self.assertTrue(made)
        self.assertFalse(any(os.path.exists(d) for d in made))

    def test_folder_is_deleted_when_scoring_fails(self):
        made, spy = self.folders()
        ok = mock.Mock(returncode=0, stderr=b'')

        def fake_ffmpeg(cmd, **kw):
            with wave.open(cmd[-1], 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b'\0\0' * 16000)
            return ok
        with spy, mock.patch.object(azure.shutil, 'which', return_value='ffmpeg'), \
                mock.patch.object(azure.subprocess, 'run', side_effect=fake_ffmpeg), self.assertRaises(RuntimeError):
            with azure.wav_file(b'x') as (path, seconds):
                self.assertEqual(seconds, 1.0)
                raise RuntimeError('boom')
        self.assertFalse(any(os.path.exists(d) for d in made))

    def test_without_ffmpeg(self):
        with mock.patch.object(azure.shutil, 'which', return_value=None), self.assertRaises(azure.AzureError):
            with azure.wav_file(b'x'):
                pass


# ─── on / off and price ───────────────────────────────────────────────────────

@override_settings(CACHES=LOCMEM, AZURE_SPEECH_KEY='test-key')
class SwitchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_next_month(self):
        utc = dt_timezone.utc
        self.assertEqual(azure.next_month_start(datetime(2026, 11, 24, 18, 0, tzinfo=utc)), datetime(2026, 12, 1, tzinfo=utc))
        self.assertEqual(azure.next_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=utc)), datetime(2027, 1, 1, tzinfo=utc))

    def test_off_until_next_month(self):
        self.assertTrue(azure.enabled())
        with self.assertLogs('speaking.azure', 'WARNING') as logs:
            until = azure.switch_off_until_next_month('Forbidden: quota')
        self.assertIn('off until', logs.output[0])
        self.assertEqual((until.day, until.hour), (1, 0))
        self.assertFalse(azure.enabled())

    @override_settings(AZURE_SPEECH_KEY='')
    def test_no_key_no_azure(self):
        self.assertFalse(azure.enabled())

    def test_price_comes_from_settings(self):
        with self.settings(AZURE_SPEECH_PRICE_PER_HOUR=0.0):
            self.assertEqual(float(cost_of('azure-pronunciation', seconds=1800)), 0.0)
        with self.settings(AZURE_SPEECH_PRICE_PER_HOUR=1.0):
            self.assertEqual(float(cost_of('azure-pronunciation', seconds=1800)), 0.5)


# ─── tasks.score_attempt: Azure first, Whisper as the fallback ────────────────

def whisper_json(text):
    words, t = [], 0.0
    for w in text.split():
        words.append({'word': w, 'start': t, 'end': t + 0.4})
        t += 0.5
    return {'text': text, 'duration': t, 'words': words, 'segments': [{'start': 0, 'end': t, 'avg_logprob': -0.1}]}


@contextmanager
def fake_wav(data):
    yield 'reading.wav', 12.5


@override_settings(CACHES=LOCMEM, AZURE_SPEECH_KEY='test-key', AZURE_SPEECH_REGION='northeurope',
                   CELERY_TASK_ALWAYS_EAGER=True, AZURE_SPEECH_PRICE_PER_HOUR=0.0)
class ScoreAttemptTests(TestCase):
    TEXT = 'The cat sat on the red mat.'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp(prefix='speaking-media-')
        cls.media_override = override_settings(MEDIA_ROOT=cls.media)
        cls.media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_override.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        logging.disable(logging.WARNING)            # the fallbacks below log on purpose
        self.addCleanup(logging.disable, logging.NOTSET)
        self.user = get_user_model().objects.create_user(username='reader@mail.uz', email='reader@mail.uz', password='x')
        self.lesson = SpeakingLesson.objects.create(title='Cats', level='A1', text=self.TEXT)
        patches = {
            'wav': mock.patch.object(azure, 'wav_file', side_effect=fake_wav),
            'recognize': mock.patch.object(azure, 'recognize'),
            'whisper': mock.patch('speaking.tasks.whisper_words', return_value=whisper_json('The cat sat on the red mat.')),
            'pause': mock.patch('speaking.tasks._pause'),
        }
        self.m = {k: p.start() for k, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)

    def attempt(self):
        a = SpeakingAttempt(user=self.user, lesson=self.lesson, mime='audio/webm', duration_sec=12.0)
        a.audio.save('reading.webm', ContentFile(b'\x1aE\xdf\xa3' + b'\0' * 2000), save=False)
        a.save()
        score_attempt.apply(args=[a.id])
        a.refresh_from_db()
        return a

    def test_azure_scores_every_word_and_sound(self):
        words = said('the cat sat on the red mat', red=45, mat=70)
        words[5]['Phonemes'][0]['PronunciationAssessment']['AccuracyScore'] = 30.0     # r
        self.m['recognize'].return_value = [segment(words)]
        a = self.attempt()
        self.assertEqual((a.status, a.engine), (SpeakingAttempt.READY, 'azure'))
        self.m['whisper'].assert_not_called()
        self.assertEqual(self.m['recognize'].call_args.args, ('reading.wav', self.TEXT, 12.5))
        red = a.words[5]
        self.assertEqual((red['status'], red['score'], red['error'], red['tip']), ('bad', 45, 'Mispronunciation', 'r'))
        self.assertEqual(red['phonemes'][0], {'p': 'r', 'score': 30})
        self.assertEqual((a.ok_count, a.fix_count, a.skip_count), (5, 2, 0))           # fix_count = fix + bad
        self.assertEqual(a.completeness_score, 100)
        self.assertEqual((a.fluency_score, a.prosody_score), (95, 90))
        self.assertIsNotNone(a.pron_score)
        self.assertEqual(a.duration_sec, 12.5)
        usage = AIUsage.objects.get(feature='speaking_game_pron')
        self.assertEqual((usage.model, usage.audio_seconds, float(usage.cost_usd)), ('azure-pronunciation', 12.5, 0.0))

        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.get(f'/api/games/speaking/attempts/{a.id}/', HTTP_ACCEPT='application/json',
                          HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36')
        data = resp.json()
        self.assertEqual((resp.status_code, data.get('engine')), (200, 'azure'), data)
        for key in ('pron_score', 'fluency_score', 'completeness_score', 'prosody_score'):
            self.assertEqual(data[key], getattr(a, key))
        self.assertEqual(data['words'][5]['tip'], 'r')

    def test_busy_azure_is_retried_then_whisper_scores(self):
        self.m['recognize'].side_effect = azure.AzureBusy('TooManyRequests: Too many requests (429)')
        a = self.attempt()
        self.assertEqual(self.m['recognize'].call_count, 4)                            # 1 + 3 retries
        self.assertEqual(self.m['pause'].call_count, 3)
        waits = [c.args[0] for c in self.m['pause'].call_args_list]
        self.assertEqual(waits, sorted(waits))
        self.m['whisper'].assert_called_once()
        self.assertEqual((a.status, a.engine, a.pron_score, a.completeness_score), (SpeakingAttempt.READY, 'whisper', None, None))
        self.assertEqual(set(w['status'] for w in a.words), {'ok'})
        self.assertTrue(azure.enabled())                                               # busy never switches Azure off
        self.assertFalse(AIUsage.objects.filter(feature='speaking_game_pron').exists())

    def test_busy_once_then_azure(self):
        self.m['recognize'].side_effect = [azure.AzureBusy('TooManyRequests'), [segment(said('the cat sat on the red mat'))]]
        a = self.attempt()
        self.assertEqual((a.engine, self.m['pause'].call_count), ('azure', 1))
        self.m['whisper'].assert_not_called()

    def test_used_up_quota_switches_azure_off_until_next_month(self):
        self.m['recognize'].side_effect = azure.AzureOff('Forbidden: Quota exceeded (403)')
        first = self.attempt()
        self.assertEqual((first.status, first.engine), (SpeakingAttempt.READY, 'whisper'))
        self.assertFalse(azure.enabled())
        second = self.attempt()
        self.assertEqual((second.status, second.engine), (SpeakingAttempt.READY, 'whisper'))
        self.assertEqual(self.m['recognize'].call_count, 1)                            # the second went straight to Whisper
        self.assertEqual(self.m['wav'].call_count, 1)
        self.assertEqual(self.m['whisper'].call_count, 2)

    def test_other_azure_failure_falls_back_to_whisper(self):
        self.m['recognize'].side_effect = azure.AzureError('ServiceError: internal error')
        a = self.attempt()
        self.assertEqual((a.status, a.engine), (SpeakingAttempt.READY, 'whisper'))
        self.assertTrue(azure.enabled())

    def test_unexpected_sdk_crash_falls_back_to_whisper(self):
        self.m['recognize'].side_effect = RuntimeError('native library failed')
        self.assertEqual(self.attempt().engine, 'whisper')

    @override_settings(AZURE_SPEECH_KEY='')
    def test_without_a_key_only_whisper(self):
        a = self.attempt()
        self.assertEqual((a.status, a.engine), (SpeakingAttempt.READY, 'whisper'))
        self.m['wav'].assert_not_called()
        self.m['recognize'].assert_not_called()

    def test_no_speech_with_azure(self):
        self.m['recognize'].return_value = []
        a = self.attempt()
        self.assertEqual((a.status, a.error), (SpeakingAttempt.FAILED, 'no-speech'))
        self.m['whisper'].assert_not_called()
        self.m['recognize'].return_value = [segment(said('hello how are you doing today'))]
        a = self.attempt()
        self.assertEqual((a.status, a.error), (SpeakingAttempt.FAILED, 'no-speech'))

    def test_no_speech_with_whisper(self):
        self.m['recognize'].side_effect = azure.AzureError('ServiceError')
        self.m['whisper'].return_value = whisper_json('Thank you.')
        a = self.attempt()
        self.assertEqual((a.status, a.error), (SpeakingAttempt.FAILED, 'no-speech'))
