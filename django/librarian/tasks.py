import csv
import json
import mimetypes
import os
import tempfile
import traceback
import urllib.parse
import uuid
from datetime import datetime
from typing import List, Optional

from django.conf import settings
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from celery import current_task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import bind_contextvars, unbind_contextvars

from otto.priorities import LOW, increase_priority
from otto.utils.common import get_temp_dir

from chat.llm import OttoLLM
from librarian.cache import (
    clear_embedding_progress,
    get_azure_operation_location,
    set_azure_operation_location,
    set_celery_task_id,
    set_pending_embedding_chunks,
)
from librarian.models import Document, SavedFile
from librarian.utils.cancel_check import CancelledError, check_cancel
from librarian.utils.derivatives import (
    DEFAULT_DERIVATION_VERSION,
    DERIVATION_AZURE_OCR_PDF,
    DERIVATION_DOC_TO_DOCX,
    DERIVATION_IMAGE_TO_JPEG,
    get_cached_derived_saved_file,
    record_saved_file_derivative,
)
from librarian.utils.office import (
    LEGACY_WORD_MIME_TYPES,
    WORDPROCESSINGML_DOCUMENT_MIME,
    convert_legacy_word_to_docx,
)
from librarian.utils.process_document import save_content_to_saved_file
from librarian.utils.process_engine import (
    create_nodes,
    extract_html_metadata,
    extract_markdown,
    fetch_from_url,
    get_azure_document_ai_result_pdf,
    get_process_engine_from_type,
    guess_content_type,
    split_markdown_into_chunks,
)

logger = get_logger(__name__)


def _bind_librarian_context(*, document_id=None, user_id=None, cost_group_id=None):
    """Bind librarian cost/logging context and clear unrelated attribution IDs."""
    try:
        unbind_contextvars("message_id", "message_next_id", "law_id")
    except Exception:
        pass

    bind_contextvars(
        feature="librarian",
        document_id=document_id,
        user_id=user_id,
        cost_group_id=cost_group_id,
    )


# Image MIME types natively supported by GPT vision (OpenAI)
# Any image outside this set should be converted to PNG before storage
GPT_SUPPORTED_IMAGE_TYPES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"}
)
ZIP_CONTENT_TYPES = frozenset({"application/x-zip-compressed", "application/zip"})

one_hour = 60 * 60
twenty_minutes = 20 * 60
ten_minutes = 10 * 60
one_minute = 60
EMBED_NON_ADVANCING_REQUEUE_LIMIT = getattr(
    settings, "EMBED_NON_ADVANCING_REQUEUE_LIMIT", 3
)

# Default log path, can be overridden in settings
EMBED_LOG_PATH = getattr(
    settings,
    "LIBRARIAN_EMBEDDING_LOG_PATH",
    os.path.join(settings.MEDIA_ROOT, "librarian_embedding_log.csv"),
)


def _ensure_embed_log_headers():
    """
    Ensure the embeddings timing log file exists with headers.
    Writes headers only when settings.DEBUG is True.
    """
    if not getattr(settings, "DEBUG", False):
        return
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(EMBED_LOG_PATH), exist_ok=True)
        file_exists = os.path.exists(EMBED_LOG_PATH)
        # Write header if file doesn't exist or is empty
        if not file_exists or os.stat(EMBED_LOG_PATH).st_size == 0:
            with open(EMBED_LOG_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["batch_size", "seconds", "error_code", "retry_after"])
    except Exception as e:
        logger.warning(f"Could not initialize embedding log headers: {e}")


def _log_embed_attempt(
    batch_size: int,
    seconds: float,
    error_code: Optional[str],
    retry_after: Optional[float],
):
    """
    Append a row to the embeddings timing log file.
    Writes only when settings.DEBUG is True.
    """
    if not getattr(settings, "DEBUG", False):
        return
    try:
        with open(EMBED_LOG_PATH, "a", newline="") as f:
            writer = csv.writer(f)
            # Convert None to empty string for CSV
            writer.writerow(
                [
                    batch_size,
                    f"{seconds:.3f}",
                    "" if error_code is None else str(error_code),
                    "" if retry_after is None else f"{retry_after:.3f}",
                ]
            )
    except Exception as e:
        logger.warning(f"Could not write embedding log row: {e}")


# Ensure log headers at module import (only in DEBUG)
_ensure_embed_log_headers()


@shared_task(soft_time_limit=ten_minutes)
def delete_documents_from_vector_store(
    document_uuids: List[str], library_uuid: str
) -> None:
    logger.info(f"Deleting documents from vector store:\n{document_uuids}")
    for document_uuid in document_uuids:
        _delete_document_vectors(document_uuid=document_uuid, library_uuid=library_uuid)


def _delete_document_vectors(document_uuid: str, library_uuid: str) -> None:
    llm = OttoLLM()
    try:
        idx = llm.get_index(library_uuid)
        idx.delete_ref_doc(document_uuid, delete_from_docstore=True)
    except Exception as e:
        logger.error(f"Failed to remove documents from vector store: {e}")


def _should_pause_large_document_embedding(chunk_count: int) -> bool:
    from otto.models import OttoStatus

    return OttoStatus.objects.singleton().should_pause_librarian_embedding(chunk_count)


def _pause_large_document_embedding(document: Document, chunks: list[str]) -> dict:
    from otto.models import OttoStatus

    chunk_count = len(chunks)
    otto_status = OttoStatus.objects.singleton()
    threshold = otto_status.librarian_auto_embed_max_chunks

    _delete_document_vectors(
        document_uuid=document.uuid_hex,
        library_uuid=document.data_source.library.uuid_hex,
    )
    clear_embedding_progress(document.id)
    set_pending_embedding_chunks(document.id, chunks)
    set_celery_task_id(document.id, None)

    document.status = "PAUSED"
    document.num_chunks = chunk_count
    document.status_details = _(
        "Embedding paused for manual approval because this document has %(chunk_count)s chunks, above the auto-embed limit of %(threshold)s."
    ) % {
        "chunk_count": chunk_count,
        "threshold": threshold,
    }
    document.save(update_fields=["status", "num_chunks", "status_details"])

    logger.info(
        "Large document paused before embedding",
        document_id=document.id,
        chunk_count=chunk_count,
        threshold=threshold,
    )

    return {
        "ok": True,
        "document_id": document.id,
        "status": "PAUSED",
        "num_chunks": chunk_count,
        "paused_for_manual_embedding": True,
    }


