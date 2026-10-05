"""
Speaking game API — mounted at /api/games/speaking/.

Learner (logged in):
  GET   lessons/?level=B1            lessons + my best accuracy, done flag, attempts (premium ones locked)
  GET   lessons/<id>/                one lesson with its sentences
  POST  lessons/<id>/attempts/       multipart audio (≤ 8 MB, ≤ 3 min), duration_sec, mime → {id, status}
  GET   attempts/<id>/               status + word-by-word result + the previous attempt's accuracy
  GET   attempts/<id>/audio/         the learner's own recording

Staff:
  POST   admin/import/               JSON list of lessons → {created, skipped, errors}
  GET    admin/lessons/?level=&q=    every lesson with attempts / learners / average accuracy
  PATCH  admin/lessons/<id>/         is_active, is_premium, order, title, text, topic, level
  DELETE admin/lessons/<id>/         the lesson, its attempts and their recordings
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import LEVEL_RANK, LEVELS, SpeakingAttempt, SpeakingLesson
from .scoring import reference_words, split_sentences
from .tasks import score_attempt

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024
MIN_BYTES = 1500
MAX_SECONDS = 180
DAILY_FREE = 30
DAILY_PREMIUM = 150
MAX_WORDS = 350          # about three minutes of reading
MIN_WORDS = 5
READING_WPM = 110        # for the "about 40 s" estimate
STALE_AFTER = timedelta(minutes=15)


def _premium(user):
    if user.is_staff:
        return True
    try:
        return bool(user.check_premium())
    except Exception:
        return bool(getattr(user, 'is_premium', False))


def _sorted(qs):
    return sorted(qs, key=lambda x: (LEVEL_RANK.get(x.level, 99), x.order, x.id))


def _lesson_meta(lesson):
    n = len(reference_words(lesson.text))
    return n, max(10, round(n / READING_WPM * 60))


def _my_stats(user, lesson_ids=None):
    qs = SpeakingAttempt.objects.filter(user=user, status=SpeakingAttempt.READY)
    if lesson_ids is not None:
        qs = qs.filter(lesson_id__in=lesson_ids)
    return {r['lesson_id']: r for r in qs.values('lesson_id').annotate(best=Max('accuracy'), n=Count('id'))}


def _next_lesson(lesson, user_premium):
    """The lesson after this one (same order as the list), or None."""
    rows = _sorted(SpeakingLesson.objects.filter(is_active=True).only('id', 'level', 'order', 'is_premium'))
    ids = [r.id for r in rows]
    try:
        k = ids.index(lesson.id)
    except ValueError:
        return None
    for r in rows[k + 1:]:
        return {'id': r.id, 'locked': r.is_premium and not user_premium}
    return None


# ─── learner ──────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lessons(request):
    qs = SpeakingLesson.objects.filter(is_active=True)
    level = (request.query_params.get('level') or '').upper()
    if level in LEVELS:
        qs = qs.filter(level=level)
    rows = _sorted(qs)
    stats = _my_stats(request.user, [r.id for r in rows])
    premium = _premium(request.user)
    out = []
    for r in rows:
        s = stats.get(r.id)
        words, seconds = _lesson_meta(r)
        out.append({
            'id': r.id, 'title': r.title, 'level': r.level, 'topic': r.topic, 'order': r.order,
            'is_premium': r.is_premium, 'locked': r.is_premium and not premium,
            'words': words, 'seconds': seconds,
            'best_accuracy': s['best'] if s else None, 'attempts': s['n'] if s else 0, 'done': bool(s),
        })
    done = sum(1 for x in out if x['done'])
    return Response({'lessons': out, 'done': done, 'total': len(out), 'premium': premium})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_detail(request, pk):
    lesson = get_object_or_404(SpeakingLesson, pk=pk, is_active=True)
    premium = _premium(request.user)
    if lesson.is_premium and not premium:
        return Response({'error': 'Bu dars Premium foydalanuvchilar uchun.', 'locked': True,
                         'id': lesson.id, 'title': lesson.title, 'level': lesson.level}, status=403)
    s = _my_stats(request.user, [lesson.id]).get(lesson.id)
    words, seconds = _lesson_meta(lesson)
    return Response({
        'id': lesson.id, 'title': lesson.title, 'level': lesson.level, 'topic': lesson.topic,
        'text': lesson.text, 'sentences': split_sentences(lesson.text), 'words': words, 'seconds': seconds,
        'best_accuracy': s['best'] if s else None, 'attempts': s['n'] if s else 0,
        'max_seconds': MAX_SECONDS, 'next': _next_lesson(lesson, premium),
    })


def sniff_audio(head):
    """The first bytes of an upload -> (mime, extension) of a recording Whisper can read, or None.
    The browser's own type and the file name are not trusted: what is stored and served is
    decided by the content."""
    head = bytes(head or b'')
    if head[:4] == b'\x1aE\xdf\xa3':                       # EBML: webm (Chrome, Android, Firefox)
        return 'audio/webm', '.webm'
    if head[:4] == b'OggS':
        return 'audio/ogg', '.ogg'
    if head[4:8] == b'ftyp':                              # ISO-BMFF: mp4 / m4a (iPhone, Safari)
        return 'audio/mp4', '.m4a'
    if head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'audio/wav', '.wav'
    if head[:3] == b'ID3' or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE6) in (0xE2, 0xE4, 0xE6)):
        return 'audio/mpeg', '.mp3'                       # (ADTS .aac is not accepted by Whisper)
    return None


def _queue_scoring(attempt_id):
    try:
        score_attempt.delay(attempt_id)
    except Exception:
        # the broker is down: say so now instead of leaving the learner on "checking…"
        log.exception('Speaking attempt %s could not be queued', attempt_id)
        SpeakingAttempt.objects.filter(id=attempt_id, status=SpeakingAttempt.PROCESSING).update(
            status=SpeakingAttempt.FAILED, error='service')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def attempt_create(request, pk):
    lesson = get_object_or_404(SpeakingLesson, pk=pk, is_active=True)
    premium = _premium(request.user)
    if lesson.is_premium and not premium:
        return Response({'error': 'Bu dars Premium foydalanuvchilar uchun.', 'locked': True}, status=403)
    clip = request.FILES.get('audio')
    if not clip:
        return Response({'error': 'Ovoz yozuvi topilmadi.'}, status=400)
    if clip.size > MAX_BYTES:
        return Response({'error': 'Yozuv juda katta (8 MB dan oshmasin).'}, status=413)
    if clip.size < MIN_BYTES:
        return Response({'error': 'Yozuv juda qisqa — matnni ovoz chiqarib o‘qing.', 'code': 'too-short'}, status=400)
    try:
        head = clip.read(16)
        clip.seek(0)
    except Exception:
        head = b''
    sniffed = sniff_audio(head)
    if not sniffed:
        return Response({'error': 'Bu ovoz yozuvi emas yoki formati qo‘llab-quvvatlanmaydi.', 'code': 'bad-format'},
                        status=400)
    mime, ext = sniffed
    try:
        duration = float(request.data.get('duration_sec') or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration > MAX_SECONDS + 5:
        return Response({'error': 'Yozuv 3 daqiqadan oshmasin.'}, status=400)

    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    used = (SpeakingAttempt.objects.filter(user=request.user, created_at__gte=today)
            .exclude(status=SpeakingAttempt.FAILED, error='service').count())
    limit = DAILY_PREMIUM if premium else DAILY_FREE
    if used >= limit:
        return Response({'error': 'Bugungi limit tugadi — ertaga yana davom eting.', 'code': 'limit'}, status=429)

    a = SpeakingAttempt(user=request.user, lesson=lesson, mime=mime, duration_sec=round(max(0, duration), 1))
    a.audio.save(f'reading{ext}', clip, save=False)
    a.save()
    transaction.on_commit(lambda: _queue_scoring(a.id))
    a.refresh_from_db(fields=['status'])
    return Response({'id': a.id, 'status': a.status}, status=201)


def _attempt_payload(a, request):
    prev = (SpeakingAttempt.objects.filter(user=a.user, lesson=a.lesson, status=SpeakingAttempt.READY,
                                           created_at__lt=a.created_at)
            .order_by('-created_at').values('id', 'accuracy').first())
    best = (SpeakingAttempt.objects.filter(user=a.user, lesson=a.lesson, status=SpeakingAttempt.READY)
            .aggregate(b=Max('accuracy'))['b'])
    lesson = a.lesson
    data = {
        'id': a.id, 'status': a.status, 'error': a.error, 'created_at': a.created_at,
        'lesson': {'id': lesson.id, 'title': lesson.title, 'level': lesson.level, 'topic': lesson.topic},
        'duration_sec': a.duration_sec, 'mime': a.mime,
        'audio_url': f'/api/games/speaking/attempts/{a.id}/audio/' if a.audio else None,
        'previous': {'id': prev['id'], 'accuracy': prev['accuracy']} if prev else None,
        'best_accuracy': best,
    }
    if a.status == SpeakingAttempt.READY:
        data.update({
            'accuracy': a.accuracy, 'fluency_wpm': a.fluency_wpm, 'transcript': a.transcript, 'words': a.words,
            'ok': a.ok_count, 'fix': a.fix_count, 'skip': a.skip_count,
            'next': _next_lesson(lesson, _premium(request.user)),
        })
    return data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def attempt_detail(request, pk):
    a = get_object_or_404(SpeakingAttempt.objects.select_related('lesson', 'user'), pk=pk)
    if a.user_id != request.user.id and not request.user.is_staff:
        raise Http404
    # the worker died or the task was lost: Whisper + retries take at most ~7 min
    if a.status == SpeakingAttempt.PROCESSING and a.created_at < timezone.now() - STALE_AFTER:
        SpeakingAttempt.objects.filter(id=a.id, status=SpeakingAttempt.PROCESSING).update(
            status=SpeakingAttempt.FAILED, error='service')
        a.refresh_from_db(fields=['status', 'error'])
    return Response(_attempt_payload(a, request))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def attempt_audio(request, pk):
    a = get_object_or_404(SpeakingAttempt, pk=pk)
    if a.user_id != request.user.id and not request.user.is_staff:
        raise Http404
    if not a.audio:
        return Response({'error': 'Yozuv saqlanmagan (30 kundan eski yozuvlar o‘chiriladi).'}, status=404)
    try:
        f = a.audio.open('rb')
    except (FileNotFoundError, OSError):
        return Response({'error': 'Yozuv topilmadi.'}, status=404)
    resp = FileResponse(f, content_type=a.mime or 'audio/webm')
    resp['Cache-Control'] = 'private, max-age=86400'
    return resp


# ─── staff ────────────────────────────────────────────────────────────────────

def _validate(item, partial=False):
    """→ (clean dict, error message or '')."""
    if not isinstance(item, dict):
        return None, 'har bir dars {...} obyekt bo‘lishi kerak'
    out = {}
    if 'title' in item or not partial:
        title = ' '.join(str(item.get('title') or '').split())
        if not title:
            return None, '"title" bo‘sh'
        if len(title) > 200:
            return None, '"title" 200 belgidan uzun'
        out['title'] = title
    if 'level' in item or not partial:
        level = str(item.get('level') or '').strip().upper()
        if level not in LEVELS:
            return None, f'"level" quyidagilardan biri bo‘lsin: {", ".join(LEVELS)}'
        out['level'] = level
    if 'text' in item or not partial:
        text = str(item.get('text') or '').replace('\r\n', '\n').strip()
        n = len(reference_words(text))
        if n < MIN_WORDS:
            return None, f'"text" juda qisqa ({n} so‘z, kamida {MIN_WORDS})'
        if n > MAX_WORDS:
            return None, f'"text" juda uzun ({n} so‘z, ko‘pi bilan {MAX_WORDS} — 3 daqiqa o‘qish)'
        out['text'] = text
    if 'topic' in item:
        out['topic'] = ' '.join(str(item.get('topic') or '').split())[:100]
    for key in ('is_premium', 'is_active'):
        if key in item:
            v = item.get(key)
            if not isinstance(v, bool):
                return None, f'"{key}" true yoki false bo‘lsin'
            out[key] = v
    if 'order' in item and item.get('order') is not None:
        try:
            order = int(item.get('order'))
        except (TypeError, ValueError):
            return None, '"order" butun son bo‘lsin'
        if order < 0 or order > 100000:
            return None, '"order" 0 dan 100000 gacha'
        out['order'] = order
    return out, ''


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([JSONParser])
def admin_import(request):
    payload = request.data
    if isinstance(payload, dict) and isinstance(payload.get('lessons'), list):
        payload = payload['lessons']
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        return Response({'error': 'JSON ro‘yxat bo‘lishi kerak: [ {...}, {...} ] yoki {"lessons": [...]}'}, status=400)
    if len(payload) > 500:
        return Response({'error': 'Bir martada ko‘pi bilan 500 ta dars.'}, status=400)

    existing = {(t.lower(), lv) for t, lv in SpeakingLesson.objects.values_list('title', 'level')}
    next_order = {r['level']: (r['m'] or 0) for r in SpeakingLesson.objects.values('level').annotate(m=Max('order'))}
    created, skipped, errors = [], [], []
    with transaction.atomic():
        for idx, item in enumerate(payload, 1):
            clean, err = _validate(item)
            title = (item.get('title') if isinstance(item, dict) else '') or ''
            if err:
                errors.append({'index': idx, 'title': str(title)[:80], 'error': err})
                continue
            key = (clean['title'].lower(), clean['level'])
            if key in existing:
                skipped.append({'index': idx, 'title': clean['title'], 'reason': 'bu nom va daraja bilan dars bor'})
                continue
            if 'order' not in clean:
                next_order[clean['level']] = next_order.get(clean['level'], 0) + 1
                clean['order'] = next_order[clean['level']]
            lesson = SpeakingLesson.objects.create(**clean)
            existing.add(key)
            created.append({'id': lesson.id, 'title': lesson.title, 'level': lesson.level})
    return Response({'created': len(created), 'items': created, 'skipped': skipped, 'errors': errors},
                    status=201 if created else 200)


def _admin_row(r, stats):
    s = stats.get(r.id) or {}
    words, seconds = _lesson_meta(r)
    return {
        'id': r.id, 'title': r.title, 'level': r.level, 'topic': r.topic, 'text': r.text, 'order': r.order,
        'is_premium': r.is_premium, 'is_active': r.is_active, 'created_at': r.created_at,
        'words': words, 'seconds': seconds,
        'attempts': s.get('n', 0), 'learners': s.get('users', 0),
        'avg_accuracy': round(s['avg']) if s.get('avg') is not None else None,
        'last_attempt': s.get('last'),
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_lessons(request):
    qs = SpeakingLesson.objects.all()
    level = (request.query_params.get('level') or '').upper()
    if level in LEVELS:
        qs = qs.filter(level=level)
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(topic__icontains=q) | Q(text__icontains=q))
    rows = _sorted(qs)
    stats = {s['lesson_id']: s for s in SpeakingAttempt.objects.filter(
        status=SpeakingAttempt.READY, lesson_id__in=[r.id for r in rows]).values('lesson_id').annotate(
        n=Count('id'), users=Count('user', distinct=True), avg=Avg('accuracy'), last=Max('created_at'))}
    ready = SpeakingAttempt.objects.filter(status=SpeakingAttempt.READY)
    totals = ready.aggregate(n=Count('id'), users=Count('user', distinct=True), avg=Avg('accuracy'))
    week = timezone.now() - timedelta(days=7)
    return Response({
        'lessons': [_admin_row(r, stats) for r in rows],
        'totals': {
            'lessons': SpeakingLesson.objects.count(),
            'active': SpeakingLesson.objects.filter(is_active=True).count(),
            'premium': SpeakingLesson.objects.filter(is_premium=True).count(),
            'attempts': totals['n'] or 0, 'learners': totals['users'] or 0,
            'avg_accuracy': round(totals['avg']) if totals['avg'] is not None else None,
            'attempts_week': ready.filter(created_at__gte=week).count(),
            'failed_week': SpeakingAttempt.objects.filter(status=SpeakingAttempt.FAILED, created_at__gte=week).count(),
        },
    })


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([JSONParser])
def admin_lesson(request, pk):
    lesson = get_object_or_404(SpeakingLesson, pk=pk)
    if request.method == 'DELETE':
        files = list(SpeakingAttempt.objects.filter(lesson=lesson).exclude(audio='').values_list('audio', flat=True))
        storage = SpeakingAttempt._meta.get_field('audio').storage
        lesson.delete()
        for name in files:
            try:
                storage.delete(name)
            except Exception:
                log.warning('Speaking game audio %s was not deleted', name, exc_info=True)
        return Response(status=204)

    clean, err = _validate(request.data, partial=True)
    if err:
        return Response({'error': err}, status=400)
    if not clean:
        return Response({'error': 'O‘zgartirish uchun maydon yo‘q.'}, status=400)
    title = clean.get('title', lesson.title)
    level = clean.get('level', lesson.level)
    if ('title' in clean or 'level' in clean) and SpeakingLesson.objects.filter(
            title__iexact=title, level=level).exclude(pk=lesson.pk).exists():
        return Response({'error': 'Bu nom va daraja bilan boshqa dars bor.'}, status=400)
    for k, v in clean.items():
        setattr(lesson, k, v)
    lesson.save(update_fields=list(clean.keys()))
    stats = {s['lesson_id']: s for s in SpeakingAttempt.objects.filter(
        status=SpeakingAttempt.READY, lesson=lesson).values('lesson_id').annotate(
        n=Count('id'), users=Count('user', distinct=True), avg=Avg('accuracy'), last=Max('created_at'))}
    return Response(_admin_row(lesson, stats))
