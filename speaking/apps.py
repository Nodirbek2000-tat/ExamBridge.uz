from django.apps import AppConfig


class SpeakingConfig(AppConfig):
    """Speaking game: read a text aloud, every word is checked and scored."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'speaking'
