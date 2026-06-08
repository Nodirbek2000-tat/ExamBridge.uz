"""
Learning Center API Views.
Role-based access: superadmin > director > admin > teacher > student
"""
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils.dateparse import parse_datetime

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import (
    LearningCenter, CenterMembership, Group, GroupMembership,
    Assignment, AssignmentSubmission, CenterNotification
)

User = get_user_model()


# ── Helpers ───────────────────────────────────────────────────────────────────

def center_role(user, center):
    try:
        m = CenterMembership.objects.get(center=center, user=user, is_active=True)
        return m.role
    except CenterMembership.DoesNotExist:
        return None


def has_role(user, center, *roles):
    return center_role(user, center) in roles or user.is_staff


def user_to_dict(user, role=None):
    return {
        'id': user.id,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'full_name': (f"{user.first_name} {user.last_name}".strip()) or user.email,
        'phone': getattr(user, 'phone', '') or '',
        'avatar': user.avatar.url if getattr(user, 'avatar', None) else None,
        'role': role,
    }


def center_to_dict(center, include_director=True):
    d = {
        'id': center.id,
        'name': center.name,
        'logo': center.logo.url if center.logo else None,
        'phone': center.phone,
        'address': center.address,
        'is_active': center.is_active,
        'student_count': center.student_count,
        'teacher_count': center.teacher_count,
        'admin_count': center.admin_count,
        'created_at': center.created_at.isoformat(),
    }
    if include_director:
        d['director'] = user_to_dict(center.director) if center.director else None
    return d


def notify(recipient, center, notif_type, title, message='', assignment=None, submission=None):
    CenterNotification.objects.create(
        recipient=recipient,
        center=center,
        notification_type=notif_type,
        title=title,
        message=message,
        assignment=assignment,
        submission=submission,
    )


