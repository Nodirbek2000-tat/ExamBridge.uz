from django.urls import path
from . import content_views, insights, lessons, olympiads, quizzes, views

urlpatterns = [
    # ── Super Admin ──────────────────────────────────────────────────────────
    path('admin/', views.admin_centers_list),
    path('admin/create/', views.admin_center_create),
    path('admin/<int:center_id>/', views.admin_center_detail),

    # ── My centers (any user) ─────────────────────────────────────────────────
    path('mine/', views.my_centers),

    # ── Center dashboard ──────────────────────────────────────────────────────
    path('<int:center_id>/dashboard/', views.center_dashboard),
    path('<int:center_id>/monitoring/', insights.center_monitoring),

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
    path('<int:center_id>/groups/<int:group_id>/journal/', insights.group_journal),
    path('<int:center_id>/groups/<int:group_id>/students/', views.group_add_student),
    path('<int:center_id>/groups/<int:group_id>/students/<int:student_id>/remove/', views.group_remove_student),

    # ── Assignments ───────────────────────────────────────────────────────────
    path('<int:center_id>/assignments/', views.center_assignments),
    path('<int:center_id>/assignments/<int:assignment_id>/', views.assignment_detail),

    # ── Available tasks (for assignment creation) ─────────────────────────────
    path('<int:center_id>/available-tasks/', views.available_tasks),

    # ── Lessons (a topic + several tasks) ─────────────────────────────────────
    path('<int:center_id>/lessons/', lessons.lessons),
    path('<int:center_id>/lessons/<int:lesson_id>/', lessons.lesson_detail),

    # ── The center's own tests (IELTS / CEFR) ────────────────────────────────
    path('<int:center_id>/content/', content_views.content_list),
    path('<int:center_id>/content/import/<str:kind>/', content_views.content_import),
    path('<int:center_id>/content/<str:type_key>/<int:pk>/', content_views.content_delete),
    path('<int:center_id>/content/<str:type_key>/<int:pk>/media/', content_views.content_media),
    path('<int:center_id>/settings/', content_views.content_settings),

    # ── Classes and olympiads ────────────────────────────────────────────────
    path('<int:center_id>/classes/', views.center_classes),
    path('<int:center_id>/members/<int:user_id>/class/', views.member_class),
    path('<int:center_id>/olympiads/', olympiads.olympiads),
    path('<int:center_id>/olympiads/<int:olympiad_id>/', olympiads.olympiad_detail),
    path('my-olympiads/', olympiads.my_olympiads),
    path('olympiads/<int:olympiad_id>/', olympiads.my_olympiad),
    path('olympiads/<int:olympiad_id>/enter/', olympiads.enter_olympiad),
    path('olympiads/<int:olympiad_id>/begin/', olympiads.begin_olympiad),

    # ── The center's own A/B/C/D tests ───────────────────────────────────────
    path('<int:center_id>/quizzes/', quizzes.quizzes),
    path('<int:center_id>/quizzes/<int:quiz_id>/', quizzes.quiz_detail),
    path('<int:center_id>/quizzes/<int:quiz_id>/import/', quizzes.quiz_import),
    path('<int:center_id>/quizzes/<int:quiz_id>/questions/', quizzes.quiz_add_question),
    path('<int:center_id>/quizzes/<int:quiz_id>/questions/<int:question_id>/', quizzes.quiz_question),
    path('<int:center_id>/quizzes/<int:quiz_id>/results/', quizzes.quiz_results),
    path('my-quizzes/', quizzes.my_quizzes),
    path('quizzes/<int:quiz_id>/start/', quizzes.quiz_start),
    path('quiz-attempts/<int:attempt_id>/', quizzes.quiz_attempt),
    path('quiz-attempts/<int:attempt_id>/answer/', quizzes.quiz_answer),
    path('quiz-attempts/<int:attempt_id>/submit/', quizzes.quiz_submit),

    # ── Student: my assignments ───────────────────────────────────────────────
    path('my-assignments/', views.my_assignments),
    path('my-progress/', insights.my_progress),
    path('submissions/<int:submission_id>/submit/', views.submit_assignment),

    # ── Notifications ─────────────────────────────────────────────────────────
    path('notifications/', views.my_notifications),
    path('notifications/read/', views.mark_notifications_read),
    path('notifications/count/', views.notification_count),
]