@shared_task(bind=True, soft_time_limit=one_hour, queue=settings.HEAVY_QUEUE)
def process_document(
    self,
    document_id,
    language=None,
    pdf_method="default",
    mock_embedding=False,
    priority=LOW,
    finalization_priority=None,
    user_id=None,
    cost_group_id=None,
    refresh_from_url=False,
):
    """
    Heavy task: fetch/read content, detect type, extract markdown and chunks.
    Schedules finalize on light queue upon success.
    """
    if language is None:
        language = translation.get_language()

    task_id = self.request.id if self else None

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found", document_id=document_id)
        return

    try:
        # Bind user and cost_group for cost attribution
        _bind_librarian_context(
            document_id=document.id,
            user_id=user_id,
            cost_group_id=cost_group_id,
        )

        # Check cancellation at the start
        check_cancel(task_id, document_id)

        with translation.override(language):
            # Fetch/read and detect
            url = document.url
            if url and current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={"status_text": "Fetching URL..."},
                )
            if not url and current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={"status_text": "Reading file..."},
                )

            check_cancel(task_id, document_id)
            content, content_type, base_url = _fetch_and_detect(
                document, refresh_from_url=bool(refresh_from_url)
            )

            if (content_type or "").lower() in LEGACY_WORD_MIME_TYPES:
                if current_task:
                    current_task.update_state(
                        state="PROCESSING",
                        meta={"status_text": _("Converting .doc to .docx...")},
                    )
                content, content_type = _convert_and_replace_legacy_word_document(
                    document, content
                )

            is_zip_container = _is_zip_container(
                document, detected_content_type=content_type
            )

            # Convert non-GPT-supported image formats (e.g. TIF, BMP) to PNG.
            # Only for file-based documents (URL documents have no SavedFile to replace).
            if (
                "image/" in content_type
                and content_type not in GPT_SUPPORTED_IMAGE_TYPES
                and document.saved_file is not None
            ):
                content = _convert_and_replace_image_for_gpt(document, content)
                content_type = "image/jpeg"

            if current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={
                        "status_text": "Extracting text...",
                    },
                )

            # Try extraction - may return early if Document Intelligence is needed
            check_cancel(task_id, document_id)
            extraction_result = _extract_and_persist(
                document,
                content,
                content_type,
                pdf_method,
                base_url,
                task_id,
                document_id,
            )

            # If ZIP file, we already extracted and created children
            # Mark as container so it won't be added to vector DB for RAG
            if is_zip_container:
                check_cancel(task_id, document_id)
                document.is_container = True
                document.save(update_fields=["is_container"])

            # Check if Document Intelligence is needed
            if extraction_result.needs_azure:
                # Generate hash of content for verification later
                import hashlib

                content_hash = hashlib.sha256(content).hexdigest()

                next_priority = increase_priority(priority)
                # Chain to light worker for Azure polling
                res = submit_and_poll_azure_document_ai.apply_async(
                    kwargs={
                        "document_id": document_id,
                        "model": extraction_result.azure_model,
                        "content_hash": content_hash,
                        "pdf_method": extraction_result.pdf_method,
                        "priority": next_priority,
                        "finalization_priority": finalization_priority,
                        "user_id": user_id,
                        "cost_group_id": cost_group_id,
                        "refresh_from_url": refresh_from_url,
                    },
                    priority=next_priority,
                )

                # Update celery_task_id so UI polling continues
                set_celery_task_id(document.id, res.id)
                res.backend = None  # Prevent BlockingSwitchOutError in gevent

                return {"ok": True, "needs_azure": True, "chained_to": res.id}

            # If this is a container document (ZIP, etc.), skip vector store and finalize
            if document.is_container:
                document.status = "SUCCESS"
                set_celery_task_id(document.id, None)
                document.save(update_fields=["status"])
                return {
                    "document_id": document_id,
                    "status": "SUCCESS",
                    "skipped_vector_store": True,
                }

            if _should_pause_large_document_embedding(len(extraction_result.chunks)):
                return _pause_large_document_embedding(
                    document, extraction_result.chunks
                )

            document.status = "TEXT_EXTRACTED"
            document.status_details = None
            document.save(update_fields=["status", "status_details"])

            next_priority = (
                finalization_priority if finalization_priority is not None else priority
            )
            # Normal path: Schedule finalize on light queue with chunks (JSON-serializable)
            # This will now run in the background for summarize mode.
            res = finalize_document_light.apply_async(
                kwargs={
                    "document_id": document_id,
                    "chunks": extraction_result.chunks,
                    "mock_embedding": mock_embedding,
                    "user_id": user_id,
                    "cost_group_id": cost_group_id,
                },
                priority=next_priority,
            )

            # Update celery_task_id to new task so UI polling continues to show progress
            set_celery_task_id(document.id, res.id)
            res.backend = None  # Prevent BlockingSwitchOutError in gevent

            return {"ok": True, "num_chunks": len(extraction_result.chunks)}

    except CancelledError:
        logger.info("Document processing cancelled by user", document_id=document_id)
        set_celery_task_id(document.id, None)
        document.status = "BLOCKED"
        document.save(update_fields=["status"])
        return {"ok": False, "error": "cancelled"}
    except SoftTimeLimitExceeded as e:
        error_id = str(uuid.uuid4())[:7]
        full_error = traceback.format_exc()
        logger.error(
            "Document processing timed out",
            document_id=document_id,
            timeout_seconds=one_hour,
            error_id=error_id,
        )
        _handle_task_error(
            document,
            document_id,
            error_id,
            e,
            full_error,
            language,
        )
        return {"ok": False, "error": "timeout", "error_id": error_id}
    except Exception as e:
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            "Error during extraction",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        _handle_task_error(document, document_id, error_id, e, full_error, language)
        return {"ok": False, "error_id": error_id}


