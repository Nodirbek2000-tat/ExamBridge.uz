"""
Real pronunciation scoring for the Speaking game with Azure AI Speech Pronunciation
Assessment (scripted / reading mode, en-US, IPA phonemes, 0-100 marks).

    enabled()                                   a key is set and Azure was not switched off this month
    with wav_file(data) as (path, seconds):     the browser recording as 16 kHz mono 16-bit PCM WAV,
        segments = recognize(path, text, seconds)   in a temp folder that is always deleted
    result = score_segments(text, segments)     word records + overall scores (shape of scoring.score_reading)
    switch_off_until_next_month(reason)         after "quota used up" / 401 / 403

Errors: AzureBusy (429, worth a retry), AzureOff (quota / key refused), AzureError (anything
else). tasks.score_attempt falls back to Whisper on every one of them.

Why continuous recognition over a file: a reading lasts up to 3 minutes (views.MAX_SECONDS),
the REST short-audio API stops at 60 s and recognize_once() stops at the first pause. In
continuous mode Azure scores each segment (speech between two pauses) on its own and never
reports omitted or inserted words, even with EnableMiscue on (Microsoft's "Use pronunciation
assessment" docs and their pronunciation_assessment_continuous_from_file sample). So the
segments are joined here, the way that sample does it:

  * every word Azure heard is lined up with the words of the text by the same normalisation
    and alignment as the Whisper path (speaking/scoring.py), so "10" / "ten", "I'm" / "i am",
    "1995" / "nineteen ninety five" and punctuation all match;
  * a word of the text that no heard word lines up with is an Omission (score 0, grey 'skip');
    heard words that are not in the text are ignored (Insertions);
  * a word's score is Azure's word AccuracyScore (a word heard as a *similar* word, "walk" for
    "walked", is capped below green); every phoneme keeps its own AccuracyScore, and `tip` is
    the weakest phoneme when it is below 60;
  * accuracy     = mean word score over the words of the text, an omitted word counting 0
                   (the sample does the same; it also keeps old Whisper and new Azure accuracies
                   comparable for "best accuracy");
    completeness = words said / words of the text;
    fluency, prosody = Azure's own per-segment FluencyScore / ProsodyScore, weighted by the
                   segment's duration; fluency also loses the share of the reading spent in long
                   pauses between segments (beyond 1 s each), which Azure never sees;
    pron_score   = Microsoft's weighting of the sorted scores, the lowest weighing most:
                   0.4 / 0.2 / 0.2 / 0.2 with prosody, 0.6 / 0.2 / 0.2 without.

Statuses: 'ok' >= 80 (green), 'fix' 60-79 (yellow), 'bad' < 60 but said (red), 'skip' not said.
"""
import gc
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache

from .scoring import align, reference_words, say_text, tokens

log = logging.getLogger(__name__)

LANGUAGE = 'en-US'                 # prosody assessment exists for en-US only
SEGMENT_SILENCE_MS = 1500          # as in Microsoft's continuous sample: fewer, longer segments
MAX_AUDIO_SECONDS = 200            # views.MAX_SECONDS is 180; never send Azure more than this
FFMPEG_TIMEOUT = 90
TICKS = 10_000_000                 # Offset / Duration are in 100-ns ticks

OK_AT = 80                         # >= 80 green
FIX_AT = 60                        # 60-79 yellow, below red (Azure's own "Mispronunciation" line)
TIP_BELOW = 60                     # a phoneme under this is the sound to practise
NEAR_CAP = OK_AT - 1               # a word heard as a different, similar word is never green
BREAK_CONFIDENCE = 0.75            # Microsoft's suggested threshold for UnexpectedBreak / MissingBreak
# ...but real answers carry MissingBreak confidence 1.0 on almost every word, so a break flag
# only counts when the timing agrees: a pause inside a clause, none after "." / ","
PAUSE_SECONDS = 0.3
NO_PAUSE_SECONDS = 0.15
PAUSE_ALLOWANCE = 1.0              # a pause between two segments costs fluency beyond this
ERRORS = ('Mispronunciation', 'UnexpectedBreak', 'MissingBreak', 'Monotone')   # most important first
OFF_KEY = 'speaking:azure-pron:off-until'

