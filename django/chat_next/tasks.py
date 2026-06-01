"""
Celery tasks for chat_next app.

Includes cleanup tasks for dangling OpenAI files and responses.
"""

from django.conf import settings
from django.core.cache import cache

from celery import shared_task
from structlog import get_logger

logger = get_logger(__name__)


def _bind_chat_next_task_context(
    *,
    message_id: str | int,
    feature: str,
    user_id: int | None = None,
    cost_group_id: int | None = None,
):
    """Bind clean cost/logging context for chat_next Celery tasks.

    Celery workers can reuse structlog contextvars across jobs. Clear the legacy
    chat/law/document attribution keys before binding the current chat_next
    message so Cost.objects.new() cannot accidentally write a stale FK.
    """
    from structlog.contextvars import bind_contextvars, unbind_contextvars

    try:
        unbind_contextvars("message_id", "message_next_id", "document_id", "law_id")
    except Exception:
        pass

    bind_kwargs = {"feature": feature}
    try:
        bind_kwargs["message_next_id"] = int(message_id)
    except (TypeError, ValueError):
        pass
    if user_id is not None:
        bind_kwargs["user_id"] = user_id
    if cost_group_id is not None:
        bind_kwargs["cost_group_id"] = cost_group_id

    bind_contextvars(**bind_kwargs)


@shared_task(queue=settings.LIGHT_QUEUE)
def generate_chat_title_task(chat_id, language=None):
    """Generate a chat title asynchronously and clear the pending cache lock."""
    lock_key = f"chat_title_generation_{chat_id}"
    try:
        from django.utils import translation
        from django.utils.translation import gettext as _

        translation.activate(language or "en")

        from chat_next.models import Chat, Message
        from chat_next.utils import (
            _build_fallback_chat_title,
            is_placeholder_chat_title,
            title_chat,
        )

        chat = Chat.objects.filter(id=chat_id).first()
        if not chat or not is_placeholder_chat_title(chat.title):
            return

        title_chat(chat.id, force_title=False)

        # title_chat may skip saving when text is too short or when generation
        # fails. For sidebar chats, we must always have a real title (never
        # "Untitled chat"). Derive a compact fallback instead of reusing the
        # entire first message verbatim.
        chat.refresh_from_db()
        if is_placeholder_chat_title(chat.title):
            first_msg = (
                Message.objects.filter(chat=chat, is_bot=False)
                .order_by("date_created")
                .first()
            )
            if first_msg and first_msg.text:
                chat.title = _build_fallback_chat_title(first_msg.text)
            elif first_msg and first_msg.files.exists():
                chat.title = _("File upload")
            else:
                chat.title = _("Chat")
            chat.save(update_fields=["title"])
    except Exception:
        logger.exception(
            "Failed to generate chat title asynchronously", chat_id=chat_id
        )
    finally:
        cache.delete(lock_key)


def get_openai_client():
    """Get a synchronous AzureOpenAI client for API operations."""
    from openai import AzureOpenAI

    api_version = settings.AZURE_AI_SERVICES_VERSION
    if not api_version or api_version in ("v1", "v1/"):
        api_version = "2025-03-01-preview"

    return AzureOpenAI(
        api_key=settings.AZURE_AI_SERVICES_KEY,
        azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
        api_version=api_version,
    )


def _build_document_processing_prompt(
    *, template_text: str | None, document_text: str, prompt_text: str
) -> str:
    """Build the LLM prompt payload for document-processing tasks."""
    prompt_parts = []
    if template_text:
        prompt_parts.append(f"<template>\n{template_text}\n</template>\n")
    prompt_parts.append(f"<document>\n{document_text}\n</document>\n")
    prompt_parts.append(f"<instruction>\n{prompt_text}\n</instruction>")
    return "\n".join(prompt_parts)


def delete_openai_file(file_id: str) -> bool:
    """
    Delete a file from OpenAI Files API.

    Args:
        file_id: The OpenAI file ID to delete

    Returns:
        True if deletion was successful, False otherwise
    """
    if not file_id:
        return False

    try:
        client = get_openai_client()
        client.files.delete(file_id)
        logger.info("Deleted OpenAI file", file_id=file_id)
        return True
    except Exception as e:
        # File may already be deleted or not exist
        error_str = str(e).lower()
        if "not found" in error_str or "does not exist" in error_str:
            logger.debug("OpenAI file already deleted or not found", file_id=file_id)
            return True
        logger.warning("Failed to delete OpenAI file", file_id=file_id, error=str(e))
        return False