@shared_task(bind=True, queue=settings.EMBED_QUEUE)
def finalize_document_light(
    self,
    document_id,
    chunks,
    mock_embedding=False,
    start_index: int = 0,
    user_id=None,
    cost_group_id=None,
):
    """
    Light task: create nodes, replace vectors, and finalize document status.
    """
    from librarian.utils.batch_embedding import (
        BatchEmbeddingProgress,
        create_document_wrapper_with_cost_tracking,
        insert_nodes_with_checkpointing,
    )

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found in finalize", document_id=document_id)
        return

    try:
        # Bind context for cost objects and tracing
        try:
            _bind_librarian_context(
                document_id=document.id,
                user_id=user_id,
                cost_group_id=cost_group_id,
            )
        except Exception:
            pass

        task_id = self.request.id if self else None

        if _is_zip_container(document):
            document.is_container = True
            document.status = "SUCCESS"
            document.fetched_at = datetime.now()
            set_celery_task_id(document.id, None)
            document.save(update_fields=["is_container", "status", "fetched_at"])
            return {
                "document_id": document_id,
                "status": "SUCCESS",
                "skipped_vector_store": True,
            }

        llm = OttoLLM(
            mock_embedding=mock_embedding,
            priority=settings.DEFAULT_DOCUMENT_LLM_PRIORITY,
        )

        # Build nodes from chunks
        nodes = create_nodes(chunks, document)
        # Child chunks exclude the document node
        child_nodes = nodes[1:]
        total_children = len(child_nodes)
        document.num_chunks = max(0, total_children)
        document.save(update_fields=["num_chunks"])

        # If no child chunks, finalize immediately
        if total_children == 0:
            document.status = "SUCCESS"
            document.fetched_at = datetime.now()
            set_celery_task_id(document.id, None)
            document.save(update_fields=["status", "fetched_at"])
            llm.create_costs()
            library = document.data_source.library
            library.update_total_chunks()
            library.check_and_build_hnsw()
            return {"document_id": document_id, "status": "SUCCESS"}

        # Setup progress tracking
        progress_tracker = BatchEmbeddingProgress(
            f"document_{document.id}_embedding_progress"
        )

        progress = progress_tracker.load_or_initialize(total_children)
        # Infinite loop guard: track if next_index is stuck
        stuck_counter = int(progress.get("stuck_counter", 0) or 0)
        last_next_index = progress.get("last_next_index", None)
        current_next_index = int(progress.get("next_index", 0))
        # Respect stored next_index; start_index is a hint
        start_index = max(start_index or 0, current_next_index)
        if last_next_index == current_next_index:
            stuck_counter += 1
        else:
            stuck_counter = 0
        progress["stuck_counter"] = stuck_counter
        progress["last_next_index"] = current_next_index
        progress_tracker.set(progress)
        if stuck_counter >= EMBED_NON_ADVANCING_REQUEUE_LIMIT:
            progress_tracker.clear()
            set_celery_task_id(document.id, None)

            document.status = "BLOCKED"
            document.status_details = _(
                "Document embedding stopped after %(attempts)s retries without advancing past chunk %(chunk_index)s of %(total_chunks)s."
            ) % {
                "attempts": stuck_counter,
                "chunk_index": start_index,
                "total_chunks": total_children,
            }
            document.save(update_fields=["status", "status_details"])

            logger.error(
                "Infinite re-enqueue detected: next_index not advancing. Exiting to prevent loop.",
                document_id=document_id,
                stuck_counter=stuck_counter,
                next_index=current_next_index,
                total_children=total_children,
            )
            return {
                "document_id": document_id,
                "status": "BLOCKED",
                "error": "stuck_progress",
            }

        library_uuid = document.data_source.library.uuid_hex
        vector_store_index = llm.get_index(library_uuid)

        # Delete existing nodes once per session, only on first batch
        if not progress.get("delete_done", False) and start_index == 0:
            document_uuid = document.uuid_hex
            try:
                vector_store_index.delete_ref_doc(
                    document_uuid, delete_from_docstore=True
                )
            except Exception as e:
                logger.error(f"Failed to delete existing vectors: {e}")
            # Mark delete done for this session
            progress["delete_done"] = True
            progress_tracker.set(progress)

        # Prepare nodes to insert (just child nodes for tracking)
        session_id = progress["session_id"]
        nodes_to_insert = child_nodes

        # Create wrappers for the embedding utility
        def check_cancel_wrapper():
            check_cancel(task_id, document_id)

        def update_status(text):
            if current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={"status_text": text},
                )

        def requeue_task(
            next_index,
            *,
            countdown_seconds: float = 0,
            requeue_reason: str | None = None,
        ):
            current_priority = self.request.delivery_info.get("priority", LOW)
            apply_async_kwargs = {
                "kwargs": {
                    "document_id": document_id,
                    "chunks": chunks,
                    "mock_embedding": mock_embedding,
                    "start_index": next_index,
                    "user_id": user_id,
                    "cost_group_id": cost_group_id,
                },
                "priority": current_priority,
            }
            if countdown_seconds > 0:
                apply_async_kwargs["countdown"] = countdown_seconds

            logger.info(
                "Requeueing embed continuation",
                document_id=document_id,
                next_index=next_index,
                priority=current_priority,
                countdown_seconds=countdown_seconds,
                requeue_reason=requeue_reason,
            )

            res = finalize_document_light.apply_async(**apply_async_kwargs)
            set_celery_task_id(document.id, res.id)
            # Update the new task's state immediately to avoid status gap
            # Use the current task's backend to set initial state for new task
            if self.backend:
                try:
                    self.backend.store_result(
                        res.id,
                        {
                            "status_text": f"Adding to library... ({next_index}/{len(child_nodes)} - waiting)"
                        },
                        "PROCESSING",
                    )
                except Exception as e:
                    # Non-critical - task will update its own state when it starts
                    logger.debug("Failed to set initial task state", error=str(e))
            task_id = res.id
            res.backend = None  # Prevent BlockingSwitchOutError in gevent
            return task_id

        def log_batch(batch_size, seconds, error_code, retry_after):
            _log_embed_attempt(batch_size, seconds, error_code, retry_after)

        wrapped_index = create_document_wrapper_with_cost_tracking(
            vector_store_index, llm, nodes[0], child_nodes, session_id, document.id
        )

        # Use the batch embedding utility
        result = insert_nodes_with_checkpointing(
            nodes=nodes_to_insert,
            vector_store_index=wrapped_index,
            progress_tracker=progress_tracker,
            check_cancel_fn=check_cancel_wrapper,
            update_status_fn=update_status,
            requeue_fn=requeue_task,
            log_batch_fn=log_batch,
            start_index=start_index,
        )

        # Handle cancellation
        if not result.get("ok"):
            if result.get("error") == "cancelled":
                BatchEmbeddingProgress(
                    f"document_{document.id}_embedding_progress"
                ).clear()
                set_celery_task_id(document.id, None)
                document.status = "BLOCKED"
                document.save(update_fields=["status"])
            return result

        # Handle requeue
        if result.get("requeued"):
            return result

        # Success! Finalize
        document.status = "SUCCESS"
        document.fetched_at = datetime.now()
        set_celery_task_id(document.id, None)
        document.save(update_fields=["status", "fetched_at"])

        # Update library total_chunks and check if HNSW build should be triggered
        library = document.data_source.library
        library.update_total_chunks()
        library.check_and_build_hnsw()

        return {"document_id": document_id, "status": "SUCCESS"}

    except CancelledError:
        logger.info("Embedding cancelled by user", document_id=document_id)
        BatchEmbeddingProgress(f"document_{document.id}_embedding_progress").clear()
        set_celery_task_id(document.id, None)
        document.status = "BLOCKED"
        document.save(update_fields=["status"])
        return {"ok": False, "error": "cancelled"}
    except Exception as e:
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        BatchEmbeddingProgress(f"document_{document.id}_embedding_progress").clear()
        logger.error(
            "Error during finalize",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        _handle_task_error(
            document, document_id, error_id, e, full_error, language=None
        )
        return {"ok": False, "error_id": error_id}


@shared_task(soft_time_limit=ten_minutes, queue=settings.LIGHT_QUEUE)
def submit_and_poll_azure_document_ai(
    document_id: int,
    model: str,
    content_hash: str,
    pdf_method: str,
    priority=LOW,
    finalization_priority=None,
    user_id=None,
    cost_group_id=None,
    refresh_from_url: bool = False,
):
    """
    Light task: Submit content to Azure Document AI, poll for completion.
    When complete, chains to parse_azure_response_and_continue on heavy queue.

    Args:
        document_id: The document ID
        model: Either "prebuilt-layout" or "prebuilt-read"
        content_hash: Hash of the content to verify integrity
        pdf_method: The PDF extraction method being used
    """
    from librarian.utils.process_engine import (
        poll_azure_document_ai,
        submit_azure_document_ai,
    )

    # Bind user and cost_group for cost attribution
    _bind_librarian_context(
        document_id=document_id,
        user_id=user_id,
        cost_group_id=cost_group_id,
    )

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found in Azure polling", document_id=document_id)
        return

    try:
        if current_task:
            current_task.update_state(
                state="PROCESSING",
                meta={"status_text": "Submitting to Azure Document Intelligence..."},
            )

        source_saved_file_id = document.saved_file_id
        cached_searchable_pdf_saved_file_id = None
        request_searchable_pdf = False

        # Check if we already have an operation_location (in case of retry)
        operation_location = get_azure_operation_location(document.id)
        if not operation_location:
            # Reuse the same content loading path as the main processing task.
            # This prefers the cached SavedFile when present, but can fall back to
            # refetching URL content when there is no saved file or a refresh is requested.
            content, _content_type, _base_url = _fetch_and_detect(
                document,
                refresh_from_url=refresh_from_url,
            )

            # Verify hash matches
            import hashlib

            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != content_hash:
                raise ValueError(
                    "Content hash mismatch - content may have been modified"
                )

            source_saved_file_id = document.saved_file_id
            source_saved_file = document.saved_file if document.saved_file_id else None
            if source_saved_file and _should_persist_azure_ocr_pdf(
                document,
                model,
                source_saved_file=source_saved_file,
            ):
                cached_searchable_pdf = get_cached_derived_saved_file(
                    source_saved_file=source_saved_file,
                    derivation_type=DERIVATION_AZURE_OCR_PDF,
                    derivation_version=DEFAULT_DERIVATION_VERSION,
                    cache_params={"model": model},
                )
                if cached_searchable_pdf:
                    cached_searchable_pdf_saved_file_id = cached_searchable_pdf.id
                else:
                    request_searchable_pdf = True

            # Submit to Azure
            operation_location = submit_azure_document_ai(
                content,
                model,
                request_searchable_pdf=request_searchable_pdf,
            )

            # Store operation location
            set_azure_operation_location(document.id, operation_location)
        else:
            operation_location = get_azure_operation_location(document.id)

        if current_task:
            current_task.update_state(
                state="PROCESSING",
                meta={"status_text": "Waiting for Azure Document Intelligence..."},
            )

        # Poll for completion (this blocks but on light worker with gevent)
        result_json = poll_azure_document_ai(operation_location)

        # Save result to temp file to avoid passing large JSON through Celery
        temp_dir = get_temp_dir()
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json", dir=temp_dir, encoding="utf-8"
        ) as f:
            json.dump(result_json, f)
            result_file_path = f.name

        # Chain to heavy worker for parsing
        next_priority = increase_priority(priority)
        res = parse_azure_response_and_continue.apply_async(
            kwargs={
                "document_id": document_id,
                "result_file_path": result_file_path,
                "model": model,
                "pdf_method": pdf_method,
                "source_saved_file_id": source_saved_file_id,
                "cached_searchable_pdf_saved_file_id": cached_searchable_pdf_saved_file_id,
                "priority": next_priority,
                "finalization_priority": finalization_priority,
                "user_id": user_id,
                "cost_group_id": cost_group_id,
            },
            priority=next_priority,
        )

        # Update celery_task_id so UI polling continues
        set_celery_task_id(document.id, res.id)
        res.backend = None  # Prevent BlockingSwitchOutError in gevent

        return {"ok": True, "chained_to": res.id}

    except Exception as e:
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            "Error during Azure polling",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        _handle_task_error(
            document, document_id, error_id, e, full_error, language=None
        )
        return {"ok": False, "error_id": error_id}


