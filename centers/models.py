"""
Learning Center (O'quv Markaz) models.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


class LearningCenter(models.Model):
    """O'quv markaz."""
    name = models.CharField(max_length=200)
    logo = models.ImageField(upload_to='centers/logos/', null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Learning Center"
        verbose_name_plural = "Learning Centers"

    def __str__(self):
        return self.name

    @property
    def student_count(self):
        return self.memberships.filter(role=CenterMembership.STUDENT, is_active=True).count()

    @property
    def teacher_count(self):
        return self.memberships.filter(role=CenterMembership.TEACHER, is_active=True).count()

    @property
    def admin_count(self):
        return self.memberships.filter(role=CenterMembership.ADMIN, is_active=True).count()

    @property
    def director(self):
        m = self.memberships.filter(role=CenterMembership.DIRECTOR, is_active=True).first()
        return m.user if m else None


class CenterMembership(models.Model):
    """User role in a learning center."""
    DIRECTOR = 'director'
    ADMIN = 'admin'
    TEACHER = 'teacher'
    STUDENT = 'student'

    ROLE_CHOICES = [
        (DIRECTOR, 'Director'),
        (ADMIN, 'Admin'),
        (TEACHER, 'Teacher'),
        (STUDENT, 'Student'),
    ]

    center = models.ForeignKey(LearningCenter, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='center_memberships'
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    # Plaintext password set by the admin who created this account, so the
    # super-admin can retrieve/hand it over later. Admin-only visibility.
    member_password = models.CharField(max_length=128, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('center', 'user')
        verbose_name = "Center Membership"
        verbose_name_plural = "Center Memberships"

    def __str__(self):
        return f"{self.user.email} – {self.role} @ {self.center.name}"


class Group(models.Model):
    """Student groups inside a center."""
    center = models.ForeignKey(LearningCenter, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='teaching_groups'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Group"
        verbose_name_plural = "Groups"

    def __str__(self):
        return f"{self.name} ({self.center.name})"

    @property
    def student_count(self):
        return self.memberships.count()


class GroupMembership(models.Model):
    """Students in a group."""
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_memberships'
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('group', 'student')

    def __str__(self):
        return f"{self.student.email} in {self.group.name}"


class Assignment(models.Model):
    """Task assigned by teacher/director to student or group."""
    SAT_MOCK = 'sat_mock'
    SAT_ENGLISH = 'sat_english'
    SAT_MATH = 'sat_math'
    IELTS_READING = 'ielts_reading'
    IELTS_LISTENING = 'ielts_listening'
    IELTS_WRITING = 'ielts_writing'
    IELTS_SPEAKING = 'ielts_speaking'
    CEFR_TEST = 'cefr_test'
    CEFR_READING = 'cefr_reading'
    CEFR_LISTENING = 'cefr_listening'

    TASK_TYPE_CHOICES = [
        (SAT_MOCK, 'SAT Full Mock'),
        (SAT_ENGLISH, 'SAT English Module'),
        (SAT_MATH, 'SAT Math Module'),
        (IELTS_READING, 'IELTS Reading'),
        (IELTS_LISTENING, 'IELTS Listening'),
        (IELTS_WRITING, 'IELTS Writing'),
        (IELTS_SPEAKING, 'IELTS Speaking'),
        (CEFR_TEST, 'CEFR Full Test'),
        (CEFR_READING, 'CEFR Reading'),
        (CEFR_LISTENING, 'CEFR Listening'),
    ]

    center = models.ForeignKey(LearningCenter, on_delete=models.CASCADE, related_name='assignments')
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='given_assignments'
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='received_assignments'
    )
    group = models.ForeignKey(
        'Group',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='assignments'
    )
    task_type = models.CharField(max_length=30, choices=TASK_TYPE_CHOICES)
    task_title = models.CharField(max_length=200)
    task_ref_id = models.IntegerField(null=True, blank=True)
    # Distinguishes WHAT task_ref_id points to: 'mock' (full mock test),
    # 'passage'/'section' (standalone practice), or '' (legacy / no ref).
    # Needed so the student "Start" button can deep-link and start the exact
    # assigned test, and so completion can be detected automatically.
    task_ref_kind = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Assignment"
        verbose_name_plural = "Assignments"

    def __str__(self):
        target = self.student.email if self.student else f"Group: {self.group.name if self.group else '-'}"
        return f"{self.task_title} → {target}"

    @property
    def is_overdue(self):
        return bool(self.deadline and timezone.now() > self.deadline)


class AssignmentSubmission(models.Model):
    """Student completion record for an assignment."""
    PENDING = 'pending'
    COMPLETED = 'completed'
    OVERDUE = 'overdue'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (COMPLETED, 'Completed'),
        (OVERDUE, 'Overdue'),
    ]

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assignment_submissions'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempt_ref_id = models.IntegerField(null=True, blank=True)
    result_data = models.JSONField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    score_detail = models.CharField(max_length=200, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('assignment', 'student')
        ordering = ['-created_at']
        verbose_name = "Assignment Submission"
        verbose_name_plural = "Assignment Submissions"

    def __str__(self):
        return f"{self.student.email} – {self.assignment.task_title} ({self.status})"


class CenterNotification(models.Model):
    """Notifications for center members."""
    ASSIGNMENT = 'assignment'
    SUBMISSION = 'submission'
    INVITE = 'invite'
    GENERAL = 'general'

    TYPE_CHOICES = [
        (ASSIGNMENT, 'New Assignment'),
        (SUBMISSION, 'Task Submitted'),
        (INVITE, 'Center Invite'),
        (GENERAL, 'General'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='center_notifications'
    )
    center = models.ForeignKey(LearningCenter, on_delete=models.CASCADE, null=True, blank=True)
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=GENERAL)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.SET_NULL, null=True, blank=True)
    submission = models.ForeignKey(
        AssignmentSubmission, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Center Notification"
        verbose_name_plural = "Center Notifications"

    def __str__(self):
        return f"[{self.notification_type}] {self.title} → {self.recipient.email}"
