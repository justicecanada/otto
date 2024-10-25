import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

from otto.secure_models import AccessKey
from text_extractor.models import OutputFile


class Command(BaseCommand):
    help = "Delete files older than 24 hours"

    def handle(self, *args, **kwargs):
        now = datetime.now()
        cutoff = now - timedelta(hours=24)
        access_key = AccessKey(bypass=True)
        old_files = OutputFile.objects.filter(
            access_key=access_key, user_request__created_at__lt=cutoff
        )

        for output_file in old_files:
            file_path = output_file.file.path
            if os.path.exists(file_path):
                os.remove(file_path)
                self.stdout.write(self.style.SUCCESS(f"Deleted file: {file_path}"))
            output_file.delete(access_key=access_key)