@shared_task(soft_time_limit=ten_minutes, queue=settings.HEAVY_QUEUE)
def parse_azure_response_and_continue(
    document_id: int,
    result_file_path: str,
    model: str,
    pdf_method: str,
    source_saved_file_id: int | None = None,
    cached_searchable_pdf_saved_file_id: int | None = None,
    priority=LOW,
    finalization_priority=None,
    user_id=None,
    cost_group_id=None,
):
    """
    Heavy task: Parse Azure Document Intelligence response, create chunks, finalize.

    Args:
        document_id: The document ID
        result_file_path: Path to temporary file containing Azure Document Intelligence result JSON
        model: Either "prebuilt-layout" or "prebuilt-read"
        pdf_method: The PDF extraction method being used
    """
    import json
    import os

    from librarian.utils.process_engine import (
        _convert_html_to_markdown,
        parse_azure_layout_result,
        parse_azure_read_result,
    )

    # Bind user and cost_group for cost attribution
    _bind_librarian_context(
        document_id=document_id,
        user_id=user_id,
        cost_group_id=cost_group_id,
    )

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found in Azure parse", document_id=document_id)
        # Clean up temp file
        if os.path.exists(result_file_path):
            os.unlink(result_file_path)
        return

    try:
        if current_task:
            current_task.update_state(
                state="PROCESSING",
                meta={"status_text": "Parsing Azure response..."},
            )

        operation_location = get_azure_operation_location(document.id)

        # Load result from temp file
        with open(result_file_path, "r", encoding="utf-8") as f:
            result_json = json.load(f)

        # Delete temp file
        os.unlink(result_file_path)

        # Parse result based on model type
        if model == "prebuilt-layout":
            html = parse_azure_layout_result(result_json)
            md = _convert_html_to_markdown(html)
        elif model == "prebuilt-read":
            md = parse_azure_read_result(result_json)
        else:
            raise ValueError(f"Unknown Azure model: {model}")

        # Save extracted text
        document.extracted_text = md
        document.pdf_extraction_method = pdf_method
        document.status = "TEXT_EXTRACTED"
        update_fields = ["extracted_text", "pdf_extraction_method", "status"]
        document.save(update_fields=update_fields)

        source_saved_file = (
            SavedFile.objects.filter(id=source_saved_file_id).first()
            if source_saved_file_id
            else None
        )
        if source_saved_file and _should_persist_azure_ocr_pdf(
            document,
            model,
            source_saved_file=source_saved_file,
        ):
            source_filename = (
                document.original_filename or document.filename or "document.pdf"
            )
            if cached_searchable_pdf_saved_file_id:
                cached_searchable_pdf = SavedFile.objects.filter(
                    id=cached_searchable_pdf_saved_file_id
                ).first()
                if cached_searchable_pdf:
                    _attach_searchable_pdf_to_document(
                        document,
                        searchable_pdf_saved_file=cached_searchable_pdf,
                        source_saved_file=source_saved_file,
                        source_filename=source_filename,
                    )
            elif operation_location:
                searchable_pdf_bytes = get_azure_document_ai_result_pdf(
                    operation_location,
                    model,
                )
                if searchable_pdf_bytes:
                    searchable_pdf_saved_file, resolved_name, _sanitized_type = (
                        save_content_to_saved_file(
                            searchable_pdf_bytes,
                            filename=document.filename or source_filename,
                            content_type="application/pdf",
                        )
                    )
                    if not searchable_pdf_saved_file.content_type:
                        searchable_pdf_saved_file.content_type = "application/pdf"
                        searchable_pdf_saved_file.save(update_fields=["content_type"])
                    record_saved_file_derivative(
                        source_saved_file=source_saved_file,
                        derived_saved_file=searchable_pdf_saved_file,
                        derivation_type=DERIVATION_AZURE_OCR_PDF,
                        derivation_version=DEFAULT_DERIVATION_VERSION,
                        derivation_params={
                            "source_filename": source_filename,
                            "model": model,
                        },
                        cache_params={"model": model},
                    )
                    _attach_searchable_pdf_to_document(
                        document,
                        searchable_pdf_saved_file=searchable_pdf_saved_file,
                        source_saved_file=source_saved_file,
                        source_filename=source_filename,
                    )
                    if source_saved_file != searchable_pdf_saved_file:
                        source_saved_file.safe_delete()

        # Clean up intermediate state
        set_azure_operation_location(document.id, None)

        # If this is a container document (ZIP, etc.), skip vector store and finalize
        if document.is_container:
            document.status = "SUCCESS"
            set_celery_task_id(document.id, None)
            document.save(update_fields=["status"])
            return {
                "document_id": document_id,
                "status": "SUCCESS",
                "skipped_vector_store": True,
            }

        # Create chunks
        chunks = split_markdown_into_chunks(
            md,
            process_engine="PDF",
            pdf_method=pdf_method,
        )

        if _should_pause_large_document_embedding(len(chunks)):
            return _pause_large_document_embedding(document, chunks)

        next_priority = (
            finalization_priority if finalization_priority is not None else priority
        )
        # Chain to finalize on light queue
        res = finalize_document_light.apply_async(
            kwargs={
                "document_id": document_id,
                "chunks": chunks,
                "mock_embedding": False,
                "user_id": user_id,
                "cost_group_id": cost_group_id,
            },
            priority=next_priority,
        )

        # Update celery_task_id so UI polling continues
        set_celery_task_id(document.id, res.id)
        res.backend = None  # Prevent BlockingSwitchOutError in gevent

        return {"ok": True, "num_chunks": len(chunks), "chained_to": res.id}

    except Exception as e:
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            "Error parsing Azure response",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        # Clean up temp file
        if os.path.exists(result_file_path):
            os.unlink(result_file_path)

        _handle_task_error(
            document, document_id, error_id, e, full_error, language=None
        )
        return {"ok": False, "error_id": error_id}


