import os
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings

from azure.ai.translation.document import DocumentTranslationClient
from azure.core.credentials import AzureKeyCredential
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import get_contextvars

from otto.models import Cost, User

logger = get_logger(__name__)
ten_minutes = 600


def azure_delete(path):
    azure_storage = settings.AZURE_STORAGE
    try:
        logger.info(f"Deleting {path} from azure storage.")
        azure_storage.delete(path)
        # Now delete the parent folder
        azure_storage.delete(path.rsplit("/", 1)[0])
    except:
        logger.error(f"Error deleting {path}")
        pass


@shared_task(soft_time_limit=ten_minutes)
def translate_file(file_path, target_language):
    input_file_path = None
    output_file_path = None
    try:
        from chat.models import ChatFile, Message

        # Azure translation client
        translation_client = DocumentTranslationClient(
            endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
        )
        logger.info(f"Processing translation for {file_path} at {datetime.now()}")
        file_name = file_path.split("/")[-1]
        input_file_name = file_name.replace(" ", "_")
        file_extension = os.path.splitext(input_file_name)[1]
        file_name_without_extension = os.path.splitext(input_file_name)[0]
        output_file_name = (
            f"{file_name_without_extension}_{target_language.upper()}{file_extension}"
        )
        file_uuid = uuid.uuid4()
        input_file_path = f"temp/translation/in/{file_uuid}/{input_file_name}"
        output_file_path = f"temp/translation/out/{file_uuid}/{output_file_name}"

        # Upload to Azure Blob Storage
        azure_storage = settings.AZURE_STORAGE
        with open(file_path, "rb") as f:
            azure_storage.save(input_file_path, f)

        # Set up translation parameters
        source_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{input_file_path}"
        target_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{output_file_path}"

        # Submit the translation job
        poller = translation_client.begin_translation(
            source_url, target_url, target_language, storage_type="File"
        )
        result = poller.result()

        usage = poller.details.total_characters_charged
        Cost.objects.new(cost_type="translate-file", count=usage)

        request_context = get_contextvars()
        out_message = Message.objects.get(id=request_context.get("message_id"))
        for document in result:
            if document.status == "Succeeded":
                new_file = ChatFile.objects.create(
                    message=out_message,
                    filename=output_file_name,
                    content_type="?",
                )
                with azure_storage.open(output_file_path) as f:
                    new_file.saved_file.save(output_file_name, f)
            else:
                logger.error("Translation failed: ", error=document.error.message)
                raise Exception(f"Translation failed:\n{document.error.message}")

        logger.info(f"Translation processed for {file_path} at {datetime.now()}")
    except SoftTimeLimitExceeded:
        logger.error(f"Translation task timed out for {file_path}")
        raise Exception(f"Translation task timed out for {file_path}")
    except Exception as e:
        logger.error(f"Error translating {file_path}: {e}")
        raise Exception(f"Error translating {file_path}")
    finally:
        if input_file_path:
            Thread(target=azure_delete, args=(input_file_path,)).start()
        if output_file_path:
            Thread(target=azure_delete, args=(output_file_path,)).start()
