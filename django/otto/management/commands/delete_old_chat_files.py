import datetime
import os

# settings
from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand

from chat.models import ChatFile


def get_dir_size(path="."):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total


# TODO: Update this logic to match new models
# (and don't delete files that are still referenced by other Document, ChatFile objects!)
class Command(BaseCommand):
    help = "Delete chat files more than 7 days old."

    def add_arguments(self, parser):
        # Delete all
        parser.add_argument(
            "--all",
            action="store_true",
            help="Delete all chat files, regardless of age.",
        )
        # before date
        parser.add_argument(
            "--before",
            type=str,
            help="Delete chat files older than this date. Format: YYYY-MM-DD",
        )
        # Purge the entire folder through OS (not recommended)
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Delete all chat files, regardless of age, using OS commands (DANGER).",
        )

    @signalcommand
    def handle(self, *args, **options):
        # If "before" flag, delete before that date
        if options["before"]:
            delete_from = datetime.datetime.strptime(options["before"], "%Y-%m-%d")
        # By default, delete before 1 week ago
        else:
            delete_from = datetime.datetime.now() - datetime.timedelta(days=7)

        # Find ChatFile objects with 'accessed_at' property older than 'delete_from'
        if options["all"]:
            chat_files = ChatFile.objects.all()
        else:
            chat_files = ChatFile.objects.filter(accessed_at__lt=delete_from)

        space_freed = 0
        files_deleted = 0
        # Delete the files
        for chat_file in chat_files:
            file_field = chat_file.file
            try:
                space_freed += file_field.size / 1024
                file_field.delete(save=False)
                files_deleted += 1
            except:
                # File must have already been deleted? Just mark as none
                pass
            chat_file.file = None
            chat_file.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {files_deleted} files and freed up {space_freed:.2f} kb."
            )
        )

        if options["purge"]:
            chat_files_dir = os.path.join(settings.MEDIA_ROOT, "chat_files")
            # Calculate size of chat_files folder
            try:
                directory_size = get_dir_size(chat_files_dir)
            except FileNotFoundError:
                self.stdout.write(
                    self.style.WARNING(f"Directory {chat_files_dir} not found.")
                )
                return
            # Delete the entire folder
            os.system(f"rm -rf {chat_files_dir}")
            self.stdout.write(
                self.style.WARNING(
                    f"Purged {chat_files_dir} and freed up {directory_size:.2f} kb."
                )
            )