# the word before ends a clause, so a pause is expected before this one
_PUNCT_END = re.compile(r'[.,;:!?…]["”’)\]]*$')
# Azure's words for "the month's free quota is used up" (concurrency throttling says "Too many requests")
_QUOTA_GONE = re.compile(r'quota (?:is |has been )?(?:exceeded|exhausted|used up|reached)|out of (?:call volume )?quota'
                         r'|exceeded (?:the |your )?(?:monthly )?quota|quota will be replenished', re.I)


class AzureError(Exception):
    """Azure could not score this recording — the Whisper path takes over."""


class AzureBusy(AzureError):
    """429: too many requests at once (the free F0 tier allows very few) — worth a retry."""


class AzureOff(AzureError):
    """The month's free quota is used up, or the key / region is refused (401 / 403)."""


# ─── on / off ─────────────────────────────────────────────────────────────────

def next_month_start(now=None):
    now = now or datetime.now(dt_timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=dt_timezone.utc)


def enabled():
    """A key is configured and Azure was not switched off for the rest of the month."""
    if not getattr(settings, 'AZURE_SPEECH_KEY', ''):
        return False
    try:
        until = float(cache.get(OFF_KEY) or 0)
    except Exception:              # cache unreachable, or a value this code did not write
        log.warning('Azure off flag unreadable — Azure pronunciation assessment is tried anyway', exc_info=True)
        return True
    return not until > time.time()


def switch_off_until_next_month(reason=''):
    """Skip Azure until the 1st of next month (UTC): the free F0 quota is used up or the key is refused."""
    until = next_month_start()
    seconds = max(60, int((until - datetime.now(dt_timezone.utc)).total_seconds()))
    try:
        cache.set(OFF_KEY, until.timestamp(), seconds)
    except Exception:
        log.warning('Could not store the Azure off flag', exc_info=True)
    log.warning('Azure pronunciation assessment is off until %s (Whisper scores readings): %s',
                until.isoformat(), str(reason)[:300])
    return until


# ─── audio ────────────────────────────────────────────────────────────────────

def _remove(folder):
    for _ in range(10):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:            # Windows: the SDK may still hold the file for a moment
            gc.collect()
            time.sleep(0.2)
    log.warning('Temp folder %s could not be deleted', folder)


def wav_seconds(path):
    with wave.open(path, 'rb') as w:
        return w.getnframes() / float(w.getframerate() or 16000)


