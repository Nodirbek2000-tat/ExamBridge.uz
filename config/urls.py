from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# ── API imports ──────────────────────────────────────────────────────────────
from api.auth_views import csrf_token, login_view, register_view, logout_view, me_view, google_login_view, token_refresh_view
from api.sat_views import (
    sat_test_list, sat_start_test, sat_attempt_detail,
    sat_submit_answer, sat_submit_module, sat_security_event, sat_stats, sat_result_detail, sat_result_delete,
    sat_practice_list, sat_practice_bank_overview, sat_practice_save, sat_practice_detail, sat_saved_questions,
    sat_test_modules, sat_start_individual_module, sat_individual_result, sat_individual_attempt_delete,
    sat_test_results_history, sat_individual_stats, sat_test_bookmarks, sat_force_finish,
    submit_question_report, admin_reports_list, admin_report_update,
    sat_exam_date, sat_ranking,
)
from api.ielts_views import (
    ielts_test_list, reading_passages, reading_passage_detail,
    reading_start, reading_submit, reading_mock_start,
    listening_sections, listening_section_detail,
    listening_start, listening_submit, listening_mock_start,
    speaking_tasks, speaking_submit, speaking_ai_analyze, speaking_history,
    speaking_tts, speaking_review,
    writing_tasks, writing_start, writing_submit, writing_result, writing_history,
    ielts_start_attempt, ielts_security_event, ielts_stats,
    ielts_history, reading_attempt_review, listening_attempt_review,
    bookmark_list, bookmark_toggle, bookmark_delete,
    ielts_analysis, writing_ai_analyze,
)
from api.cefr_views import (
    cefr_test_list, cefr_test_detail, cefr_start_attempt,
    cefr_submit_attempt, cefr_security_event, cefr_attempt_review,
    cefr_reading_list, cefr_reading_detail, cefr_reading_start, cefr_reading_submit,
    cefr_listening_list, cefr_listening_detail, cefr_listening_start, cefr_listening_submit,
    cefr_history, cefr_analysis,
)
from api.system_views import system_health, celery_tasks, platform_stats, admin_leaderboard
from api.ai_views import (
    ai_chat, ai_conversations, ai_conversation_detail,
    admin_ai_structures, admin_ai_structure_detail,
)
from api.import_views import (
    import_sat_questions, import_sat_test, import_sat_practice, import_sat_mock,
    admin_sat_question_detail,
    import_ielts_reading, import_ielts_listening, import_ielts_speaking, import_ielts_writing,
    import_cefr_test,
    admin_user_list, admin_toggle_premium, admin_toggle_staff, admin_user_detail, admin_set_user_exam_date,
    admin_sat_tests, admin_sat_test_update, admin_sat_questions,
    admin_ielts_content,
    admin_ielts_reading_list, admin_ielts_reading_delete, admin_ielts_reading_update,
    admin_ielts_reading_delete_all,
    admin_ielts_listening_list, admin_ielts_listening_delete, admin_ielts_listening_delete_all,
    admin_ielts_listening_update, admin_ielts_listening_upload_audio,
    admin_ielts_speaking_list, admin_ielts_speaking_delete,
    admin_ielts_writing_list, admin_ielts_writing_delete, admin_ielts_writing_upload_image,
    admin_cefr_tests, admin_cefr_test_delete,
    admin_cefr_reading_delete, admin_cefr_reading_delete_all,
    admin_cefr_listening_delete, admin_cefr_listening_delete_all,
    admin_ielts_reading_detail, admin_ielts_listening_detail,
    admin_ielts_tests_list, admin_ielts_test_detail, admin_ielts_test_audio, admin_ielts_test_premium,
    admin_cefr_reading_list, admin_cefr_reading_detail,
    admin_cefr_listening_list, admin_cefr_listening_detail, admin_cefr_listening_audio,
    admin_bank_questions, admin_bank_question_create, admin_bank_question_detail,
    admin_bank_question_choice_image,
    admin_sat_question_choice_image,
)