# ── Super Admin: Center CRUD ──────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_centers_list(request):
    centers = LearningCenter.objects.all()
    return Response([center_to_dict(c) for c in centers])


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_center_create(request):
    data = request.data
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    address = data.get('address', '').strip()
    dir_first = data.get('director_first_name', '').strip()
    dir_last = data.get('director_last_name', '').strip()
    dir_email = data.get('director_email', '').strip()
    dir_phone = data.get('director_phone', '').strip()
    dir_password = data.get('director_password', '').strip()

    if not name:
        return Response({'error': 'Center name required'}, status=400)
    if not dir_email or not dir_password:
        return Response({'error': 'Director email and password required'}, status=400)
    if User.objects.filter(email=dir_email).exists():
        return Response({'error': f'User {dir_email} already exists'}, status=400)

    with transaction.atomic():
        center = LearningCenter.objects.create(name=name, phone=phone, address=address)
        if 'logo' in request.FILES:
            center.logo = request.FILES['logo']
            center.save()
        director = User.objects.create_user(
            email=dir_email, username=dir_email, password=dir_password,
            first_name=dir_first, last_name=dir_last,
        )
        if dir_phone:
            director.phone = dir_phone
            director.save(update_fields=['phone'])
        CenterMembership.objects.create(
            center=center, user=director, role=CenterMembership.DIRECTOR,
            member_password=dir_password,
        )

    return Response(center_to_dict(center), status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_center_detail(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if request.method == 'GET':
        from datetime import timedelta
        from django.db.models import Count
        now = timezone.now()
        month_ago = now - timedelta(days=30)

        memberships = list(
            center.memberships.filter(is_active=True).select_related('user')
        )

        # Collect user ids to batch-query attempt counts
        all_user_ids = [m.user_id for m in memberships]
        student_ids  = [m.user_id for m in memberships if m.role == 'student']

        # Test completion counts per user (SAT + IELTS + CEFR)
        try:
            from tests_app.models import TestAttempt
            from ielts.models import IELTSAttempt
            from cefr.models import CEFRAttempt

            sat_map = dict(
                TestAttempt.objects.filter(user_id__in=all_user_ids, status='COMPLETED')
                .values('user_id').annotate(c=Count('id')).values_list('user_id', 'c')
            )
            ielts_map = dict(
                IELTSAttempt.objects.filter(user_id__in=all_user_ids, status='COMPLETED')
                .values('user_id').annotate(c=Count('id')).values_list('user_id', 'c')
            )
            cefr_map = dict(
                CEFRAttempt.objects.filter(user_id__in=all_user_ids, status='COMPLETED')
                .values('user_id').annotate(c=Count('id')).values_list('user_id', 'c')
            )
        except Exception:
            sat_map = ielts_map = cefr_map = {}

        def tests_for(uid):
            return sat_map.get(uid, 0) + ielts_map.get(uid, 0) + cefr_map.get(uid, 0)

        data = center_to_dict(center)
        data['members'] = [
            {
                **user_to_dict(m.user, m.role),
                'password': m.member_password,
                'joined_at': m.joined_at.isoformat() if m.joined_at else None,
                'last_login': m.user.last_login.isoformat() if m.user.last_login else None,
                'is_active_user': bool(m.user.last_login and m.user.last_login >= month_ago),
                'tests_count': tests_for(m.user_id),
            }
            for m in memberships
        ]
        data['groups'] = [
            {'id': g.id, 'name': g.name, 'student_count': g.student_count}
            for g in center.groups.all()
        ]

        # Center-level aggregated stats
        active_students  = sum(1 for m in memberships if m.role == 'student' and m.user.last_login and m.user.last_login >= month_ago)
        total_students   = sum(1 for m in memberships if m.role == 'student')
        total_tests      = sum(tests_for(m.user_id) for m in memberships if m.role == 'student')
        total_assignments= center.assignments.count()
        completed_subs   = AssignmentSubmission.objects.filter(
            assignment__center=center, status='completed'
        ).count()

        data['stats'] = {
            'total_students':    total_students,
            'active_students':   active_students,
            'inactive_students': total_students - active_students,
            'total_tests':       total_tests,
            'total_assignments': total_assignments,
            'completed_subs':    completed_subs,
        }
        return Response(data)

    if request.method == 'PATCH':
        d = request.data
        for field in ('name', 'phone', 'address'):
            if field in d:
                setattr(center, field, d[field])
        if 'is_active' in d:
            center.is_active = d['is_active']
        if 'logo' in request.FILES:
            center.logo = request.FILES['logo']
        center.save()
        return Response(center_to_dict(center))

    if request.method == 'DELETE':
        center.delete()
        return Response({'deleted': True})


# ── Members ───────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def center_members_list(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    qs = center.memberships.filter(is_active=True).select_related('user')
    role_filter = request.query_params.get('role')
    if role_filter:
        qs = qs.filter(role=role_filter)

    return Response([user_to_dict(m.user, m.role) for m in qs])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def center_member_add(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    my_role = center_role(request.user, center)
    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    d = request.data
    target_role = d.get('role', 'student')

    if my_role == 'teacher' and target_role != 'student':
        return Response({'error': 'Teachers can only add students'}, status=403)
    if my_role == 'admin' and target_role not in ('teacher', 'student'):
        return Response({'error': 'Admins can add teachers and students only'}, status=403)

    email = d.get('email', '').strip()
    password = d.get('password', '').strip()
    if not email or not password:
        return Response({'error': 'Email and password required'}, status=400)

    with transaction.atomic():
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': email,
                'first_name': d.get('first_name', '').strip(),
                'last_name': d.get('last_name', '').strip(),
            }
        )
        if created:
            user.set_password(password)
            phone = d.get('phone', '').strip()
            if phone:
                user.phone = phone
            if 'avatar' in request.FILES:
                user.avatar = request.FILES['avatar']
            user.save()

        existing = CenterMembership.objects.filter(center=center, user=user).first()
        if existing:
            if existing.is_active:
                return Response({'error': 'User is already a member'}, status=400)
            existing.is_active = True
            existing.role = target_role
            existing.member_password = password
            existing.save()
            return Response(user_to_dict(user, target_role))

        CenterMembership.objects.create(
            center=center, user=user, role=target_role, member_password=password,
        )

    return Response(user_to_dict(user, target_role), status=201)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def center_member_remove(request, center_id, user_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
        m = CenterMembership.objects.get(center=center, user_id=user_id)
    except (LearningCenter.DoesNotExist, CenterMembership.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin'):
        return Response({'error': 'Forbidden'}, status=403)

    m.is_active = False
    m.save()
    return Response({'removed': True})


# ── Groups ────────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def center_groups(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    if request.method == 'GET':
        my_role = center_role(request.user, center)
        groups = center.groups.all()
        if my_role == 'teacher':
            groups = groups.filter(teacher=request.user)
        result = []
        for g in groups:
            students = [user_to_dict(gm.student) for gm in g.memberships.select_related('student').all()]
            result.append({
                'id': g.id,
                'name': g.name,
                'teacher': user_to_dict(g.teacher) if g.teacher else None,
                'student_count': g.student_count,
                'students': students,
            })
        return Response(result)

    name = request.data.get('name', '').strip()
    if not name:
        return Response({'error': 'Group name required'}, status=400)

    teacher_id = request.data.get('teacher_id')
    teacher = None
    if teacher_id:
        try:
            tm = CenterMembership.objects.get(center=center, user_id=teacher_id, role='teacher', is_active=True)
            teacher = tm.user
        except CenterMembership.DoesNotExist:
            return Response({'error': 'Teacher not found'}, status=400)

    group = Group.objects.create(center=center, name=name, teacher=teacher)
    return Response({'id': group.id, 'name': group.name, 'student_count': 0, 'students': []}, status=201)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def group_delete(request, center_id, group_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
        group = Group.objects.get(id=group_id, center=center)
    except (LearningCenter.DoesNotExist, Group.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin'):
        return Response({'error': 'Forbidden'}, status=403)

    group.delete()
    return Response({'deleted': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def group_add_student(request, center_id, group_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
        group = Group.objects.get(id=group_id, center=center)
    except (LearningCenter.DoesNotExist, Group.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    student_id = request.data.get('student_id')
    if not student_id:
        return Response({'error': 'student_id required'}, status=400)

    try:
        sm = CenterMembership.objects.get(center=center, user_id=student_id, role='student', is_active=True)
    except CenterMembership.DoesNotExist:
        return Response({'error': 'Student not in center'}, status=400)

    GroupMembership.objects.get_or_create(group=group, student=sm.user)
    return Response({'added': True})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def group_remove_student(request, center_id, group_id, student_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
        group = Group.objects.get(id=group_id, center=center)
    except (LearningCenter.DoesNotExist, Group.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    GroupMembership.objects.filter(group=group, student_id=student_id).delete()
    return Response({'removed': True})


# ── Assignments ───────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def center_assignments(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    my_role = center_role(request.user, center)
    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    if request.method == 'GET':
        qs = center.assignments.select_related('assigned_by', 'student', 'group')
        if my_role == 'teacher':
            qs = qs.filter(assigned_by=request.user)
        result = []
        for a in qs:
            subs = a.submissions.all()
            result.append({
                'id': a.id,
                'task_type': a.task_type,
                'task_title': a.task_title,
                'task_ref_id': a.task_ref_id,
                'description': a.description,
                'deadline': a.deadline.isoformat() if a.deadline else None,
                'is_overdue': a.is_overdue,
                'assigned_by': user_to_dict(a.assigned_by),
                'student': user_to_dict(a.student) if a.student else None,
                'group': {'id': a.group.id, 'name': a.group.name} if a.group else None,
                'completed_count': subs.filter(status='completed').count(),
                'pending_count': subs.filter(status='pending').count(),
                'total_submissions': subs.count(),
                'created_at': a.created_at.isoformat(),
            })
        return Response(result)

    # POST
    d = request.data
    task_type = d.get('task_type')
    task_title = d.get('task_title', '').strip()
    if not task_type or not task_title:
        return Response({'error': 'task_type and task_title required'}, status=400)

    student_id = d.get('student_id')
    group_id = d.get('group_id')
    if not student_id and not group_id:
        return Response({'error': 'student_id or group_id required'}, status=400)

    deadline_raw = parse_datetime(d['deadline']) if d.get('deadline') else None
    if deadline_raw and timezone.is_naive(deadline_raw):
        deadline = timezone.make_aware(deadline_raw)
    else:
        deadline = deadline_raw

    with transaction.atomic():
        assignment = Assignment(
            center=center,
            assigned_by=request.user,
            task_type=task_type,
            task_title=task_title,
            task_ref_id=d.get('task_ref_id'),
            task_ref_kind=d.get('task_ref_kind', '') or '',
            description=d.get('description', ''),
            deadline=deadline,
        )

        if student_id:
            try:
                sm = CenterMembership.objects.get(center=center, user_id=student_id, role='student', is_active=True)
                assignment.student = sm.user
                assignment.save()
                AssignmentSubmission.objects.create(assignment=assignment, student=sm.user)
                notify(sm.user, center, 'assignment',
                       f"New assignment: {task_title}",
                       f"From: {request.user.get_full_name() or request.user.email}",
                       assignment=assignment)
            except CenterMembership.DoesNotExist:
                return Response({'error': 'Student not in center'}, status=400)
        else:
            try:
                group = Group.objects.get(id=group_id, center=center)
                assignment.group = group
                assignment.save()
                for gm in group.memberships.select_related('student').all():
                    AssignmentSubmission.objects.create(assignment=assignment, student=gm.student)
                    notify(gm.student, center, 'assignment',
                           f"New assignment: {task_title}",
                           f"From: {request.user.get_full_name() or request.user.email}",
                           assignment=assignment)
            except Group.DoesNotExist:
                return Response({'error': 'Group not found'}, status=400)

    return Response({'id': assignment.id, 'task_title': assignment.task_title}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def assignment_detail(request, center_id, assignment_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
        assignment = Assignment.objects.get(id=assignment_id, center=center)
    except (LearningCenter.DoesNotExist, Assignment.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    subs = assignment.submissions.select_related('student').all()
    return Response({
        'id': assignment.id,
        'task_type': assignment.task_type,
        'task_title': assignment.task_title,
        'task_ref_id': assignment.task_ref_id,
        'description': assignment.description,
        'deadline': assignment.deadline.isoformat() if assignment.deadline else None,
        'is_overdue': assignment.is_overdue,
        'group': {'id': assignment.group.id, 'name': assignment.group.name} if assignment.group else None,
        'student': user_to_dict(assignment.student) if assignment.student else None,
        'submissions': [
            {
                'id': s.id,
                'student': user_to_dict(s.student),
                'status': s.status,
                'score': s.score,
                'score_detail': s.score_detail,
                'submitted_at': s.submitted_at.isoformat() if s.submitted_at else None,
            }
            for s in subs
        ],
    })


# ── Student views ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_assignments(request):
    """All assignments for logged-in student."""
    subs = AssignmentSubmission.objects.filter(
        student=request.user
    ).select_related('assignment', 'assignment__center', 'assignment__assigned_by').order_by('assignment__deadline')

    now = timezone.now()
    result = []
    for sub in subs:
        a = sub.assignment
        if a.deadline and now > a.deadline and sub.status == 'pending':
            sub.status = 'overdue'
            sub.save(update_fields=['status'])
        result.append({
            'submission_id': sub.id,
            'assignment_id': a.id,
            'task_type': a.task_type,
            'task_title': a.task_title,
            'task_ref_id': a.task_ref_id,
            'task_ref_kind': a.task_ref_kind,
            'description': a.description,
            'deadline': a.deadline.isoformat() if a.deadline else None,
            'status': sub.status,
            'score': sub.score,
            'score_detail': sub.score_detail,
            'assigned_by': user_to_dict(a.assigned_by),
            'center': {'id': a.center.id, 'name': a.center.name},
            'submitted_at': sub.submitted_at.isoformat() if sub.submitted_at else None,
        })
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_assignment(request, submission_id):
    try:
        sub = AssignmentSubmission.objects.get(id=submission_id, student=request.user)
    except AssignmentSubmission.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if sub.status == 'completed':
        return Response({'error': 'Already submitted'}, status=400)

    d = request.data
    sub.status = 'completed'
    sub.attempt_ref_id = d.get('attempt_ref_id')
    sub.score = d.get('score')
    sub.score_detail = d.get('score_detail', '')
    sub.result_data = d.get('result_data')
    sub.submitted_at = timezone.now()
    sub.save()

    a = sub.assignment
    notify(
        a.assigned_by, a.center, 'submission',
        f"{request.user.get_full_name() or request.user.email} completed: {a.task_title}",
        f"Score: {sub.score_detail or sub.score or 'N/A'}",
        assignment=a, submission=sub,
    )
    return Response({'submitted': True, 'status': sub.status})


def auto_complete_assignments(user, task_type, task_ref_id, *, score=None,
                              score_detail='', result_data=None, attempt_ref_id=None):
    """Automatically mark a student's matching assignment(s) complete.

    Called from the exam-submission flow (e.g. IELTS reading submit) when a
    student finishes the EXACT test they were assigned. Matches by
    (student, task_type, task_ref_id) on any still-open submission so the
    student never has to press a manual "Done" button.

    Returns the number of submissions completed.
    """
    if task_ref_id is None:
        return 0
    try:
        subs = AssignmentSubmission.objects.filter(
            student=user,
            assignment__task_type=task_type,
            assignment__task_ref_id=task_ref_id,
            status__in=['pending', 'overdue'],
        ).select_related('assignment', 'assignment__center', 'assignment__assigned_by')
    except Exception:
        return 0

    count = 0
    for sub in subs:
        sub.status = 'completed'
        sub.score = score
        sub.score_detail = score_detail or ''
        sub.result_data = result_data
        sub.attempt_ref_id = attempt_ref_id
        sub.submitted_at = timezone.now()
        sub.save()
        a = sub.assignment
        try:
            notify(
                a.assigned_by, a.center, 'submission',
                f"{user.get_full_name() or user.email} completed: {a.task_title}",
                f"Score: {score_detail or score or 'N/A'}",
                assignment=a, submission=sub,
            )
        except Exception:
            pass
        count += 1
    return count


# ── Notifications ─────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_notifications(request):
    notifs = CenterNotification.objects.filter(
        recipient=request.user
    ).select_related('center', 'assignment')[:50]
    return Response([
        {
            'id': n.id,
            'type': n.notification_type,
            'title': n.title,
            'message': n.message,
            'is_read': n.is_read,
            'center': {'id': n.center.id, 'name': n.center.name} if n.center else None,
            'assignment_id': n.assignment_id,
            'created_at': n.created_at.isoformat(),
        }
        for n in notifs
    ])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_notifications_read(request):
    CenterNotification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return Response({'ok': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def notification_count(request):
    count = CenterNotification.objects.filter(recipient=request.user, is_read=False).count()
    return Response({'unread': count})


# ── My Centers ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_centers(request):
    memberships = CenterMembership.objects.filter(user=request.user, is_active=True).select_related('center')
    return Response([
        {
            'id': m.center.id,
            'name': m.center.name,
            'logo': m.center.logo.url if m.center.logo else None,
            'role': m.role,
            'is_active': m.center.is_active,
        }
        for m in memberships
    ])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def center_dashboard(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    my_role = center_role(request.user, center)
    # Only management roles may enter the center portal. Students are blocked
    # (they use the /app student interface instead).
    if my_role not in ('director', 'admin', 'teacher') and not request.user.is_staff:
        return Response({'error': 'Forbidden'}, status=403)

    if my_role == 'teacher':
        # Teacher-specific dashboard data
        teacher_groups = center.groups.filter(teacher=request.user)
        student_ids = set(
            GroupMembership.objects.filter(group__in=teacher_groups).values_list('student_id', flat=True)
        )
        my_assignments = center.assignments.filter(assigned_by=request.user)
        my_subs = AssignmentSubmission.objects.filter(assignment__assigned_by=request.user, assignment__center=center)

        groups_data = []
        for g in teacher_groups:
            gsubs = AssignmentSubmission.objects.filter(assignment__group=g)
            groups_data.append({
                'id': g.id,
                'name': g.name,
                'student_count': g.student_count,
                'tasks': g.assignments.count(),
                'completed': gsubs.filter(status='completed').count(),
                'pending': gsubs.filter(status='pending').count(),
                'overdue': gsubs.filter(status='overdue').count(),
            })

        data = {
            'center': center_to_dict(center),
            'my_role': my_role,
            'stats': {
                'groups': teacher_groups.count(),
                'students': len(student_ids),
                'assignments': my_assignments.count(),
                'completed': my_subs.filter(status='completed').count(),
                'pending': my_subs.filter(status='pending').count(),
                'overdue': my_subs.filter(status='overdue').count(),
            },
            'teacher_groups': groups_data,
            'recent_submissions': [
                {
                    'student': f"{s.student.first_name} {s.student.last_name}".strip() or s.student.email,
                    'task_title': s.assignment.task_title,
                    'task_type': s.assignment.task_type,
                    'status': s.status,
                    'score': s.score,
                }
                for s in my_subs.select_related('student', 'assignment')
                              .filter(status='completed').order_by('-submitted_at')[:5]
            ],
        }
        return Response(data)

    data = {
        'center': center_to_dict(center),
        'my_role': my_role,
        'stats': {
            'students': center.student_count,
            'teachers': center.teacher_count,
            'admins': center.admin_count,
            'groups': center.groups.count(),
            'assignments': center.assignments.count(),
            'completed': AssignmentSubmission.objects.filter(
                assignment__center=center, status='completed'
            ).count(),
        }
    }

    if my_role in ('director', 'admin') or request.user.is_staff:
        recent = center.assignments.order_by('-created_at')[:5]
        data['recent_assignments'] = [
            {
                'id': a.id,
                'task_title': a.task_title,
                'task_type': a.task_type,
                'deadline': a.deadline.isoformat() if a.deadline else None,
                'total': a.submissions.count(),
                'completed': a.submissions.filter(status='completed').count(),
            }
            for a in recent
        ]

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def available_tasks(request, center_id):
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin', 'teacher'):
        return Response({'error': 'Forbidden'}, status=403)

    task_type = request.query_params.get('type', 'sat_mock')
    results = []

    try:
        if task_type in ('sat_mock', 'sat_english', 'sat_math'):
            from tests_app.models import Test
            tests = Test.objects.filter(is_active=True).order_by('-year', '-month')
            results = [{'id': t.id, 'title': f"SAT {t.year}/{t.month} — Form {t.form}"} for t in tests]

        elif task_type == 'ielts_reading':
            # Full mock reading tests (IELTSTest with passages attached)
            from ielts.models import IELTSTest, ReadingPassage
            mock_tests = IELTSTest.objects.filter(
                is_active=True, passages__isnull=False
            ).distinct().order_by('title')
            results = [{'id': t.id, 'title': t.title, 'kind': 'mock'} for t in mock_tests]
            # Standalone practice passages
            standalones = ReadingPassage.objects.filter(is_standalone=True).order_by('passage_number')
            results += [{'id': p.id, 'title': p.title, 'kind': 'passage'} for p in standalones]

        elif task_type == 'ielts_listening':
            from ielts.models import IELTSTest, ListeningSection
            mock_tests = IELTSTest.objects.filter(
                is_active=True, listening_sections__isnull=False
            ).distinct().order_by('title')
            results = [{'id': t.id, 'title': t.title, 'kind': 'mock'} for t in mock_tests]
            standalones = ListeningSection.objects.filter(is_standalone=True).order_by('section_number')
            results += [{'id': s.id, 'title': s.title, 'kind': 'section'} for s in standalones]

        elif task_type == 'cefr_test':
            from cefr.models import CEFRTest
            results = [{'id': t.id, 'title': t.title} for t in CEFRTest.objects.filter(is_active=True).order_by('title')]

        elif task_type == 'cefr_reading':
            from cefr.models import CEFRReadingPassage
            results = [{'id': p.id, 'title': p.title} for p in CEFRReadingPassage.objects.all().order_by('title')]

        elif task_type == 'cefr_listening':
            from cefr.models import CEFRListeningSection
            results = [{'id': s.id, 'title': s.title} for s in CEFRListeningSection.objects.all().order_by('title')]

    except Exception as e:
        results = []

    return Response(results)


# ── Analytics: Teacher & Group detail (director/admin only) ───────────────────

def _submission_stats(qs):
    """Aggregate completion stats for a submissions queryset."""
    total = qs.count()
    completed = qs.filter(status='completed').count()
    overdue = qs.filter(status='overdue').count()
    pending = qs.filter(status='pending').count()
    scored = [s.score for s in qs if s.score is not None]
    avg_score = round(sum(scored) / len(scored), 1) if scored else None
    completion_rate = round(completed / total * 100) if total else 0
    return {
        'total': total,
        'completed': completed,
        'overdue': overdue,
        'pending': pending,
        'avg_score': avg_score,
        'completion_rate': completion_rate,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def teacher_detail(request, center_id, teacher_id):
    """Full analytics for one teacher. Director / admin / staff only."""
    try:
        center = LearningCenter.objects.get(id=center_id)
    except LearningCenter.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin'):
        return Response({'error': 'Forbidden'}, status=403)

    try:
        tm = CenterMembership.objects.select_related('user').get(
            center=center, user_id=teacher_id, role='teacher', is_active=True
        )
    except CenterMembership.DoesNotExist:
        return Response({'error': 'Teacher not found'}, status=404)

    teacher = tm.user
    groups = center.groups.filter(teacher=teacher)

    # Distinct students across all this teacher's groups
    student_ids = set(
        GroupMembership.objects.filter(group__in=groups).values_list('student_id', flat=True)
    )

    groups_data = [
        {'id': g.id, 'name': g.name, 'student_count': g.student_count}
        for g in groups
    ]

    assignments = center.assignments.filter(assigned_by=teacher)
    submissions = AssignmentSubmission.objects.filter(assignment__assigned_by=teacher, assignment__center=center)

    return Response({
        'teacher': user_to_dict(teacher, 'teacher'),
        'stats': {
            'groups': groups.count(),
            'students': len(student_ids),
            'assignments': assignments.count(),
            **_submission_stats(submissions),
        },
        'groups': groups_data,
        'recent_assignments': [
            {
                'id': a.id,
                'task_title': a.task_title,
                'task_type': a.task_type,
                'deadline': a.deadline.isoformat() if a.deadline else None,
                'target': (a.group.name if a.group else (
                    f"{a.student.first_name} {a.student.last_name}".strip() or a.student.email
                ) if a.student else '-'),
                'total': a.submissions.count(),
                'completed': a.submissions.filter(status='completed').count(),
            }
            for a in assignments.select_related('group', 'student').order_by('-created_at')[:10]
        ],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def group_detail(request, center_id, group_id):
    """Full analytics for one group. Director/admin: any group. Teacher: own groups only."""
    try:
        center = LearningCenter.objects.get(id=center_id)
        group = Group.objects.select_related('teacher').get(id=group_id, center=center)
    except (LearningCenter.DoesNotExist, Group.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    my_role = center_role(request.user, center)
    if my_role == 'teacher':
        # Teachers can only see groups they are assigned to teach
        if group.teacher != request.user:
            return Response({'error': 'Forbidden'}, status=403)
    elif not has_role(request.user, center, 'director', 'admin'):
        return Response({'error': 'Forbidden'}, status=403)

    members = group.memberships.select_related('student').all()
    students = [gm.student for gm in members]

    # Group-level assignments (assigned to this group)
    group_assignments = group.assignments.all()

    # Per-student stats: count BOTH group assignments AND individual assignments to them
    students_data = []
    for s in students:
        subs = AssignmentSubmission.objects.filter(
            Q(assignment__group=group) | Q(assignment__student=s, assignment__center=center),
            student=s
        )
        stats = _submission_stats(subs)
        students_data.append({
            **user_to_dict(s, 'student'),
            'completed': stats['completed'],
            'pending': stats['pending'],
            'overdue': stats['overdue'],
            'total_tasks': stats['total'],
            'avg_score': stats['avg_score'],
            'completion_rate': stats['completion_rate'],
        })

    # Overall group stats: group assignments + individual assignments to any group member
    student_ids = [s.id for s in students]
    all_subs = AssignmentSubmission.objects.filter(
        Q(assignment__group=group) |
        Q(assignment__student_id__in=student_ids, assignment__center=center)
    )

    # Assignments to show: group assignments + individual assignments to group members
    all_assignments = center.assignments.filter(
        Q(group=group) | Q(student_id__in=student_ids)
    ).distinct().order_by('-created_at')

    # Build a mapping: student_id → list of submission dicts for student modal
    all_subs_detail = AssignmentSubmission.objects.filter(
        Q(assignment__group=group) |
        Q(assignment__student_id__in=student_ids, assignment__center=center)
    ).select_related('assignment', 'student').order_by('assignment__deadline')

    # student_id → [submission detail]
    student_sub_map = {}
    for sub in all_subs_detail:
        uid = sub.student_id
        if uid not in student_sub_map:
            student_sub_map[uid] = []
        student_sub_map[uid].append({
            'submission_id': sub.id,
            'assignment_id': sub.assignment_id,
            'task_title':    sub.assignment.task_title,
            'task_type':     sub.assignment.task_type,
            'deadline':      sub.assignment.deadline.isoformat() if sub.assignment.deadline else None,
            'is_overdue':    sub.assignment.is_overdue,
            'status':        sub.status,
            'score':         sub.score,
            'score_detail':  sub.score_detail,
            'submitted_at':  sub.submitted_at.isoformat() if sub.submitted_at else None,
        })

    # Enrich students_data with their submission list
    for sd in students_data:
        sd['submissions'] = student_sub_map.get(sd['id'], [])

    # Per-assignment submission details (who completed, when)
    assignments_list = []
    for a in all_assignments[:20]:
        subs = a.submissions.select_related('student').all()
        assignments_list.append({
            'id': a.id,
            'task_title': a.task_title,
            'task_type': a.task_type,
            'description': a.description,
            'deadline': a.deadline.isoformat() if a.deadline else None,
            'is_overdue': a.is_overdue,
            'total': subs.count(),
            'completed': subs.filter(status='completed').count(),
            'pending': subs.filter(status='pending').count(),
            'overdue_count': subs.filter(status='overdue').count(),
            'submissions': [
                {
                    'student_id': s.student_id,
                    'student_name': s.student.get_full_name() or s.student.email,
                    'status': s.status,
                    'score': s.score,
                    'score_detail': s.score_detail,
                    'submitted_at': s.submitted_at.isoformat() if s.submitted_at else None,
                }
                for s in subs
            ],
        })

    return Response({
        'group': {
            'id': group.id,
            'name': group.name,
            'teacher': user_to_dict(group.teacher, 'teacher') if group.teacher else None,
            'student_count': group.student_count,
        },
        'stats': {
            'students': len(students),
            'assignments': all_assignments.count(),
            **_submission_stats(all_subs),
        },
        'students': students_data,
        'assignments': assignments_list,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def member_profile(request, center_id, user_id):
    """Member profile with password — director/admin/staff only."""
    try:
        center = LearningCenter.objects.get(id=center_id)
        membership = CenterMembership.objects.select_related('user').get(
            center=center, user_id=user_id, is_active=True
        )
    except (LearningCenter.DoesNotExist, CenterMembership.DoesNotExist):
        return Response({'error': 'Not found'}, status=404)

    if not has_role(request.user, center, 'director', 'admin'):
        return Response({'error': 'Forbidden'}, status=403)

    user = membership.user
    return Response({
        **user_to_dict(user, membership.role),
        'password': membership.member_password,
        'joined_at': membership.joined_at.isoformat(),
    })