@contextmanager
def wav_file(data):
    """The recording (webm / ogg / mp4 / mp3 / wav) as 16 kHz mono 16-bit PCM WAV → (path, seconds).
    Both the copy of the upload and the WAV live in one temp folder that is always deleted."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise AzureError('ffmpeg is not installed (it converts the recording for Azure)')
    folder = tempfile.mkdtemp(prefix='speaking-azure-')
    try:
        src = os.path.join(folder, 'upload')
        dst = os.path.join(folder, 'reading.wav')
        with open(src, 'wb') as f:
            f.write(data or b'')
        cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
               '-t', str(MAX_AUDIO_SECONDS), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
               # a plain 44-byte header: no LIST / encoder chunks for the SDK's WAV reader
               '-map_metadata', '-1', '-fflags', '+bitexact', '-flags:a', '+bitexact', '-f', 'wav', dst]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise AzureError('ffmpeg took too long to convert the recording') from None
        if proc.returncode != 0 or not os.path.exists(dst):
            raise AzureError('ffmpeg could not convert the recording: '
                             + proc.stderr.decode('utf-8', 'replace').strip()[-300:])
        try:
            seconds = wav_seconds(dst)
        except (wave.Error, EOFError, OSError) as exc:
            raise AzureError(f'the converted recording is not a readable WAV: {exc}') from exc
        yield dst, seconds
    finally:
        _remove(folder)


# ─── Azure Speech SDK ─────────────────────────────────────────────────────────

def _sdk():
    """The SDK is imported on first use: inside each Celery child, never before the fork."""
    try:
        import azure.cognitiveservices.speech as speechsdk
    except Exception as exc:      # not installed, or its native library (OpenSSL / ALSA) is missing
        raise AzureError(f'Azure Speech SDK is not available: {exc}') from exc
    return speechsdk


def pron_config(reference_text, sdk=None):
    sdk = sdk or _sdk()
    cfg = sdk.PronunciationAssessmentConfig(
        reference_text=' '.join(str(reference_text or '').split()),
        grading_system=sdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=sdk.PronunciationAssessmentGranularity.Phoneme,
        # the service ignores it in continuous mode (omissions are found in score_segments);
        # kept on as in Microsoft's continuous sample
        enable_miscue=True)
    cfg.phoneme_alphabet = 'IPA'
    if hasattr(cfg, 'enable_prosody_assessment'):      # SDK >= 1.35
        cfg.enable_prosody_assessment()
    return cfg


def classify(code, details=''):
    """A cancellation from the SDK → the AzureError subclass that says what to do next."""
    name = getattr(code, 'name', None) or str(code or 'Error')
    text = ' '.join(str(details or '').split())[:300]
    message = f'{name}: {text}' if text else name
    if name in ('AuthenticationFailure', 'Forbidden') or _QUOTA_GONE.search(text):
        return AzureOff(message)
    if name == 'TooManyRequests':
        return AzureBusy(message)
    return AzureError(message)


def _wait_seconds(seconds):
    """The file is read faster than real time; a stuck session must not hold the worker forever."""
    return max(60.0, float(seconds or 0) * 1.5 + 30)


def recognize(path, reference_text, seconds=0):
    """Continuous pronunciation assessment of a WAV file → the JSON of every recognised segment."""
    key = getattr(settings, 'AZURE_SPEECH_KEY', '')
    region = getattr(settings, 'AZURE_SPEECH_REGION', '')
    if not key or not region:
        raise AzureError('Azure Speech is not configured')
    sdk = _sdk()
    segments, failures = [], []
    done = threading.Event()
    try:
        speech_config = sdk.SpeechConfig(subscription=key, region=region)
        speech_config.set_property(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, str(SEGMENT_SILENCE_MS))
        audio_config = sdk.audio.AudioConfig(filename=path)
        recognizer = sdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config, language=LANGUAGE)
        pron_config(reference_text, sdk).apply_to(recognizer)
    except Exception as exc:
        raise AzureError(f'Azure Speech SDK setup failed: {type(exc).__name__}: {str(exc)[:200]}') from exc

    def on_recognized(evt):
        try:
            result = evt.result
            if result.reason != sdk.ResultReason.RecognizedSpeech:
                return                                  # NoMatch: a stretch of noise / silence
            segments.append(json.loads(result.properties.get(sdk.PropertyId.SpeechServiceResponse_JsonResult)))
        except Exception:
            log.warning('An Azure pronunciation segment could not be read', exc_info=True)

    def on_canceled(evt):
        try:
            details = evt.cancellation_details
            if details.reason == sdk.CancellationReason.Error:     # (EndOfStream = the file is done)
                failures.append((details.code, details.error_details))
        except Exception:
            failures.append(('Error', 'unreadable cancellation'))
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda evt: done.set())
    timeout = _wait_seconds(seconds)
    try:
        try:
            recognizer.start_continuous_recognition()
        except Exception as exc:
            raise AzureError(f'Azure recognition did not start: {type(exc).__name__}: {str(exc)[:200]}') from exc
        finished = done.wait(timeout)
    finally:
        try:
            recognizer.stop_continuous_recognition()
        except Exception:
            log.info('Azure stop_continuous_recognition failed', exc_info=True)
        for signal in (recognizer.recognized, recognizer.canceled, recognizer.session_stopped):
            try:
                signal.disconnect_all()
            except Exception:
                pass
        del recognizer, audio_config          # lets the SDK close the WAV before the folder is deleted
    if failures:
        raise classify(*failures[0])
    if not finished:
        raise AzureError(f'no answer from Azure within {timeout:.0f} s')
    return segments


# ─── scoring ──────────────────────────────────────────────────────────────────

def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _mark(value):
    """0-100, 0 when missing."""
    return max(0.0, min(100.0, _num(value) or 0.0))


def _best(segment):
    nbest = segment.get('NBest') or []
    return nbest[0] if nbest and isinstance(nbest[0], dict) else {}


def _prosody_flags(pa):
    """UnexpectedBreak / MissingBreak before the word, Monotone on its utterance (Feedback.Prosody)."""
    prosody = ((pa.get('Feedback') or {}).get('Prosody') or {})
    brk = prosody.get('Break') or {}
    flags = {t for t in (brk.get('ErrorTypes') or []) if t in ('UnexpectedBreak', 'MissingBreak')}
    for kind in ('UnexpectedBreak', 'MissingBreak'):
        if (_num((brk.get(kind) or {}).get('Confidence')) or 0) > BREAK_CONFIDENCE:
            flags.add(kind)
    if 'Monotone' in ((prosody.get('Intonation') or {}).get('ErrorTypes') or []):
        flags.add('Monotone')
    return flags


def heard_words(segments):
    """Every word Azure heard, in order: {word, start, end (s), score, error, flags, phonemes}."""
    out = []
    for segment in segments:
        if segment.get('RecognitionStatus') not in (None, 0, 'Success'):
            continue
        for w in _best(segment).get('Words') or []:
            if not isinstance(w, dict):
                continue
            pa = w.get('PronunciationAssessment') or {}
            error = pa.get('ErrorType') or 'None'
            text = str(w.get('Word') or '').strip()
            # Omission / Insertion only come with miscue in single-shot mode; omissions are
            # worked out over the whole text below, so a word "omitted" from one segment
            # but read in the next is not lost
            if not text or error in ('Omission', 'Insertion'):
                continue
            offset, duration = _num(w.get('Offset')) or 0.0, _num(w.get('Duration')) or 0.0
            phonemes = []
            for ph in w.get('Phonemes') or []:
                p = str((ph or {}).get('Phoneme') or '').strip()
                if p:
                    phonemes.append({'p': p, 'score': round(_mark((ph.get('PronunciationAssessment') or {})
                                                                  .get('AccuracyScore')))})
            out.append({'word': text, 'start': offset / TICKS, 'end': (offset + max(0.0, duration)) / TICKS,
                        'score': _mark(pa.get('AccuracyScore')), 'error': error if error in ERRORS else '',
                        'flags': _prosody_flags(pa), 'phonemes': phonemes})
    return out


def _span(segment):
    """(start, end) in seconds of the speech in one segment, or None."""
    offset, duration = _num(segment.get('Offset')), _num(segment.get('Duration'))
    if offset is not None and duration:
        return offset / TICKS, (offset + duration) / TICKS
    timed = [w for w in _best(segment).get('Words') or [] if isinstance(w, dict) and _num(w.get('Offset')) is not None]
    if not timed:
        return None
    last = timed[-1]
    return _num(timed[0]['Offset']) / TICKS, (_num(last['Offset']) + (_num(last.get('Duration')) or 0)) / TICKS


def _weighted(segments, key):
    """A segment-level score over the whole reading, each segment weighted by its duration."""
    total = weight = 0.0
    for segment in segments:
        best = _best(segment)
        value = _num((best.get('PronunciationAssessment') or {}).get(key))
        if value is None:
            continue
        span = _span(segment)
        w = (span[1] - span[0]) if span else 0.0
        total += max(0.0, min(100.0, value)) * (w if w > 0 else 0.1)
        weight += w if w > 0 else 0.1
    return total / weight if weight else None


def _pause_factor(segments):
    """Share of the reading not lost to long pauses *between* segments, 0-1. Azure judges the pauses
    inside a segment itself; a silence of >= 1.5 s splits the reading, so those hesitations would go
    unseen. Each one counts beyond PAUSE_ALLOWANCE (a natural pause between sentences) — the same idea
    as Microsoft's continuous sample, which divides speaking time by the whole span."""
    spans = sorted(s for s in (_span(seg) for seg in segments) if s)
    if len(spans) < 2:
        return 1.0
    total = spans[-1][1] - spans[0][0]
    lost = sum(max(0.0, b[0] - a[1] - PAUSE_ALLOWANCE) for a, b in zip(spans, spans[1:]))
    return max(0.0, min(1.0, (total - lost) / total)) if total > 0 else 1.0