# ── URL patterns ──────────────────────────────────────────────────────────────
urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),

    # ── AUTH API ──────────────────────────────────────────────────────────────
    path('api/auth/csrf/', csrf_token),
    path('api/auth/login/', login_view),
    path('api/auth/register/', register_view),
    path('api/auth/logout/', logout_view),
    path('api/auth/me/', me_view),
    path('api/auth/google/', google_login_view),
    path('api/auth/refresh/', token_refresh_view),

    # ── SAT API ───────────────────────────────────────────────────────────────
    path('api/sat/tests/', sat_test_list),
    path('api/sat/tests/<int:test_id>/start/', sat_start_test),
    path('api/sat/attempt/<int:attempt_id>/', sat_attempt_detail),
    path('api/sat/attempt/<int:attempt_id>/answer/', sat_submit_answer),
    path('api/sat/attempt/<int:attempt_id>/submit-module/', sat_submit_module),
    path('api/sat/attempt/<int:attempt_id>/security/', sat_security_event),
    path('api/sat/attempt/<int:attempt_id>/force-finish/', sat_force_finish),
    path('api/sat/stats/', sat_stats),
    path('api/sat/exam-date/', sat_exam_date),
    path('api/sat/ranking/', sat_ranking),
    path('api/sat/result/<int:result_id>/', sat_result_detail),
    path('api/sat/result/<int:result_id>/delete/', sat_result_delete),
    path('api/sat/practice/', sat_practice_list),
    path('api/sat/practice/bank-overview/', sat_practice_bank_overview),
    path('api/sat/practice/<int:question_id>/', sat_practice_detail),
    path('api/sat/practice/<int:question_id>/save/', sat_practice_save),
    path('api/sat/saved/', sat_saved_questions),
    path('api/sat/saved/test-bookmarks/', sat_test_bookmarks),
    path('api/sat/tests/<int:test_id>/results/', sat_test_results_history),
    path('api/sat/tests/<int:test_id>/modules/', sat_test_modules),
    path('api/sat/module/start/', sat_start_individual_module),
    path('api/sat/module-result/<int:attempt_id>/', sat_individual_result),
    path('api/sat/module-attempt/<int:attempt_id>/delete/', sat_individual_attempt_delete),
    path('api/sat/tests/<int:test_id>/individual-stats/', sat_individual_stats),
    path('api/sat/practice/<int:question_id>/report/', submit_question_report),
    path('api/admin/reports/', admin_reports_list),
    path('api/admin/reports/<int:report_id>/status/', admin_report_update),

    # ── IELTS API ─────────────────────────────────────────────────────────────
    path('api/ielts/tests/', ielts_test_list),
    path('api/ielts/attempt/start/', ielts_start_attempt),
    path('api/ielts/attempt/<int:attempt_id>/security/', ielts_security_event),
    path('api/ielts/attempt/<int:attempt_id>/reading-review/', reading_attempt_review),
    path('api/ielts/attempt/<int:attempt_id>/listening-review/', listening_attempt_review),
    path('api/ielts/stats/', ielts_stats),
    path('api/ielts/history/', ielts_history),
    path('api/ielts/analysis/', ielts_analysis),
    # Reading
    path('api/ielts/reading/', reading_passages),
    path('api/ielts/reading/<int:passage_id>/', reading_passage_detail),
    path('api/ielts/reading/<int:passage_id>/start/', reading_start),
    path('api/ielts/reading/<int:passage_id>/submit/', reading_submit),
    path('api/ielts/reading/mock/<int:test_id>/start/', reading_mock_start),
    # Listening
    path('api/ielts/listening/', listening_sections),
    path('api/ielts/listening/<int:section_id>/', listening_section_detail),
    path('api/ielts/listening/<int:section_id>/start/', listening_start),
    path('api/ielts/listening/<int:section_id>/submit/', listening_submit),
    path('api/ielts/listening/mock/<int:test_id>/start/', listening_mock_start),
    # Speaking
    path('api/ielts/speaking/', speaking_tasks),
    path('api/ielts/speaking/tts/', speaking_tts),
    path('api/ielts/speaking/analyze/', speaking_ai_analyze),
    path('api/ielts/speaking/history/', speaking_history),
    path('api/ielts/speaking/review/<int:response_id>/', speaking_review),
    path('api/ielts/speaking/<int:attempt_id>/submit/', speaking_submit),
    # Writing
    path('api/ielts/writing/', writing_tasks),
    path('api/ielts/writing/history/', writing_history),
    path('api/ielts/writing/<int:task_id>/start/', writing_start),
    path('api/ielts/writing/<int:attempt_id>/submit/', writing_submit),
    path('api/ielts/writing/result/<int:response_id>/', writing_result),
    path('api/ielts/writing/analyze/', writing_ai_analyze),
    # Bookmarks
    path('api/ielts/bookmarks/', bookmark_list),
    path('api/ielts/bookmarks/toggle/', bookmark_toggle),
    path('api/ielts/bookmarks/<int:bookmark_id>/', bookmark_delete),

    # ── CEFR API ──────────────────────────────────────────────────────────────
    path('api/cefr/tests/', cefr_test_list),
    path('api/cefr/tests/<int:test_id>/', cefr_test_detail),
    path('api/cefr/tests/<int:test_id>/start/', cefr_start_attempt),
    path('api/cefr/attempt/<int:attempt_id>/submit/', cefr_submit_attempt),
    path('api/cefr/attempt/<int:attempt_id>/security/', cefr_security_event),
    path('api/cefr/attempt/<int:attempt_id>/review/', cefr_attempt_review),
    path('api/cefr/history/', cefr_history),
    path('api/cefr/analysis/', cefr_analysis),
    # CEFR Reading
    path('api/cefr/reading/', cefr_reading_list),
    path('api/cefr/reading/<int:passage_id>/', cefr_reading_detail),
    path('api/cefr/reading/<int:passage_id>/start/', cefr_reading_start),
    path('api/cefr/reading/<int:passage_id>/submit/', cefr_reading_submit),
    # CEFR Listening
    path('api/cefr/listening/', cefr_listening_list),
    path('api/cefr/listening/<int:section_id>/', cefr_listening_detail),
    path('api/cefr/listening/<int:section_id>/start/', cefr_listening_start),
    path('api/cefr/listening/<int:section_id>/submit/', cefr_listening_submit),

    # ── AI TUTOR API ──────────────────────────────────────────────────────────
    path('api/ai/chat/', ai_chat),
    path('api/ai/conversations/', ai_conversations),
    path('api/ai/conversations/<int:conv_id>/', ai_conversation_detail),
    path('api/admin/ai/structures/', admin_ai_structures),
    path('api/admin/ai/structures/<int:pk>/', admin_ai_structure_detail),

    # ── SYSTEM / ADMIN API ────────────────────────────────────────────────────
    path('api/system/health/', system_health),
    path('api/system/tasks/', celery_tasks),
    path('api/system/stats/', platform_stats),

    # ── ADMIN MANAGEMENT ──────────────────────────────────────────────────────
    path('api/admin/users/', admin_user_list),
    path('api/admin/users/<int:user_id>/toggle-premium/', admin_toggle_premium),
    path('api/admin/users/<int:user_id>/toggle-staff/', admin_toggle_staff),
    path('api/admin/leaderboard/', admin_leaderboard),
    path('api/admin/users/<int:user_id>/exam-date/', admin_set_user_exam_date),
    path('api/admin/users/<int:user_id>/', admin_user_detail),
    path('api/admin/sat/tests/', admin_sat_tests),
    path('api/admin/sat/tests/<int:test_id>/', admin_sat_test_update),
    path('api/admin/sat/questions/', admin_sat_questions),
    path('api/admin/ielts/content/', admin_ielts_content),
    path('api/admin/ielts/reading/', admin_ielts_reading_list),
    path('api/admin/ielts/reading/all/', admin_ielts_reading_delete_all),
    path('api/admin/ielts/reading/<int:pk>/', admin_ielts_reading_delete),
    path('api/admin/ielts/reading/<int:pk>/update/', admin_ielts_reading_update),
    path('api/admin/ielts/listening/', admin_ielts_listening_list),
    path('api/admin/ielts/listening/all/', admin_ielts_listening_delete_all),
    path('api/admin/ielts/listening/<int:pk>/', admin_ielts_listening_delete),
    path('api/admin/ielts/listening/<int:pk>/update/', admin_ielts_listening_update),
    path('api/admin/ielts/listening/<int:pk>/audio/', admin_ielts_listening_upload_audio),
    path('api/admin/ielts/speaking/', admin_ielts_speaking_list),
    path('api/admin/ielts/speaking/<int:pk>/', admin_ielts_speaking_delete),
    path('api/admin/ielts/writing/', admin_ielts_writing_list),
    path('api/admin/ielts/writing/<int:pk>/', admin_ielts_writing_delete),
    path('api/admin/ielts/writing/<int:pk>/image/', admin_ielts_writing_upload_image),
    path('api/admin/cefr/tests/', admin_cefr_tests),
    path('api/admin/cefr/tests/<int:pk>/', admin_cefr_test_delete),
    path('api/admin/cefr/reading/', admin_cefr_reading_list),
    path('api/admin/cefr/reading/all/', admin_cefr_reading_delete_all),
    path('api/admin/cefr/reading/<int:pk>/', admin_cefr_reading_delete),
    path('api/admin/cefr/reading/<int:pk>/detail/', admin_cefr_reading_detail),
    path('api/admin/cefr/listening/', admin_cefr_listening_list),
    path('api/admin/cefr/listening/all/', admin_cefr_listening_delete_all),
    path('api/admin/cefr/listening/<int:pk>/', admin_cefr_listening_delete),
    path('api/admin/cefr/listening/<int:pk>/detail/', admin_cefr_listening_detail),
    path('api/admin/cefr/listening/<int:pk>/audio/', admin_cefr_listening_audio),
    path('api/admin/ielts/reading/<int:pk>/detail/', admin_ielts_reading_detail),
    path('api/admin/ielts/listening/<int:pk>/detail/', admin_ielts_listening_detail),
    path('api/admin/ielts/tests/', admin_ielts_tests_list),
    path('api/admin/ielts/tests/<int:pk>/', admin_ielts_test_detail),
    path('api/admin/ielts/tests/<int:pk>/audio/', admin_ielts_test_audio),
    path('api/admin/ielts/tests/<int:pk>/premium/', admin_ielts_test_premium),
    path('api/admin/sat/bank-questions/', admin_bank_questions),
    path('api/admin/sat/bank-questions/create/', admin_bank_question_create),
    path('api/admin/sat/bank-questions/<int:pk>/', admin_bank_question_detail),
    path('api/admin/sat/bank-questions/<int:pk>/image/<str:letter>/', admin_bank_question_choice_image),
    path('api/admin/sat/questions/<int:pk>/choice-image/<str:letter>/', admin_sat_question_choice_image),

    # ── IMPORT ENDPOINTS ──────────────────────────────────────────────────────
    path('api/import/sat/test/', import_sat_test),
    path('api/import/sat/questions/', import_sat_questions),
    path('api/import/sat/practice/', import_sat_practice),
    path('api/import/sat/mock/', import_sat_mock),
    path('api/admin/sat/questions/<int:pk>/', admin_sat_question_detail),
    path('api/import/ielts/reading/', import_ielts_reading),
    path('api/import/ielts/listening/', import_ielts_listening),
    path('api/import/ielts/speaking/', import_ielts_speaking),
    path('api/import/ielts/writing/', import_ielts_writing),
    path('api/import/cefr/', import_cefr_test),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
