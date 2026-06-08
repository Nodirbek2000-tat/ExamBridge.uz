from django.contrib import admin
from .models import (
    LearningCenter, CenterMembership, Group, GroupMembership,
    Assignment, AssignmentSubmission, CenterNotification
)


@admin.register(LearningCenter)
class LearningCenterAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active', 'student_count', 'teacher_count', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'phone']


@admin.register(CenterMembership)
class CenterMembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'center', 'role', 'is_active', 'joined_at']
    list_filter = ['role', 'is_active']
    search_fields = ['user__email', 'center__name']


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'center', 'teacher', 'student_count', 'created_at']
    search_fields = ['name', 'center__name']


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ['task_title', 'task_type', 'center', 'assigned_by', 'deadline', 'created_at']
    list_filter = ['task_type']
    search_fields = ['task_title', 'center__name']


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = ['student', 'assignment', 'status', 'score', 'submitted_at']
    list_filter = ['status']


@admin.register(CenterNotification)
class CenterNotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'notification_type', 'title', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read']