def delete_openai_response(response_id: str) -> bool:
    """
    Delete a response from OpenAI Responses API.

    Args:
        response_id: The OpenAI response ID to delete

    Returns:
        True if deletion was successful, False otherwise
    """
    if not response_id:
        return False

    try:
        client = get_openai_client()
        # The Responses API uses client.responses.delete() for deletion
        client.responses.delete(response_id)
        logger.info("Deleted OpenAI response", response_id=response_id)
        return True
    except Exception as e:
        error_str = str(e).lower()
        if "not found" in error_str or "does not exist" in error_str:
            logger.debug(
                "OpenAI response already deleted or not found", response_id=response_id
            )
            return True
        logger.warning(
            "Failed to delete OpenAI response", response_id=response_id, error=str(e)
        )
        return False


@shared_task
def delete_openai_file_async(file_id: str):
    """
    Async task to delete an OpenAI file.
    Used when we want to fire-and-forget the deletion without blocking.
    """
    return delete_openai_file(file_id)


@shared_task
def delete_openai_response_async(response_id: str):
    """
    Async task to delete an OpenAI response.
    Used when we want to fire-and-forget the deletion without blocking.
    """
    return delete_openai_response(response_id)


@shared_task
def delete_openai_responses_batch(response_ids: list):
    """
    Delete multiple OpenAI responses.
    Used when deleting a chat or message that has many responses.
    """
    deleted_count = 0
    for response_id in response_ids:
        if delete_openai_response(response_id):
            deleted_count += 1
    logger.info(
        "Batch deleted OpenAI responses",
        total=len(response_ids),
        deleted=deleted_count,
    )
    return deleted_count


@shared_task
def delete_openai_files_batch(file_ids: list):
    """
    Delete multiple OpenAI files.
    Used when deleting a chat that has many files.
    """
    deleted_count = 0
    for file_id in file_ids:
        if delete_openai_file(file_id):
            deleted_count += 1
    logger.info(
        "Batch deleted OpenAI files",
        total=len(file_ids),
        deleted=deleted_count,
    )
    return deleted_count


@shared_task
def cleanup_dangling_openai_files():
    """
    Nightly task to clean up dangling OpenAI files.

    Lists all files from OpenAI Files API and deletes ones that don't have
    a matching SavedFile with openai_file_id in our database.
    """
    from librarian.models import SavedFile

    try:
        client = get_openai_client()

        # Get all files from OpenAI
        openai_files = client.files.list()
        openai_file_ids = {f.id for f in openai_files.data}

        if not openai_file_ids:
            logger.info("No files found in OpenAI Files API")
            return {"deleted": 0, "total_openai": 0}

        # Get all OpenAI file IDs we have in our database
        db_file_ids = set(
            SavedFile.objects.filter(openai_file_id__isnull=False)
            .exclude(openai_file_id="")
            .values_list("openai_file_id", flat=True)
        )

        # Find dangling files (in OpenAI but not in our DB)
        dangling_ids = openai_file_ids - db_file_ids

        deleted_count = 0
        for file_id in dangling_ids:
            if delete_openai_file(file_id):
                deleted_count += 1

        logger.info(
            "Cleaned up dangling OpenAI files",
            total_openai=len(openai_file_ids),
            total_db=len(db_file_ids),
            dangling=len(dangling_ids),
            deleted=deleted_count,
        )

        return {
            "deleted": deleted_count,
            "total_openai": len(openai_file_ids),
            "dangling": len(dangling_ids),
        }

    except Exception as e:
        logger.error("Failed to cleanup dangling OpenAI files", error=str(e))
        return {"error": str(e)}


@shared_task
def cleanup_dangling_openai_responses():
    """
    Nightly task to clean up dangling OpenAI responses.

    Lists all stored responses from OpenAI Responses API and deletes ones
    that don't have a matching Message with response_id in our database.

    Note: OpenAI stores responses for 30 days when store=True, so some
    responses may have already been auto-deleted by OpenAI.
    """
    from chat_next.models import Message

    try:
        client = get_openai_client()

        # Get all stored responses from OpenAI
        # Note: The Responses API may have pagination - handle it
        openai_responses = []
        try:
            response_list = client.responses.list()
            openai_responses = list(response_list.data)
        except Exception as e:
            # List endpoint may not be available or may fail
            logger.warning(
                "Could not list OpenAI responses, skipping cleanup", error=str(e)
            )
            return {"error": f"List not supported: {e}"}

        if not openai_responses:
            logger.info("No stored responses found in OpenAI Responses API")
            return {"deleted": 0, "total_openai": 0}

        openai_response_ids = {r.id for r in openai_responses}

        # Get all response IDs we have in our database
        db_response_ids = set(
            Message.objects.filter(response_id__isnull=False)
            .exclude(response_id="")
            .values_list("response_id", flat=True)
        )

        # Find dangling responses (in OpenAI but not in our DB)
        dangling_ids = openai_response_ids - db_response_ids

        deleted_count = 0
        for response_id in dangling_ids:
            if delete_openai_response(response_id):
                deleted_count += 1

        logger.info(
            "Cleaned up dangling OpenAI responses",
            total_openai=len(openai_response_ids),
            total_db=len(db_response_ids),
            dangling=len(dangling_ids),
            deleted=deleted_count,
        )

        return {
            "deleted": deleted_count,
            "total_openai": len(openai_response_ids),
            "dangling": len(dangling_ids),
        }

    except Exception as e:
        logger.error("Failed to cleanup dangling OpenAI responses", error=str(e))
        return {"error": str(e)}


