"""
Lessons: a teacher gives a topic plus several tests at once (any mix of
IELTS, CEFR and SAT) to a group or to one student, and can take them back.

Only teachers give lessons — directors and admins watch. ExamBridge owner
and level-1 admins may act anywhere (support).
"""
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .analytics import DONE, LATE, lesson_state, task_state
from .models import Assignment, AssignmentSubmission, CenterMembership, Group, LearningCenter, Lesson
from .progress import TASK_KINDS
from .views import center_role, notify, sync_center, teacher_student_ids, user_to_dict

MAX_TASKS = 15


def _center(center_id):
    return LearningCenter.objects.filter(id=center_id).first()


def _is_support(user):
    return bool(getattr(user, 'can_write_admin', False))


def _can_watch(user, center):
    return center_role(user, center) in ('director', 'admin', 'teacher') or user.is_staff


def _aware(raw):
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _name(u):
    return f'{u.first_name} {u.last_name}'.strip() or u.email


def _lesson_dict(lesson, subs):
    """List view of one lesson: its tasks and how many students finished."""
    by_task = {}
    by_student = {}
    for s in subs:
        by_task.setdefault(s.assignment_id, []).append(s)
        by_student.setdefault(s.student_id, []).append(task_state(s))
    students_done = sum(1 for states in by_student.values() if all(st in (DONE, LATE) for st in states))
    tasks = [
        {
            'id': a.id, 'task_type': a.task_type, 'task_title': a.task_title,
            'task_ref_kind': a.task_ref_kind,
            'done': sum(1 for s in by_task.get(a.id, []) if s.status == 'completed'),
            'total': len(by_task.get(a.id, [])),
        }
        for a in lesson.tasks.all()
    ]
    return {
        'id': lesson.id,
        'topic': lesson.topic,
        'description': lesson.description,
        'date': lesson.date.isoformat(),
        'deadline': lesson.deadline.isoformat() if lesson.deadline else None,
        'is_overdue': bool(lesson.deadline and lesson.deadline < timezone.now()),
        'teacher': {'id': lesson.teacher_id, 'name': _name(lesson.teacher)},
        'group': {'id': lesson.group_id, 'name': lesson.group.name} if lesson.group_id else None,
        'student': {'id': lesson.student_id, 'name': _name(lesson.student)} if lesson.student_id else None,
        'tasks': tasks,
        'students_total': len(by_student),
        'students_done': students_done,
        'created_at': lesson.created_at.isoformat(),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def lessons(request, center_id):
    center = _center(center_id)
    if not center:
        return Response({'error': 'Not found'}, status=404)
    role = center_role(request.user, center)

    if request.method == 'GET':
        if not _can_watch(request.user, center):
            return Response({'error': 'Forbidden'}, status=403)
        sync_center(center)
        qs = center.lessons.select_related('teacher', 'group', 'student').prefetch_related('tasks')
        if role == 'teacher':
            qs = qs.filter(teacher=request.user)
        if request.query_params.get('group_id'):
            qs = qs.filter(group_id=request.query_params['group_id'])
        qs = list(qs[:60])
        subs = {}
        for s in AssignmentSubmission.objects.filter(assignment__lesson__in=qs).select_related('assignment'):
            subs.setdefault(s.assignment.lesson_id, []).append(s)
        return Response([_lesson_dict(l, subs.get(l.id, [])) for l in qs])

    # POST — teachers only (plus ExamBridge support)
    if role != 'teacher' and not _is_support(request.user):
        return Response({'error': 'Only teachers give lessons'}, status=403)

    d = request.data
    topic = (d.get('topic') or '').strip()
    tasks = d.get('tasks') or []
    if not topic:
        return Response({'error': 'Topic is required'}, status=400)
    if not isinstance(tasks, list) or not tasks:
        return Response({'error': 'Add at least one task'}, status=400)
    if len(tasks) > MAX_TASKS:
        return Response({'error': f'At most {MAX_TASKS} tasks in one lesson'}, status=400)
    for t in tasks:
        kind_ok = not t.get('task_ref_id') or t.get('task_ref_kind') in TASK_KINDS.get(t.get('task_type'), ())
        if t.get('task_type') not in TASK_KINDS or not kind_ok or not (t.get('task_title') or '').strip():
            return Response({'error': 'One of the tasks is not valid'}, status=400)

    group = student = None
    if d.get('group_id'):
        group = Group.objects.filter(id=d['group_id'], center=center).first()
        if not group:
            return Response({'error': 'Group not found'}, status=400)
        if role == 'teacher' and group.teacher_id != request.user.id:
            return Response({'error': 'You can only teach your own groups'}, status=403)
        recipients = [gm.student for gm in group.memberships.select_related('student')]
    elif d.get('student_id'):
        m = CenterMembership.objects.filter(center=center, user_id=d['student_id'], role='student',
                                            is_active=True).select_related('user').first()
        if not m:
            return Response({'error': 'Student not in center'}, status=400)
        if role == 'teacher' and m.user_id not in teacher_student_ids(request.user, center):
            return Response({'error': 'You can only teach your own students'}, status=403)
        student = m.user
        recipients = [student]
    else:
        return Response({'error': 'Choose a group or a student'}, status=400)

    with transaction.atomic():
        lesson = Lesson.objects.create(
            center=center, teacher=request.user, group=group, student=student, topic=topic,
            description=(d.get('description') or '').strip(),
            date=parse_date(d.get('date') or '') or timezone.localdate(),
            deadline=_aware(d.get('deadline')),
        )
        for t in tasks:
            a = Assignment.objects.create(
                center=center, assigned_by=request.user, lesson=lesson, group=group, student=student,
                task_type=t['task_type'], task_title=t['task_title'].strip()[:200],
                task_ref_id=t.get('task_ref_id') or None, task_ref_kind=t.get('task_ref_kind') or '',
                deadline=lesson.deadline,
            )
            AssignmentSubmission.objects.bulk_create(
                [AssignmentSubmission(assignment=a, student=r) for r in recipients])
        who = request.user.get_full_name() or request.user.email
        for r in recipients:
            notify(r, center, 'assignment', f'New lesson: {topic}',
                   f'{len(tasks)} task{"s" if len(tasks) > 1 else ""} from {who}')

    return Response({'id': lesson.id}, status=201)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def lesson_detail(request, center_id, lesson_id):
    center = _center(center_id)
    lesson = center and center.lessons.select_related('teacher', 'group', 'student').filter(id=lesson_id).first()
    if not lesson:
        return Response({'error': 'Not found'}, status=404)
    role = center_role(request.user, center)
    own = lesson.teacher_id == request.user.id

    if request.method == 'DELETE':
        if not own and not _is_support(request.user):
            return Response({'error': 'Only the teacher who gave it can delete it'}, status=403)
        lesson.delete()
        return Response({'deleted': True})

    if not _can_watch(request.user, center) or (role == 'teacher' and not own):
        return Response({'error': 'Forbidden'}, status=403)
    sync_center(center)
    subs = list(AssignmentSubmission.objects.filter(assignment__lesson=lesson)
                .select_related('assignment', 'student'))
    rows = {}
    for s in subs:
        r = rows.setdefault(s.student_id, {'student': user_to_dict(s.student), 'tasks': {}})
        r['tasks'][s.assignment_id] = {'state': task_state(s), 'detail': s.score_detail,
                                       'at': s.submitted_at.isoformat() if s.submitted_at else None}
    for r in rows.values():
        r['state'] = lesson_state([t['state'] for t in r['tasks'].values()])
    data = _lesson_dict(lesson, subs)
    data['students'] = sorted(rows.values(), key=lambda r: r['student']['full_name'])
    return Response(data)
