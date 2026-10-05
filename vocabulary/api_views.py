"""
Word bank — staff endpoints, mounted at /api/games/words/ (RUNNER_PLAN §B8.3, shared with Word Battle).

    POST  admin/import/?dry_run=1            the §B6 JSON (≤ 2 MB, ≤ 5,000 rows) → report
    GET   admin/items/?kind=w|p&level=&topic=&status=&tag=&q=&page=   50 per page
    PATCH admin/items/<kind>/<id>/           {status, level, topic, picture, uz, speak, say_also, …}
    GET   admin/export/?level=&topic=&kind=&status=&tag=              the §B6 format (round-trips)
    GET   admin/summary/                     counts per level × topic × status, phrases per kind, unwarmed lines
    GET   admin/warm/                        the warm job's status (same as /api/games/stats/admin/warm-voices/status/)
    POST  admin/warm/ {level?, topic?}       synthesize the published items' lines → 202 · 409 while one runs
"""
import math

from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from . import bank
from .models import Phrase, Word

PAGE_SIZE = 50


def _params(request, *names):
    return [str(request.query_params.get(n) or '').strip() for n in names]


def _level(v):
    v = v.upper()
    return v if v in bank.LEVELS else ''


def _status(v):
    return v if v in bank.STATUSES else ''


def _truthy(v):
    return str(v or '').lower() in ('1', 'true', 'yes', 'on')


# ── import ───────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_import(request):
    try:
        size = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        size = 0
    if size > bank.MAX_BYTES:
        return Response({'error': f'Fayl juda katta ({size // 1024} KB) — ko‘pi bilan 2 MB.'}, status=413)
    dry_run = _truthy(request.query_params.get('dry_run'))
    try:
        report = bank.import_bank(request.data, dry_run=dry_run)
    except bank.BankError as e:
        return Response({'error': str(e)}, status=400)
    return Response(report)


# ── items ────────────────────────────────────────────────────────────────────

def word_item(w):
    return {
        'k': 'w', 'id': w.id, 'word': w.word, 'uz': w.uz, 'pos': w.pos, 'level': w.level, 'topic': w.topic,
        'picture': w.picture, 'definition': w.definition, 'example': w.example, 'say_also': w.say_also,
        'synonyms': w.synonyms, 'antonyms': w.antonyms, 'distractors': w.distractors, 'tags': w.tags,
        'speak': w.speak, 'speak_risk': w.speak_risk, 'status': w.status, 'source': w.source,
        'say_seen': w.say_seen, 'say_ok': w.say_ok, 'mean_seen': w.mean_seen, 'mean_ok': w.mean_ok,
        'say_heard': w.say_heard, 'voice': bank.WORD_VOICE, 'updated_at': w.updated_at,
    }


