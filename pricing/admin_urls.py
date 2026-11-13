from django.urls import path

from . import card_views

# Mounted at /api/admin/payments/ — the admin panel's Payments section.
urlpatterns = [
    path('', card_views.admin_payments),
    path('settings/', card_views.admin_settings),
    path('reject-reasons/', card_views.admin_reject_reasons),
    path('<int:payment_id>/receipt/', card_views.admin_receipt),
    path('<int:payment_id>/approve/', card_views.admin_approve),
    path('<int:payment_id>/reject/', card_views.admin_reject),
]
