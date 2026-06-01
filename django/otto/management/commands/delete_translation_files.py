from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from django_extensions.management.utils import signalcommand
from structlog import get_logger
from translate.models import UserRequest

from otto.secure_models import AccessKey

logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Delete dangling azure translation files and old translate app files"

    def _cleanup_azure_translation_blobs(self):
        """Delete Azure translation blobs from configured input/output folders."""
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

    def _cleanup_translate_media(self):
        """Delete translate requests older than 24h (cascade removes input/output files)."""
        cutoff = timezone.now() - timedelta(hours=24)
        access_key = AccessKey(bypass=True)

        old_requests = UserRequest.objects.filter(
            access_key=access_key,
            created_at__lt=cutoff,
        )

        deleted_count = 0
        for user_request in old_requests:
            request_id = user_request.id
            created_at = user_request.created_at
            user_request.delete(access_key=access_key)
            deleted_count += 1
            logger.info(
                "Deleted old translate user request",
                user_request_id=str(request_id),
                created_at=created_at.isoformat(),
            )

        logger.info(
            "Translate media cleanup complete",
            deleted_request_count=deleted_count,
            cutoff=cutoff.isoformat(),
        )

    @signalcommand
    def handle(self, *args, **options):
        self._cleanup_azure_translation_blobs()
        self._cleanup_translate_media()
