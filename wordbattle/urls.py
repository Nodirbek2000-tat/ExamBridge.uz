from django.urls import path

from . import views

urlpatterns = [
    path('home/', views.home),
    path('rounds/', views.round_start),
    path('rounds/<uuid:round_id>/', views.round_detail),
    path('rounds/<uuid:round_id>/next/', views.round_next),
    path('rounds/<uuid:round_id>/answer/', views.round_answer),
    path('rounds/<uuid:round_id>/finish/', views.round_finish),
    path('leaderboard/', views.leaderboard),
    path('duels/', views.duels),
    path('duels/<str:code>/', views.duel_detail),
    path('duels/<str:code>/accept/', views.duel_accept),
    path('admin/stats/', views.admin_stats),
]
