"""
Read-only screens: the center overview (different for director, admin and
teacher), a group's journal, and a student's own progress.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .analytics import (
    activity_days, daily_active, growth, lesson_key, lesson_state, summarize, task_state, weekly,
)
from .models import AssignmentSubmission, GroupMembership, LearningCenter
from .progress import sync_submissions
from .views import center_role, has_role, sync_center

User = get_user_model()


def _name(u):
    return f'{u.first_name} {u.last_name}'.strip() or u.email


def _iso(dt):
    return dt.isoformat() if dt else None


def _last_seen(user, items, days):
    """Latest of: sign-in, finished task, day with exam activity."""
    marks = [user.last_login] + [s.submitted_at for s in items if s.submitted_at]
    if days:
        latest = max(days)
        marks.append(timezone.make_aware(datetime.combine(latest, time.min)))
    marks = [m for m in marks if m]
    return max(marks) if marks else None


def _task_row(s):
    a = s.assignment
    return {
        'title': a.task_title, 'type': a.task_type, 'status': s.status, 'state': task_state(s),
        'detail': s.score_detail, 'lesson': a.lesson.topic if a.lesson_id else None,
        'deadline': _iso(a.deadline), 'at': _iso(s.submitted_at),
    }


# ── Center overview ───────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def center_monitoring(request, center_id):
    """Overview for whoever runs the center.

    teacher  — their own groups and the students they teach
    admin    — the whole center plus "to fix" lists (no group, no teacher…)
    director — the whole center plus trends and teacher comparison
    ExamBridge staff see what a director sees.
    """
    center = LearningCenter.objects.filter(id=center_id).first()
    if not center:
        return Response({'error': 'Not found'}, status=404)
    role = center_role(request.user, center)
    if role not in ('director', 'admin', 'teacher') and not request.user.is_staff:
        return Response({'error': 'Forbidden'}, status=403)
    if role not in ('director', 'admin', 'teacher'):
        role = 'staff'
    is_teacher = role == 'teacher'

    sync_center(center)
    now = timezone.now()
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)

    groups = list(center.groups.select_related('teacher'))
    if is_teacher:
        groups = [g for g in groups if g.teacher_id == request.user.id]
    group_students, student_groups = defaultdict(set), defaultdict(list)
    for gm in GroupMembership.objects.filter(group__in=groups).select_related('group'):
        group_students[gm.group_id].add(gm.student_id)
        student_groups[gm.student_id].append(gm.group.name)

    subs_qs = AssignmentSubmission.objects.filter(assignment__center=center).select_related(
        'assignment', 'assignment__lesson')
    if is_teacher:
        subs_qs = subs_qs.filter(Q(assignment__assigned_by=request.user) | Q(assignment__group__in=groups))
        student_ids = set(student_groups) | set(subs_qs.values_list('student_id', flat=True))
    else:
        student_ids = set(center.memberships.filter(role='student', is_active=True)
                          .values_list('user_id', flat=True))
    subs = list(subs_qs.filter(student_id__in=student_ids))
    students = list(User.objects.filter(id__in=student_ids))
    days = activity_days(student_ids, month_ago)

    per_student = defaultdict(list)
    for s in subs:
        per_student[s.student_id].append(s)

    student_rows = []
    for u in students:
        items = per_student.get(u.id, [])
        seen = _last_seen(u, items, days.get(u.id))
        finished = [s for s in items if s.status == 'completed' and s.submitted_at]
        last = max(finished, key=lambda s: s.submitted_at, default=None)
        student_rows.append({
            'id': u.id, 'name': _name(u), 'email': u.email,
            'groups': student_groups.get(u.id, []),
            **summarize(items, len(days.get(u.id, ()))),
            'last_seen': _iso(seen),
            'inactive': not seen or seen < week_ago,
            'last_result': {'title': last.assignment.task_title, 'type': last.assignment.task_type,
                            'detail': last.score_detail, 'at': _iso(last.submitted_at)} if last else None,
            'tasks': [_task_row(s) for s in sorted(items, key=lambda s: s.assignment.created_at, reverse=True)[:12]],
        })
    student_rows.sort(key=lambda r: (-r['missed'], r['rate'] if r['rate'] is not None else 101, r['name']))

    group_rows = []
    for g in groups:
        members = group_students.get(g.id, set())
        items = [s for s in subs if s.student_id in members]
        st = summarize(items)
        group_rows.append({
            'id': g.id, 'name': g.name, 'teacher': _name(g.teacher) if g.teacher else None,
            'students': len(members), 'rate': st['rate'], 'missed': st['missed'],
            'done': st['done'], 'total': st['total'], 'lessons': st['lessons']['total'],
            'study_days': round(sum(len(days.get(i, ())) for i in members) / len(members), 1) if members else 0,
        })

    # Recent lessons with how many students finished them
    by_lesson = defaultdict(lambda: defaultdict(list))
    for s in subs:
        if s.assignment.lesson_id:
            by_lesson[s.assignment.lesson_id][s.student_id].append(task_state(s))
    lessons_qs = center.lessons.select_related('group', 'teacher')
    if is_teacher:
        lessons_qs = lessons_qs.filter(teacher=request.user)
    recent_lessons = []
    for l in lessons_qs[:6]:
        per = by_lesson.get(l.id, {})
        recent_lessons.append({
            'id': l.id, 'topic': l.topic, 'date': l.date.isoformat(),
            'group': l.group.name if l.group_id else None, 'teacher': _name(l.teacher),
            'students': len(per),
            'done': sum(1 for states in per.values() if lesson_state(states) in ('done', 'late')),
        })

    recent = sorted((s for s in subs if s.status == 'completed' and s.submitted_at),
                    key=lambda s: s.submitted_at, reverse=True)[:12]
    names = {u.id: _name(u) for u in students}
    total = summarize(subs)
    active_7d = sum(1 for r in student_rows if not r['inactive'])

    data = {
        'center': {'id': center.id, 'name': center.name},
        'my_role': role,
        'kpis': {
            'students': len(students),
            'active_7d': active_7d,
            'study_days_avg': round(sum(len(days.get(i, ())) for i in student_ids) / len(student_ids), 1)
            if student_ids else 0,
            'tasks': total['total'], 'done': total['done'], 'rate': total['rate'],
            'late': total['late'], 'missed': total['missed'], 'pending': total['pending'],
            'lessons_7d': lessons_qs.filter(created_at__gte=week_ago).count(),
            'results_7d': sum(1 for s in subs if s.status == 'completed'
                              and s.submitted_at and s.submitted_at >= week_ago),
        },
        'students': student_rows,
        'groups': group_rows,
        'lessons': recent_lessons,
        'weekly': weekly(subs),
        'recent_results': [
            {'student': names.get(s.student_id, ''), 'student_id': s.student_id,
             'title': s.assignment.task_title, 'type': s.assignment.task_type,
             'lesson': s.assignment.lesson.topic if s.assignment.lesson_id else None,
             'detail': s.score_detail, 'at': _iso(s.submitted_at)}
            for s in recent
        ],
    }

    if not is_teacher:
        data['daily'] = daily_active(days)
        teachers = []
        for m in center.memberships.filter(role='teacher', is_active=True).select_related('user'):
            t = m.user
            t_groups = [g for g in groups if g.teacher_id == t.id]
            items = [s for s in subs if s.assignment.assigned_by_id == t.id]
            st = summarize(items)
            last_lesson = center.lessons.filter(teacher=t).order_by('-created_at').first()
            teachers.append({
                'id': t.id, 'name': _name(t), 'groups': len(t_groups),
                'students': len(set().union(*(group_students.get(g.id, set()) for g in t_groups))),
                'lessons': center.lessons.filter(teacher=t).count(),
                'lessons_7d': center.lessons.filter(teacher=t, created_at__gte=week_ago).count(),
                'last_lesson': _iso(last_lesson.created_at) if last_lesson else None,
                'rate': st['rate'], 'missed': st['missed'], 'done': st['done'], 'total': st['total'],
                'last_seen': _iso(t.last_login),
            })
        teachers.sort(key=lambda r: (-(r['rate'] or -1), r['name']))
        data['teachers'] = teachers
        grouped = set(student_groups)
        data['to_fix'] = {
            'students_without_group': [{'id': u.id, 'name': _name(u)} for u in students if u.id not in grouped],
            'groups_without_teacher': [{'id': g.id, 'name': g.name} for g in groups if not g.teacher_id],
            'teachers_without_group': [{'id': t['id'], 'name': t['name']} for t in teachers if not t['groups']],
        }
    return Response(data)


# ── Group journal ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def group_journal(request, center_id, group_id):
    """Students × lessons grid for one group, with each student's numbers."""
    center = LearningCenter.objects.filter(id=center_id).first()
    group = center and center.groups.select_related('teacher').filter(id=group_id).first()
    if not group:
        return Response({'error': 'Not found'}, status=404)
    role = center_role(request.user, center)
    if role == 'teacher':
        if group.teacher_id != request.user.id:
            return Response({'error': 'Forbidden'}, status=403)
    elif role not in ('director', 'admin') and not request.user.is_staff:
        return Response({'error': 'Forbidden'}, status=403)

    sync_center(center)
    members = [gm.student for gm in group.memberships.select_related('student')]
    ids = [u.id for u in members]
    subs = list(AssignmentSubmission.objects.filter(assignment__group=group, student_id__in=ids)
                .select_related('assignment', 'assignment__lesson'))
    days = activity_days(ids, timezone.now() - timedelta(days=30))

    units = {}
    for s in subs:
        key = lesson_key(s)
        if key in units:
            continue
        a = s.assignment
        if a.lesson_id:
            units[key] = {'key': f'l{a.lesson_id}', 'kind': 'lesson', 'id': a.lesson_id,
                          'title': a.lesson.topic, 'date': a.lesson.date.isoformat(),
                          'deadline': _iso(a.lesson.deadline)}
        else:
            units[key] = {'key': f'a{a.id}', 'kind': 'task', 'id': a.id, 'title': a.task_title,
                          'date': timezone.localtime(a.created_at).date().isoformat(),
                          'deadline': _iso(a.deadline)}
    columns = sorted(units.items(), key=lambda kv: kv[1]['date'], reverse=True)[:20]
    keep = {k for k, _ in columns}

    states = defaultdict(lambda: defaultdict(list))
    per_student = defaultdict(list)
    for s in subs:
        per_student[s.student_id].append(s)
        k = lesson_key(s)
        if k in keep:
            states[s.student_id][k].append(task_state(s))

    rows = []
    for u in members:
        items = per_student.get(u.id, [])
        seen = _last_seen(u, items, days.get(u.id))
        rows.append({
            'id': u.id, 'name': _name(u), 'email': u.email,
            **summarize(items, len(days.get(u.id, ()))),
            'last_seen': _iso(seen),
            'cells': {units[k]['key']: lesson_state(v) for k, v in states[u.id].items()},
        })
    rows.sort(key=lambda r: r['name'])

    return Response({
        'group': {'id': group.id, 'name': group.name,
                  'teacher': {'id': group.teacher_id, 'name': _name(group.teacher)} if group.teacher else None},
        'can_manage_members': has_role(request.user, center, 'admin'),
        'can_teach': role == 'teacher' or bool(getattr(request.user, 'can_write_admin', False)),
        'stats': summarize(subs, round(sum(len(days.get(i, ())) for i in ids) / len(ids), 1) if ids else 0),
        'columns': [u for _, u in columns],
        'students': rows,
        'weekly': weekly(subs),
    })


# ── Student: my progress ──────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_progress(request):
    """A center student's own numbers: tasks, lessons, study days, growth."""
    mine = AssignmentSubmission.objects.filter(student=request.user)
    sync_submissions(mine)
    subs = list(mine.select_related('assignment', 'assignment__lesson'))
    days = activity_days([request.user.id], timezone.now() - timedelta(days=60)).get(request.user.id, set())

    today = timezone.localdate()
    streak, d = 0, today if today in days else today - timedelta(days=1)
    while d in days:
        streak += 1
        d -= timedelta(days=1)

    weeks = weekly(subs)
    # Compare finished weeks only: this week's tasks are still being done.
    rated = [w['rate'] for w in weeks[:-1] if w['rate'] is not None]
    month = {x for x in days if x >= today - timedelta(days=29)}
    return Response({
        **summarize(subs, len(month)),
        'streak': streak,
        'last_14': [{'date': (today - timedelta(days=i)).isoformat(),
                     'studied': (today - timedelta(days=i)) in days} for i in range(13, -1, -1)],
        'weekly': weeks,
        'rate_change': (rated[-1] - rated[-2]) if len(rated) >= 2 else None,
        'growth': growth(subs),
    })
