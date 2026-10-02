from django.apps import AppConfig


class ProxiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "proxies"

    def ready(self):
        from . import security_checks  # noqa: F401
