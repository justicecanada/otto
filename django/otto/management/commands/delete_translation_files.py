from django.conf import settings
from django.core.management.base import BaseCommand

from django_extensions.management.utils import signalcommand
from structlog import get_logger

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Delete dangling azure translation files"

    @signalcommand
    def handle(self, *args, **options):
        # Upload to Azure Blob Storage
        azure_storage = settings.AZURE_STORAGE
        folders = [
            f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/",
            f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/",
        ]

        try:
            files = azure_storage.list_all()
            # reverse the list so we delete the files first before the folders
            files.reverse()
        except Exception as e:
            logger.error(f"Failed to list files in azure storage: {str(e)}")
            return
        if not files:
            logger.info("No files found in azure storage.")
            return
        for file in files:
            if any(file.startswith(folder) for folder in folders):
                try:
                    azure_storage.delete(file)
                    logger.info(f"Removed {file} from azure storage.")
                except Exception as e:
                    logger.error(f"Failed to remove {file}: {str(e)}")
