import os

from structlog import get_logger

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next.models import TOOL_CATEGORY_TRANSCRIPTION

logger = get_logger(__name__)


async def transcribe_files(arguments: dict, context: ToolContext) -> dict:
    """
    Transcribe audio/video files using Azure Batch Speech-to-Text API.

    This tool accepts document IDs from the user's uploaded files and submits
    them for transcription. The transcription happens asynchronously and the
    transcript files will appear attached to the bot message when complete.
    """
    from asgiref.sync import sync_to_async
    from structlog.contextvars import get_contextvars

    from librarian.models import Document

    from chat_next.tasks import transcribe_file_next

    user = context.user

    document_ids = arguments.get("document_ids", [])
    main_language = arguments.get("main_language", "en-CA")
    min_speakers = arguments.get("min_speakers", 1)
    max_speakers = arguments.get("max_speakers", 6)

    if not document_ids:
        return {
            "error": "No document IDs provided. Please specify which audio/video files to transcribe."
        }

    if len(document_ids) > 5:
        return {"error": "Maximum of 5 files can be transcribed at once."}

    supported_extensions = {
        ".wav",
        ".mp3",
        ".mp4",
        ".m4a",
        ".ogg",
        ".flac",
        ".wma",
        ".aac",
        ".webm",
        ".avi",
        ".mov",
        ".mkv",
    }

    # Get the bot message ID from contextvars
    request_context = get_contextvars()
    message_id = request_context.get("message_next_id")
    if not message_id:
        return {"error": "No message context available for file attachment."}

    @sync_to_async
    def validate_and_submit_transcription_tasks():
        """Validate documents and submit all transcription tasks in parallel."""
        submitted = []  # List of (doc_id, filename, task) tuples
        errors = []  # List of error result dicts

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

                # Check if file type is supported for transcription
                _, ext = os.path.splitext(doc.filename)
                if ext.lower() not in supported_extensions:
                    errors.append(
                        {
                            "document_id": doc_id,
                            "filename": doc.filename,
                            "status": "error",
                            "error": f"File type {ext} is not supported for transcription. "
                            f"Supported: {', '.join(sorted(supported_extensions))}",
                        }
                    )
                    continue

                # Submit the transcription task (non-blocking)
                chat_id = str(context.chat.id) if context.chat else ""
                task = transcribe_file_next.apply_async(
                    args=[
                        file_path,
                        str(message_id),
                        chat_id,
                        main_language,
                        min_speakers,
                        max_speakers,
                    ],
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
                logger.exception(
                    f"Error submitting document {doc_id} for transcription"
                )
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
                        "label": f"Transcribing: {fn}",
                    }
                    for _, fn, t in submitted
                ]
                msg.save(update_fields=["details"])
            except Exception:
                pass

        return submitted, errors

    submitted, errors = await validate_and_submit_transcription_tasks()

    # Poll all tasks until complete, with progress logging
    import asyncio
    import time

    results = list(errors)  # Start with validation errors
    pending = {
        task.id: (doc_id, filename, task) for doc_id, filename, task in submitted
    }
    total = len(submitted)
    completed_count = 0
    poll_start = time.monotonic()
    max_poll_seconds = (
        300  # Safety cap to avoid hanging forever if workers are unavailable
    )

    while pending:
        if time.monotonic() - poll_start > max_poll_seconds:
            for task_id, (doc_id, filename, _) in pending.items():
                results.append(
                    {
                        "document_id": doc_id,
                        "filename": filename,
                        "status": "error",
                        "error": (
                            "Processing timed out while waiting for background worker. "
                            "Please try again or verify Celery workers are running."
                        ),
                    }
                )
                logger.warning(
                    "Document processing timed out",
                    task_id=task_id,
                    filename=filename,
                    waited_seconds=max_poll_seconds,
                )
            pending.clear()
            break

        await asyncio.sleep(2)  # Check every 2 seconds
        done_ids = []
        for task_id, (doc_id, filename, task) in pending.items():
            is_ready = False
            # Prefer the task object returned by apply_async(). In tests this can be a
            # lightweight fake that has get() but no backend-ready state.
            if hasattr(task, "ready"):
                try:
                    is_ready = bool(task.ready())
                except Exception:
                    is_ready = False
            else:
                # If no readiness API exists, treat as immediately retrievable (test doubles).
                is_ready = True

            if is_ready:
                done_ids.append(task_id)
                completed_count += 1
                try:
                    task_result = task.get(timeout=10)
                    if task_result.get("success"):
                        results.append(
                            {
                                "document_id": doc_id,
                                "filename": filename,
                                "status": "completed",
                                "output_filename": task_result.get("filename"),
                                "output_document_id": task_result.get("document_id"),
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
                            "error": f"Transcription failed: {e}",
                        }
                    )
                logger.info(
                    "Transcription progress",
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
            "message": "No files could be transcribed.",
            "files": results,
        }

    return {
        "success": True,
        "message": f"Transcribed {len(completed)} file(s). "
        f"The transcript files have been attached to this message and indexed for Q&A.",
        "files": results,
    }


# Register Transcription tool

TOOL_REGISTRY.register(
    OttoTool(
        name="transcribe_files",
        description=(
            "Transcribe audio or video files using Azure Batch Speech-to-Text."
            # "Supports WAV, MP3, MP4, M4A, OGG, FLAC, WMA, AAC, WEBM, AVI, MOV, MKV files. "
            # "Use list_documents or rag_search first to identify which files to transcribe. "
            # "Produces a text transcript with timestamps and speaker diarization. "
            # "Returns the transcript filenames and document IDs so you can reference them in subsequent tool calls."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of document IDs of audio/video files to transcribe (from list_documents or search results).",
                    "maximum": 5,
                },
                "main_language": {
                    "type": "string",
                    "enum": ["en-CA", "fr-CA"],
                    "description": "Primary language of the audio.",
                    "default": "en-CA",
                },
                "min_speakers": {
                    "type": "integer",
                    "description": "Minimum number of speakers for diarization.",
                    "default": 1,
                },
                "max_speakers": {
                    "type": "integer",
                    "description": "Maximum number of speakers for diarization.",
                    "default": 6,
                },
            },
            "required": ["document_ids"],
            "additionalProperties": False,
        },
        execute=transcribe_files,
        category=TOOL_CATEGORY_TRANSCRIPTION,
        requires_user=True,
        requires_chat=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
    )
)
