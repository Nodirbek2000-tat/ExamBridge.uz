from django.urls import path
from . import card_views, views
from tgbot.site_views import telegram_open

urlpatterns = [
    path('checkout/', views.create_checkout, name='stripe-checkout'),
    path('webhook/', views.stripe_webhook, name='stripe-webhook'),
    path('status/', views.subscription_status, name='subscription-status'),
    path('cancel/', views.cancel_subscription, name='subscription-cancel'),
    path('stats/', views.payment_stats, name='payment-stats'),
    # Card payments through the Telegram bot
    path('plans/', card_views.public_plans, name='card-plans'),
    path('telegram/', telegram_open, name='telegram-open'),
]