# ---- Shared helpers (non-task) ---------------------------------------------


def _handle_task_error(
    document,
    document_id,
    error_id,
    exception,
    full_error=None,
    language=None,
    **extra_fields,
):
    """
    Shared error handling for document processing tasks.

    Args:
        document: Document instance or None
        document_id: Document ID
        error_id: Unique error ID
        exception: The exception object that was raised
        full_error: Optional full traceback string (for DEBUG mode)
        language: User's language preference (e.g., 'en', 'fr'). If None, will try to infer from document/library.
        **extra_fields: Additional fields to update on the document
    """
    from otto.utils.common import generate_ai_error_summary

    try:
        document.refresh_from_db()
        document.status = "ERROR"

        if settings.DEBUG:
            # In debug mode, show full traceback
            error_text = full_error if full_error else traceback.format_exc()
            document.status_details = error_text + f" ({_('Error ID')}: {error_id})"
        else:
            # In production, use AI to generate user-friendly error message
            # Try to infer language if not provided
            if language is None:
                # Try to get language from the library creator's preferences
                if (
                    document.data_source
                    and document.data_source.library
                    and document.data_source.library.created_by
                ):
                    user = document.data_source.library.created_by
                    # Check if user has UserOptions with language preference
                    try:
                        from otto.models import UserOptions

                        user_options = UserOptions.objects.get(user=user)
                        language = user_options.language
                    except UserOptions.DoesNotExist:
                        language = "en"
                else:
                    language = "en"

            # Use translation context to ensure AI generates message in correct language
            with translation.override(language):
                ai_summary = generate_ai_error_summary(
                    exception, error_id, include_trace=False, plain_text=True
                )
                # Convert line breaks to spaces for inline display compatibility
                ai_summary = ai_summary.replace("\n\n", " ").replace("\n", " ")
                document.status_details = ai_summary

        set_celery_task_id(document.id, None)

        update_fields = ["status", "status_details"]

        # Clean up Azure intermediate state if present
        set_azure_operation_location(document.id, None)

        # Add any extra fields to update
        for field, value in extra_fields.items():
            setattr(document, field, value)
            if field not in update_fields:
                update_fields.append(field)

        document.save(update_fields=update_fields)
    except Document.DoesNotExist:
        # Document was deleted mid-processing - just clean up cache and return
        logger.info(
            "Document was deleted during processing, skipping error save",
            document_id=document_id,
            error_id=error_id,
        )
        set_celery_task_id(document_id, None)
        set_azure_operation_location(document_id, None)


