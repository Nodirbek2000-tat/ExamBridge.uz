from django.apps import AppConfig


class CentersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'centers'

    def ready(self):
        # Tests created by a center import belong to that center (centers/content.py)
        from django.db.models.signals import pre_save

        from .content import content_models, stamp_center
        for model in content_models():
            pre_save.connect(stamp_center, sender=model, dispatch_uid=f'center-stamp-{model.__name__}')
