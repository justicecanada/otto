import os
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings
from django.core.cache import cache

from azure.ai.translation.document import DocumentTranslationClient, TranslationGlossary
from azure.core.credentials import AzureKeyCredential
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import get_contextvars

from otto.models import Cost

from chat.utils import swap_glossary_columns

logger = get_logger(__name__)
ten_minutes = 600


def azure_delete(path):
    azure_storage = settings.AZURE_STORAGE
    try:
        logger.info(f"Deleting {path} from azure storage.")
        azure_storage.delete(path)
        # Now delete the parent folder
        azure_storage.delete(path.rsplit("/", 1)[0])
    except Exception:
        logger.error(f"Error deleting {path}")
        pass


@shared_task(queue=settings.LIGHT_QUEUE)
def generate_chat_title_task(chat_id, language=None):
    """Generate a chat title asynchronously and clear the pending cache lock."""
    lock_key = f"chat_title_generation_{chat_id}"
    try:
        from django.utils import translation
        from django.utils.translation import gettext as _

        translation.activate(language or "en")

        from chat.llm import OttoLLM
        from chat.models import Chat
        from chat.utils import is_placeholder_chat_title, title_chat

        chat = Chat.objects.filter(id=chat_id).first()
        if not chat or not is_placeholder_chat_title(chat.title):
            return

        llm = OttoLLM()
        title_chat(chat.id, llm=llm)
        llm.create_costs()

        # title_chat may skip saving when text is too short. For sidebar chats,
        # we must always have a real title (never "Untitled chat"). Use first
        # message text as fallback.
        chat.refresh_from_db()
        if is_placeholder_chat_title(chat.title):
            from chat.models import Message

            first_msg = (
                Message.objects.filter(chat=chat, is_bot=False)
                .order_by("date_created")
                .first()
            )
            if first_msg and first_msg.text:
                fallback = first_msg.text[:50].strip()
                if len(fallback) > 47:
                    fallback = fallback[:47] + "..."
                chat.title = fallback or _("Chat")
            elif first_msg and first_msg.files.exists():
                # File upload with no text
                chat.title = _("File upload")
            else:
                chat.title = _("Chat")
            chat.save()
    except Exception:
        logger.exception(
            "Failed to generate chat title asynchronously", chat_id=chat_id
        )
    finally:
        cache.delete(lock_key)


@shared_task(soft_time_limit=ten_minutes, queue=settings.LIGHT_QUEUE)
def translate_file(
    file_path, target_language, custom_translator_id=None, glossary_path=None
):
    if target_language == "fr":
        target_language = "fr-ca"
    input_file_path = None
    output_file_path = None
    glossary_file_path = None
    try:
        from chat.models import ChatFile, Message

        # Azure translation client
        translation_client = DocumentTranslationClient(
            endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_AI_SERVICES_KEY),
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
            try:
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
            except FileNotFoundError:
                # If the glossary has been deleted from disk, log and continue without it
                logger.warning(
                    "Glossary file not found on disk, continuing without glossary",
                    glossary_path=glossary_path,
                )
                glossary_file_path = None
                glossaries = None
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
        message = f"Translation failed: task timed out for {file_path}"
        logger.error(message)
        raise Exception(message)
    except Exception as e:
        logger.exception(f"Error translating {file_path}: {e}")
        message = str(e)
        if message.startswith("Translation failed:"):
            raise Exception(message)
        raise Exception(f"Translation failed: {message}")
    finally:
        if input_file_path:
            Thread(target=azure_delete, args=(input_file_path,)).start()
        if output_file_path:
            Thread(target=azure_delete, args=(output_file_path,)).start()
        if glossary_file_path:
            Thread(target=azure_delete, args=(glossary_file_path,)).start()
