from django.urls import path

from . import views

urlpatterns = [
    path('lessons/', views.lessons),
    path('lessons/<int:pk>/', views.lesson_detail),
    path('lessons/<int:pk>/attempts/', views.attempt_create),
    path('attempts/<int:pk>/', views.attempt_detail),
    path('attempts/<int:pk>/audio/', views.attempt_audio),
    path('admin/import/', views.admin_import),
    path('admin/lessons/', views.admin_lessons),
    path('admin/lessons/<int:pk>/', views.admin_lesson),
]