@shared_task
def cleanup_dangling_transcription_blobs():
    """
    Nightly task to clean up dangling transcription input blobs.

    Deletes blobs in the transcription input segment of Azure Blob Storage that
    have not been modified in at least 24 hours. This avoids interrupting active
    (but slow) transcription jobs while still cleaning up orphaned blobs left by
    crashed or timed-out tasks.
    """
    from datetime import datetime, timedelta, timezone

    from azure.storage.blob import BlobServiceClient

    account_name = settings.AZURE_ACCOUNT_NAME
    account_key = settings.AZURE_ACCOUNT_KEY
    container = settings.AZURE_CONTAINER
    input_prefix = f"{settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT}/"

    if not account_name or not account_key or not container:
        logger.error("Azure storage not configured for transcription blob cleanup")
        return {"error": "Azure storage not configured."}

    try:
        blob_service = BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key,
        )
        container_client = blob_service.get_container_client(container)

        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

        deleted_count = 0
        skipped_count = 0
        for blob in container_client.list_blobs(name_starts_with=input_prefix):
            last_modified = blob.last_modified
            if last_modified and last_modified >= cutoff:
                logger.info(
                    "Skipping recent transcription blob (possibly in use)",
                    blob=blob.name,
                    last_modified=last_modified.isoformat(),
                )
                skipped_count += 1
                continue
            try:
                container_client.delete_blob(blob.name)
                logger.info("Deleted dangling transcription blob", blob=blob.name)
                deleted_count += 1
            except Exception as e:
                logger.error(
                    "Failed to delete transcription blob",
                    blob=blob.name,
                    error=str(e),
                )

        logger.info(
            "Cleaned up dangling transcription blobs",
            deleted=deleted_count,
            skipped=skipped_count,
        )
        return {"deleted": deleted_count, "skipped": skipped_count}

    except Exception as e:
        logger.error("Failed to cleanup dangling transcription blobs", error=str(e))
        return {"error": str(e)}


# ============================================================================
# File Translation Task
# ============================================================================

ten_minutes = 60 * 10


