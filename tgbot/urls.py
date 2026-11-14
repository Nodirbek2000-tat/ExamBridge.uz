from django.urls import path, re_path

from . import views

# Mounted at /api/bot/ — only the Telegram bot calls these (X-Bot-Secret).
urlpatterns = [
    path('users/sync/', views.user_sync),
    path('users/register/', views.user_register),
    path('users/link/', views.user_link),
    path('users/login-link/', views.user_login_link),
    path('users/login-confirm/', views.user_login_confirm),
    path('users/ids/', views.user_ids),
    path('users/pay-check/', views.pay_check),
    path('users/pay-confirm/', views.pay_confirm),
    path('plans/', views.plans),
    path('payments/', views.payment_create),
    path('payments/<int:payment_id>/approve/', views.payment_approve),
    path('payments/<int:payment_id>/reject/', views.payment_reject),
    path('channels/', views.channels),
    re_path(r'^channels/(?P<chat_id>-?\d+)/$', views.channel_delete),   # channel ids are negative
    path('stats/', views.stats),
]
