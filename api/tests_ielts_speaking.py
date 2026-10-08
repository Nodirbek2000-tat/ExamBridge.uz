"""
IELTS speaking for native clients: recordings keep their own format, and answers that arrive
without text are transcribed from the recording with Whisper before scoring.

    python manage.py test api.tests_ielts_speaking

Whisper and the GPT scorer are always mocked (a real network call fails the test), the cache
is a local in-memory one and recordings go to a temporary MEDIA_ROOT.
"""
import json
import os
import shutil
import tempfile
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from api import ielts_views
from api.stt import audio_ext
from ielts.models import IELTSAttempt, SpeakingResponse, SpeakingTask

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'ielts-speaking-tests'}}
BROWSER = dict(HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0 Safari/537.36',
               HTTP_ACCEPT='application/json', HTTP_HOST='localhost')
PHONE = dict(BROWSER, HTTP_USER_AGENT='ExamBridgeApp/1.0 (android; sdk57)')
M4A = b'\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00' + b'\x00' * 500
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 500

AI_REPLY = {
    'overall_band': 6.5,
    **{k: {'band': 6.5, 'label': k, 'feedback': 'ok', 'strengths': [], 'errors': []}
       for k in ('fluency_coherence', 'lexical_resource', 'grammatical_range', 'pronunciation')},
    'good_phrases': ['a real eye-opener'], 'answer_corrections': [],
}


def m4a(i=0, body=M4A):
    return SimpleUploadedFile(f'answer_{i}.m4a', body, content_type='audio/mp4')


def web_blob(i=0, body=WEBM):
    # the web page appends `new Blob(chunks)` (no type) as `q<i>.webm`
    return SimpleUploadedFile(f'q{i}.webm', body, content_type='application/octet-stream')


class AudioExtTests(SimpleTestCase):
    def test_extension_from_type_then_name(self):
        cases = [
            (('audio/mp4', 'answer_0.m4a'), 'm4a'), (('audio/x-m4a', ''), 'm4a'), (('audio/aac', ''), 'm4a'),
            (('audio/mp4;codecs=mp4a.40.2', ''), 'm4a'), (('application/octet-stream', 'answer.m4a'), 'm4a'),
            (('', 'clip.mp4'), 'm4a'), (('', 'clip.AAC'), 'm4a'),
            (('audio/webm;codecs=opus', ''), 'webm'), (('video/webm', ''), 'webm'),
            (('application/octet-stream', 'q0.webm'), 'webm'),
            (('audio/ogg; codecs=opus', ''), 'ogg'), (('', 'x.ogg'), 'ogg'),
            (('audio/wav', ''), 'wav'), (('audio/x-wav', ''), 'wav'), (('', 'x.wav'), 'wav'),
            (('audio/mpeg', ''), 'mp3'), (('', 'x.mp3'), 'mp3'),
            # unknown → webm, never a dangerous extension from the client
            (('', ''), 'webm'), ((None, None), 'webm'), (('text/html', 'evil.html'), 'webm'), (('', 'x.svg'), 'webm'),
        ]
        for (ctype, name), ext in cases:
            with self.subTest(ctype=ctype, name=name):
                self.assertEqual(audio_ext(ctype, name), ext)


