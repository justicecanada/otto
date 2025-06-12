import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete all files in the transcriber_uploads media subdirectory."

    def handle(self, *args, **options):
        upload_dir = os.path.join(settings.MEDIA_ROOT, "transcriber_uploads")
        if not os.path.exists(upload_dir):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Directory {upload_dir} does not exist. Nothing to clean."
                )
            )
            return
        deleted = 0
        for filename in os.listdir(upload_dir):
            file_path = os.path.join(upload_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    deleted += 1
            except Exception as e:
                self.stderr.write(f"Failed to delete {file_path}: {e}")
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted} files from {upload_dir}")
        )