def _convert_and_replace_image_for_gpt(document: Document, content: bytes) -> bytes:
    """
    Convert a non-GPT-supported image (e.g. TIF, BMP) to a reasonably-sized JPEG.

    Replaces document.saved_file with a SavedFile pointing to the converted JPEG.
    Deduplicates by hash: if a SavedFile with the same JPEG hash already exists it
    is reused rather than a new one being created (mirrors the process_file pattern).
    The original SavedFile (and its underlying file) is released via safe_delete(),
    which only physically removes it when no other objects still reference it.

    Returns the JPEG bytes so the caller can continue processing with the new content.
    Only call this when document.saved_file is set (i.e. file-based documents).
    """
    from io import BytesIO

    from django.core.files.base import ContentFile

    from PIL import Image, ImageSequence

    old_filename = document.filename or "image"
    stem = old_filename.rsplit(".", 1)[0] if "." in old_filename else old_filename
    new_filename = stem + ".jpg"
    old_saved_file = document.saved_file

    if old_saved_file is None:
        raise ValueError("Image conversion requires an existing SavedFile")

    _ensure_document_original_file(
        document,
        source_saved_file=old_saved_file,
        source_filename=old_filename,
    )

    derivative_params = {
        "source_filename": old_filename,
        "target_content_type": "image/jpeg",
        "min_side": 512,
        "quality": 95,
    }
    cache_params = {
        "target_content_type": "image/jpeg",
        "min_side": 512,
        "quality": 95,
    }
    cached_saved_file = get_cached_derived_saved_file(
        source_saved_file=old_saved_file,
        derivation_type=DERIVATION_IMAGE_TO_JPEG,
        derivation_version=DEFAULT_DERIVATION_VERSION,
        cache_params=cache_params,
    )
    if cached_saved_file:
        jpg_bytes = _read_saved_file_content(cached_saved_file)
        _set_document_current_file(
            document,
            saved_file=cached_saved_file,
            filename=new_filename,
            content_type="image/jpeg",
        )
        if not document.extracted_title and not document.manual_title:
            document.extracted_title = old_filename
            document.save(update_fields=["extracted_title"])
        if old_saved_file != cached_saved_file:
            old_saved_file.safe_delete()
        logger.info(
            "Reused cached JPEG derivative for GPT vision compatibility",
            document_id=document.id,
            old_filename=old_filename,
            new_filename=new_filename,
            cached_saved_file_id=cached_saved_file.id,
        )
        return jpg_bytes

    with Image.open(BytesIO(content)) as img:
        # For multi-frame images (e.g. multi-page TIF), use the first frame only.
        # Multi-page TIFs are rare as uploads; Azure OCR will receive a single-frame JPEG.
        frames = list(ImageSequence.Iterator(img))
        if len(frames) > 1:
            logger.warning(
                "Multi-frame image converted to single-frame JPEG; "
                "only first frame retained",
                document_id=document.id,
                filename=old_filename,
                frames=len(frames),
            )
        first_frame = frames[0].copy() if frames else img.copy()
        rgb_img = first_frame.convert("RGB")

        # If the image is undersized (shortest side < 512 px), double it using
        # nearest-neighbour resampling to avoid interpolation artefacts.
        # No downscaling is applied; images are passed at their native resolution.
        from PIL.Image import Resampling

        min_side = 512
        while min(rgb_img.size) < min_side:
            rgb_img = rgb_img.resize(
                (rgb_img.width * 2, rgb_img.height * 2), Resampling.NEAREST
            )

        buffer = BytesIO()
        # JPEG (quality=95) is GPT-supported and far smaller than lossless PNG
        # for typical scanned/photographic content (the main source of TIF uploads).
        rgb_img.save(buffer, format="JPEG", quality=95, optimize=True)
        jpg_bytes = buffer.getvalue()

    # Deduplicate by hash, exactly as process_file does for incoming uploads.
    # Two documents that shared the same TIF SavedFile will each convert to an
    # identical JPEG; reusing the existing SavedFile keeps the 1-file-per-hash
    # invariant intact and avoids leaving orphaned duplicates on storage.
    from librarian.utils.process_engine import generate_hash

    jpg_hash = generate_hash(BytesIO(jpg_bytes))
    existing = SavedFile.objects.filter(sha256_hash=jpg_hash).first()
    if existing:
        new_saved_file = existing
        logger.info(
            "Reusing existing SavedFile for converted JPEG",
            document_id=document.id,
            saved_file_id=existing.id,
        )
    else:
        new_saved_file = SavedFile.objects.create(content_type="image/jpeg")
        new_saved_file.file.save(new_filename, ContentFile(jpg_bytes))
        new_saved_file.sha256_hash = jpg_hash
        new_saved_file.save(update_fields=["sha256_hash"])
        record_saved_file_derivative(
            source_saved_file=old_saved_file,
            derived_saved_file=new_saved_file,
            derivation_type=DERIVATION_IMAGE_TO_JPEG,
            derivation_version=DEFAULT_DERIVATION_VERSION,
            derivation_params=derivative_params,
            cache_params=cache_params,
        )

    _set_document_current_file(
        document,
        saved_file=new_saved_file,
        filename=new_filename,
        content_type="image/jpeg",
    )
    # Preserve the original filename as the document title so users can track provenance.
    # Only set if no title has been assigned yet (manual title takes precedence).
    if not document.extracted_title and not document.manual_title:
        document.extracted_title = old_filename
        document.save(update_fields=["extracted_title"])

    if old_saved_file is not None:
        old_saved_file.safe_delete()

    logger.info(
        "Converted image to JPEG for GPT vision compatibility",
        document_id=document.id,
        old_filename=old_filename,
        new_filename=new_filename,
        old_size_bytes=len(content),
        new_size_bytes=len(jpg_bytes),
    )

    return jpg_bytes


def _convert_and_replace_legacy_word_document(
    document: Document, content: bytes
) -> tuple[bytes, str]:
    """Convert a legacy Word document to DOCX and update the stored SavedFile."""
    old_filename = document.filename or "document.doc"
    stem = old_filename.rsplit(".", 1)[0] if "." in old_filename else old_filename
    new_filename = stem + ".docx"

    old_saved_file = document.saved_file
    if old_saved_file is None:
        raise ValueError("Legacy Word conversion requires an existing SavedFile")

    _ensure_document_original_file(
        document,
        source_saved_file=old_saved_file,
        source_filename=old_filename,
    )

    derivative_params = {
        "source_filename": old_filename,
        "target_content_type": WORDPROCESSINGML_DOCUMENT_MIME,
    }
    cache_params = {"target_content_type": WORDPROCESSINGML_DOCUMENT_MIME}
    cached_saved_file = get_cached_derived_saved_file(
        source_saved_file=old_saved_file,
        derivation_type=DERIVATION_DOC_TO_DOCX,
        derivation_version=DEFAULT_DERIVATION_VERSION,
        cache_params=cache_params,
    )
    if cached_saved_file:
        docx_bytes = _read_saved_file_content(cached_saved_file)
        _set_document_current_file(
            document,
            saved_file=cached_saved_file,
            filename=new_filename,
            content_type=WORDPROCESSINGML_DOCUMENT_MIME,
        )
        if not document.manual_title:
            document.manual_title = old_filename
            document.save(update_fields=["manual_title"])
        if old_saved_file != cached_saved_file:
            old_saved_file.safe_delete()
        logger.info(
            "Reused cached DOCX derivative for legacy Word document",
            document_id=document.id,
            old_filename=old_filename,
            new_filename=new_filename,
            cached_saved_file_id=cached_saved_file.id,
        )
        return docx_bytes, WORDPROCESSINGML_DOCUMENT_MIME

    docx_bytes = convert_legacy_word_to_docx(
        content,
        source_filename=old_filename,
    )

    new_saved_file, resolved_name, sanitized_type = save_content_to_saved_file(
        docx_bytes,
        filename=new_filename,
        content_type=WORDPROCESSINGML_DOCUMENT_MIME,
    )
    if not new_saved_file.content_type:
        new_saved_file.content_type = WORDPROCESSINGML_DOCUMENT_MIME
        new_saved_file.save(update_fields=["content_type"])
    record_saved_file_derivative(
        source_saved_file=old_saved_file,
        derived_saved_file=new_saved_file,
        derivation_type=DERIVATION_DOC_TO_DOCX,
        derivation_version=DEFAULT_DERIVATION_VERSION,
        derivation_params=derivative_params,
        cache_params=cache_params,
    )

    _set_document_current_file(
        document,
        saved_file=new_saved_file,
        filename=resolved_name,
        content_type=sanitized_type or WORDPROCESSINGML_DOCUMENT_MIME,
    )
    update_fields = []
    if not document.manual_title:
        document.manual_title = old_filename
        update_fields.append("manual_title")
    if update_fields:
        document.save(update_fields=update_fields)

    if old_saved_file is not None and old_saved_file != new_saved_file:
        old_saved_file.safe_delete()

    logger.info(
        "Converted legacy Word document to DOCX for processing",
        document_id=document.id,
        old_filename=old_filename,
        new_filename=resolved_name,
        old_size_bytes=len(content),
        new_size_bytes=len(docx_bytes),
    )

    return docx_bytes, WORDPROCESSINGML_DOCUMENT_MIME


