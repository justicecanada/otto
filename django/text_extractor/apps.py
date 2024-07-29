import os

from django.apps import AppConfig
from django.conf import settings


class OcrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "text_extractor"

    def ready(self):
        # This line ensures that the import happens when the app is ready,
        # preventing any AppRegistryNotReady exception.
        from django.core.files.storage import default_storage as storage

        # Define the path for the ocr_output_files directory
        ocr_output_path = os.path.join(settings.MEDIA_ROOT, "ocr_output_files")

        # Check if the directory exists, and create it if it doesn't
        if not storage.exists(ocr_output_path):
            os.makedirs(ocr_output_path, exist_ok=True)
