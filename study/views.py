import os
import shutil
import subprocess
import tempfile

from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

import requests
from django.conf import settings

from .models import (
    Article, ArticleView, WritingSample, WritingSampleRead, Podcast, PodcastListen,
)


def _abs(request, filefield):
    """
    Return a same-origin relative URL (e.g. /media/...).

    Relative is deliberate: the PDF is embedded in an <iframe> on the frontend,
    and an absolute backend URL would be a different origin in dev (5173 vs 8000),
    which browsers refuse to frame. The dev proxy and nginx both map /media/.
    """
    if not filefield:
        return None
    try:
        return filefield.url
    except Exception:
        return None


def _serialize_article(article, request, include_pdf=True):
    data = {
        'id': article.id,
        'title': article.title,
        'excerpt': article.excerpt,
        'topic': article.topic,
        'level': article.level,
        'cover_url': _abs(request, article.cover),
        'views_count': article.views_count,
        'is_premium': article.is_premium,
        'order': article.order,
        'created_at': article.created_at.isoformat(),
    }
    if include_pdf:
        data['pdf_url'] = _abs(request, article.pdf)
    return data


# ── Learner endpoints ────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def article_list(request):
    """All articles for the Study Tools → Articles grid."""
    articles = Article.objects.all()
    return Response([_serialize_article(a, request, include_pdf=False) for a in articles])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def article_detail(request, pk):
    """
    Open one article. Counts a read: every open adds +1 to views_count
    and stores a per-user row so we can show "you've read this" later.
    """
    article = get_object_or_404(Article, pk=pk)

    if article.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'error': 'Premium required'}, status=403)

    Article.objects.filter(pk=article.pk).update(views_count=F('views_count') + 1)
    ArticleView.objects.create(article=article, user=request.user)
    article.refresh_from_db(fields=['views_count'])

    return Response(_serialize_article(article, request))


