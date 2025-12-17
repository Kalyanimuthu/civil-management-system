from django.apps import AppConfig

class CivilAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'civil_app'

    def ready(self):
        import civil_app.signals
