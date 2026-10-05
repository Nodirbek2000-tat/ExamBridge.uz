from django.apps import AppConfig


class GamestatsConfig(AppConfig):
    """Games platform: which games are on, their order, and how often they are opened."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gamestats'
