"""
Reusable batch embedding utility with checkpointing, time-slicing, and retry logic.

This module provides a robust way to insert large numbers of nodes into a vector store
with support for:
- Checkpointing and resumption (via cache)
- Time-slicing to prevent worker monopolization
- 429-aware exponential backoff
- Progress tracking
- Cancellation checks
"""

import random
import time
import uuid
from typing import Callable, List, Optional

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError

from structlog import get_logger
from structlog.contextvars import bind_contextvars, unbind_contextvars

from chat.llm import _extract_status_and_retry_after

logger = get_logger(__name__)


def create_cost_tracking_wrapper(vector_store_index, llm):
    """
    Create a wrapper that tracks costs after each batch insertion.

    Args:
        vector_store_index: The vector store index to wrap
        llm: The LLM instance for cost tracking

    Returns:
        A wrapper object with insert_nodes method
    """

    class CostTrackingWrapper:
        def __init__(self, index, llm):
            self.index = index
            self.llm = llm

        def insert_nodes(self, batch_nodes):
            self.index.insert_nodes(batch_nodes)
            self.llm.create_costs()

    return CostTrackingWrapper(vector_store_index, llm)


def create_document_wrapper_with_cost_tracking(
    vector_store_index, llm, document_node, child_nodes, session_id, document_id
):
    """
    Create a wrapper for document embedding that:
    - Adds document node only on first batch
    - Binds proper request_id for cost tracking
    - Tracks costs after each batch

    This is specifically for librarian documents which have a document node
    that should only be included in the first batch.
    """

    class DocumentVectorStoreWrapper:
        def __init__(self, index, llm, doc_node, children, session_id, doc_id):
            self.index = index
            self.llm = llm
            self.document_node = doc_node
            self.child_nodes = children
            self.session_id = session_id
            self.document_id = doc_id
            self._batch_count = 0

        def insert_nodes(self, batch_nodes):
            """Insert nodes and bind proper request_id for cost tracking."""
            # Determine the actual start index for this batch
            if batch_nodes and batch_nodes[0] == self.document_node:
                # First batch with document node
                actual_start = 0
                actual_batch = batch_nodes
            else:
                # Find where this batch starts in child_nodes
                try:
                    actual_start = (
                        self.child_nodes.index(batch_nodes[0]) if batch_nodes else 0
                    )
                except (ValueError, IndexError):
                    actual_start = self._batch_count * settings.EMBEDDING_BATCH_SIZE
                # Add document node if this is the first batch
                if actual_start == 0:
                    actual_batch = [self.document_node] + batch_nodes
                else:
                    actual_batch = batch_nodes

            # Bind request_id for cost tracking
            batch_request_id = (
                f"embed:{self.document_id}:{self.session_id}:{actual_start}"
            )
            try:
                unbind_contextvars("message_id", "message_next_id", "law_id")
                bind_contextvars(request_id=batch_request_id)
            except Exception:
                pass

            # Insert and track costs
            self.index.insert_nodes(actual_batch)
            self.llm.create_costs()
            self._batch_count += 1

    return DocumentVectorStoreWrapper(
        vector_store_index, llm, document_node, child_nodes, session_id, document_id
    )


class BatchEmbeddingProgress:
    """Handles progress tracking for batch embedding operations."""

    def __init__(self, cache_key: str):
        self.cache_key = cache_key

    def get(self) -> Optional[dict]:
        """Get current progress state."""
        return cache.get(self.cache_key)

    def set(self, progress: dict):
        """Update progress state."""
        cache.set(self.cache_key, progress, timeout=None)

    def clear(self):
        """Clear progress state."""
        cache.delete(self.cache_key)

    def initialize(self, total: int) -> dict:
        """Initialize or reset progress for a new session."""
        progress = {
            "session_id": uuid.uuid4().hex,
            "next_index": 0,
            "total": total,
            "last_update_ts": time.time(),
        }
        self.set(progress)
        return progress

    def load_or_initialize(self, total: int) -> dict:
        """Load existing progress or initialize new session."""
        progress = self.get()
        if not progress or progress.get("total") != total:
            # Fresh session if no progress or total changed
            progress = self.initialize(total)
        return progress


