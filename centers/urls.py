from django.urls import path
from . import views

urlpatterns = [
    # ── Super Admin ──────────────────────────────────────────────────────────
    path('admin/', views.admin_centers_list),
    path('admin/create/', views.admin_center_create),
    path('admin/<int:center_id>/', views.admin_center_detail),

    # ── My centers (any user) ─────────────────────────────────────────────────
    path('mine/', views.my_centers),

    # ── Center dashboard ──────────────────────────────────────────────────────
    path('<int:center_id>/dashboard/', views.center_dashboard),

    # ── Members ───────────────────────────────────────────────────────────────
    path('<int:center_id>/members/', views.center_members_list),
    path('<int:center_id>/members/add/', views.center_member_add),
    path('<int:center_id>/members/<int:user_id>/remove/', views.center_member_remove),

    # ── Analytics & Profiles (director/admin only) ────────────────────────────
    path('<int:center_id>/teachers/<int:teacher_id>/', views.teacher_detail),
    path('<int:center_id>/groups/<int:group_id>/detail/', views.group_detail),
    path('<int:center_id>/members/<int:user_id>/profile/', views.member_profile),

    # ── Groups ────────────────────────────────────────────────────────────────
    path('<int:center_id>/groups/', views.center_groups),
    path('<int:center_id>/groups/<int:group_id>/delete/', views.group_delete),
    path('<int:center_id>/groups/<int:group_id>/students/', views.group_add_student),
    path('<int:center_id>/groups/<int:group_id>/students/<int:student_id>/remove/', views.group_remove_student),

    # ── Assignments ───────────────────────────────────────────────────────────
    path('<int:center_id>/assignments/', views.center_assignments),
    path('<int:center_id>/assignments/<int:assignment_id>/', views.assignment_detail),

    # ── Available tasks (for assignment creation) ─────────────────────────────
    path('<int:center_id>/available-tasks/', views.available_tasks),

    # ── Student: my assignments ───────────────────────────────────────────────
    path('my-assignments/', views.my_assignments),
    path('submissions/<int:submission_id>/submit/', views.submit_assignment),

    # ── Notifications ─────────────────────────────────────────────────────────
    path('notifications/', views.my_notifications),
    path('notifications/read/', views.mark_notifications_read),
    path('notifications/count/', views.notification_count),
]