def pron_score(accuracy, fluency, completeness, prosody):
    """Microsoft's overall score for a scripted reading: the sorted scores, the lowest weighing most."""
    if fluency is None:
        scores, weights = sorted([accuracy, completeness]), (0.6, 0.4)
    elif prosody is None:
        scores, weights = sorted([accuracy, fluency, completeness]), (0.6, 0.2, 0.2)
    else:
        scores, weights = sorted([accuracy, prosody, completeness, fluency]), (0.4, 0.2, 0.2, 0.2)
    return round(sum(s * w for s, w in zip(scores, weights)))


def score_segments(reference_text, segments):
    segments = [s for s in (segments or []) if isinstance(s, dict)]
    shown = reference_words(reference_text)
    ref, owner = [], []
    for wi, w in enumerate(shown):
        for t in tokens(w):
            ref.append(t)
            owner.append(wi)

    hwords = heard_words(segments)
    heard, howner = [], []
    for hi, w in enumerate(hwords):
        for t in tokens(w['word']):
            heard.append(t)
            howner.append(hi)

    # per word of the text: (score or None when not said, near match?, Azure word indexes)
    parts = [[] for _ in shown]
    for k, (sim, hidx) in enumerate(align(ref, heard)):
        hw = sorted({howner[j] for j in hidx})
        if not sim or not hw:
            parts[owner[k]].append((None, False, []))
            continue
        score = sum(hwords[h]['score'] for h in hw) / len(hw)
        if sim < 1.0:                     # "walk" for "walked": a sound is missing, so never green
            score = min(score, NEAR_CAP)
        parts[owner[k]].append((score, sim < 1.0, hw))

    words = []
    prev_end = None                       # end of the previous word of the text, when it was said
    for wi, w in enumerate(shown):
        ps = parts[wi]
        said = [p for p in ps if p[0] is not None]
        item = {'i': wi, 'word': w, 'say': say_text(w)}
        if not said:
            item.update({'score': 0, 'status': 'skip', 'heard': '', 'start': None, 'end': None,
                         'phonemes': [], 'error': 'Omission', 'tip': ''})
            words.append(item)
            prev_end = None
            continue
        hw = sorted({h for p in said for h in p[2]})
        score = round(sum(p[0] for p in said) / len(ps))          # a part not said counts 0
        status = 'ok' if score >= OK_AT else 'fix' if score >= FIX_AT else 'bad'
        phonemes = [dict(ph) for h in hw for ph in hwords[h]['phonemes']]
        weakest = min(phonemes, key=lambda ph: ph['score']) if phonemes else None

        errors = {hwords[h]['error'] for h in hw} - {''}
        # break flags are about the pause before the word: wrong inside a clause (UnexpectedBreak),
        # missing after "." / "," (MissingBreak) — and only when the word timings show it
        after_punct = wi > 0 and bool(_PUNCT_END.search(shown[wi - 1]))
        gap = hwords[hw[0]]['start'] - prev_end if prev_end is not None else None
        for flag in hwords[hw[0]]['flags']:
            if flag == 'Monotone':
                errors.add(flag)
            elif gap is None:
                continue
            elif flag == 'UnexpectedBreak' and not after_punct and gap >= PAUSE_SECONDS:
                errors.add(flag)
            elif flag == 'MissingBreak' and after_punct and gap < NO_PAUSE_SECONDS:
                errors.add(flag)
        prev_end = hwords[hw[-1]]['end']
        if score < FIX_AT or len(said) < len(ps) or any(p[1] for p in said):
            errors.add('Mispronunciation')
        item.update({
            'score': score, 'status': status,
            'heard': ' '.join(say_text(hwords[h]['word']) for h in hw)[:60],
            'start': round(hwords[hw[0]]['start'], 2), 'end': round(hwords[hw[-1]]['end'], 2),
            'phonemes': phonemes,
            'error': next((e for e in ERRORS if e in errors), ''),
            'tip': weakest['p'] if weakest and weakest['score'] < TIP_BELOW else '',
        })
        words.append(item)

    n = len(words)
    accuracy = round(sum(w['score'] for w in words) / n) if n else 0
    completeness = round(100 * sum(w['status'] != 'skip' for w in words) / n) if n else 0
    fluency = _weighted(segments, 'FluencyScore')
    if fluency is not None:
        fluency = round(fluency * _pause_factor(segments))
    prosody = _weighted(segments, 'ProsodyScore')
    if prosody is not None:
        prosody = round(prosody)
    wpm = 0
    if len(hwords) >= 3:
        span = hwords[-1]['end'] - hwords[0]['start']
        if span >= 2:
            wpm = min(400, round(len(hwords) / span * 60))
    transcript = ' '.join(str(s.get('DisplayText') or _best(s).get('Display') or '').strip() for s in segments)
    return {
        'words': words,
        'accuracy': accuracy,
        'ok': sum(w['status'] == 'ok' for w in words),
        'fix': sum(w['status'] == 'fix' for w in words),
        'bad': sum(w['status'] == 'bad' for w in words),
        'skip': sum(w['status'] == 'skip' for w in words),
        'fluency_wpm': wpm,
        'transcript': ' '.join(transcript.split()),
        'heard_any': bool(heard),
        # nothing of the text was said (silence, or a different text): the learner reads again
        'read_the_text': any(w['status'] != 'skip' for w in words),
        'pron_score': pron_score(accuracy, fluency, completeness, prosody) if n else 0,
        'fluency_score': fluency,
        'completeness_score': completeness,
        'prosody_score': prosody,
    }
