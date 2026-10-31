"""AI lessons student routes — included from config/urls.py under api/ai-lessons/."""
from django.urls import path

from . import student_views

urlpatterns = [
    path('', student_views.lesson_list),
    path('<int:pk>/', student_views.lesson_detail),
    path('<int:pk>/progress/', student_views.lesson_progress),
    path('questions/<int:pk>/answer/', student_views.question_answer),
]