# ── Admin endpoints ──────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_article_list(request):
    """GET: list every article. POST: create one (multipart: cover + pdf)."""
    if request.method == 'GET':
        return Response([_serialize_article(a, request) for a in Article.objects.all()])

    title = (request.data.get('title') or '').strip()
    if not title:
        return Response({'error': 'title is required'}, status=400)

    article = Article.objects.create(
        title=title,
        excerpt=(request.data.get('excerpt') or '').strip(),
        topic=(request.data.get('topic') or '').strip(),
        level=(request.data.get('level') or '').strip(),
        is_premium=str(request.data.get('is_premium', '')).lower() in ('1', 'true', 'yes'),
        order=int(request.data.get('order') or 0),
    )
    if 'cover' in request.FILES:
        article.cover = request.FILES['cover']
    if 'pdf' in request.FILES:
        article.pdf = request.FILES['pdf']
    article.save()

    return Response(_serialize_article(article, request), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_article_detail(request, pk):
    """PATCH: update fields/files. DELETE: remove the article."""
    article = get_object_or_404(Article, pk=pk)

    if request.method == 'DELETE':
        article.delete()
        return Response(status=204)

    for field in ('title', 'excerpt', 'topic', 'level'):
        if field in request.data:
            setattr(article, field, (request.data.get(field) or '').strip())
    if 'is_premium' in request.data:
        article.is_premium = str(request.data.get('is_premium')).lower() in ('1', 'true', 'yes')
    if 'order' in request.data:
        article.order = int(request.data.get('order') or 0)
    if 'cover' in request.FILES:
        article.cover = request.FILES['cover']
    if 'pdf' in request.FILES:
        article.pdf = request.FILES['pdf']

    article.save()
    return Response(_serialize_article(article, request))


# ── Writing samples ──────────────────────────────────────────────────────────

def _serialize_sample(sample, *, read=None, include_essay=False):
    data = {
        'id': sample.id,
        'task_type': sample.task_type,
        'prompt': sample.prompt,
        'band': sample.band,
        'word_count': sample.word_count,
        'is_premium': sample.is_premium,
        'order': sample.order,
        # "Analyzed" badge on the card — true once this user has opened it
        'is_read': bool(read),
        'note': read.note if read else '',
    }
    if include_essay:
        data['instruction'] = sample.instruction
        data['essay'] = sample.essay
    return data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_sample_list(request):
    """Cards for Study Tools → Writing Samples."""
    samples = WritingSample.objects.all()
    reads = {
        r.sample_id: r
        for r in WritingSampleRead.objects.filter(user=request.user, sample__in=samples)
    }
    return Response([_serialize_sample(s, read=reads.get(s.id)) for s in samples])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def writing_sample_detail(request, pk):
    """Open one sample — this is what marks it as read ('Analyzed')."""
    sample = get_object_or_404(WritingSample, pk=pk)

    if sample.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'error': 'Premium required'}, status=403)

    read, _ = WritingSampleRead.objects.get_or_create(sample=sample, user=request.user)
    return Response(_serialize_sample(sample, read=read, include_essay=True))


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def writing_sample_note(request, pk):
    """Save the learner's private note for this sample."""
    sample = get_object_or_404(WritingSample, pk=pk)
    read, _ = WritingSampleRead.objects.get_or_create(sample=sample, user=request.user)
    read.note = request.data.get('note', '') or ''
    read.save(update_fields=['note', 'updated_at'])
    return Response({'note': read.note})


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def admin_writing_sample_list(request):
    """GET: list every sample. POST: create one."""
    if request.method == 'GET':
        return Response([
            _serialize_sample(s, include_essay=True) for s in WritingSample.objects.all()
        ])

    prompt = (request.data.get('prompt') or '').strip()
    essay = (request.data.get('essay') or '').strip()
    if not prompt or not essay:
        return Response({'error': 'prompt and essay are required'}, status=400)

    sample = WritingSample.objects.create(
        task_type=int(request.data.get('task_type') or 2),
        prompt=prompt,
        instruction=(request.data.get('instruction') or '').strip(),
        essay=essay,
        band=(request.data.get('band') or '8.0+').strip(),
        is_premium=str(request.data.get('is_premium', '')).lower() in ('1', 'true', 'yes'),
        order=int(request.data.get('order') or 0),
    )
    return Response(_serialize_sample(sample, include_essay=True), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_writing_sample_detail(request, pk):
    """PATCH: update fields. DELETE: remove the sample."""
    sample = get_object_or_404(WritingSample, pk=pk)

    if request.method == 'DELETE':
        sample.delete()
        return Response(status=204)

    for field in ('prompt', 'instruction', 'essay', 'band'):
        if field in request.data:
            setattr(sample, field, (request.data.get(field) or '').strip())
    if 'task_type' in request.data:
        sample.task_type = int(request.data.get('task_type') or 2)
    if 'is_premium' in request.data:
        sample.is_premium = str(request.data.get('is_premium')).lower() in ('1', 'true', 'yes')
    if 'order' in request.data:
        sample.order = int(request.data.get('order') or 0)

    sample.save()
    return Response(_serialize_sample(sample, include_essay=True))


# ── Podcasts ─────────────────────────────────────────────────────────────────

# Whisper's hard limit on a single upload
WHISPER_MAX_BYTES = 25 * 1024 * 1024
# Length of each piece when a long file has to be split. 10 minutes of
# 64 kbps mono mp3 is roughly 5 MB, comfortably under the limit.
CHUNK_SECONDS = 600


def _ffmpeg_path():
    return shutil.which('ffmpeg')


def _whisper_call(filename, payload, api_key):
    """One Whisper request. Returns (text, words, duration, error)."""
    try:
        resp = requests.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {api_key}'},
            files={'file': (filename, payload)},
            data={
                'model': 'whisper-1',
                'language': 'en',
                'response_format': 'verbose_json',
                'timestamp_granularities[]': 'word',
            },
            timeout=900,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get('error', {}).get('message', '')
        except Exception:
            detail = str(e)
        return '', [], 0, f'Whisper xatosi: {detail}'
    except Exception as e:
        return '', [], 0, f'Whisper xatosi: {e}'

    words = [
        {'word': w.get('word', ''), 'start': w.get('start', 0), 'end': w.get('end', 0)}
        for w in (data.get('words') or [])
    ]
    return data.get('text', ''), words, float(data.get('duration') or 0), None


def _transcribe_long(payload, original_name, api_key):
    """
    Split audio over the 25 MB limit into CHUNK_SECONDS pieces with ffmpeg,
    transcribe each, then stitch the results back together — every word timing
    from chunk N is shifted by that chunk's start offset so the karaoke
    highlighting stays in sync across the whole file.
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        mb = len(payload) / (1024 * 1024)
        return '', [], 0, (
            f'Fayl {mb:.0f} MB — Whisper limiti 25 MB. '
            'Uzun fayllarni bo\'lish uchun serverda ffmpeg o\'rnatilishi kerak.'
        )

    tmpdir = tempfile.mkdtemp(prefix='podcast_')
    src = os.path.join(tmpdir, 'source' + (os.path.splitext(original_name)[1] or '.mp3'))
    try:
        with open(src, 'wb') as f:
            f.write(payload)

        # Re-encode to 64 kbps mono and cut into fixed-length pieces in one pass
        pattern = os.path.join(tmpdir, 'part_%03d.mp3')
        proc = subprocess.run(
            [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', src,
                '-vn', '-ac', '1', '-ar', '16000', '-b:a', '64k',
                '-f', 'segment', '-segment_time', str(CHUNK_SECONDS),
                '-reset_timestamps', '1', pattern,
            ],
            capture_output=True, timeout=1800,
        )
        if proc.returncode != 0:
            return '', [], 0, f'Audio bo\'lishda xato: {proc.stderr.decode("utf-8", "ignore")[:200]}'

        parts = sorted(p for p in os.listdir(tmpdir) if p.startswith('part_'))
        if not parts:
            return '', [], 0, 'Audio bo\'laklarga bo\'linmadi'

        all_words = []
        texts = []
        offset = 0.0
        for name in parts:
            path = os.path.join(tmpdir, name)
            with open(path, 'rb') as f:
                chunk_bytes = f.read()

            text, words, dur, error = _whisper_call(name, chunk_bytes, api_key)
            if error:
                return '', [], 0, error

            for w in words:
                all_words.append({
                    'word': w['word'],
                    'start': (w['start'] or 0) + offset,
                    'end': (w['end'] or 0) + offset,
                })
            if text:
                texts.append(text.strip())

            # Prefer the real decoded length; fall back to the nominal chunk size
            offset += dur if dur else CHUNK_SECONDS

        return ' '.join(texts), all_words, offset, None
    except subprocess.TimeoutExpired:
        return '', [], 0, 'Audio juda uzun — bo\'lish vaqti tugadi'
    except Exception as e:
        return '', [], 0, f'Transkripsiya xatosi: {e}'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_audio(payload, original_name):
    """
    Pull a compact mono audio track out of a video file.

    Video files are large and Whisper only needs the sound, so stripping the
    picture keeps most uploads under the 25 MB limit in the first place.
    Returns (audio_bytes, error).
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None, 'Video uchun serverda ffmpeg o\'rnatilishi kerak.'

    tmpdir = tempfile.mkdtemp(prefix='podcast_video_')
    src = os.path.join(tmpdir, 'source' + (os.path.splitext(original_name)[1] or '.mp4'))
    out = os.path.join(tmpdir, 'audio.mp3')
    try:
        with open(src, 'wb') as f:
            f.write(payload)

        proc = subprocess.run(
            [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', src,
                '-vn', '-ac', '1', '-ar', '16000', '-b:a', '64k', out,
            ],
            capture_output=True, timeout=1800,
        )
        if proc.returncode != 0 or not os.path.exists(out):
            return None, f'Videodan ovoz ajratishda xato: {proc.stderr.decode("utf-8", "ignore")[:200]}'

        with open(out, 'rb') as f:
            return f.read(), None
    except subprocess.TimeoutExpired:
        return None, 'Video juda uzun — qayta ishlash vaqti tugadi'
    except Exception as e:
        return None, f'Videoni o\'qishda xato: {e}'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _transcribe_words(django_file, *, is_video=False):
    """
    Run Whisper with word-level timestamps.

    Returns (transcript_text, words, duration_sec, error). `words` is a list of
    {"word", "start", "end"} — this is what drives the karaoke highlighting, and
    it is computed once here at upload time so playback costs nothing.

    Video uploads have their audio extracted first; anything still above
    Whisper's 25 MB limit is split with ffmpeg and stitched back together, so
    podcast-length media works too.
    """
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return '', [], 0, 'OPENAI_API_KEY sozlanmagan'

    django_file.seek(0)
    payload = django_file.read()
    name = getattr(django_file, 'name', 'audio.mp3')

    if is_video:
        payload, error = _extract_audio(payload, name)
        if error:
            return '', [], 0, error
        name = 'audio.mp3'

    if len(payload) > WHISPER_MAX_BYTES:
        return _transcribe_long(payload, name, api_key)

    return _whisper_call(name, payload, api_key)


def _serialize_podcast(podcast, *, listen=None, include_words=False):
    data = {
        'id': podcast.id,
        'title': podcast.title,
        'author': podcast.author,
        'cover_url': _abs(None, podcast.cover),
        'duration_sec': podcast.duration_sec,
        'duration_label': podcast.duration_label,
        'media_kind': podcast.media_kind,
        'section': podcast.section,
        'is_premium': podcast.is_premium,
        'order': podcast.order,
        'has_transcript': bool(podcast.words),
        # "Seen" badge on the card
        'is_listened': bool(listen),
        'position_sec': listen.position_sec if listen else 0,
    }
    if include_words:
        data['audio_url'] = _abs(None, podcast.audio)
        data['video_url'] = _abs(None, podcast.video)
        data['transcript'] = podcast.transcript
        data['words'] = podcast.words
    return data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def podcast_list(request):
    """Cards for a Study Tools shelf — ?section=shadowing|podcast."""
    podcasts = Podcast.objects.all()
    section = request.query_params.get('section')
    if section in dict(Podcast.Section.choices):
        podcasts = podcasts.filter(section=section)
    listens = {
        l.podcast_id: l
        for l in PodcastListen.objects.filter(user=request.user, podcast__in=podcasts)
    }
    return Response([_serialize_podcast(p, listen=listens.get(p.id)) for p in podcasts])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def podcast_detail(request, pk):
    """Open one podcast — returns audio + word timings, and marks it as listened."""
    podcast = get_object_or_404(Podcast, pk=pk)

    if podcast.is_premium and not getattr(request.user, 'is_premium', False):
        return Response({'error': 'Premium required'}, status=403)

    listen, _ = PodcastListen.objects.get_or_create(podcast=podcast, user=request.user)
    return Response(_serialize_podcast(podcast, listen=listen, include_words=True))


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def podcast_position(request, pk):
    """Remember where the learner stopped so they can resume."""
    podcast = get_object_or_404(Podcast, pk=pk)
    listen, _ = PodcastListen.objects.get_or_create(podcast=podcast, user=request.user)
    try:
        listen.position_sec = float(request.data.get('position_sec') or 0)
    except (TypeError, ValueError):
        listen.position_sec = 0
    listen.save(update_fields=['position_sec', 'updated_at'])
    return Response({'position_sec': listen.position_sec})


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_podcast_list(request):
    """
    GET: list every podcast.
    POST: upload audio — Whisper transcribes it with word timings right here,
    so the learner side never pays for transcription.
    """
    if request.method == 'GET':
        qs = Podcast.objects.all()
        section = request.query_params.get('section')
        if section in dict(Podcast.Section.choices):
            qs = qs.filter(section=section)
        return Response([_serialize_podcast(p) for p in qs])

    title = (request.data.get('title') or '').strip()
    if not title:
        return Response({'error': 'title is required'}, status=400)

    section = request.data.get('section') or Podcast.Section.SHADOWING
    if section not in dict(Podcast.Section.choices):
        section = Podcast.Section.SHADOWING

    is_video = 'video' in request.FILES
    media = request.FILES.get('video') or request.FILES.get('audio')
    if not media:
        return Response({'error': 'audio yoki video fayl yuborilishi kerak'}, status=400)
    # Podcasts is a video-only shelf
    if section == Podcast.Section.PODCAST and not is_video:
        return Response({'error': 'Podcasts bo\'limiga faqat video yuklanadi'}, status=400)

    transcript, words, duration, error = _transcribe_words(media, is_video=is_video)
    if error:
        return Response({'error': error}, status=400)

    media.seek(0)
    podcast = Podcast.objects.create(
        title=title,
        author=(request.data.get('author') or '').strip(),
        section=section,
        video=media if is_video else None,
        audio=None if is_video else media,
        transcript=transcript,
        words=words,
        duration_sec=duration,
        is_premium=str(request.data.get('is_premium', '')).lower() in ('1', 'true', 'yes'),
        order=int(request.data.get('order') or 0),
    )
    if 'cover' in request.FILES:
        podcast.cover = request.FILES['cover']
        podcast.save(update_fields=['cover'])

    return Response(_serialize_podcast(podcast, include_words=True), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_podcast_detail(request, pk):
    """PATCH: update metadata (re-transcribes if a new audio file is sent). DELETE: remove."""
    podcast = get_object_or_404(Podcast, pk=pk)

    if request.method == 'DELETE':
        podcast.delete()
        return Response(status=204)

    for field in ('title', 'author'):
        if field in request.data:
            setattr(podcast, field, (request.data.get(field) or '').strip())
    if 'is_premium' in request.data:
        podcast.is_premium = str(request.data.get('is_premium')).lower() in ('1', 'true', 'yes')
    if 'order' in request.data:
        podcast.order = int(request.data.get('order') or 0)
    if 'cover' in request.FILES:
        podcast.cover = request.FILES['cover']

    if 'section' in request.data:
        section = request.data.get('section')
        if section in dict(Podcast.Section.choices):
            podcast.section = section

    is_video = 'video' in request.FILES
    media = request.FILES.get('video') or request.FILES.get('audio')
    if media:
        if podcast.section == Podcast.Section.PODCAST and not is_video:
            return Response({'error': 'Podcasts bo\'limiga faqat video yuklanadi'}, status=400)
        transcript, words, duration, error = _transcribe_words(media, is_video=is_video)
        if error:
            return Response({'error': error}, status=400)
        media.seek(0)
        # Replacing the media also replaces its kind — only one is ever set
        if is_video:
            podcast.video = media
            podcast.audio = None
        else:
            podcast.audio = media
            podcast.video = None
        podcast.transcript = transcript
        podcast.words = words
        podcast.duration_sec = duration

    podcast.save()
    return Response(_serialize_podcast(podcast, include_words=True))
