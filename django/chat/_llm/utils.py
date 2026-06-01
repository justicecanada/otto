"""Utility functions for LLM operations."""

import random
import re
import time
from typing import Callable, Optional, Tuple, TypeVar

from structlog import get_logger

logger = get_logger(__name__)


def _extract_status_and_retry_after(
    exc: Exception,
) -> Tuple[Optional[int], Optional[float]]:
    """
    Best-effort extraction of HTTP status code and Retry-After seconds from an exception.
    Returns (status_code, retry_after_seconds) where either may be None.
    """
    status_code = getattr(exc, "status_code", None)
    headers = None

    # Common libraries attach response with headers
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            status_code = getattr(resp, "status_code", status_code)
        except Exception:
            pass
        try:
            headers = getattr(resp, "headers", None)
        except Exception:
            headers = None

    # Some exceptions expose headers directly
    if headers is None:
        headers = getattr(exc, "headers", None)

    retry_after_seconds = None
    if headers:
        # Case-insensitive header lookup
        ra = None
        for key in ("Retry-After", "retry-after"):
            if key in headers:
                ra = headers.get(key)
                break

        # Parse Retry-After if present
        if ra is not None:
            # Azure typically returns integer seconds; handle numeric strings
            try:
                retry_after_seconds = float(ra)
            except Exception:
                # If it's a date string, we could parse to seconds until that date,
                # but most Azure rate limits return seconds. Leave None if non-numeric.
                retry_after_seconds = None

    # Sometimes the exception message contains retry-after info, e.g. "Please retry after 56 seconds."
    if retry_after_seconds is None:
        msg = str(exc)
        # Look for "retry after N seconds" or "retry in N seconds" patterns
        match = re.search(
            r"retry (?:after|in) (\d+)\s*second(?:s)?", msg, re.IGNORECASE
        )
        if match:
            try:
                retry_after_seconds = float(match.group(1))
            except Exception:
                retry_after_seconds = None

    return status_code, retry_after_seconds


T = TypeVar("T")


def retry_with_backoff(
    operation: Callable[[], T],
    max_attempts: int = 15,
    operation_name: str = "operation",
) -> T:
    """
    Retry an operation with exponential backoff and 429-aware retry logic.

    Uses capped exponential backoff (max 64s between tries) with jitter.
    For 429 errors with Retry-After headers, respects the provider's timing.

    Args:
        operation: Callable that performs the operation. Should raise on failure.
        max_attempts: Maximum number of attempts (default: 15)
        operation_name: Name for logging purposes

    Returns:
        Result of the operation

    Raises:
        The last exception encountered if all attempts fail
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as e:
            status_code, retry_after = _extract_status_and_retry_after(e)

            logger.error(
                f"Error during {operation_name} (attempt {attempt}/{max_attempts}): {e}"
            )

            if attempt == max_attempts:
                # Propagate on last failure
                raise

            # 429-aware backoff: prefer provider 'Retry-After' if available
            if status_code == 429 and retry_after is not None:
                # Add small jitter to avoid thundering herd at the minute boundary
                delay_seconds = retry_after + random.uniform(0, 2)
                logger.error(
                    f"429 received. Retrying in {delay_seconds:.2f}s (Retry-After={retry_after}s)."
                )
                time.sleep(delay_seconds)
            else:
                # Default capped exponential: 8,16,32,64,... with jitter
                delay_seconds = 2 ** min(attempt + 2, 6) + random.uniform(0, 1)
                logger.error(f"Retrying in {delay_seconds:.2f} seconds...")
                time.sleep(delay_seconds)

    # Should never reach here, but just in case
    raise RuntimeError(f"{operation_name} failed after {max_attempts} attempts")


def chat_history_to_prompt(chat_history: list) -> str:
    """
    Convert a list of ChatMessage objects to a single prompt string.
    Each message will be formatted as: "<role>: <content>"
    """
    from llama_index.core.base.llms.types import ChatMessage

    lines = []
    for msg in chat_history:
        # If msg is a dict, convert to ChatMessage
        if not isinstance(msg, ChatMessage) and hasattr(ChatMessage, "model_validate"):
            msg = ChatMessage.model_validate(msg)
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)
        if role and content:
            lines.append(f"{role.value}: {content}")
        elif content:
            lines.append(str(content))
    return "\n".join(lines)
