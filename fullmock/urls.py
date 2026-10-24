"""Full mock routes — included from config/urls.py (see the paths there)."""
from django.urls import path

from . import views


def admin_urls(exam):
    return [
        path(f'api/admin/{exam}/full-mocks/', views.admin_mocks, {'exam': exam}),
        path(f'api/admin/{exam}/full-mocks/options/', views.admin_options, {'exam': exam}),
        path(f'api/admin/{exam}/full-mocks/<int:pk>/', views.admin_mock_detail, {'exam': exam}),
    ]


def student_urls(exam):
    return [
        path(f'api/{exam}/full-mocks/', views.student_mocks, {'exam': exam}),
        path(f'api/{exam}/full-mocks/<int:pk>/start/', views.student_start, {'exam': exam}),
    ]


urlpatterns = (
    admin_urls('ielts') + admin_urls('cefr') + student_urls('ielts') + student_urls('cefr') + [
        path('api/full-mocks/attempts/<int:attempt_id>/', views.attempt_detail),
        path('api/full-mocks/attempts/<int:attempt_id>/link/', views.attempt_link),
    ]
)
