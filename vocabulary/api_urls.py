"""Word bank API — mounted at /api/games/words/ (see api_views.py)."""
from django.urls import path

from . import api_views

urlpatterns = [
    path('admin/import/', api_views.admin_import),
    path('admin/items/', api_views.admin_items),
    path('admin/items/<str:kind>/<int:item_id>/', api_views.admin_item_update),
    path('admin/export/', api_views.admin_export),
    path('admin/summary/', api_views.admin_summary),
    path('admin/warm/', api_views.admin_warm),
]
