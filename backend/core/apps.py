from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Import so drf-spectacular registers the JWT OpenApiAuthentication
        # extension (theme is a __init_subclass__ side-effect of importing
        # the module — without this the Swagger UI can't advertise the
        # Bearer security scheme).
        from core import spectacular  # noqa: F401
