"""Admin panel uchun JSON eksport.

Maqsad: bazadagi materialni AYNAN import qilingan ko'rinishda qaytarish.
Ya'ni bu yerdan olingan JSON'ni to'g'ridan-to'g'ri import endpointiga
qaytarib yuborsa, xuddi o'sha material qayta yaratiladi.

Har bir funksiyaning tepasida qaysi import endpointga mos kelishi yozilgan.
Import formati o'zgarsa, bu yer ham o'zgarishi kerak.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django.shortcuts import get_object_or_404


# ── Umumiy yordamchilar ──────────────────────────────────────────────────────

def _questions(queryset):
    """Savollarni import formatidagi ro'yxatga aylantiradi.

    IELTS va CEFR savol modellari bir xil maydonlarga ega, shuning uchun
    bitta funksiya to'rttala turga ham yetadi.
    """
    out = []
    for q in queryset.prefetch_related('choices').order_by('number'):
        item = {
            'number': q.number,
            'question_type': q.question_type,
            'content': q.content or '',
            'correct_answer': q.correct_answer or '',
        }
        # Bo'sh maydonlarni qo'shmaymiz — import ularsiz ham ishlaydi va
        # JSON ancha toza ko'rinadi
        if q.explanation:
            item['explanation'] = q.explanation
        if q.group_instruction:
            item['group_instruction'] = q.group_instruction
        if q.max_selections and q.max_selections != 1:
            item['max_selections'] = q.max_selections
        if q.word_bank:
            item['word_bank'] = q.word_bank
        if getattr(q, 'answer_review', ''):
            item['answer_review'] = q.answer_review

        choices = [{'option': c.option, 'text': c.text} for c in q.choices.all()]
        if choices:
            item['choices'] = choices
        out.append(item)
    return out


def _filename(title, suffix):
    """Yuklab olish uchun xavfsiz fayl nomi."""
    safe = ''.join(ch if ch.isalnum() or ch in ' -_' else '' for ch in (title or 'export'))
    safe = '-'.join(safe.split()).lower()[:60] or 'export'
    return f'{safe}-{suffix}.json'


# ── IELTS READING ────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_ielts_reading(request, pk):
    """Bitta passage → import_ielts_reading ning "SINGLE PASSAGE" formati."""
    from ielts.models import ReadingPassage

    p = get_object_or_404(ReadingPassage, pk=pk)
    data = {
        'title': p.title,
        'content': p.content,
        'passage_number': p.passage_number,
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'is_standalone': p.is_standalone,
        'is_premium': p.is_premium,
        'questions': _questions(p.questions),
    }
    return Response({'filename': _filename(p.title, 'reading'), 'data': data})


# ── IELTS LISTENING ──────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_ielts_listening(request, pk):
    """Bitta section → import_ielts_listening ning "SINGLE SECTION" formati.

    Diqqat: audio fayl JSON ichida bo'lmaydi — import ham uni alohida
    /api/admin/ielts/listening/<id>/audio/ orqali qabul qiladi.
    """
    from ielts.models import ListeningSection

    s = get_object_or_404(ListeningSection, pk=pk)
    data = {
        'title': s.title,
        'section_number': s.section_number,
        'difficulty': s.difficulty,
        'is_standalone': s.is_standalone,
        'is_premium': s.is_premium,
        'transcript': s.transcript or '',
        'questions': _questions(s.questions),
    }
    return Response({'filename': _filename(s.title, 'listening'), 'data': data})


# ── IELTS FULL MOCK (test) ───────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_ielts_test(request, pk):
    """To'liq mock test → "parts" (reading) yoki "sections" (listening) formati.

    Test ichida ham passage, ham section bo'lishi mumkin. Qaysi biri
    so'ralayotgani ?kind=reading|listening bilan bildiriladi; berilmasa
    mavjudiga qarab o'zi tanlaydi.
    """
    from ielts.models import IELTSTest

    t = get_object_or_404(IELTSTest, pk=pk)
    kind = request.GET.get('kind')
    passages = list(t.passages.order_by('passage_number'))
    sections = list(t.listening_sections.order_by('section_number'))

    if kind == 'reading' or (kind is None and passages and not sections):
        data = {
            'title': t.title,
            'test_type': t.test_type,
            'description': t.description or '',
            'is_premium': t.is_premium,
            'difficulty': passages[0].difficulty if passages else 'MEDIUM',
            'parts': [{
                'passage_number': p.passage_number,
                'title': p.title,
                'content': p.content,
                'questions': _questions(p.questions),
            } for p in passages],
        }
        return Response({'filename': _filename(t.title, 'reading-mock'), 'data': data})

    data = {
        'title': t.title,
        'description': t.description or '',
        'is_premium': t.is_premium,
        'difficulty': sections[0].difficulty if sections else 'MEDIUM',
        'sections': [{
            'section_number': s.section_number,
            'title': s.title,
            'transcript': s.transcript or '',
            'questions': _questions(s.questions),
        } for s in sections],
    }
    return Response({'filename': _filename(t.title, 'listening-mock'), 'data': data})


# ── CEFR READING ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_cefr_reading(request, pk):
    """Bitta passage → import_cefr_test ning "type": "reading" formati."""
    from cefr.models import CEFRReadingPassage

    p = get_object_or_404(CEFRReadingPassage, pk=pk)
    data = {
        'type': 'reading',
        'level': p.level or '',
        'title': p.title,
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'is_premium': p.is_premium,
        'is_mock': p.is_mock,
        'passage': {
            'title': p.title,
            'content': p.content,
            'passage_number': p.passage_number,
            'is_standalone': p.is_standalone,
        },
        'questions': _questions(p.questions),
    }
    return Response({'filename': _filename(p.title, 'cefr-reading'), 'data': data})


# ── CEFR LISTENING ───────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_cefr_listening(request, pk):
    """Bitta section → import_cefr_test ning "type": "listening" formati."""
    from cefr.models import CEFRListeningSection

    s = get_object_or_404(CEFRListeningSection, pk=pk)
    data = {
        'type': 'listening',
        'level': s.level or '',
        'title': s.title,
        'time_limit': s.time_limit,
        'is_premium': s.is_premium,
        'is_mock': s.is_mock,
        'section': {
            'title': s.title,
            'section_number': s.section_number,
            'audio_url': s.audio_url or '',
            'transcript': s.transcript or '',
            'is_standalone': s.is_standalone,
        },
        'questions': _questions(s.questions),
    }
    return Response({'filename': _filename(s.title, 'cefr-listening'), 'data': data})


# ── CEFR TEST (full mock) ────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def export_cefr_test(request, pk):
    """CEFR testi → "parts" (reading), "sections" (listening) yoki
    grammar savollari formati."""
    from cefr.models import CEFRTest

    t = get_object_or_404(CEFRTest, pk=pk)
    kind = request.GET.get('kind')
    passages = list(t.reading_passages.order_by('passage_number'))
    sections = list(t.listening_sections.order_by('section_number'))

    if kind == 'reading' or (kind is None and passages):
        data = {
            'type': 'reading',
            'title': t.title,
            'level': t.level,
            'time_limit': t.time_limit,
            'is_premium': t.is_premium,
            'difficulty': passages[0].difficulty if passages else 'MEDIUM',
            'parts': [{
                'passage_number': p.passage_number,
                'title': p.title,
                'content': p.content,
                'time_limit': p.time_limit,
                'questions': _questions(p.questions),
            } for p in passages],
        }
        return Response({'filename': _filename(t.title, 'cefr-reading-mock'), 'data': data})

    if kind == 'listening' or sections:
        data = {
            'type': 'listening',
            'title': t.title,
            'level': t.level,
            'time_limit': t.time_limit,
            'is_premium': t.is_premium,
            'is_mock': True,
            'sections': [{
                'section_number': s.section_number,
                'title': s.title,
                'audio_url': s.audio_url or '',
                'transcript': s.transcript or '',
                'is_standalone': s.is_standalone,
                'questions': _questions(s.questions),
            } for s in sections],
        }
        return Response({'filename': _filename(t.title, 'cefr-listening-mock'), 'data': data})

    # Grammar / vocabulary testi
    data = {
        'type': 'grammar',
        'title': t.title,
        'level': t.level,
        'test_type': t.test_type,
        'time_limit': t.time_limit,
        'is_premium': t.is_premium,
        'questions': [{
            **q_item,
            **({'passage': q.passage} if q.passage else {}),
        } for q, q_item in zip(t.questions.order_by('number'), _questions(t.questions))],
    }
    return Response({'filename': _filename(t.title, 'cefr-grammar'), 'data': data})
