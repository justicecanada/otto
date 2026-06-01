import os
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from structlog import get_logger

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._tools.utils import (
    EST_CHARS_PER_TOKEN,
    TOKENS_PER_CHUNK,
    _calculate_cost_for_units,
)
from chat_next.models import TOOL_CATEGORY_TRANSLATION

logger = get_logger(__name__)


def estimate_translation_cost(arguments: dict, chat=None) -> str | None:
    """Estimate translation cost based on character count of documents.

    counts characters from extracted_text and applies the 'translate-file' cost type
    (Azure document translation), then converts to CAD.

    When extracted_text is unavailable, falls back to estimating chars from num_chunks
    using EST_CHARS_PER_TOKEN (4 chars/token * 768 tokens/chunk)

    Returns a formatted cost string (e.g. "0.15") or None on failure.

    NOTE: The Azure document translation tool (and this cost-estimation helper)
    is not currently used by the assistant in production. The function is
    retained here for potential future use if file translation via Azure is
    re-enabled.
    """
    from otto.utils.common import cad_cost

    from librarian.models import Document

    document_ids = arguments.get("document_ids", [])
    if not document_ids:
        return None

    total_cost = Decimal("0")
    for doc_id in document_ids:
        try:
            doc = Document.objects.get(id=doc_id)
            if doc.extracted_text:
                # Primary: count actual characters from extracted text
                char_count = len(doc.extracted_text)
            elif doc.num_chunks:
                # Fallback: estimate chars from vector DB chunk count
                # (768 tokens/chunk * 4 chars/token)
                char_count = doc.num_chunks * TOKENS_PER_CHUNK * EST_CHARS_PER_TOKEN
            else:
                continue
            total_cost += _calculate_cost_for_units("translate-file", char_count)
        except Document.DoesNotExist:
            continue

    if total_cost == 0:
        return None

    return f"{cad_cost(total_cost):.2f}"


