from django.conf import settings


def global_settings(request):
    """Global context available in all templates."""
    return {
        'GA_TRACKING_ID': settings.GA_TRACKING_ID,
        'DEBUG': settings.DEBUG,
        'SITE_NAME': 'SAT+',
    }
