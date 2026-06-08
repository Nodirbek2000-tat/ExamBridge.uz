from django.urls import path
from . import views

urlpatterns = [
    path('checkout/', views.create_checkout, name='stripe-checkout'),
    path('webhook/', views.stripe_webhook, name='stripe-webhook'),
    path('status/', views.subscription_status, name='subscription-status'),
    path('cancel/', views.cancel_subscription, name='subscription-cancel'),
    path('stats/', views.payment_stats, name='payment-stats'),
]