def phrase_item(p):
    return {
        'k': 'p', 'id': p.id, 'kind': p.kind, 'text': p.text, 'prompt': p.prompt, 'answer': p.answer,
        'accept': p.accept, 'min_words': p.min_words, 'uz': p.uz, 'level': p.level, 'topic': p.topic,
        'picture': p.picture, 'voice': p.voice or bank.KIND_VOICE.get(p.kind, 'narrator'), 'status': p.status,
        'source': p.source, 'say_seen': p.say_seen, 'say_ok': p.say_ok, 'say_heard': p.say_heard,
        'updated_at': p.updated_at,
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_items(request):
    kind, level, topic, status, tag, q, kind_of = _params(request, 'kind', 'level', 'topic', 'status', 'tag', 'q', 'pkind')
    level, status = _level(level), _status(status)
    q = q[:80]
    try:
        page = max(1, int(request.query_params.get('page') or 1))
    except ValueError:
        page = 1
    if kind == 'p':
        qs = bank.filter_phrases(level, topic, status, q, kind_of if kind_of in bank.PHRASE_KINDS else '')
        qs = qs.order_by('level', 'topic', 'kind', 'id')
        to_item = phrase_item
    else:
        kind = 'w'
        qs = bank.filter_words(level, topic, status, tag, q).order_by('level', 'topic', 'word')
        to_item = word_item
    total = qs.count()
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    rows = qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return Response({'kind': kind, 'count': total, 'page': page, 'pages': pages, 'page_size': PAGE_SIZE,
                     'items': [to_item(x) for x in rows]})


WORD_PATCH_FIELDS = set(bank.WORD_EXPORT_FIELDS) | {'speak', 'status', 'source'}
PHRASE_PATCH_FIELDS = set(bank.PHRASE_EXPORT_FIELDS) | {'status', 'source'}


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def admin_item_update(request, kind, item_id):
    body = request.data if isinstance(request.data, dict) else None
    if not body:
        return Response({'error': 'o‘zgartirish yo‘q'}, status=400)
    if kind == 'w':
        obj = Word.objects.filter(pk=item_id).first()
        allowed, to_row, validate = WORD_PATCH_FIELDS, bank.word_row, bank.validate_word
    elif kind == 'p':
        obj = Phrase.objects.filter(pk=item_id).first()
        allowed, to_row, validate = PHRASE_PATCH_FIELDS, bank.phrase_row, bank.validate_phrase
    else:
        return Response({'error': 'kind: w yoki p'}, status=404)
    if obj is None:
        return Response({'error': 'topilmadi'}, status=404)
    unknown = sorted(k for k in body if k not in allowed)
    if unknown:
        return Response({'error': f'o‘zgartirib bo‘lmaydi: {", ".join(unknown)}'}, status=400)

    row = to_row(obj, full=True)
    row.update(body)
    clean, errors, warnings = validate(row, {})
    if errors:
        return Response({'error': '; '.join(errors)}, status=400)

    if kind == 'w':
        if clean['word'].lower() != obj.word.lower() and \
                Word.objects.filter(word__iexact=clean['word']).exclude(pk=obj.pk).exists():
            return Response({'error': f'«{clean["word"]}» bankda allaqachon bor'}, status=400)
        warnings = warnings + bank.word_warnings({**row, **clean})
    elif clean['key'] != obj.key and Phrase.objects.filter(key=clean['key']).exclude(pk=obj.pk).exists():
        return Response({'error': 'xuddi shu ibora (kind + level + matn) bankda bor'}, status=400)

    changed = [f for f, v in clean.items() if getattr(obj, f) != v]
    if changed:
        for f in changed:
            setattr(obj, f, clean[f])
        obj.save(update_fields=changed + ['updated_at'])
    item = word_item(obj) if kind == 'w' else phrase_item(obj)
    return Response({'item': item, 'changed': changed, 'warnings': warnings})


# ── export ───────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_export(request):
    level, topic, kind, status, tag = _params(request, 'level', 'topic', 'kind', 'status', 'tag')
    data = bank.export_bank(level=_level(level), topic=topic, kind=kind if kind in ('w', 'p') else '',
                            status=_status(status), tag=tag)
    name = '-'.join(x for x in ('bank', _level(level).lower(), topic, kind, _status(status), tag,
                                timezone.localdate().isoformat()) if x)
    resp = JsonResponse(data, json_dumps_params={'ensure_ascii': False, 'indent': 1})
    resp['Content-Disposition'] = f'attachment; filename="{name}.json"'
    return resp


# ── summary + warm ───────────────────────────────────────────────────────────

def _grid(qs, *keys):
    return [dict(r) for r in qs.values(*keys).annotate(n=Count('id')).order_by(*keys)]


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_summary(request):
    from gamestats.tasks import warm_status

    published_words = Word.objects.filter(status='published')
    published_phrases = Phrase.objects.filter(status='published')
    lines = bank.warm_lines(published_words.only('word'),
                            published_phrases.only('kind', 'text', 'prompt', 'voice'), fixed=True)
    unwarmed = len(bank.uncached(lines))
    tags = {}
    for t_list in Word.objects.exclude(tags=[]).values_list('tags', flat=True):
        for t in t_list or ():
            tags[t] = tags.get(t, 0) + 1
    return Response({
        'levels': list(bank.LEVELS),
        'topics': [{'slug': k, 'title': v} for k, v in bank.TOPICS.items()],
        'statuses': list(bank.STATUSES),
        'words': {
            'total': Word.objects.count(),
            'by_status': dict(Word.objects.values_list('status').annotate(n=Count('id')).order_by()),
            'grid': _grid(Word.objects.all(), 'level', 'topic', 'status'),
        },
        'phrases': {
            'total': Phrase.objects.count(),
            'by_status': dict(Phrase.objects.values_list('status').annotate(n=Count('id')).order_by()),
            'by_kind': _grid(Phrase.objects.all(), 'level', 'kind', 'status'),
            'grid': _grid(Phrase.objects.all(), 'level', 'topic', 'status'),
        },
        'tags': tags,
        'warm': {'lines': len(lines), 'unwarmed': unwarmed, 'status': warm_status()},
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def admin_warm(request):
    from gamestats.tasks import WARM_MAX_LINES, start_warm, warm_running, warm_status

    if request.method == 'GET':
        return Response(warm_status())
    body = request.data if isinstance(request.data, dict) else {}
    level = _level(str(body.get('level') or ''))
    topic = str(body.get('topic') or '').strip()
    if warm_running():
        return Response({'error': 'already running', **warm_status()}, status=409)
    words = bank.filter_words(level, topic, 'published').only('word')
    phrases = bank.filter_phrases(level, topic, 'published').only('kind', 'text', 'prompt', 'voice')
    lines = bank.uncached(bank.warm_lines(words, phrases, fixed=not (level or topic)))
    if not lines:
        return Response({'state': 'done', 'total': 0, 'done': 0, 'cached': 0, 'made': 0, 'failed': 0,
                         'nothing': True}, status=200)
    capped = max(0, len(lines) - WARM_MAX_LINES)
    st = start_warm(lines[:WARM_MAX_LINES])
    return Response({**st, 'capped': capped}, status=202)
