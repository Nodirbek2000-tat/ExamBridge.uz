"""TOBY RUN learner endpoints, mounted at /api/games/runner/ (RUNNER_PLAN §B8.3)."""
from django.urls import path

from . import runner_views as v

urlpatterns = [
    path('deck/', v.deck),
    path('finish/', v.finish),
    path('practice/', v.practice),
    path('me/', v.me),
    path('board/', v.board),
    path('admin/stats/', v.admin_stats),
]
