from django.urls import path
from . import views

urlpatterns = [
    path('', views.test_list_view, name='test_list'),
    path('<int:test_id>/', views.test_detail_view, name='test_detail'),
    path('<int:test_id>/start/', views.start_test_view, name='start_test'),
    path('attempt/<int:attempt_id>/', views.test_attempt_view, name='test_attempt'),
    path('attempt/<int:attempt_id>/save-answer/', views.save_answer_view, name='save_answer'),
    path('attempt/<int:attempt_id>/submit-module/', views.submit_module_view, name='submit_module'),
    path('result/<int:attempt_id>/', views.test_result_view, name='test_result'),
    path('result/<int:attempt_id>/review/', views.test_review_view, name='test_review'),
    path('saved/', views.saved_questions_view, name='saved_questions'),
    path('question/<int:question_id>/save/', views.toggle_save_question_view, name='toggle_save_question'),
    path('saved-count/', views.saved_count_view, name='saved_count'),
    path('result/<int:attempt_id>/ai-analysis/', views.ai_analysis_view, name='ai_analysis'),
]
