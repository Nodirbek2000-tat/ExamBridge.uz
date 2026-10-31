"""AI lessons admin routes — included from config/urls.py under api/admin/ai-lessons/."""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.admin_lessons),
    path('<int:pk>/', views.admin_lesson_detail),
    path('<int:pk>/play/', views.admin_lesson_play),
    path('<int:pk>/publish/', views.admin_lesson_publish),
    path('<int:pk>/generate/', views.admin_lesson_generate),
    path('<int:pk>/approve/', views.admin_lesson_approve),
    path('<int:pk>/sources/', views.admin_lesson_sources),
    path('<int:pk>/questions/', views.admin_lesson_questions),
    path('sources/<int:pk>/', views.admin_source_detail),
    path('questions/<int:pk>/', views.admin_question_detail),
    path('steps/<int:pk>/', views.admin_step_detail),
]