def _is_zip_container(
    document: Document, detected_content_type: Optional[str] = None
) -> bool:
    """Return True when the document should be treated as a ZIP container."""
    normalized_type = (detected_content_type or document.content_type or "").lower()
    filename = (document.filename or "").lower()
    return normalized_type in ZIP_CONTENT_TYPES or filename.endswith(".zip")


def _derive_url_filename(document: Document, url: str, content_type: str) -> str:
    """Choose a filename for URL-fetched content, falling back to document UUID."""
    parsed = urllib.parse.urlparse(url or "")
    path = urllib.parse.unquote(parsed.path or "")
    basename = os.path.basename(path.rstrip("/")) if path else ""
    if not basename:
        basename = document.uuid_hex

    extension = (mimetypes.guess_extension(content_type or "") or "").lower()
    if extension == ".jpe":
        extension = ".jpg"
    if extension and not basename.lower().endswith(extension):
        basename = f"{basename}{extension}"
    return basename or document.uuid_hex


def _read_saved_file_content(saved_file) -> bytes:
    """Read bytes from a SavedFile while preserving historical HTML handling."""
    with saved_file.file.open("rb") as f:
        if saved_file.content_type == "text/html":
            try:
                return b" ".join(line.decode().encode() for line in f.readlines())
            except UnicodeDecodeError:
                f.seek(0)
        return f.read()


def _ensure_document_original_file(
    document: Document,
    source_saved_file: SavedFile | None = None,
    source_filename: str | None = None,
) -> None:
    update_fields = []

    if source_saved_file and not document.original_saved_file_id:
        document.original_saved_file = source_saved_file
        update_fields.append("original_saved_file")

    if source_filename and not document.original_filename:
        document.original_filename = source_filename
        update_fields.append("original_filename")

    if update_fields:
        document.save(update_fields=update_fields)


def _set_document_current_file(
    document: Document,
    saved_file: SavedFile,
    filename: str,
    content_type: str | None = None,
) -> None:
    update_fields = []

    if document.saved_file_id != saved_file.id:
        document.saved_file = saved_file
        update_fields.append("saved_file")
    if filename and document.filename != filename:
        document.filename = filename
        update_fields.append("filename")
    if content_type and document.url and document.url_content_type != content_type:
        document.url_content_type = content_type
        update_fields.append("url_content_type")

    if update_fields:
        document.save(update_fields=update_fields)


def _should_persist_azure_ocr_pdf(
    document: Document,
    model: str,
    source_saved_file: SavedFile | None = None,
) -> bool:
    if model != "prebuilt-read":
        return False

    content_type = (
        (source_saved_file.content_type if source_saved_file else None)
        or document.content_type
        or ""
    ).lower()
    filename = (document.original_filename or document.filename or "").lower()
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _attach_searchable_pdf_to_document(
    document: Document,
    searchable_pdf_saved_file: SavedFile,
    source_saved_file: SavedFile,
    source_filename: str,
) -> None:
    _ensure_document_original_file(
        document,
        source_saved_file=source_saved_file,
        source_filename=source_filename,
    )
    _set_document_current_file(
        document,
        saved_file=searchable_pdf_saved_file,
        filename=document.filename or source_filename,
        content_type="application/pdf",
    )


def _fetch_and_detect(document: Document, refresh_from_url: bool = False):
    """
    Fetch content and detect content type.
    Returns (content, content_type, base_url).
    Persists url_content_type for URL documents.
    """
    url = document.url
    saved_file = document.saved_file
    if not (url or saved_file):
        raise ValueError("URL or file is required")

    base_url = None
    if url:
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme and parsed_url.netloc:
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    if saved_file and not refresh_from_url:
        logger.info("Processing file", document_id=document.id)
        content = _read_saved_file_content(saved_file)
        content_type = guess_content_type(
            content, saved_file.content_type, document.filename
        )
        return content, content_type, base_url

    logger.info("Processing URL", url=url)
    content, detected_type = fetch_from_url(url)
    content_type = guess_content_type(content, detected_type, document.url)
    previous_saved_file = document.saved_file if document.saved_file_id else None
    update_fields = ["url_content_type"]

    preferred_name = document.filename or _derive_url_filename(
        document, url, content_type
    )
    saved_file_obj, resolved_name, sanitized_type = save_content_to_saved_file(
        content,
        filename=preferred_name,
        content_type=content_type,
    )
    document.saved_file = saved_file_obj
    document.filename = resolved_name
    document.fetched_at = timezone.now()
    content_type = sanitized_type or content_type
    update_fields.extend(["saved_file", "filename", "fetched_at"])

    document.original_saved_file = saved_file_obj
    document.original_filename = resolved_name
    update_fields.extend(["original_saved_file", "original_filename"])

    document.url_content_type = content_type
    document.save(update_fields=update_fields)

    if previous_saved_file and previous_saved_file != saved_file_obj:
        previous_saved_file.safe_delete()

    return content, content_type, base_url


def _extract_and_persist(
    document: Document,
    content: bytes,
    content_type: str,
    pdf_method: str,
    base_url: str,
    task_id: str = None,
    document_id: int = None,
):
    """
    Run extraction and persist extracted_text/pdf_extraction_method and HTML metadata.
    Returns ExtractionResult.
    """
    process_engine = get_process_engine_from_type(content_type)
    if process_engine == "HTML":
        extracted_metadata = extract_html_metadata(content)
        for key, value in extracted_metadata.items():
            setattr(document, key, value)
        document.save(
            update_fields=["extracted_title", "extracted_modified_at"]
        )  # small write

    extraction_result = extract_markdown(
        content,
        process_engine,
        pdf_method=pdf_method,
        base_url=base_url,
        selector=document.selector,
        root_document_id=document.id,
        task_id=task_id,
        document_id=document_id,
        content_type=content_type,
    )

    document.extracted_text = extraction_result.markdown
    if document.content_type == "application/pdf":
        document.pdf_extraction_method = extraction_result.pdf_method
        document.save(update_fields=["extracted_text", "pdf_extraction_method"])
    else:
        document.save(update_fields=["extracted_text"])

    return extraction_result


