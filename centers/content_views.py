"""
A learning center's own tests (IELTS and CEFR).

Admins and teachers import tests with the same JSON as the ExamBridge admin
panel — the request is handed to the admin importer, and everything it
creates belongs to the center (content.importing_for). They can delete their
center's tests and add listening audio / task pictures. The director decides
whether students also see ExamBridge's tests (show_site_mocks).
"""
from django.db.models import Count
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .content import importing_for
from .models import LearningCenter
from .views import center_role

MANAGE = ('admin', 'teacher')
WATCH = ('director', 'admin', 'teacher')


def _support(user):
    return bool(getattr(user, 'can_write_admin', False))


def _center_for(request, center_id, roles):
    """(center, None) or (None, error response)."""
    center = LearningCenter.objects.filter(id=center_id).first()
    if not center:
        return None, Response({'error': 'Not found'}, status=404)
    if center_role(request.user, center) not in roles and not _support(request.user):
        return None, Response({'error': 'Forbidden'}, status=403)
    return center, None


def _delegate(view_func, request, **kwargs):
    """Run an admin view for a center user (the caller already checked access)."""
    base = view_func.cls
    view = type(f'Center{base.__name__}', (base,), {'permission_classes': [IsAuthenticated]}).as_view()
    return view(request._request, **kwargs)


# ── what each kind is ─────────────────────────────────────────────────────────

def _importers():
    from api import cefr_speaking, cefr_writing
    from api.import_views import (
        import_cefr_test, import_ielts_listening, import_ielts_reading, import_ielts_speaking, import_ielts_writing,
    )
    return {
        'ielts-reading': import_ielts_reading, 'ielts-listening': import_ielts_listening,
        'ielts-writing': import_ielts_writing, 'ielts-speaking': import_ielts_speaking,
        'cefr-reading': import_cefr_test, 'cefr-listening': import_cefr_test,
        'cefr-writing': cefr_writing.import_writing, 'cefr-speaking': cefr_speaking.import_speaking,
    }


def _models():
    from cefr.models import CEFRListeningSection, CEFRReadingPassage, CEFRSpeakingTest, CEFRTest, CEFRWritingTest
    from ielts.models import IELTSTest, ListeningSection, ReadingPassage, SpeakingTask, WritingTask
    return {
        'ielts-test': IELTSTest, 'ielts-passage': ReadingPassage, 'ielts-section': ListeningSection,
        'ielts-writing': WritingTask, 'ielts-speaking': SpeakingTask,
        'cefr-test': CEFRTest, 'cefr-passage': CEFRReadingPassage, 'cefr-section': CEFRListeningSection,
        'cefr-writing': CEFRWritingTest, 'cefr-speaking': CEFRSpeakingTest,
    }


def _has_audio(obj):
    return bool(getattr(obj, 'audio_file', None) or getattr(obj, 'audio_url', ''))


