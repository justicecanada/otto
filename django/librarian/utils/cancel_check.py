"""Shared utility for checking task cancellation across librarian tasks."""

from celery.result import AsyncResult
from structlog import get_logger

logger = get_logger(__name__)


class CancelledError(Exception):
    """Raised when a task is cancelled by user action."""

    pass


def check_cancel(task_id, document_id=None, check_db=False):
    """Check if a task has been cancelled/revoked and raise if so.

    Args:
        task_id: The Celery task ID to check via Celery's AsyncResult
        document_id: Optional document ID for database status check
        check_db: If True, also check document status in DB (adds DB query overhead).
                  Useful for long-running operations like ZIP extraction where there's
                  a higher chance of catching stop requests between task chains.
                  Default False for performance.
    """
    if task_id:
        result = AsyncResult(task_id)
        if result.state in ["REVOKED", "FAILURE"]:
            logger.info(
                f"Task {task_id} was cancelled (state: {result.state}), stopping execution"
            )
            raise CancelledError(f"Task {task_id} was cancelled")

    # Optional: Also check if document was stopped (handles edge case where task_id is None)
    # This adds a DB query, so only use when check_db=True
    if check_db and document_id:
        from librarian.models import Document

        try:
            doc = Document.objects.only("status").get(id=document_id)
            if doc.status == "BLOCKED":
                logger.info(f"Document {document_id} is BLOCKED, stopping execution")
                raise CancelledError(f"Document {document_id} was stopped")
        except Document.DoesNotExist:
            pass  # Document deleted, will fail elsewhere