async def translate_files(arguments: dict, context: ToolContext) -> dict:
    """
    Translate document files using Azure Document Translation API.

    This tool accepts document IDs from the user's uploaded files and submits
    them for translation. The translation happens asynchronously and the
    translated files will appear attached to the bot message when complete.

    Args:
        arguments: dict with:
            - document_ids: List of document IDs to translate
            - target_language: Target language code ('fr' or 'en')

    Returns:
        dict with status information about the translation jobs
    """
    from asgiref.sync import sync_to_async
    from structlog.contextvars import get_contextvars

    from librarian.models import Document

    from chat_next.tasks import translate_file_next

    user = context.user

    document_ids = arguments.get("document_ids", [])
    target_language = arguments.get("target_language", "fr")

    if not document_ids:
        return {
            "error": "No document IDs provided. Please specify which files to translate."
        }

    if len(document_ids) > 20:
        return {"error": "Maximum of 20 files can be translated at once."}

    if target_language not in ("fr", "en"):
        return {
            "error": f"Unsupported target language: {target_language}. Use 'fr' for French or 'en' for English."
        }

    # Get the bot message ID from contextvars (set in responses.py)
    request_context = get_contextvars()
    message_id = request_context.get("message_next_id")
    user_id = request_context.get("user_id")
    cost_group_id = request_context.get("cost_group_id")
    if not message_id:
        return {"error": "No message context available for file attachment."}

    @sync_to_async
    def validate_and_submit_translation_tasks():
        """Validate documents and submit all translation tasks in parallel."""
        submitted = []  # List of (doc_id, filename, task) tuples
        errors = []

        for doc_id in document_ids:
            try:
                doc = Document.objects.get(id=doc_id)

                # Check if user has access to this document's library
                library = doc.data_source.library if doc.data_source else None
                if library:
                    can_access = (
                        library.is_public
                        or library.created_by == user
                        or library.user_roles.filter(user=user).exists()
                    )
                    if not can_access:
                        errors.append(
                            {
                                "document_id": doc_id,
                                "filename": doc.filename,
                                "status": "error",
                                "error": "You don't have permission to access this document.",
                            }
                        )
                        continue

                # Get the file path
                if doc.saved_file and doc.saved_file.file:
                    file_path = doc.saved_file.file.path
                else:
                    errors.append(
                        {
                            "document_id": doc_id,
                            "filename": doc.filename,
                            "status": "error",
                            "error": "Document file not found.",
                        }
                    )
                    continue

                # Check if file type is supported for translation
                _, ext = os.path.splitext(doc.filename)
                supported_extensions = {
                    ".pdf",
                    ".docx",
                    ".doc",
                    ".pptx",
                    ".ppt",
                    ".xlsx",
                    ".xls",
                    ".txt",
                    ".html",
                    ".htm",
                    ".rtf",
                    ".odt",
                    ".odp",
                    ".ods",
                }
                if ext.lower() not in supported_extensions:
                    errors.append(
                        {
                            "document_id": doc_id,
                            "filename": doc.filename,
                            "status": "error",
                            "error": f"File type {ext} is not supported for translation.",
                        }
                    )
                    continue

                # Submit the translation task (non-blocking)
                chat_id = str(context.chat.id) if context.chat else ""
                task = translate_file_next.apply_async(
                    args=[file_path, target_language, str(message_id), chat_id],
                    kwargs={
                        **({"user_id": user_id} if user_id is not None else {}),
                        **(
                            {"cost_group_id": cost_group_id}
                            if cost_group_id is not None
                            else {}
                        ),
                    },
                    priority=5,
                )
                submitted.append((doc_id, doc.filename, task))

            except Document.DoesNotExist:
                errors.append(
                    {
                        "document_id": doc_id,
                        "status": "error",
                        "error": f"Document with ID {doc_id} not found.",
                    }
                )
            except Exception as e:
                logger.exception(f"Error processing document {doc_id}")
                errors.append(
                    {"document_id": doc_id, "status": "error", "error": str(e)}
                )

        # Store pending_tasks in message.details for frontend polling
        if submitted and message_id:
            try:
                from chat_next.models import Message as Msg

                msg = Msg.objects.get(id=message_id)
                msg.details["pending_tasks"] = [
                    {
                        "task_id": t.id,
                        "label": f"Translating: {fn}",
                    }
                    for _, fn, t in submitted
                ]
                msg.save(update_fields=["details"])
            except Exception:
                pass

        return submitted, errors

    submitted, errors = await validate_and_submit_translation_tasks()

    # Poll all tasks until complete
    import asyncio

    from celery.result import AsyncResult

    results = list(errors)
    pending = {
        task.id: (doc_id, filename, task) for doc_id, filename, task in submitted
    }
    total = len(submitted)
    completed_count = 0

    while pending:
        await asyncio.sleep(2)
        done_ids = []
        for task_id, (doc_id, filename, task) in pending.items():
            result = AsyncResult(task_id)
            if result.ready():
                done_ids.append(task_id)
                completed_count += 1
                try:
                    task_result = result.get(timeout=10)
                    if task_result.get("success"):
                        results.append(
                            {
                                "document_id": doc_id,
                                "filename": filename,
                                "status": "completed",
                                "output_filename": task_result.get("filename"),
                                "output_document_id": task_result.get("document_id"),
                                "target_language": target_language,
                            }
                        )
                    else:
                        results.append(
                            {
                                "document_id": doc_id,
                                "filename": filename,
                                "status": "error",
                                "error": task_result.get("error", "Unknown error"),
                            }
                        )
                except Exception as e:
                    results.append(
                        {
                            "document_id": doc_id,
                            "filename": filename,
                            "status": "error",
                            "error": f"Translation failed: {e}",
                        }
                    )
                logger.info(
                    "Translation progress",
                    completed=completed_count,
                    total=total,
                    filename=filename,
                )
        for task_id in done_ids:
            del pending[task_id]

    completed = [r for r in results if r.get("status") == "completed"]
    if not completed:
        return {
            "success": False,
            "message": "No files could be translated.",
            "files": results,
        }

    language_name = "French" if target_language == "fr" else "English"
    return {
        "success": True,
        "message": f"Translated {len(completed)} file(s) to {language_name}.",
        "files": results,
    }


# Register File Translation tool

TOOL_REGISTRY.register(
    OttoTool(
        name="translate_files",
        description=(
            "Translate document files using Azure Document Translation API."
            # "Supports PDF, Word, PowerPoint, Excel, and text files. "
            # "Use list_documents or rag_search first to identify which documents to translate. "
            # "Returns the translated filenames and document IDs so you can reference them in subsequent tool calls."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of document IDs to translate (from list_documents or search results).",
                    "maximum": 20,
                },
                "target_language": {
                    "type": "string",
                    "enum": ["fr", "en"],
                    "description": "",
                },
            },
            "required": ["document_ids", "target_language"],
            "additionalProperties": False,
        },
        execute=translate_files,
        category=TOOL_CATEGORY_TRANSLATION,
        requires_user=True,
        requires_chat=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        requires_approval=False,
        approval_label=_("File translation"),
        estimate_cost=estimate_translation_cost,
    )
)
