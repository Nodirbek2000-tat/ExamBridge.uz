from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='panel_dashboard'),
    path('users/', views.users_view, name='panel_users'),
    path('users/<int:user_id>/toggle-premium/', views.toggle_premium_view, name='panel_toggle_premium'),
    path('tests/', views.tests_view, name='panel_tests'),
    path('tests/<int:test_id>/toggle-active/', views.toggle_test_active_view, name='panel_toggle_test'),
    path('tests/import/', views.import_test_json_view, name='panel_import_test'),
    path('questions/', views.questions_view, name='panel_questions'),
    path('questions/import/', views.import_questions_view, name='panel_import_questions'),
    path('questions/<int:question_id>/delete/', views.delete_question_view, name='panel_delete_question'),
    path('analytics/', views.analytics_view, name='panel_analytics'),
]