def insert_nodes_with_checkpointing(
    nodes: List,
    vector_store_index,
    progress_tracker: BatchEmbeddingProgress,
    check_cancel_fn: Optional[Callable[[], None]] = None,
    update_status_fn: Optional[Callable[[str], None]] = None,
    batch_size: Optional[int] = None,
    time_slice_seconds: Optional[int] = None,
    requeue_fn: Optional[Callable[..., str]] = None,
    log_batch_fn: Optional[
        Callable[[int, float, Optional[str], Optional[float]], None]
    ] = None,
    start_index: int = 0,
) -> dict:
    """
    Insert nodes into vector store with checkpointing and time-slicing.

    Args:
        nodes: List of nodes to insert
        vector_store_index: LlamaIndex vector store index
        progress_tracker: Progress tracking object
        check_cancel_fn: Optional function to check for cancellation (raises CancelledError)
        update_status_fn: Optional function to update status text
        batch_size: Batch size for insertions (defaults to settings.EMBEDDING_BATCH_SIZE)
        time_slice_seconds: Time limit per execution (defaults to settings.EMBED_TIME_SLICE_SECONDS)
        requeue_fn: Function that takes start_index and may accept optional
            countdown_seconds and requeue_reason keyword arguments, returning
            the new task_id for requeued work
        log_batch_fn: Optional function to log batch attempts (batch_size, seconds, error_code, retry_after)
        start_index: Starting index (usually from checkpoint)

    Returns:
        dict with keys:
            - ok: bool (success)
            - requeued: str (new task_id if requeued)
            - next_index: int (progress checkpoint)
            - error: str (if failed)

    The function will:
    1. Load or initialize progress
    2. Process nodes in batches with retry/backoff
    3. Checkpoint progress after each batch
    4. Requeue if time slice exceeded
    5. Return success when all nodes inserted
    """
    batch_size = batch_size or settings.EMBEDDING_BATCH_SIZE
    time_slice_seconds = time_slice_seconds or settings.EMBED_TIME_SLICE_SECONDS

    # Load or initialize progress
    total = len(nodes)

    if start_index:
        # Requeue: load existing checkpoint (or init if cache was lost)
        progress = progress_tracker.load_or_initialize(total)
        # Respect stored next_index in case cache advanced further
        start_index = max(start_index, int(progress.get("next_index", 0)))
    else:
        # Fresh start: always initialize to avoid stale cache from previous runs
        progress = progress_tracker.initialize(total)
        start_index = 0

    # Initial status for this (possibly resumed) run
    if update_status_fn:
        initial_text = progress.get("initial_status_text")
        if not initial_text:
            initial_text = f"Adding to library... ({start_index}/{total})"
        update_status_fn(initial_text)

        # Clear the initial status hint so subsequent updates use live progress
        if progress.get("initial_status_text"):
            progress.pop("initial_status_text", None)
            progress_tracker.set(progress)

    slice_deadline = time.monotonic() + time_slice_seconds

    while start_index < total:
        # Cancellation check between batches
        if check_cancel_fn:
            try:
                check_cancel_fn()
            except Exception:
                # Assume this is CancelledError or similar
                logger.info(
                    "Batch embedding cancelled", start_index=start_index, total=total
                )
                return {"ok": False, "error": "cancelled"}

        # Update status with current progress
        if start_index > 0 and update_status_fn:
            update_status_fn(f"Adding to library... ({start_index}/{total})")

        # Determine this batch
        end_index = min(start_index + batch_size, total)
        batch_nodes = nodes[start_index:end_index]
        actual_batch_size = len(batch_nodes)

        # Wrap operation with timing and logging
        def insert_batch_with_logging():
            start_time = time.time()
            try:
                vector_store_index.insert_nodes(batch_nodes)
                duration = time.time() - start_time
                # Log success attempt
                if log_batch_fn:
                    log_batch_fn(actual_batch_size, duration, None, None)
            except Exception as e:
                duration = time.time() - start_time
                status_code, retry_after = _extract_status_and_retry_after(e)
                # Log failure attempt
                if log_batch_fn:
                    log_batch_fn(
                        actual_batch_size,
                        duration,
                        (
                            str(status_code)
                            if status_code is not None
                            else e.__class__.__name__
                        ),
                        retry_after,
                    )
                raise

        # Retry with 429-aware backoff, but yield early when retry_after would exceed remaining slice
        max_attempts = 15
        attempt = 1
        while True:
            try:
                insert_batch_with_logging()
                if attempt > 1:
                    logger.info(
                        "Embedding batch succeeded after retries",
                        start_index=start_index,
                        end_index=end_index,
                        batch_size=actual_batch_size,
                        attempts=attempt,
                        total=total,
                    )
                break  # success
            except Exception as e:
                # On failure, decide next delay or yield
                status_code, retry_after = _extract_status_and_retry_after(e)
                if isinstance(e, IntegrityError):
                    logger.error(
                        "Embedding batch hit non-retryable integrity failure",
                        start_index=start_index,
                        end_index=end_index,
                        batch_size=actual_batch_size,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        total=total,
                        error_class=e.__class__.__name__,
                        error=str(e),
                        rate_limited=False,
                        will_requeue=False,
                    )
                    raise
                # Compute remaining time in this slice
                remaining = slice_deadline - time.monotonic()
                # Decide delay using same policy as retry_with_backoff
                if status_code == 429 and retry_after is not None:
                    delay_seconds = retry_after + random.uniform(0, 2)
                else:
                    delay_seconds = 2 ** min(attempt + 2, 6) + random.uniform(0, 1)

                will_requeue = bool(
                    delay_seconds >= max(0.0, remaining - 1.0) and requeue_fn
                )
                log_fields = {
                    "start_index": start_index,
                    "end_index": end_index,
                    "batch_size": actual_batch_size,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "status_code": status_code,
                    "retry_after": retry_after,
                    "delay_seconds": round(delay_seconds, 3),
                    "remaining_slice_seconds": round(remaining, 3),
                    "total": total,
                    "error_class": e.__class__.__name__,
                    "error": str(e),
                    "rate_limited": status_code == 429,
                }

                # If delay exceeds remaining time (with a small 1s buffer), checkpoint and re-queue now
                if will_requeue:
                    if status_code == 429:
                        logger.warning(
                            "Embedding batch failure will requeue continuation",
                            **log_fields,
                            requeue_reason="retry_backoff",
                        )
                    else:
                        logger.error(
                            "Embedding batch non-rate-limit failure will requeue continuation",
                            **log_fields,
                            requeue_reason="retry_backoff",
                        )
                    # Persist checkpoint
                    progress["next_index"] = start_index
                    progress["last_update_ts"] = time.time()
                    progress["initial_status_text"] = (
                        f"Adding to library... ({start_index}/{total} - waiting)"
                    )
                    progress["requeue_reason"] = "retry_backoff"
                    progress["requeue_countdown_seconds"] = delay_seconds
                    progress_tracker.set(progress)

                    # Re-queue continuation so higher-priority work can run
                    if update_status_fn:
                        update_status_fn(
                            f"Adding to library... ({start_index}/{total} - waiting)"
                        )

                    new_task_id = requeue_fn(
                        start_index,
                        countdown_seconds=delay_seconds,
                        requeue_reason="retry_backoff",
                    )
                    return {
                        "ok": True,
                        "requeued": new_task_id,
                        "next_index": start_index,
                    }

                # Otherwise sleep then retry, unless out of attempts
                if attempt >= max_attempts:
                    logger.error(
                        "Embedding batch retries exhausted",
                        **log_fields,
                        will_requeue=False,
                    )
                    raise
                if status_code == 429:
                    logger.warning(
                        "Embedding batch rate limited; retrying in current task",
                        **log_fields,
                        will_requeue=False,
                    )
                else:
                    logger.error(
                        "Embedding batch failed; retrying in current task",
                        **log_fields,
                        will_requeue=False,
                    )
                time.sleep(delay_seconds)
                attempt += 1

        # Successful batch: advance checkpoint
        start_index = end_index
        progress["next_index"] = start_index
        progress["last_update_ts"] = time.time()
        progress_tracker.set(progress)

        # Time-slice: if we're out of time, re-queue continuation
        if time.monotonic() >= slice_deadline and start_index < total:
            if not requeue_fn:
                # Can't requeue, continue processing
                continue

            if update_status_fn:
                update_status_fn(
                    f"Adding to library... ({start_index}/{total} - waiting)"
                )

            # Persist checkpoint + initial status for next run
            progress["initial_status_text"] = (
                f"Adding to library... ({start_index}/{total} - waiting)"
            )
            progress["requeue_reason"] = "time_slice"
            progress["requeue_countdown_seconds"] = 0
            progress_tracker.set(progress)

            logger.info(
                "Embedding batch continuation scheduled due to time slice",
                next_index=start_index,
                total=total,
                batch_size=batch_size,
                time_slice_seconds=time_slice_seconds,
                requeue_reason="time_slice",
            )

            new_task_id = requeue_fn(
                start_index,
                countdown_seconds=0,
                requeue_reason="time_slice",
            )
            return {"ok": True, "requeued": new_task_id, "next_index": start_index}

    # Done!
    progress_tracker.clear()
    return {"ok": True, "next_index": total}
