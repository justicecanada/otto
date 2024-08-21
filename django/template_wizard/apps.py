import os

from django.apps import AppConfig
from django.conf import settings


class TemplateWizardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "template_wizard"

    def ready(self):
        # This line ensures that the import happens when the app is ready,
        # preventing any AppRegistryNotReady exception.
        from django.core.files.storage import default_storage as storage

        # Define the path for the wizard_input_path directory
        wizard_input_path = os.path.join(settings.MEDIA_ROOT, "uploads")

        # Check if the directory exists, and create it if it doesn't
        if not storage.exists(wizard_input_path):
            os.makedirs(wizard_input_path, exist_ok=True)

        # Define the path for the wizard_output_path directory
        wizard_output_path = os.path.join(settings.MEDIA_ROOT, "generated_reports")

        # Check if the directory exists, and create it if it doesn't
        if not storage.exists(wizard_output_path):
            os.makedirs(wizard_output_path, exist_ok=True)
