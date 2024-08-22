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
def translate_file(file_path, out_message_id, target_language):
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
        # Get extension from filename
        file_extension = os.path.splitext(input_file_name)[1]
        file_name_without_extension = os.path.splitext(input_file_name)[0]
        output_file_name = (
            f"{file_name_without_extension}_{target_language.upper()}{file_extension}"
        )
        file_uuid = uuid.uuid4()
        input_file_path = f"temp/translation/in/{file_uuid}/{input_file_name}"
        output_file_path = f"temp/translation/out/{file_uuid}/{output_file_name}"

        # We need to upload to Azure Blob Storage for Translation API to work
        azure_storage = settings.AZURE_STORAGE
        azure_storage.save(input_file_path, open(file_path, "rb"))

        # Set up translation parameters
        source_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{input_file_path}"
        target_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{output_file_path}"

        # Submit the translation job
        poller = translation_client.begin_translation(
            source_url, target_url, target_language, storage_type="File"
        )
        # Wait for translation to finish
        result = poller.result()

        # Estimate the cost
        usage = poller.details.total_characters_charged
        # TODO(cost)
        # cost = Cost.objects.new(user=user, feature="translate", cost_type="translate-doc", count=usage)

        out_message = Message.objects.get(id=out_message_id)
        for document in result:
            if document.status == "Succeeded":
                # Save the translated file to the database
                new_file = ChatFile.objects.create(
                    message=out_message,
                    filename=output_file_name,
                    content_type="?",
                )

                new_file.saved_file.file.save(
                    output_file_name, azure_storage.open(output_file_path)
                )
            else:
                logger.error("Translation failed: ", error=document.error.message)
                raise Exception(f"Translation failed:\n{document.error.message}")

        Thread(target=azure_delete, args=(input_file_path,)).start()
        Thread(target=azure_delete, args=(output_file_path,)).start()

        logger.info(f"Translation processed for {file_path} at {datetime.now()}")
    except SoftTimeLimitExceeded:
        logger.error(f"Translation task timed out for {file_path}")
        raise Exception(f"Translation task timed out for {file_path}")