@shared_task(soft_time_limit=ten_minutes, queue=settings.LIGHT_QUEUE)
def translate_file_next(
    file_path: str,
    target_language: str,
    message_id: str,
    chat_id: str = "",
    custom_translator_id: str = None,
    user_id: int | None = None,
    cost_group_id: int | None = None,
):
    """
    Translate a file using Azure Document Translation API.

    This task is used by the chat_next translate_files tool. It:
    1. Uploads the file to Azure Blob Storage
    2. Submits a translation job to Azure Document Translation
    3. Creates a ChatFile object with the translated file
    4. Creates a Cost object for the translation
    5. Adds the translated file to the chat_files library

    Args:
        file_path: Path to the file to translate
        target_language: Target language code (e.g., 'fr', 'en')
        message_id: The bot message ID to associate the translated file with
        chat_id: The chat ID (for library linking)
        custom_translator_id: Optional custom translator ID for Azure
    """
    import os
    import uuid
    from threading import Thread

    from azure.ai.translation.document import DocumentTranslationClient
    from azure.core.credentials import AzureKeyCredential
    from celery.exceptions import SoftTimeLimitExceeded

    from otto.models import Cost

    from chat_next.models import Chat, ChatFile, Message

    # Map common language codes to Azure format
    if target_language == "fr":
        target_language = "fr-ca"
    elif target_language == "en":
        target_language = "en"

    resolved_user_id = user_id
    normalized_message_id = None
    try:
        normalized_message_id = int(message_id)
    except (TypeError, ValueError):
        normalized_message_id = None

    if resolved_user_id is None and normalized_message_id is not None:
        try:
            out_message = Message.objects.select_related("chat__user").get(
                id=normalized_message_id
            )
            if out_message.chat and out_message.chat.user:
                resolved_user_id = out_message.chat.user.id
        except Message.DoesNotExist:
            pass

    _bind_chat_next_task_context(
        message_id=message_id,
        feature="translate",
        user_id=resolved_user_id,
        cost_group_id=cost_group_id,
    )

    input_file_path = None
    output_file_path = None

    def azure_delete(path):
        """Delete a file from Azure storage."""
        try:
            azure_storage = settings.AZURE_STORAGE
            logger.info(f"Deleting {path} from azure storage.")
            azure_storage.delete(path)
            # Now delete the parent folder
            azure_storage.delete(path.rsplit("/", 1)[0])
        except Exception:
            logger.error(f"Error deleting {path}")

    try:
        translation_endpoint = getattr(
            settings,
            "AZURE_AI_SERVICES_ENDPOINT",
            getattr(settings, "AZURE_COGNITIVE_SERVICE_ENDPOINT", None),
        )
        translation_key = getattr(
            settings,
            "AZURE_AI_SERVICES_KEY",
            getattr(settings, "AZURE_COGNITIVE_SERVICE_KEY", None),
        )

        if not translation_endpoint or not translation_key:
            logger.error("Azure translation credentials not configured")
            return {
                "success": False,
                "error": "Azure translation credentials not configured.",
            }

        # Azure translation client
        translation_client = DocumentTranslationClient(
            endpoint=translation_endpoint,
            credential=AzureKeyCredential(translation_key),
        )
        logger.info(f"Processing translation for {file_path}")

        # Prepare file names
        file_name = file_path.split("/")[-1]
        input_file_name = file_name.replace(" ", "_")
        file_extension = os.path.splitext(input_file_name)[1]
        file_name_without_extension = os.path.splitext(input_file_name)[0]
        output_file_name = (
            f"{file_name_without_extension}_{target_language.upper()}{file_extension}"
        )

        # Generate unique paths for Azure storage
        file_uuid = uuid.uuid4()
        input_file_path = f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/{file_uuid}/{input_file_name}"
        output_file_path = f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/{file_uuid}/{output_file_name}"

        # Upload input file to Azure Blob Storage
        azure_storage = settings.AZURE_STORAGE
        with open(file_path, "rb") as f:
            azure_storage.save(input_file_path, f)

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
        )
        result = poller.result()

        # Calculate cost
        usage = poller.details.total_characters_charged
        cost_type = "translate-custom" if custom_translator_id else "translate-file"
        Cost.objects.new(cost_type=cost_type, count=usage)

        # Get the message to attach the file to
        out_message = Message.objects.get(id=message_id)

        output_document_id = None
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

                # Add translated file to the chat_files library
                if chat_id:
                    try:
                        from librarian.models import Document as LibrarianDocument

                        chat = Chat.objects.get(id=chat_id)
                        data_source = chat.data_source
                        if data_source:
                            output_doc = LibrarianDocument.objects.create(
                                data_source=data_source,
                                saved_file=new_file.saved_file,
                                filename=output_file_name,
                                provenance=LibrarianDocument.PROVENANCE_GENERATED_OUTPUT,
                            )
                            new_file.document = output_doc
                            new_file.save(update_fields=["document"])
                            output_doc.process()
                            output_document_id = output_doc.id
                    except Chat.DoesNotExist:
                        logger.warning(
                            "Chat not found for library linking", chat_id=chat_id
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to add translation to library", error=str(e)
                        )
            else:
                logger.error("Translation failed", error=document.error.message)
                raise Exception(f"Translation failed:\n{document.error.message}")

        logger.info(f"Translation processed for {file_path}")
        return {
            "success": True,
            "filename": output_file_name,
            "document_id": output_document_id,
        }

    except SoftTimeLimitExceeded:
        logger.error(f"Translation task timed out for {file_path}")
        return {
            "success": False,
            "error": f"Translation task timed out for {file_path}",
        }
    except Exception as e:
        logger.exception(f"Error translating {file_path}: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if input_file_path:
            Thread(target=azure_delete, args=(input_file_path,)).start()
        if output_file_path:
            Thread(target=azure_delete, args=(output_file_path,)).start()


# ============================================================================
# Document Processing (LLM) Task
# ============================================================================


@shared_task(soft_time_limit=ten_minutes, queue=settings.LIGHT_QUEUE)
def prompt_document_next(
    document_id: int,
    prompt_text: str,
    message_id: str,
    chat_id: str,
    model_name: str = None,
    reasoning_effort: str = None,
    truncate_chars: int = None,
    template_text: str = None,
    original_filename: str = None,
    start_char: int = None,
    end_char: int = None,
    start_page: int = None,
    end_page: int = None,
    range_label: str = None,
    overlap_chars: int = None,
    user_id: int | None = None,
    cost_group_id: int | None = None,
):
    """
    Process a document with an LLM prompt and save the result as a ChatFile.

    This task is used by the chat_next prompt_documents tool. It:
    1. Gets the document's extracted text
    2. Sends it with the prompt to an LLM
    3. Saves the result as a markdown ChatFile on the bot message
    4. Creates a Document in the chat_files library for subsequent LLM access
    5. Creates Cost objects for the LLM usage

    Args:
        document_id: The librarian Document ID to process
        prompt_text: The prompt/instruction to apply to the document
        message_id: The bot message ID to attach the output file to
        chat_id: The chat ID (for library linking)
        model_name: Optional model ID to use
        reasoning_effort: Optional reasoning effort to apply for reasoning-capable models
        truncate_chars: Optional number of characters to read from the start of the document
        template_text: Optional template document text for template-filling tasks
        original_filename: The original document filename for naming the output
    """
    import os
    import re

    from celery.exceptions import SoftTimeLimitExceeded

    from otto.models import Cost

    from chat_next.models import Chat, ChatFile, Message

    resolved_user_id = user_id
    normalized_message_id = None
    try:
        normalized_message_id = int(message_id)
    except (TypeError, ValueError):
        normalized_message_id = None

    try:
        if normalized_message_id is not None:
            msg = Message.objects.select_related("chat__user").get(
                id=normalized_message_id
            )
            if resolved_user_id is None and msg.chat and msg.chat.user:
                resolved_user_id = msg.chat.user.id
    except Message.DoesNotExist:
        pass

    _bind_chat_next_task_context(
        message_id=message_id,
        feature="prompt_document",
        user_id=resolved_user_id,
        cost_group_id=cost_group_id,
    )

    try:
        from librarian.models import Document

        doc = Document.objects.get(id=document_id)
        full_doc_text = doc.extracted_text or ""

        if not full_doc_text.strip():
            logger.error(
                "Document has no extracted text",
                document_id=document_id,
            )
            return {"success": False, "error": "Document has no extracted text."}

        source_start_char = 0
        source_end_char = len(full_doc_text)
        source_start_page = None
        source_end_page = None

        if start_page is not None or end_page is not None:
            from chat_next._tools.qa_libraries import (
                _compute_page_boundaries,
                _get_page_for_offset,
                _map_pages_to_char_offsets,
            )

            page_map = _map_pages_to_char_offsets(full_doc_text)
            if not page_map:
                return {
                    "success": False,
                    "error": "Document has no page tags; use character ranges instead of page ranges.",
                }

            sorted_pages = sorted(page_map.keys())
            resolved_start_page = (
                start_page if start_page is not None else sorted_pages[0]
            )
            resolved_end_page = end_page if end_page is not None else sorted_pages[-1]
            if resolved_start_page not in page_map or resolved_end_page not in page_map:
                return {
                    "success": False,
                    "error": "Requested page range is outside the document's available pages.",
                }
            if resolved_end_page < resolved_start_page:
                return {
                    "success": False,
                    "error": "end_page cannot be earlier than start_page.",
                }

            source_start_char = page_map[resolved_start_page][0]
            source_end_char = page_map[resolved_end_page][1]
            source_start_page = resolved_start_page
            source_end_page = resolved_end_page
        else:
            source_start_char = max(0, start_char or 0)
            source_end_char = min(len(full_doc_text), end_char or len(full_doc_text))
            if source_end_char <= source_start_char:
                return {
                    "success": False,
                    "error": "Requested character range is empty or invalid.",
                }

            from chat_next._tools.qa_libraries import (
                _compute_page_boundaries,
                _get_page_for_offset,
            )

            page_boundaries = _compute_page_boundaries(full_doc_text)
            if page_boundaries:
                source_start_page = _get_page_for_offset(
                    page_boundaries, source_start_char
                )
                source_end_page = _get_page_for_offset(
                    page_boundaries,
                    max(source_start_char, source_end_char - 1),
                )

        doc_text = full_doc_text[source_start_char:source_end_char]

        input_truncated = False
        input_chars_used = len(doc_text)
        if truncate_chars and len(doc_text) > truncate_chars:
            doc_text = doc_text[:truncate_chars]
            input_truncated = True
            input_chars_used = truncate_chars

        # Build the prompt with document text
        full_prompt = _build_document_processing_prompt(
            template_text=template_text,
            document_text=doc_text,
            prompt_text=prompt_text,
        )

        # Get the LLM model configuration
        from chat_next._llm.models import (
            DEFAULT_CHAT_MODEL_ID,
            get_model,
            normalize_reasoning_effort,
        )

        model_id = model_name or DEFAULT_CHAT_MODEL_ID
        llm = get_model(model_id)
        resolved_reasoning_effort = None
        if llm.reasoning:
            resolved_reasoning_effort = normalize_reasoning_effort(
                model_id, reasoning_effort
            )

        # Use the synchronous OpenAI client
        client = get_openai_client()

        # Check token count and truncate if needed
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(llm.deployment_name)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        prompt_tokens = len(encoding.encode(full_prompt))
        # Use a conservative max (leave room for output)
        max_input = getattr(llm, "max_tokens_in", 272000) or 272000

        truncation_note = ""
        if input_truncated:
            truncation_note += (
                "\n\n---\n*Note: Only the first "
                f"{truncate_chars} characters of the source document were processed.*\n"
            )
        if prompt_tokens > max_input:
            # Truncate the document text to fit
            overhead_tokens = prompt_tokens - len(encoding.encode(doc_text))
            max_doc_tokens = max_input - overhead_tokens
            doc_token_ids = encoding.encode(doc_text)[:max_doc_tokens]
            doc_text = encoding.decode(doc_token_ids)
            truncation_note = (
                "\n\n---\n*Note: The original document was truncated to fit "
                "within the model's context window.*\n"
            )
            # Rebuild prompt with truncated text
            full_prompt = _build_document_processing_prompt(
                template_text=template_text,
                document_text=doc_text,
                prompt_text=prompt_text,
            )
            input_chars_used = len(doc_text)

        logger.info(
            "Processing document with LLM",
            document_id=document_id,
            model=llm.deployment_name,
            prompt_tokens=prompt_tokens,
        )

        # Call the LLM
        response = client.chat.completions.create(
            model=llm.deployment_name,
            messages=[
                {
                    "role": "user",
                    "content": full_prompt,
                }
            ],
            reasoning_effort=resolved_reasoning_effort,
        )

        result_text = response.choices[0].message.content or ""
        provenance_metadata = {
            "source_document_id": document_id,
            "source_filename": doc.filename,
            "label": range_label,
            "start_char": source_start_char,
            "end_char": source_start_char + input_chars_used,
            "start_page": source_start_page,
            "end_page": source_end_page,
            "overlap_chars": overlap_chars,
            "llm_model": llm.model_id,
        }

        def _metadata_scalar(value):
            if value is None:
                return "null"
            if isinstance(value, (int, float)):
                return str(value)
            return str(value)

        metadata_block = "\n".join(
            ["```text"]
            + [
                f"{key}: {_metadata_scalar(value)}"
                for key, value in provenance_metadata.items()
            ]
            + ["```", ""]
        )
        result_text = metadata_block + result_text
        if truncation_note:
            result_text += truncation_note

        # Track costs
        usage = response.usage
        if usage:
            if usage.prompt_tokens:
                Cost.objects.new(
                    cost_type=f"{llm.model_id}-in",
                    count=usage.prompt_tokens,
                )
            if usage.completion_tokens:
                Cost.objects.new(
                    cost_type=f"{llm.model_id}-out",
                    count=usage.completion_tokens,
                )

        # Generate output filename
        if original_filename:
            base_name = os.path.splitext(original_filename)[0]
        else:
            base_name = f"document_{document_id}"

        safe_label = None
        if range_label:
            safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", range_label).strip("_")

        if safe_label:
            output_filename = f"{base_name}__{safe_label}"
        elif start_page is not None or end_page is not None:
            output_filename = f"{base_name}__pages_{(source_start_page or 0):06d}_{(source_end_page or 0):06d}"
        elif (
            start_char is not None or end_char is not None or overlap_chars is not None
        ):
            output_filename = f"{base_name}__chars_{source_start_char:06d}_{(source_start_char + input_chars_used):06d}"
        else:
            output_filename = f"{base_name}_output"

        output_filename += ".md"

        # Create ChatFile on the bot message
        out_message = Message.objects.get(id=message_id)
        new_file = ChatFile.objects.create(
            message=out_message,
            filename=output_filename,
            content_type="text/markdown",
        )
        # Save the markdown content to the file
        from django.core.files.base import ContentFile

        new_file.saved_file.file.save(
            output_filename,
            ContentFile(result_text.encode("utf-8")),
        )

        # Add the output file to the chat_files library as a Document
        output_doc = None
        try:
            chat = Chat.objects.get(id=chat_id)
            data_source = chat.data_source
            if data_source:
                output_doc = Document.objects.create(
                    data_source=data_source,
                    saved_file=new_file.saved_file,
                    filename=output_filename,
                    provenance=Document.PROVENANCE_GENERATED_OUTPUT,
                    parent_document=doc,
                )
                new_file.document = output_doc
                new_file.save(update_fields=["document"])
                # Queue processing so it gets indexed for Q&A
                output_doc.process()
        except Chat.DoesNotExist:
            logger.warning("Chat not found for library linking", chat_id=chat_id)
        except Exception as e:
            logger.warning("Failed to add output to library", error=str(e))

        logger.info(
            "Document processing complete",
            document_id=document_id,
            output_filename=output_filename,
        )
        return {
            "success": True,
            "filename": output_filename,
            "document_id": output_doc.id if output_doc else None,
            "truncate_chars": truncate_chars,
            "input_truncated": input_truncated,
            "input_chars_used": input_chars_used,
            "source_document_id": document_id,
            "source_filename": doc.filename,
            "label": range_label,
            "start_char": source_start_char,
            "end_char": source_start_char + input_chars_used,
            "start_page": source_start_page,
            "end_page": source_end_page,
            "overlap_chars": overlap_chars,
            "model_used": llm.model_id,
        }

    except SoftTimeLimitExceeded:
        logger.error(
            "Document processing task timed out",
            document_id=document_id,
        )
        return {
            "success": False,
            "error": f"Processing timed out for document {document_id}",
        }
    except Exception as e:
        logger.exception(
            "Error processing document",
            document_id=document_id,
            error=str(e),
        )
        return {"success": False, "error": str(e)}


# ============================================================================
# Audio/Video Transcription (Batch Speech-to-Text) Task
# ============================================================================

thirty_minutes = 60 * 30


@shared_task(soft_time_limit=thirty_minutes, queue=settings.LIGHT_QUEUE)
def transcribe_file_next(
    file_path: str,
    message_id: str,
    chat_id: str = "",
    main_language: str = "en-CA",
    min_speakers: int = 1,
    max_speakers: int = 6,
):
    """
    Transcribe an audio/video file using Azure Batch Speech-to-Text REST API.

    This task is used by the chat_next transcribe_files tool. It:
    1. Uploads the file to Azure Blob Storage with a SAS token
    2. Submits a batch transcription job to Azure Speech-to-Text
    3. Polls until the job completes
    4. Parses the transcription results (with speaker diarization)
    5. Creates a ChatFile with the transcript attached to the bot message
    6. Adds the transcript to the chat_files library

    Args:
        file_path: Path to the audio/video file to transcribe
        message_id: The bot message ID to associate the transcript with
        chat_id: The chat ID (for library linking)
        main_language: Primary language locale (e.g. 'en-CA', 'fr-CA')
        min_speakers: Minimum expected number of speakers for diarization
        max_speakers: Maximum expected number of speakers for diarization
    """
    import os
    import time
    from datetime import datetime, timedelta

    import requests
    from azure.storage.blob import (
        BlobSasPermissions,
        BlobServiceClient,
        generate_blob_sas,
    )
    from celery.exceptions import SoftTimeLimitExceeded

    from chat_next.models import Chat, ChatFile, Message

    speech_endpoint = settings.AZURE_SPEECH_TO_TEXT_ENDPOINT
    speech_key = settings.AZURE_AI_SERVICES_KEY
    account_name = settings.AZURE_ACCOUNT_NAME
    account_key = settings.AZURE_ACCOUNT_KEY
    container = settings.AZURE_CONTAINER
    input_segment = settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT

    if not speech_endpoint or not speech_key:
        logger.error("Azure Speech-to-Text credentials not configured")
        return {
            "success": False,
            "error": "Azure Speech-to-Text credentials not configured.",
        }

    if not account_name or not account_key or not container:
        logger.error("Azure storage not configured for transcription")
        return {"success": False, "error": "Azure storage not configured."}

    blob_name = None

    def cleanup_blob():
        """Delete the uploaded blob after transcription."""
        if not blob_name:
            return
        try:
            blob_service = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=account_key,
            )
            blob_client = blob_service.get_blob_client(container, blob_name)
            blob_client.delete_blob()
            logger.info("Cleaned up transcription input blob", blob_name=blob_name)
        except Exception:
            logger.warning("Failed to clean up transcription blob", blob_name=blob_name)

    try:
        # 1. Upload file to Azure Blob Storage
        blob_service = BlobServiceClient(
            account_url=f"https://{account_name}.blob.core.windows.net",
            credential=account_key,
        )
        container_client = blob_service.get_container_client(container)

        blob_name = f"{input_segment}/{int(time.time())}_{os.path.basename(file_path)}"
        with open(file_path, "rb") as fh:
            container_client.upload_blob(name=blob_name, data=fh, overwrite=True)

        # Generate SAS token so Speech service can access the blob
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(hours=12),
        )
        file_url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"

        # 2. Create batch transcription job via REST API
        # The STT endpoint (e.g. https://canadaeast.stt.speech.microsoft.com)
        # is for real-time; batch API uses https://{region}.api.cognitive.microsoft.com
        from urllib.parse import urlparse

        parsed = urlparse(speech_endpoint)
        hostname = parsed.hostname or ""
        # Extract region: "canadaeast.stt.speech.microsoft.com" -> "canadaeast"
        region = hostname.split(".")[0] if hostname else ""
        if not region:
            return {
                "success": False,
                "error": "Could not determine region from speech endpoint.",
            }

        api_endpoint = f"https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2/transcriptions"
        headers = {
            "Ocp-Apim-Subscription-Key": speech_key,
            "Content-Type": "application/json",
        }

        display_name = f"otto_transcription_{int(time.time())}"
        body = {
            "displayName": display_name,
            "locale": main_language,
            "contentUrls": [file_url],
            "properties": {
                "diarizationEnabled": True,
                "diarization": {
                    "speakers": {
                        "minCount": int(min_speakers),
                        "maxCount": int(max_speakers),
                    }
                },
            },
        }

        r = requests.post(api_endpoint, headers=headers, json=body, timeout=30)
        if r.status_code not in (201, 202):
            logger.error(
                "Transcription job creation failed",
                status=r.status_code,
                response=r.text[:500],
            )
            return {
                "success": False,
                "error": f"Transcription creation failed: {r.status_code}",
            }

        location = r.headers.get("Location")
        if not location:
            return {"success": False, "error": "No transcription location returned."}

        # 3. Poll until finished
        poll_headers = {"Ocp-Apim-Subscription-Key": speech_key}
        status = None
        while True:
            try:
                status_resp = requests.get(location, headers=poll_headers, timeout=30)
                if status_resp.status_code == 200:
                    status_json = status_resp.json()
                    status = status_json.get("status")
                    if status in ("Succeeded", "Failed", "Cancelled"):
                        break
            except Exception:
                pass
            time.sleep(5)

        if status != "Succeeded":
            return {
                "success": False,
                "error": f"Transcription did not succeed: status={status}",
            }

        # 4. Retrieve and parse transcription results
        files_url = f"{location}/files"
        files_resp = requests.get(files_url, headers=poll_headers, timeout=30)
        files_resp.raise_for_status()
        files_json = files_resp.json()

        transcript_lines = []
        for fentry in files_json.get("values", []):
            if fentry.get("kind") != "Transcription":
                continue
            content_url = (fentry.get("links") or {}).get("contentUrl") or fentry.get(
                "contentUrl"
            )
            if not content_url:
                continue

            file_resp = requests.get(content_url, timeout=60)
            if file_resp.status_code != 200:
                continue

            ctype = file_resp.headers.get("Content-Type", "")
            if "application/json" not in ctype and not content_url.endswith(".json"):
                # Plain text fallback
                transcript_lines.append(file_resp.text)
                continue

            try:
                payload = file_resp.json()
            except Exception:
                transcript_lines.append(file_resp.text)
                continue

            if not isinstance(payload, dict):
                continue

            phrases = (
                payload.get("recognizedPhrases")
                or payload.get("combinedRecognizedPhrases")
                or payload.get("segments")
                or []
            )
            if isinstance(phrases, dict):
                for v in phrases.values():
                    if isinstance(v, list):
                        phrases = v
                        break

            if not isinstance(phrases, list) or not phrases:
                # Fallback: dump raw JSON
                transcript_lines.append(file_resp.text)
                continue

            for item in phrases:
                offset = (
                    item.get("offsetInTicks")
                    or item.get("start")
                    or item.get("startTime")
                    or 0
                )
                seconds = float(offset) / 1e7
                timestamp = time.strftime("%H:%M:%S", time.gmtime(seconds))
                speaker = f"Speaker {item.get('speaker') or item.get('channel') or '?'}"
                # Get best available text
                text = item.get("display") or item.get("lexical") or ""
                if not text and item.get("nBest"):
                    text = item["nBest"][0].get("display", "")
                transcript_lines.append(f"[{timestamp}] {speaker}\n{text}\n")

        if not transcript_lines:
            return {
                "success": False,
                "error": "Transcription completed but no text was returned.",
            }

        transcript_text = "\n".join(transcript_lines)

        # 5. Save transcript as a ChatFile on the bot message
        out_message = Message.objects.get(id=message_id)
        original_name = os.path.splitext(os.path.basename(file_path))[0]
        output_filename = f"{original_name}_transcript.txt"

        new_file = ChatFile.objects.create(
            message=out_message,
            filename=output_filename,
            content_type="text/plain",
        )
        from django.core.files.base import ContentFile

        new_file.saved_file.file.save(
            output_filename, ContentFile(transcript_text.encode("utf-8"))
        )

        # Add transcript to the chat_files library
        output_document_id = None
        if chat_id:
            try:
                from librarian.models import Document as LibrarianDocument

                chat = Chat.objects.get(id=chat_id)
                data_source = chat.data_source
                if data_source:
                    output_doc = LibrarianDocument.objects.create(
                        data_source=data_source,
                        saved_file=new_file.saved_file,
                        filename=output_filename,
                        provenance=LibrarianDocument.PROVENANCE_GENERATED_OUTPUT,
                    )
                    new_file.document = output_doc
                    new_file.save(update_fields=["document"])
                    output_doc.process()
                    output_document_id = output_doc.id
            except Chat.DoesNotExist:
                logger.warning("Chat not found for library linking", chat_id=chat_id)
            except Exception as e:
                logger.warning("Failed to add transcript to library", error=str(e))

        logger.info("Transcription complete", filename=output_filename)
        return {
            "success": True,
            "filename": output_filename,
            "document_id": output_document_id,
        }

    except SoftTimeLimitExceeded:
        logger.error("Transcription task timed out", file_path=file_path)
        return {"success": False, "error": f"Transcription timed out for {file_path}"}
    except Exception as e:
        logger.exception("Error transcribing file", file_path=file_path, error=str(e))
        return {"success": False, "error": str(e)}
    finally:
        from threading import Thread

        Thread(target=cleanup_blob).start()
