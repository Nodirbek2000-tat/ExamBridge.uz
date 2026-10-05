from django.urls import path

from . import views

urlpatterns = [
    path('open/', views.open_game),
    path('hub/', views.hub),
    path('admin/overview/', views.admin_overview),
    path('admin/games/<slug:slug>/', views.admin_game_update),
    path('admin/warm-voices/', views.admin_warm_voices),
    path('admin/warm-voices/status/', views.admin_warm_status),
]
