from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


def _seed_browser_test_personas_after_migrate(sender, using, **kwargs):
    if settings.IS_RUNNING_TESTS or not settings.BROWSER_TEST_AUTH_ENABLED:
        return

    from otto.browser_test_auth import seed_browser_test_users

    seed_browser_test_users(using=using)


class OttoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "otto"

    def ready(self):
        from otto.api import openapi  # noqa: F401

        post_migrate.connect(
            _seed_browser_test_personas_after_migrate,
            sender=self,
            dispatch_uid="otto.seed_browser_test_personas_after_migrate",
        )
