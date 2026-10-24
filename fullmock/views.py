"""
Full mock API.

Admin  /api/admin/<exam>/full-mocks/…   build a rehearsal from imported tests
Student /api/<exam>/full-mocks/…         list, start or resume one
        /api/full-mocks/attempts/<id>/…  follow it section by section
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import FullMock, FullMockAttempt
from .sections import (
    blocking_issues, missing_sections, options, overall, step_by_key, step_state, steps_for,
)

EXAMS = (FullMock.IELTS, FullMock.CEFR)


def _bad_exam(exam):
    return Response({'error': 'Unknown exam'}, status=404) if exam not in EXAMS else None


def _sections(mock):
    """{step_key: {id, title}} for the tests picked in each section."""
    out = {}
    for s in steps_for(mock.exam):
        obj = getattr(mock, s['field'])
        out[s['key']] = {'id': obj.id, 'title': obj.title} if obj else None
    return out


def _mock_dict(mock):
    steps = steps_for(mock.exam)
    return {
        'id': mock.id, 'exam': mock.exam, 'title': mock.title, 'description': mock.description,
        'is_published': mock.is_published, 'is_premium': mock.is_premium,
        'sections': _sections(mock),
        'missing': missing_sections(mock),
        'steps': [{'key': s['key'], 'label': s['label'], 'minutes': s['minutes']} for s in steps],
        'total_minutes': sum(s['minutes'] for s in steps),
        'created_at': mock.created_at.isoformat(),
        'updated_at': mock.updated_at.isoformat(),
    }


def _publish_problems(mock):
    problems = [f"{step_by_key(mock.exam, k)['label']}: no test picked" for k in missing_sections(mock)]
    for s in steps_for(mock.exam):
        ref = getattr(mock, f"{s['field']}_id")
        if ref:
            problems += [f"{s['label']}: {i}" for i in blocking_issues(mock.exam, s['key'], ref)]
    return problems


def _apply(mock, data):
    """Copy title / flags / picked sections from request data. Returns an error or None."""
    if 'title' in data:
        title = (data.get('title') or '').strip()
        if not title:
            return 'Title is required'
        mock.title = title[:200]
    if 'description' in data:
        mock.description = (data.get('description') or '').strip()
    if 'is_premium' in data:
        mock.is_premium = bool(data['is_premium'])
    picked = data.get('sections') or {}
    for s in steps_for(mock.exam):
        if s['key'] not in picked:
            continue
        ref = picked[s['key']]
        if ref in (None, '', 0):
            setattr(mock, f"{s['field']}_id", None)
            continue
        if not any(o['id'] == int(ref) for o in options(mock.exam, s['key'])):
            return f"{s['label']}: this test cannot be used here"
        setattr(mock, f"{s['field']}_id", int(ref))
    return None


# ── Admin ─────────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def admin_mocks(request, exam):
    if (bad := _bad_exam(exam)):
        return bad
    if request.method == 'GET':
        return Response([_mock_dict(m) for m in FullMock.objects.filter(exam=exam)])
    mock = FullMock(exam=exam, title='')
    err = _apply(mock, {'title': '', **request.data})
    if err:
        return Response({'error': err}, status=400)
    mock.save()
    return Response(_mock_dict(mock), status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_mock_detail(request, exam, pk):
    mock = get_object_or_404(FullMock, pk=pk, exam=exam)
    if request.method == 'DELETE':
        mock.delete()
        return Response(status=204)
    if request.method == 'PATCH':
        err = _apply(mock, request.data)
        if err:
            return Response({'error': err}, status=400)
        if request.data.get('is_published'):
            problems = _publish_problems(mock)
            if problems:
                return Response({'error': 'Cannot publish yet', 'problems': problems}, status=400)
            mock.is_published = True
        elif 'is_published' in request.data:
            mock.is_published = False
        if mock.is_published and _publish_problems(mock):
            mock.is_published = False   # a section was swapped for an unusable test
        mock.save()
    data = _mock_dict(mock)
    data['problems'] = _publish_problems(mock)
    data['attempts'] = mock.attempts.count()
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_options(request, exam):
    if (bad := _bad_exam(exam)):
        return bad
    key = request.query_params.get('section', '')
    if not step_by_key(exam, key):
        return Response({'error': 'Unknown section'}, status=400)
    return Response(options(exam, key))


# ── Student ───────────────────────────────────────────────────────────────────

def _attempt_summary(a):
    return {'id': a.id, 'status': a.status, 'started_at': a.started_at.isoformat(),
            'finished_at': a.finished_at.isoformat() if a.finished_at else None,
            'result': a.result or None}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def student_mocks(request, exam):
    if (bad := _bad_exam(exam)):
        return bad
    from centers.content import scope
    if not scope(request.user)[0]:
        return Response([])       # this student's center shows only its own tests
    mocks = [m for m in FullMock.objects.filter(exam=exam, is_published=True) if not missing_sections(m)]
    mine = {}
    for a in FullMockAttempt.objects.filter(user=request.user, mock__in=mocks).order_by('-started_at'):
        mine.setdefault(a.mock_id, []).append(a)
    out = []
    for m in mocks:
        d = _mock_dict(m)
        for k in ('is_published', 'missing', 'updated_at'):
            d.pop(k)
        attempts = mine.get(m.id, [])
        d['in_progress'] = next((_attempt_summary(a) for a in attempts if a.status == FullMockAttempt.IN_PROGRESS), None)
        d['history'] = [_attempt_summary(a) for a in attempts if a.status == FullMockAttempt.COMPLETED][:5]
        out.append(d)
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def student_start(request, exam, pk):
    mock = get_object_or_404(FullMock, pk=pk, exam=exam, is_published=True)
    if missing_sections(mock):
        return Response({'error': 'This mock is not complete yet'}, status=400)
    user = request.user
    if mock.is_premium and not (getattr(user, 'is_premium', False) or user.is_staff):
        return Response({'error': 'premium_required'}, status=403)
    with transaction.atomic():
        attempt = (FullMockAttempt.objects.select_for_update()
                   .filter(mock=mock, user=user, status=FullMockAttempt.IN_PROGRESS).first())
        if not attempt:
            attempt = FullMockAttempt.objects.create(mock=mock, user=user)
    return Response({'attempt_id': attempt.id}, status=201)


def _attempt_payload(attempt):
    mock = attempt.mock
    states = {}
    steps = []
    reachable = True
    for s in steps_for(mock.exam):
        st = step_state(mock, s, attempt.user, attempt.steps.get(s['key']), attempt.started_at)
        states[s['key']] = st
        test = getattr(mock, s['field'])
        if st and st['state'] == 'done':
            state = 'done'
        elif not reachable:
            state = 'locked'
        else:
            state = 'in_progress' if st else 'todo'
            reachable = False
        steps.append({
            'key': s['key'], 'label': s['label'], 'minutes': s['minutes'], 'skill': s['skill'],
            'state': state,
            'score': st['score'] if st else None, 'detail': st['detail'] if st else '',
            'scored': bool(st and st['scored']),
            'review_url': st['review_url'] if st else None,
            'response_id': st['response_id'] if st else None,
            'test': {'type': s['type'], 'id': test.id if test else None, 'kind': s['kind'],
                     'title': test.title if test else ''},
        })

    result = overall(mock.exam, states)
    all_done = all(s['state'] == 'done' for s in steps)
    changed = []
    if all_done and attempt.status != FullMockAttempt.COMPLETED:
        attempt.status, attempt.finished_at = FullMockAttempt.COMPLETED, timezone.now()
        changed += ['status', 'finished_at']
    if result['complete'] and attempt.result != result:
        attempt.result = result
        changed.append('result')
    if changed:
        attempt.save(update_fields=changed)

    return {
        **_attempt_summary(attempt),
        'mock': {'id': mock.id, 'exam': mock.exam, 'title': mock.title, 'description': mock.description},
        'steps': steps,
        'current': next((s['key'] for s in steps if s['state'] != 'done'), None),
        'result': result,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def attempt_detail(request, attempt_id):
    attempt = get_object_or_404(FullMockAttempt.objects.select_related('mock'), id=attempt_id, user=request.user)
    return Response(_attempt_payload(attempt))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def attempt_link(request, attempt_id):
    """Remember which section attempt belongs to this rehearsal.

    Sections go in exam order: a section can only be linked once every
    section before it is finished, and a finished one cannot be replaced.
    """
    attempt = get_object_or_404(FullMockAttempt.objects.select_related('mock'), id=attempt_id, user=request.user)
    if attempt.status == FullMockAttempt.COMPLETED:
        return Response({'error': 'This mock is already finished'}, status=400)
    mock = attempt.mock
    key = request.data.get('step')
    step = step_by_key(mock.exam, key)
    try:
        ref_id = int(request.data.get('ref_id'))
    except (TypeError, ValueError):
        return Response({'error': 'ref_id required'}, status=400)
    if not step:
        return Response({'error': 'Unknown section'}, status=400)

    for s in steps_for(mock.exam):
        st = step_state(mock, s, request.user, attempt.steps.get(s['key']), attempt.started_at)
        if s['key'] == key:
            if st and st['state'] == 'done':
                return Response({'error': 'This section is already finished'}, status=400)
            break
        if not st or st['state'] != 'done':
            return Response({'error': f"Finish {s['label']} first"}, status=400)

    from .sections import valid_link
    if not valid_link(mock, step, request.user, ref_id):
        return Response({'error': 'That attempt does not belong to this section'}, status=400)
    attempt.steps = {**attempt.steps, key: ref_id}
    attempt.save(update_fields=['steps'])
    return Response(_attempt_payload(attempt))
