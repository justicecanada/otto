import os
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings

from azure.ai.translation.document import DocumentTranslationClient, TranslationGlossary
from azure.core.credentials import AzureKeyCredential
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import bind_contextvars, get_contextvars

from chat.utils import swap_glossary_columns
from otto.models import Cost

logger = get_logger(__name__)
ten_minutes = 600


@shared_task(bind=True)
def extract_text_task(
    self, file_id, pdf_method="default", context_vars=None, rerouted=False
):
    """
    Celery task to extract text from a ChatFile.
    If file is small (< threshold), routes to light queue; otherwise uses heavy worker.
    Returns the file_id when complete, or raises an exception on error.
    """

    MAX_LIGHT_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    try:
        from chat.models import ChatFile
        from librarian.utils.process_engine import (
            extract_markdown,
            get_process_engine_from_type,
            guess_content_type,
        )

        # Bind context variables for cost tracking
        if context_vars:
            bind_contextvars(**context_vars)

        file = ChatFile.objects.get(id=file_id)

        if not file.saved_file:
            raise Exception("No saved file found")
        file_size = file.saved_file.file.size

        if not rerouted:
            queue = self.request.delivery_info.get("routing_key")
            if file_size < MAX_LIGHT_FILE_SIZE and queue != "light":
                logger.info(
                    f"extract_text_task: rerouting small file ({file_size} bytes) to lightworker."
                )
                result = extract_text_task.apply_async(
                    args=[file_id, pdf_method, context_vars],
                    kwargs={"rerouted": True},
                    queue="light",
                )
                return result.get(timeout=ten_minutes)
            elif file_size >= MAX_LIGHT_FILE_SIZE and queue != "heavy":
                logger.info(
                    f"extract_text_task: rerouting large file ({file_size} bytes) to heavyworker."
                )
                result = extract_text_task.apply_async(
                    args=[file_id, pdf_method, context_vars],
                    kwargs={"rerouted": True},
                    queue="heavy",
                )
                return result.get(timeout=ten_minutes)

        logger.info(
            f"extract_text_task: processing file {file_id} of size {file_size} bytes in queue {self.request.delivery_info.get('routing_key')}."
        )
        # Process the file and extract text
        with file.saved_file.file.open("rb") as file_handle:
            content = file_handle.read()
            content_type = guess_content_type(
                content, file.saved_file.content_type, file.filename
            )
            process_engine = get_process_engine_from_type(content_type)
            extraction_result = extract_markdown(
                content, process_engine, pdf_method=pdf_method
            )
            file.text = extraction_result.markdown
            file.save()
        return file_id
    except Exception as e:
        logger.exception(f"Error in extract_text_task for file {file_id}: {e}")
        raise


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


@shared_task(bind=True)
def translate_file(
    self,
    file_path,
    target_language,
    custom_translator_id=None,
    glossary_path=None,
    rerouted=False,
):
    """
    Celery task to process file translation.
    Dynamically routes to heavy or light queue by input file size.
    Returns translation result or raises on error.
    """
    MAX_LIGHT_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    # Look up the file on disk and determine size
    file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
    queue = self.request.delivery_info.get("routing_key")

    if not rerouted:
        if file_size < MAX_LIGHT_FILE_SIZE and queue != "light":
            logger.info(
                f"translate_file: rerouting small file ({file_size} bytes) to lightworker."
            )
            result = translate_file.apply_async(
                args=[file_path, target_language, custom_translator_id, glossary_path],
                kwargs={"rerouted": True},
                queue="light",
            )
            return result.get(timeout=ten_minutes)
        elif file_size >= MAX_LIGHT_FILE_SIZE and queue != "heavy":
            logger.info(
                f"translate_file: rerouting large file ({file_size} bytes) to heavyworker."
            )
            result = translate_file.apply_async(
                args=[file_path, target_language, custom_translator_id, glossary_path],
                kwargs={"rerouted": True},
                queue="heavy",
            )
            return result.get(timeout=ten_minutes)

    logger.info(
        f"translate_file: processing file {file_path} of size {file_size} bytes in queue {self.request.delivery_info.get('routing_key')}."
    )
    if target_language == "fr":
        target_language = "fr-ca"
    input_file_path = None
    output_file_path = None
    glossary_file_path = None
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
        input_file_path = f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/{file_uuid}/{input_file_name}"
        output_file_path = f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/{file_uuid}/{output_file_name}"

        # Upload input file to Azure Blob Storage
        azure_storage = settings.AZURE_STORAGE
        with open(file_path, "rb") as f:
            azure_storage.save(input_file_path, f)

        # Upload glossary to Azure Blob Storage
        if glossary_path:
            glossary_file_path = f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/{file_uuid}/glossary/glossary.csv"
            with open(glossary_path, "rb") as f:
                # If the target language is not "fr-ca", we need to swap the columns in the glossary file
                if target_language != "fr-ca":
                    f = swap_glossary_columns(f)
                azure_storage.save(glossary_file_path, f)

            glossaries = [
                TranslationGlossary(
                    glossary_url=f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{glossary_file_path}",
                    file_format="CSV",
                )
            ]
        else:
            glossaries = None

        # Set up translation parameters
        source_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{input_file_path}"
        target_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_CONTAINER}/{output_file_path}"

        # Submit the translation job
        poller = translation_client.begin_translation(
            source_url,
            target_url,
            target_language,
            storage_type="File",
            category_id=custom_translator_id,
            glossaries=glossaries,
        )
        result = poller.result()

        usage = poller.details.total_characters_charged
        cost_type = "translate-custom" if custom_translator_id else "translate-file"
        Cost.objects.new(cost_type=cost_type, count=usage)

        request_context = get_contextvars()
        out_message = Message.objects.get(id=request_context.get("message_id"))
        for document in result:

            if document.status == "Succeeded":
                new_file = ChatFile.objects.create(
                    message=out_message,
                    filename=output_file_name,
                    content_type="?",
                )
                logger.info(f"Translation succeeded for {new_file.filename}")
                with azure_storage.open(output_file_path) as f:
                    new_file.saved_file.file.save(output_file_name, f)
            else:
                logger.error("Translation failed: ", error=document.error.message)
                raise Exception(f"Translation failed:\n{document.error.message}")

        logger.info(f"Translation processed for {file_path} at {datetime.now()}")
    except SoftTimeLimitExceeded:
        logger.error(f"Translation task timed out for {file_path}")
        raise Exception(f"Translation task timed out for {file_path}")
    except Exception as e:
        logger.exception(f"Error translating {file_path}: {e}")
        raise Exception(f"Error translating {file_path}")
    finally:
        if input_file_path:
            Thread(target=azure_delete, args=(input_file_path,)).start()
        if output_file_path:
            Thread(target=azure_delete, args=(output_file_path,)).start()
        if glossary_file_path:
            Thread(target=azure_delete, args=(glossary_file_path,)).start()