def _list(center, kind):
    """Tests of one kind that belong to the center: full mocks and single parts."""
    from cefr.models import CEFRListeningSection, CEFRReadingPassage, CEFRSpeakingTest, CEFRTest, CEFRWritingTest
    from ielts.models import IELTSTest, ListeningSection, ReadingPassage, SpeakingTask, WritingTask
    mocks, items = [], []

    def part_rows(parts, num_field):
        return [{'id': p.id, 'title': p.title, 'number': getattr(p, num_field, None),
                 'questions': p.questions.count(), 'audio': _has_audio(p)} for p in parts]

    if kind in ('ielts-reading', 'ielts-listening'):
        reading = kind == 'ielts-reading'
        rel, model, num = ('passages', ReadingPassage, 'passage_number') if reading else \
            ('listening_sections', ListeningSection, 'section_number')
        for t in IELTSTest.objects.filter(center=center, **{f'{rel}__isnull': False}).distinct():
            parts = getattr(t, rel).order_by(num)
            row = {'type': 'ielts-test', 'id': t.id, 'title': t.title, 'parts': part_rows(parts, num)}
            if not reading:
                row['audio'] = _has_audio(t) or all(_has_audio(p) for p in parts)
            mocks.append(row)
        for p in model.objects.filter(center=center, test__isnull=True).order_by(num, '-created_at'):
            items.append({'type': 'ielts-passage' if reading else 'ielts-section', 'id': p.id, 'title': p.title,
                          'meta': f"{'Passage' if reading else 'Part'} {getattr(p, num)}",
                          'questions': p.questions.count(), **({} if reading else {'audio': _has_audio(p)})})
    elif kind == 'ielts-writing':
        for t in WritingTask.objects.filter(center=center).order_by('task_type', '-created_at'):
            items.append({'type': 'ielts-writing', 'id': t.id, 'title': t.title,
                          'meta': f'Task {t.task_type} · {t.get_test_type_display()}',
                          'needs_image': t.task_type == 1 and t.test_type == 'ACADEMIC' and not t.image,
                          'image': bool(t.image)})
    elif kind == 'ielts-speaking':
        for t in SpeakingTask.objects.filter(center=center).order_by('part', '-created_at'):
            items.append({'type': 'ielts-speaking', 'id': t.id, 'title': t.title,
                          'meta': ('Full mock' if t.test_type == 'MOCK' else f'Part {t.part}')
                          + (' · CEFR' if t.source == 'CEFR' else '')})
    elif kind in ('cefr-reading', 'cefr-listening'):
        reading = kind == 'cefr-reading'
        rel, model, num = ('reading_passages', CEFRReadingPassage, 'passage_number') if reading else \
            ('listening_sections', CEFRListeningSection, 'section_number')
        for t in CEFRTest.objects.filter(center=center, test_type='READING' if reading else 'LISTENING'):
            parts = getattr(t, rel).order_by(num)
            row = {'type': 'cefr-test', 'id': t.id, 'title': t.title, 'meta': t.level,
                   'parts': part_rows(parts, num)}
            if not reading:
                row['audio'] = _has_audio(t) or (parts.exists() and all(_has_audio(p) for p in parts))
            mocks.append(row)
        for p in model.objects.filter(center=center, test__isnull=True).order_by(num, '-created_at'):
            items.append({'type': 'cefr-passage' if reading else 'cefr-section', 'id': p.id, 'title': p.title,
                          'meta': f'Part {getattr(p, num)}' + (f' · {p.level}' if p.level else ''),
                          'questions': p.questions.count(), **({} if reading else {'audio': _has_audio(p)})})
    elif kind == 'cefr-writing':
        for t in CEFRWritingTest.objects.filter(center=center).annotate(n=Count('responses')):
            items.append({'type': 'cefr-writing', 'id': t.id, 'title': t.title, 'meta': t.get_kind_display(),
                          'attempts': t.n})
    elif kind == 'cefr-speaking':
        from api.cefr_speaking import is_ready, parts_of
        for t in CEFRSpeakingTest.objects.filter(center=center).annotate(n=Count('responses')):
            items.append({'type': 'cefr-speaking', 'id': t.id, 'title': t.title,
                          'meta': 'Parts ' + ', '.join(parts_of(t)), 'attempts': t.n,
                          'needs_images': not is_ready(t),
                          'slots': [s for s, f in (('p12a', t.part12_image1), ('p12b', t.part12_image2),
                                                   ('p2', t.part2_image)) if not f]})
    return {'mocks': mocks, 'items': items}


# ── endpoints ─────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def content_list(request, center_id):
    center, err = _center_for(request, center_id, WATCH)
    if err:
        return err
    kind = request.query_params.get('kind', '')
    if kind not in _importers():
        return Response({'error': 'Unknown kind'}, status=400)
    data = _list(center, kind)
    data['can_manage'] = center_role(request.user, center) in MANAGE or _support(request.user)
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def content_import(request, center_id, kind):
    center, err = _center_for(request, center_id, MANAGE)
    if err:
        return err
    importer = _importers().get(kind)
    if not importer:
        return Response({'error': 'Unknown kind'}, status=400)
    with importing_for(center):
        return _delegate(importer, request)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def content_delete(request, center_id, type_key, pk):
    center, err = _center_for(request, center_id, MANAGE)
    if err:
        return err
    model = _models().get(type_key)
    obj = model and model.objects.filter(pk=pk, center=center).first()
    if not obj:
        return Response({'error': 'Not found'}, status=404)
    obj.delete()
    return Response({'deleted': True})


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def content_media(request, center_id, type_key, pk):
    """Listening audio, a Task 1 chart, CEFR speaking pictures — on the center's own tests."""
    center, err = _center_for(request, center_id, MANAGE)
    if err:
        return err
    from api import cefr_speaking
    from api.import_views import (
        admin_cefr_listening_audio, admin_cefr_test_audio, admin_ielts_listening_upload_audio,
        admin_ielts_test_audio, admin_ielts_writing_upload_image,
    )
    handlers = {
        'ielts-test': admin_ielts_test_audio, 'ielts-section': admin_ielts_listening_upload_audio,
        'cefr-test': admin_cefr_test_audio, 'cefr-section': admin_cefr_listening_audio,
        'ielts-writing': admin_ielts_writing_upload_image, 'cefr-speaking': cefr_speaking.admin_speaking_image,
    }
    model = _models().get(type_key)
    if type_key not in handlers or not model.objects.filter(pk=pk, center=center).exists():
        return Response({'error': 'Not found'}, status=404)
    return _delegate(handlers[type_key], request, pk=pk)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def content_settings(request, center_id):
    """Director's switch: do students also see ExamBridge's tests?"""
    center, err = _center_for(request, center_id, ('director',))
    if err:
        return err
    if 'show_site_mocks' in request.data:
        center.show_site_mocks = bool(request.data['show_site_mocks'])
        center.save(update_fields=['show_site_mocks'])
    return Response({'show_site_mocks': center.show_site_mocks})