@override_settings(CACHES=LOCMEM, OPENAI_API_KEY='sk-test-never-used')
class SpeakingSubmitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create(username='spk1@example.test', email='spk1@example.test')
        cls.task = SpeakingTask.objects.create(title='Hometown', part=1, questions=['Where are you from?', 'Do you like it?'])

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix='ielts-speaking-tests-')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        # never touch the real local media folder (attempt ids here can match real file names)
        self.assertTrue(os.path.samefile(default_storage.location, self.media))

        # no real OpenAI call can happen: the transport itself fails loudly
        for target in ('api.stt.requests.post', 'api.ielts_views.urllib.request.urlopen'):
            p = mock.patch(target, side_effect=AssertionError(f'real network call: {target}'))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch('api.ielts_views.whisper_transcribe', return_value='I am from Tashkent, the capital.')
        self.whisper = p.start()
        self.addCleanup(p.stop)

        self.attempt = IELTSAttempt.objects.create(user=self.user)

    def client_for(self, headers=BROWSER):
        c = APIClient(**headers)
        c.force_authenticate(self.user)
        return c

    def submit(self, answers, files=None, headers=BROWSER):
        data = {'task_id': str(self.task.id), 'transcripts': json.dumps(answers), **(files or {})}
        r = self.client_for(headers).post(f'/api/ielts/speaking/{self.attempt.id}/submit/', data, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def stored_path(self, name):
        return os.path.join(self.media, 'ielts', 'speaking', name)

    # ── (a) the recording keeps its format ─────────────────────────────────

    def test_m4a_upload_keeps_m4a(self):
        body = self.submit([{'question': 'Q1', 'transcript': 'I live in Tashkent.'}], {'audio_0': m4a()}, headers=PHONE)
        url = body['transcripts'][0]['audio_url']
        self.assertTrue(url.endswith(f'/media/ielts/speaking/attempt_{self.attempt.id}_q0.m4a'), url)
        with open(self.stored_path(f'attempt_{self.attempt.id}_q0.m4a'), 'rb') as fh:
            self.assertEqual(fh.read(), M4A)
        self.assertFalse(os.path.exists(self.stored_path(f'attempt_{self.attempt.id}_q0.webm')))
        self.whisper.assert_not_called()

    def test_web_upload_still_webm(self):
        body = self.submit([{'question': 'Q1', 'transcript': 'Hello there.'}], {'audio_0': web_blob()})
        self.assertTrue(body['transcripts'][0]['audio_url'].endswith(f'attempt_{self.attempt.id}_q0.webm'))
        self.assertTrue(os.path.exists(self.stored_path(f'attempt_{self.attempt.id}_q0.webm')))

    def test_other_formats(self):
        files = {'audio_0': SimpleUploadedFile('a', b'OggS' + b'\0' * 100, content_type='audio/ogg'),
                 'audio_1': SimpleUploadedFile('b.wav', b'RIFF' + b'\0' * 100, content_type='application/octet-stream'),
                 'audio_2': SimpleUploadedFile('c', b'ID3' + b'\0' * 100, content_type='audio/mpeg')}
        body = self.submit([{'question': f'Q{i}', 'transcript': 'words'} for i in range(3)], files)
        self.assertEqual([t['audio_url'].rsplit('.', 1)[1] for t in body['transcripts']], ['ogg', 'wav', 'mp3'])

    # ── (b) Whisper fallback ───────────────────────────────────────────────

    def test_empty_transcript_is_transcribed_once(self):
        body = self.submit([{'question': 'Where are you from?', 'transcript': ''}], {'audio_0': m4a()}, headers=PHONE)
        self.assertEqual(self.whisper.call_count, 1)
        args, kwargs = self.whisper.call_args
        self.assertEqual(args, (M4A, 'audio/mp4'))
        self.assertEqual(kwargs, {'timeout': 60})
        answer = body['transcripts'][0]
        self.assertEqual((answer['transcript'], answer['stt']), ('I am from Tashkent, the capital.', 'whisper'))
        stored = SpeakingResponse.objects.get(attempt=self.attempt, task=self.task).transcripts[0]
        self.assertEqual((stored['transcript'], stored['stt']), ('I am from Tashkent, the capital.', 'whisper'))

    def test_placeholder_is_transcribed_once(self):
        body = self.submit([{'question': 'Q1', 'transcript': '  (No Transcript) '}], {'audio_0': web_blob()})
        self.assertEqual(self.whisper.call_count, 1)
        self.assertEqual(self.whisper.call_args.args, (WEBM, 'audio/webm'))
        self.assertEqual(body['transcripts'][0]['transcript'], 'I am from Tashkent, the capital.')

    def test_browser_transcript_means_no_whisper(self):
        answers = [{'question': 'Q1', 'transcript': 'I am from Samarkand.'}, {'question': 'Q2', 'transcript': 'Yes, a lot.'}]
        body = self.submit(answers, {'audio_0': web_blob(0), 'audio_1': web_blob(1)})
        self.whisper.assert_not_called()
        self.assertEqual([t['transcript'] for t in body['transcripts']], ['I am from Samarkand.', 'Yes, a lot.'])
        self.assertTrue(all('stt' not in t for t in body['transcripts']))

    def test_only_empty_answers_with_a_recording_are_transcribed(self):
        answers = [{'question': 'Q1', 'transcript': 'Spoken in the browser.'},
                   {'question': 'Q2', 'transcript': '(no transcript)'},
                   {'question': 'Q3', 'transcript': ''}]                       # no recording for Q3
        body = self.submit(answers, {'audio_0': web_blob(0), 'audio_1': m4a(1)})
        self.assertEqual(self.whisper.call_count, 1)
        self.assertEqual([t['transcript'] for t in body['transcripts']],
                         ['Spoken in the browser.', 'I am from Tashkent, the capital.', ''])

    def test_each_answer_gets_its_own_transcript(self):
        clips = [M4A + bytes([i]) * 10 for i in range(5)]
        self.whisper.side_effect = lambda data, mime, timeout: f'answer {clips.index(data)}'
        body = self.submit([{'question': f'Q{i}', 'transcript': ''} for i in range(5)],
                           {f'audio_{i}': m4a(i, clips[i]) for i in range(5)}, headers=PHONE)
        self.assertEqual(self.whisper.call_count, 5)
        self.assertEqual([t['transcript'] for t in body['transcripts']], [f'answer {i}' for i in range(5)])

    def test_whisper_failure_degrades_like_an_empty_answer(self):
        self.whisper.side_effect = RuntimeError('OpenAI is down')
        with self.assertLogs('api.ielts_views', level='WARNING'):
            body = self.submit([{'question': 'Q1', 'transcript': ''}, {'question': 'Q2', 'transcript': '(no transcript)'}],
                               {'audio_0': m4a(0), 'audio_1': m4a(1)}, headers=PHONE)
        self.assertEqual(self.whisper.call_count, 2)
        self.assertEqual([t['transcript'] for t in body['transcripts']], ['', '(no transcript)'])
        self.assertTrue(all('stt' not in t for t in body['transcripts']))
        self.assertTrue(body['transcripts'][0]['audio_url'].endswith('_q0.m4a'))       # the recording is still kept

        # scoring: band 0 without calling the AI, exactly as for an empty answer today
        with mock.patch('api.ielts_views.urllib.request.urlopen') as ai:
            r = self.client_for(PHONE).post('/api/ielts/speaking/analyze/', {
                'transcripts': body['transcripts'], 'test_type': 'PART', 'parts_info': 'Part 1',
                'response_id': body['id']}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['overall_band'], 0)
        ai.assert_not_called()
        self.assertEqual(SpeakingResponse.objects.get(id=body['id']).ai_band, 0)

    def test_silent_clip_keeps_its_placeholder(self):
        self.whisper.return_value = '   '
        body = self.submit([{'question': 'Q1', 'transcript': '(no transcript)'}], {'audio_0': m4a()}, headers=PHONE)
        self.assertEqual(self.whisper.call_count, 1)
        self.assertEqual(body['transcripts'][0]['transcript'], '(no transcript)')
        self.assertNotIn('stt', body['transcripts'][0])

    def test_whisper_silence_phrases_are_not_an_answer(self):
        # on a silent clip Whisper often writes "Thank you." / "you" (speaking/tasks.py) or only punctuation
        for heard in ('Thank you.', 'you', ' Thanks for watching! ', '...', 'Bye-bye.'):
            with self.subTest(heard=heard):
                self.whisper.return_value = heard
                body = self.submit([{'question': 'Q1', 'transcript': '(no transcript)'}], {'audio_0': m4a()}, headers=PHONE)
                answer = body['transcripts'][0]
                self.assertEqual(answer['transcript'], '(no transcript)')
                self.assertNotIn('stt', answer)
                with mock.patch('api.ielts_views.urllib.request.urlopen') as ai:
                    r = self.client_for(PHONE).post('/api/ielts/speaking/analyze/', {
                        'transcripts': body['transcripts'], 'test_type': 'PART', 'response_id': body['id']}, format='json')
                self.assertEqual(r.json()['overall_band'], 0)
                ai.assert_not_called()

    def test_a_real_answer_with_thank_you_is_kept(self):
        self.whisper.return_value = 'Thank you, I really love my hometown.'
        body = self.submit([{'question': 'Q1', 'transcript': ''}], {'audio_0': m4a()}, headers=PHONE)
        self.assertEqual(body['transcripts'][0]['transcript'], 'Thank you, I really love my hometown.')

    def test_whisper_is_told_the_real_format(self):
        # Safari (before 18.4) records mp4, and the web page uploads it as a typeless Blob named q0.webm:
        # the file is stored exactly as before, but Whisper is told it is mp4
        body = self.submit([{'question': 'Q1', 'transcript': '(no transcript)'}], {'audio_0': web_blob(0, M4A)})
        self.assertTrue(body['transcripts'][0]['audio_url'].endswith('_q0.webm'))
        self.assertEqual(self.whisper.call_args.args, (M4A, 'audio/mp4'))
        # content that cannot be recognised keeps the stored extension's type (a new attempt: a
        # re-submission of the same one reuses the text Whisper already gave, see tests_speaking_limit)
        self.whisper.reset_mock()
        self.attempt = IELTSAttempt.objects.create(user=self.user)
        self.submit([{'question': 'Q1', 'transcript': ''}],
                    {'audio_0': SimpleUploadedFile('a.ogg', b'\x00' * 300, content_type='audio/ogg')}, headers=PHONE)
        self.assertEqual(self.whisper.call_args.args[1], 'audio/ogg')

    def test_too_big_clip_is_not_sent(self):
        with mock.patch.object(ielts_views, 'STT_MAX_BYTES', 100):
            body = self.submit([{'question': 'Q1', 'transcript': ''}], {'audio_0': m4a()}, headers=PHONE)
        self.whisper.assert_not_called()
        self.assertEqual(body['transcripts'][0]['transcript'], '')

    def test_late_clip_stays_empty(self):
        def slow(data, mime, timeout):
            time.sleep(1.5)
            return 'too late'
        self.whisper.side_effect = slow
        started = time.monotonic()
        with mock.patch.object(ielts_views, 'STT_BUDGET_SECONDS', 0.1):
            body = self.submit([{'question': 'Q1', 'transcript': ''}], {'audio_0': m4a()}, headers=PHONE)
        self.assertLess(time.monotonic() - started, 1.2)          # did not wait for the slow clip
        self.assertEqual(body['transcripts'][0]['transcript'], '')
        time.sleep(1.5)                                            # let the worker finish before the mock is undone

    def test_transcribed_answer_is_scored_by_the_ai(self):
        body = self.submit([{'question': 'Where are you from?', 'transcript': ''}], {'audio_0': m4a()}, headers=PHONE)
        review = self.client_for(PHONE).get(f'/api/ielts/speaking/review/{body["id"]}/').json()
        self.assertEqual(review['transcripts'][0]['transcript'], 'I am from Tashkent, the capital.')

        reply = mock.MagicMock()
        reply.__enter__.return_value.read.return_value = json.dumps(
            {'choices': [{'message': {'content': json.dumps(AI_REPLY)}}]}).encode()
        with mock.patch('api.ielts_views.urllib.request.urlopen', return_value=reply) as ai:
            r = self.client_for(PHONE).post('/api/ielts/speaking/analyze/', {
                'transcripts': review['transcripts'], 'test_type': 'PART', 'parts_info': 'Part 1',
                'response_id': body['id']}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ai.call_count, 1)
        prompt = json.loads(ai.call_args.args[0].data)['messages'][1]['content']
        self.assertIn('A1: I am from Tashkent, the capital.', prompt)
        self.assertEqual(float(SpeakingResponse.objects.get(id=body['id']).ai_band), 6.5)
