import re
import difflib

import requests
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .models import ShadowingText, ShadowingAttempt


def _tokenize(text):
    """Lowercase word tokens, punctuation stripped — used for alignment."""
    return re.findall(r"[a-zA-Z']+", (text or '').lower())


def _display_words(text):
    """Words as they should be shown to the reader — keeps original casing/punctuation-adjacent form."""
    return (text or '').split()


def _transcribe(audio_file):
    """OpenAI Whisper transcription. Returns '' on any failure (never raises to the caller)."""
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return ''
    try:
        resp = requests.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {api_key}'},
            files={'file': ('shadowing.webm', audio_file.read(), audio_file.content_type or 'audio/webm')},
            data={'model': 'whisper-1', 'language': 'en'},
            timeout=45,
        )
        resp.raise_for_status()
        return resp.json().get('text', '')
    except Exception:
        return ''


def _score_words(ref_display_words, said_tokens):
    """
    Aligns reference words against what Whisper heard and scores each reference
    word 0-100. Returns (word_results, correct, flagged, skipped).
    """
    ref_tokens = [re.sub(r"[^a-zA-Z']", '', w).lower() for w in ref_display_words]
    matcher = difflib.SequenceMatcher(None, ref_tokens, said_tokens, autojunk=False)

    results = [None] * len(ref_display_words)
    correct = flagged = skipped = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for k in range(i1, i2):
                results[k] = {'word': ref_display_words[k], 'status': 'correct', 'score': 100}
                correct += 1
        elif tag == 'replace':
            ref_slice = list(range(i1, i2))
            said_slice = said_tokens[j1:j2]
            for idx, k in enumerate(ref_slice):
                said_word = said_slice[idx] if idx < len(said_slice) else ''
                if said_word:
                    ratio = difflib.SequenceMatcher(None, ref_tokens[k], said_word).ratio()
                    score = max(1, round(ratio * 100))
                    status = 'correct' if score >= 90 else 'flagged'
                    if status == 'correct':
                        correct += 1
                    else:
                        flagged += 1
                    results[k] = {'word': ref_display_words[k], 'status': status, 'score': score}
                else:
                    results[k] = {'word': ref_display_words[k], 'status': 'skipped', 'score': 0}
                    skipped += 1
        elif tag == 'delete':
            for k in range(i1, i2):
                results[k] = {'word': ref_display_words[k], 'status': 'skipped', 'score': 0}
                skipped += 1
        # 'insert' = extra words the reader said that aren't in the reference — ignored for per-word display

    # Safety net — shouldn't happen, but never leave a None in the list
    for k, r in enumerate(results):
        if r is None:
            results[k] = {'word': ref_display_words[k], 'status': 'skipped', 'score': 0}
            skipped += 1

    return results, correct, flagged, skipped


def _cefr_estimate(score):
    if score < 35:
        return 'A1'
    if score < 50:
        return 'A2'
    if score < 65:
        return 'B1'
    if score < 80:
        return 'B2'
    return 'C1'


# ── Endpoints ──────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def shadowing_texts(request):
    """List available shadowing passages."""
    level = request.query_params.get('level')
    qs = ShadowingText.objects.all()
    if level:
        qs = qs.filter(level=level)
    return Response([{
        'id': t.id,
        'title': t.title,
        'topic': t.topic,
        'level': t.level,
        'word_count': t.word_count,
        'is_premium': t.is_premium,
    } for t in qs])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def shadowing_text_detail(request, pk):
    t = get_object_or_404(ShadowingText, id=pk)
    return Response({
        'id': t.id,
        'title': t.title,
        'topic': t.topic,
        'level': t.level,
        'body': t.body,
        'word_count': t.word_count,
        'is_premium': t.is_premium,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def shadowing_submit(request, pk):
    """
    Body (multipart): audio=<file>, duration_sec=<float>
    Transcribes the recording via Whisper, aligns it against the reference
    passage word-by-word, and returns a full scoring report.
    """
    text_obj = get_object_or_404(ShadowingText, id=pk)
    audio_file = request.FILES.get('audio')
    try:
        duration_sec = float(request.data.get('duration_sec', 0) or 0)
    except (TypeError, ValueError):
        duration_sec = 0

    if not audio_file:
        return Response({'error': 'audio file is required'}, status=400)

    transcript = _transcribe(audio_file)
    said_tokens = _tokenize(transcript)
    ref_display_words = _display_words(text_obj.body)

    word_results, correct, flagged, skipped = _score_words(ref_display_words, said_tokens)

    total_ref = max(1, len(ref_display_words))
    accuracy_pct = round(correct / total_ref * 100)
    fluency_wpm = round(len(said_tokens) / (duration_sec / 60)) if duration_sec > 0 else 0
    fluency_score = min(100, round(fluency_wpm / 130 * 100)) if fluency_wpm else 0
    overall_score = max(0, min(100, round(0.7 * accuracy_pct + 0.3 * fluency_score)))
    cefr = _cefr_estimate(overall_score)

    attempt = ShadowingAttempt.objects.create(
        user=request.user,
        text=text_obj,
        audio_file=audio_file,
        duration_sec=duration_sec,
        transcript=transcript,
        word_results=word_results,
        overall_score=overall_score,
        accuracy_pct=accuracy_pct,
        fluency_wpm=fluency_wpm,
        cefr_estimate=cefr,
        correct_count=correct,
        flagged_count=flagged,
        skipped_count=skipped,
    )

    return Response({
        'id': attempt.id,
        'overall_score': overall_score,
        'accuracy_pct': accuracy_pct,
        'fluency_wpm': fluency_wpm,
        'cefr_estimate': cefr,
        'correct_count': correct,
        'flagged_count': flagged,
        'skipped_count': skipped,
        'word_results': word_results,
        'transcript': transcript,
        'audio_url': request.build_absolute_uri(attempt.audio_file.url) if attempt.audio_file else None,
        'duration_sec': duration_sec,
    })