@shared_task(soft_time_limit=one_hour, queue=settings.LIGHT_QUEUE)
def build_hnsw_index(library_uuid_hex: str) -> dict:
    """Build HNSW index for a library using CREATE INDEX CONCURRENTLY.

    This allows queries to continue working during index build.
    Build time estimates:
    - 50k chunks: ~5-10 minutes
    - 100k chunks: ~10-15 minutes
    - 600k chunks: ~30-40 minutes
    """
    from sqlalchemy import text

    from chat.llm import get_pg_engines
    from librarian.models import Library

    try:
        library = Library.objects.get(uuid_hex=library_uuid_hex)
        logger.info(
            "Starting HNSW index build",
            library_id=library.id,
            library_name=library.name,
            total_chunks=library.total_chunks,
        )

        # Update status to building
        library.hnsw_status = "building"
        library.save(update_fields=["hnsw_status"])

        # Get database connection
        pg_sync_engine, _ = get_pg_engines()

        # Check if index already exists
        table_name = f"data_{library_uuid_hex}"
        index_name = f"{table_name}_embedding_idx"

        # Log the database we're connecting to
        logger.info(
            "Database connection info",
            library_id=library.id,
            engine_url=str(pg_sync_engine.url).replace(
                pg_sync_engine.url.password or "", "***"
            ),
        )

        # First check if table and index exist
        with pg_sync_engine.connect() as conn:
            # Check if table exists
            table_check = conn.execute(
                text(
                    """
                    SELECT tablename FROM pg_tables 
                    WHERE schemaname = 'public' AND tablename = :table_name
                """
                ),
                {"table_name": table_name},
            )
            table_exists = table_check.fetchone()

            logger.info(
                "Table existence check",
                library_id=library.id,
                table_name=table_name,
                exists=bool(table_exists),
            )

            if not table_exists:
                logger.error(
                    "Vector table does not exist - library needs to be re-processed",
                    library_id=library.id,
                    table_name=table_name,
                    total_chunks=library.total_chunks,
                )
                library.hnsw_status = "error"
                library.total_chunks = 0  # Reset since vector data doesn't exist
                library.save(update_fields=["hnsw_status", "total_chunks"])
                return {
                    "status": "error",
                    "message": "Vector table does not exist. Library documents need to be re-processed.",
                    "library_uuid": library_uuid_hex,
                    "action_required": "Re-process library documents to recreate vector embeddings",
                }

            # Check if index exists
            result = conn.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes 
                    WHERE tablename = :table_name 
                    AND indexname = :index_name
                """
                ),
                {"table_name": table_name, "index_name": index_name},
            )
            existing_index = result.fetchone()

        if existing_index:
            logger.info(
                "HNSW index already exists, skipping build",
                library_id=library.id,
                index_name=index_name,
            )
            library.hnsw_status = "ready"
            library.save(update_fields=["hnsw_status"])
            return {
                "status": "already_exists",
                "library_uuid": library_uuid_hex,
                "index_name": index_name,
            }

        # Build HNSW index with CONCURRENTLY to avoid blocking queries
        # Parameters: M=16 (edges per node), ef_construction=256 (build-time accuracy)
        logger.info("Creating HNSW index", library_id=library.id)

        # Create new connection with autocommit for CREATE INDEX CONCURRENTLY
        with pg_sync_engine.execution_options(
            isolation_level="AUTOCOMMIT"
        ).connect() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE INDEX CONCURRENTLY {index_name}
                    ON {table_name}
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 256)
                """
                )
            )

            logger.info(
                "HNSW index build completed",
                library_id=library.id,
                index_name=index_name,
            )

        # Update status to ready
        library.hnsw_status = "ready"
        library.save(update_fields=["hnsw_status"])

        return {
            "status": "success",
            "library_uuid": library_uuid_hex,
            "index_name": index_name,
            "total_chunks": library.total_chunks,
        }

    except Library.DoesNotExist:
        logger.error("Library not found for HNSW build", library_uuid=library_uuid_hex)
        return {"status": "error", "message": "Library not found"}
    except Exception as e:
        logger.exception(
            "HNSW index build failed", library_uuid=library_uuid_hex, error=str(e)
        )
        try:
            library = Library.objects.get(uuid_hex=library_uuid_hex)
            library.hnsw_status = "error"
            library.save(update_fields=["hnsw_status"])
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


@shared_task(name="delete_hnsw_index")
def delete_hnsw_index(library_uuid_hex):
    """Delete the HNSW index for a library."""

    import structlog
    from sqlalchemy import text

    from chat.llm import get_pg_engines
    from librarian.models import Library

    logger = structlog.get_logger(__name__)

    try:
        library = Library.objects.get(uuid_hex=library_uuid_hex)
        logger.info(
            "Starting HNSW index deletion",
            library_id=library.id,
            library_name=library.name,
        )

        # Update status to deleting
        library.hnsw_status = "deleting"
        library.save(update_fields=["hnsw_status"])

        # Get database connection
        pg_sync_engine, _ = get_pg_engines()

        table_name = f"data_{library_uuid_hex}"
        index_name = f"{table_name}_embedding_idx"

        # Check if index exists before trying to delete
        with pg_sync_engine.connect() as conn:
            index_check = conn.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes 
                    WHERE schemaname = 'public' AND indexname = :index_name
                """
                ),
                {"index_name": index_name},
            )
            index_exists = index_check.fetchone()

            if index_exists:
                logger.info(
                    "Dropping HNSW index",
                    library_id=library.id,
                    index_name=index_name,
                )
                # Drop the index
                conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
                conn.commit()
                logger.info(
                    "HNSW index deleted successfully",
                    library_id=library.id,
                    index_name=index_name,
                )
            else:
                logger.info(
                    "HNSW index does not exist, nothing to delete",
                    library_id=library.id,
                    index_name=index_name,
                )

        # Update status to none
        library.hnsw_status = "none"
        library.hnsw_task_id = None
        library.save(update_fields=["hnsw_status", "hnsw_task_id"])

        return {
            "status": "success",
            "library_uuid": library_uuid_hex,
            "index_name": index_name,
        }

    except Library.DoesNotExist:
        logger.error(
            "Library not found for HNSW deletion", library_uuid=library_uuid_hex
        )
        return {"status": "error", "message": "Library not found"}
    except Exception as e:
        logger.exception(
            "HNSW index deletion failed", library_uuid=library_uuid_hex, error=str(e)
        )
        try:
            library = Library.objects.get(uuid_hex=library_uuid_hex)
            library.hnsw_status = "error"
            library.save(update_fields=["hnsw_status"])
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


# =============================================================================
# TIMING TEST HELPER - Use production finalize_document_light with test data
# =============================================================================
# Just use the existing librarian_embedding_log.csv which already logs all
# insert_nodes timing from finalize_document_light in DEBUG mode
