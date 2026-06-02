"""
Direct OpenAI Responses API client for chat_next.

This module provides a clean interface to the OpenAI Responses API.

Key concepts:
- Messages are dicts with 'role' and 'content' keys
- Roles: 'developer' (system), 'user', 'assistant'
- For reasoning models, we include encrypted_content to preserve reasoning state
- Conversation history is built from Message.response_output fields
- When possible, we pass previous_response_id to leverage OpenAI's caching mechanism
  rather than rebuilding full context each time
"""

import copy
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from django.conf import settings
from django.utils import timezone
from django.utils.functional import Promise

import httpx
from asgiref.sync import sync_to_async
from openai import APIError, APIStatusError, AsyncAzureOpenAI, BadRequestError
from openai.types.responses import (
    Response,
    ResponseCodeInterpreterCallCodeDeltaEvent,
    ResponseCodeInterpreterCallCodeDoneEvent,
    ResponseCodeInterpreterCallCompletedEvent,
    ResponseCodeInterpreterCallInProgressEvent,
    ResponseCodeInterpreterCallInterpretingEvent,
    ResponseCompletedEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningSummaryTextDoneEvent,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
)
from structlog import get_logger

from chat_next._llm.code_interpreter import (
    extract_code_interpreter_outputs,
    extract_container_id,
    extract_file_citations,
    extract_unique_container_ids,
)
from chat_next._llm.constants import (
    CODE_INTERPRETER_SUPPORTED_EXTENSIONS,
    CONTEXT_STUFFING_EXTENSIONS,
    TOOL_COST_TYPES,
)
from chat_next._llm.models import (
    DEFAULT_CHAT_MODEL_ID,
    get_compaction_threshold_tokens,
    get_model,
    should_compact_context,
)
from chat_next._tools.approval import (
    APPROVAL_SOURCE_CACHE,
    APPROVAL_SOURCE_MANUAL,
    APPROVAL_SOURCE_USER_ALLOWLIST,
    evaluate_approval_policy,
)
from chat_next._tools.risk_review import review_external_tool_call
from chat_next._utils.context_hints import (
    is_valid_tool_context_hint_id,
    sanitize_runtime_context_hints,
)

# Note: chat_next.tools and chat_next.models imports are done lazily inside
# functions to avoid circular imports (TOOL_REGISTRY, LOCAL_TOOL_CATEGORIES, etc.)

logger = get_logger(__name__)


COMPACTION_PROCESSING_STEP = {
    "type": "tool_call",
    "tool_type": "compaction",
    "status": "completed",
    "details": {
        "name": "compact_conversation",
        "tool_label": "Compacted conversation",
    },
}

COMPACTION_IN_PROGRESS_PROCESSING_STEP = {
    "type": "tool_call",
    "tool_type": "compaction",
    "status": "in_progress",
    "details": {
        "name": "compact_conversation",
        "tool_label": "Compacting conversation...",
    },
}


def _make_json_serializable(value):
    """Recursively convert lazy translation proxies into JSON-safe values."""
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, dict):
        return {
            _make_json_serializable(key): _make_json_serializable(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_make_json_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [_make_json_serializable(item) for item in value]
    return value


# ============================================================================
# Response types
# ============================================================================


@dataclass
class ToolCall:
    """Represents a tool call made during the response.

    Attributes:
        tool_type: Internal tool type (e.g., "web_search_preview", "code_interpreter")
        status: Current status ("in_progress", "searching", "completed")
        query: Search query for web search tools
        details: Tool-specific details (e.g., code for code_interpreter)
        container_id: Container ID for code interpreter calls (used to track session reuse)
    """

    tool_type: str
    status: str = "in_progress"
    query: Optional[str] = None
    # Sources returned by the web search (list of dicts with 'url', 'title')
    sources: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    # Container ID for code interpreter (to track session reuse)
    container_id: Optional[str] = None

    def is_billable(self) -> bool:
        """
        Check if this tool call should incur a cost.

        Some APIs (e.g., GPT-5.1) send "container" events with incomplete data
        followed by the real event. We only bill for complete tool calls.
        """
        # Default: tool is billable if it has a cost type defined
        return self.tool_type in TOOL_COST_TYPES


@dataclass
class StreamChunk:
    """Structured chunk yielded during streaming."""

    text: str = ""
    reasoning_steps: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)  # List of ToolCall objects
    # Unified list of all processing steps in chronological order
    # Each step is a dict with 'type' ('reasoning' or 'tool_call') and step data
    processing_steps: list = field(default_factory=list)
    is_reasoning: bool = False
    is_complete: bool = False
    usage: Optional[dict] = None
    # Raw output items from the completed response (to store in DB)
    output_items: Optional[list] = None
    # File citations from code interpreter (container files to download)
    file_citations: list = field(default_factory=list)
    # Code interpreter outputs (images, logs) from tool execution
    code_interpreter_outputs: list = field(default_factory=list)
    # Container ID for downloading sandbox files (from code_interpreter_call)
    container_id: Optional[str] = None
    # Response ID from the API (for chaining via previous_response_id)
    response_id: Optional[str] = None


@dataclass
class TokenUsage:
    """Token usage information from API response."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0

    @classmethod
    def from_response(cls, response: Response) -> "TokenUsage":
        """Extract token usage from a completed Response object."""
        usage = cls()
        if not response or not hasattr(response, "usage") or not response.usage:
            return usage

        api_usage = response.usage
        usage.input_tokens = getattr(api_usage, "input_tokens", 0) or 0
        usage.output_tokens = getattr(api_usage, "output_tokens", 0) or 0

        # Extract cached tokens from input_tokens_details
        input_details = getattr(api_usage, "input_tokens_details", None)
        if input_details:
            usage.cached_tokens = getattr(input_details, "cached_tokens", 0) or 0

        # Extract reasoning tokens from output_tokens_details
        output_details = getattr(api_usage, "output_tokens_details", None)
        if output_details:
            usage.reasoning_tokens = getattr(output_details, "reasoning_tokens", 0) or 0

        return usage

    def create_costs(self, model_id: str) -> float:
        """
        Create Otto Cost objects for this token usage.

        Args:
            model_id: The model ID for looking up cost types

        Returns:
            Total USD cost for this usage
        """
        # Import here to avoid circular import
        from decimal import Decimal

        from otto.models import Cost

        usd_cost = Decimal("0.0")

        # Regular input tokens (not from cache)
        regular_input_tokens = self.input_tokens - self.cached_tokens
        if regular_input_tokens > 0:
            c = Cost.objects.new(
                cost_type=f"{model_id}-in",
                count=regular_input_tokens,
            )
            usd_cost += c.usd_cost

        # Cached input tokens (charged at reduced rate)
        if self.cached_tokens > 0:
            c = Cost.objects.new(
                cost_type=f"{model_id}-in-cached",
                count=self.cached_tokens,
            )
            usd_cost += c.usd_cost

        # Output tokens
        if self.output_tokens > 0:
            c = Cost.objects.new(
                cost_type=f"{model_id}-out",
                count=self.output_tokens,
            )
            usd_cost += c.usd_cost

        return float(usd_cost)


def create_compaction_costs(compaction_usage: dict | None, model_id: str) -> float:
    """Create cost records for a `/responses/compact` call when usage is available."""
    usage = compaction_usage or {}
    compaction_token_usage = TokenUsage(
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        reasoning_tokens=usage.get("reasoning_tokens", 0) or 0,
        cached_tokens=usage.get("cached_tokens", 0) or 0,
    )
    if (
        compaction_token_usage.input_tokens
        or compaction_token_usage.output_tokens
        or compaction_token_usage.reasoning_tokens
        or compaction_token_usage.cached_tokens
    ):
        return float(compaction_token_usage.create_costs(model_id) or 0.0)
    return 0.0


def create_tool_costs(
    tool_calls: list,
    reuse_container: bool = False,
    code_interpreter_sessions: int = None,
) -> float:
    """
    Create Cost objects for tool calls.

    Args:
        tool_calls: List of ToolCall objects or dicts from the response
        reuse_container: Whether the container was reused from a previous response
                         (suppress ALL code interpreter costs for this response)
        code_interpreter_sessions: Number of unique Code Interpreter sessions used.
                                   If provided, this overrides counting from tool_calls.
                                   Azure bills per session, not per tool call.

    Returns:
        Total USD cost for tool calls
    """
    from decimal import Decimal

    from otto.models import Cost

    usd_cost = Decimal("0.0")

    # Handle code interpreter separately based on session count
    has_code_interpreter = any(
        (
            tc.tool_type
            if isinstance(tc, ToolCall)
            else tc.get("tool_type", tc.get("type", ""))
        )
        == "code_interpreter"
        for tc in tool_calls
    )

    if has_code_interpreter and not reuse_container:
        # Determine number of sessions to bill
        if code_interpreter_sessions is not None:
            num_sessions = code_interpreter_sessions
        else:
            # Fallback: count unique container_ids from tool calls, or default to 1
            container_ids = set()
            for tc in tool_calls:
                if isinstance(tc, ToolCall) and tc.tool_type == "code_interpreter":
                    if tc.container_id:
                        container_ids.add(tc.container_id)
                elif isinstance(tc, dict):
                    tt = tc.get("tool_type") or tc.get("type", "")
                    if tt == "code_interpreter" and tc.get("container_id"):
                        container_ids.add(tc.get("container_id"))
            # If we have container_ids, use their count; otherwise assume 1 session
            num_sessions = len(container_ids) if container_ids else 1

        if num_sessions > 0:
            cost_type = TOOL_COST_TYPES.get("code_interpreter")
            if cost_type:
                c = Cost.objects.new(cost_type=cost_type, count=num_sessions)
                usd_cost += c.usd_cost
                logger.info(
                    "Code interpreter session cost",
                    sessions=num_sessions,
                    cost_usd=float(c.usd_cost),
                )

    # Handle other tool types (web search, etc.)
    for tool_call in tool_calls:
        # Normalize to ToolCall object
        if isinstance(tool_call, dict):
            tool_call = ToolCall(
                tool_type=tool_call.get("tool_type") or tool_call.get("type", ""),
                status=tool_call.get("status", "completed"),
                query=tool_call.get("query"),
                details=tool_call.get("details", {}),
            )
        elif not isinstance(tool_call, ToolCall):
            continue

        # Skip code interpreter (already handled above)
        if tool_call.tool_type == "code_interpreter":
            continue

        # Skip non-billable tool calls (e.g., container events without data)
        if not tool_call.is_billable():
            continue

        # Look up cost type for this tool
        cost_type = TOOL_COST_TYPES.get(tool_call.tool_type)
        if cost_type:
            c = Cost.objects.new(cost_type=cost_type, count=1)
            usd_cost += c.usd_cost

    return float(usd_cost)


def estimate_tool_call_costs(function_calls: list, chat=None):
    """Estimate total cost for pending function calls before execution.

    Dispatches to each tool's ``estimate_cost`` callable (when defined) and
    sums the results.

    Args:
        function_calls: List of function_call dicts from the API response.
        chat: The Chat instance (passed through to individual estimators).

    Returns:
        Total estimated cost as a Decimal (0 when no tools have estimates).
    """
    from decimal import Decimal, InvalidOperation

    from chat_next.tools import TOOL_REGISTRY

    total = Decimal("0")
    for fc in function_calls:
        tool_name = fc.get("name") or ""
        tool = TOOL_REGISTRY.get(tool_name)
        if not tool or not tool.estimate_cost:
            continue
        try:
            raw_args = fc.get("arguments", "{}")
            parsed_args = (
                json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            )
            estimate = tool.estimate_cost(parsed_args, chat=chat)
            if estimate is not None:
                total += Decimal(str(estimate))
        except (TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
            logger.warning(
                "Failed to estimate tool cost",
                tool_name=tool_name,
            )
    return total


def check_tool_cost_warning(function_calls: list, chat=None):
    """Estimate costs for pending function calls and decide whether to warn.

    Combines :func:`estimate_tool_call_costs` with the
    ``settings.WARN_COST`` threshold check.

    Args:
        function_calls: List of function_call dicts from the API response.
        chat: The Chat instance (passed through to individual estimators).

    Returns:
        Tuple of ``(cost_warning, estimated_cost, formatted_cost)``:
        - **cost_warning** (*bool*): ``True`` when cost exceeds threshold.
        - **estimated_cost** (*Decimal*): Raw estimated cost.
        - **formatted_cost** (*str | None*): e.g. ``"0.15"``; ``None`` when
          no warning is needed.
    """
    from decimal import Decimal

    estimated_cost = estimate_tool_call_costs(function_calls, chat=chat)
    warn_threshold = Decimal(str(settings.WARN_COST))
    cost_warning = estimated_cost > warn_threshold
    formatted_cost = f"{estimated_cost:.2f}" if cost_warning else None
    return cost_warning, estimated_cost, formatted_cost


# ============================================================================
# Helper functions for building messages
# ============================================================================


def _sanitize_input_item(item: dict) -> dict:
    """
    Sanitize a stored output item for use as input.

    Removes or filters output-only items/fields that should not be sent back to the API.
    Returns None for items that should be skipped entirely.

    When using previous_response_id, most items are already in the cached context.
    When not using it, function calls require their outputs to be paired, which we
    can't guarantee after a failed/interrupted response, so we skip them.
    """
    if not isinstance(item, dict):
        return item

    item_type = item.get("type", "")

    # Skip reasoning items - these are internal to the API and should never be
    # sent as input. They're only stored for debugging/display purposes.
    if item_type == "reasoning":
        return None

    # Preserve compaction items — these are opaque encrypted summaries produced
    # by server-side context management. They must be passed back as input so
    # the model can reconstruct summarized conversation state on rebuilt turns.
    if item_type == "compaction":
        return item

    # Skip function_call and function_call_output items - these must be paired,
    # and when rebuilding from storage we can't guarantee pairing.
    # In the function call continuation loop, fresh function_call_output items
    # are sent directly without going through sanitization.
    if item_type in ("function_call", "function_call_output"):
        return None

    # Skip web_search_call items entirely - they are output-only tool call records.
    # Kept for backward compatibility with messages stored before web search was removed.
    if item_type == "web_search_call":
        return None

    # Skip legacy MCP items from pre-release experiments.
    if item_type == "mcp_call":
        return None

    # Skip legacy MCP approval items from pre-release experiments.
    if item_type in ("mcp_approval_request", "mcp_approval_response"):
        return None

    # Skip code_interpreter_call items entirely - when using store=false, the item IDs
    # are not persisted on OpenAI's side, so we cannot reference them in subsequent
    # requests. The code execution results are already in the assistant's text response.
    if item_type == "code_interpreter_call":
        return None

    # Remove output-only fields that should not be sent as input
    # These fields are added by the API in responses but rejected in requests
    cleaned_item = {
        k: v
        for k, v in item.items()
        if k not in ("status", "container_id", "parsed_arguments")
    }

    role = cleaned_item.get("role")
    if role == "assistant":
        expected_text_type = "output_text"
        content = cleaned_item.get("content")

        if isinstance(content, str):
            cleaned_item["content"] = [{"type": expected_text_type, "text": content}]
            if "type" not in cleaned_item:
                cleaned_item["type"] = "message"
        elif isinstance(content, list):
            normalized_content = []
            content_changed = False
            for chunk in content:
                if (
                    isinstance(chunk, dict)
                    and chunk.get("type") in ("input_text", "output_text")
                    and chunk.get("type") != expected_text_type
                ):
                    normalized_content.append({**chunk, "type": expected_text_type})
                    content_changed = True
                else:
                    normalized_content.append(chunk)

            if content_changed:
                cleaned_item["content"] = normalized_content
            if "type" not in cleaned_item:
                cleaned_item["type"] = "message"

    return cleaned_item


def _sanitize_input_items(items: list) -> list:
    """Sanitize a list of stored items for use as API input."""
    return [
        item for item in (_sanitize_input_item(i) for i in items) if item is not None
    ]


def _sanitize_compaction_input_item(item: dict) -> dict:
    """Sanitize an item for the compaction endpoint.

    Unlike normal conversation rebuilds, compaction can safely preserve paired
    ``function_call`` and ``function_call_output`` items from the live in-memory
    turn so the compacted state still reflects the current tool exchange.
    Inline vision payloads are still stripped from function_call_output items to
    avoid bloating the compaction request with base64 data URLs.
    """
    if not isinstance(item, dict):
        return item

    item_type = item.get("type", "")

    if item_type == "function_call":
        return {
            k: v
            for k, v in item.items()
            if k not in ("status", "container_id", "parsed_arguments")
        }

    if item_type == "function_call_output":
        cleaned_item = {
            k: v
            for k, v in item.items()
            if k not in ("status", "container_id", "parsed_arguments")
        }
        return _sanitize_function_call_output_for_storage(cleaned_item)

    return _sanitize_input_item(item)


def _sanitize_input_items_for_compaction(items: list) -> list:
    """Sanitize items for the compaction endpoint.

    Preserves live tool-call pairs while filtering unsupported/stale items.
    """
    return [
        item
        for item in (_sanitize_compaction_input_item(i) for i in items)
        if item is not None
    ]


def _extract_chained_input_items(input_items: list) -> tuple[list, bool]:
    """Return items to send when chaining via previous_response_id.

    Returns a tuple of ``(items_to_send, is_function_continuation)``.
    """
    has_user_messages = any(
        isinstance(item, dict) and item.get("role") == "user" for item in input_items
    )

    if has_user_messages:
        new_input = []
        for item in reversed(input_items):
            if isinstance(item, dict):
                role = item.get("role", "")
                item_type = item.get("type", "")
                if role == "user":
                    new_input.insert(0, item)
                elif role == "assistant" or item_type in (
                    "reasoning",
                    "message",
                    "function_call",
                    "function_call_output",
                ):
                    break
        return _sanitize_input_items(new_input) if new_input else [], False

    return list(input_items), True


def _is_context_window_error(error_str: str) -> bool:
    """Return whether an API error indicates the request exceeded context."""
    return (
        "context window" in error_str
        or "maximum context length" in error_str
        or "context_length_exceeded" in error_str
        or "input_too_long" in error_str
        or (
            "input exceeds" in error_str
            and ("context" in error_str or "maximum" in error_str)
        )
    )


def _tool_is_user_auto_approved(tool, auto_approve_tools: list[str]) -> bool:
    """Return True when the tool matches a saved user allowlist entry.

    The canonical saved value is the tool ID, but older chats may still contain
    the display label from a previous implementation.
    """
    if not tool or not auto_approve_tools:
        return False

    tool_id = (tool.name or "").strip()
    tool_display_name = str(tool.display_name or "").strip()
    return tool_id in auto_approve_tools or (
        bool(tool_display_name) and tool_display_name in auto_approve_tools
    )


def get_context_management_mode(chat_or_client=None) -> str:
    """Return the effective chat context-management mode."""
    chat = getattr(chat_or_client, "chat", chat_or_client)
    if chat and hasattr(chat, "settings"):
        return getattr(chat.settings, "chat_context_management", "compact") or "compact"
    return "compact"


def user_message(content: str) -> dict:
    """Create a simple user text message."""
    return {"role": "user", "content": content}


def _format_context_hints_for_input(message) -> str:
    """Format stored context hints into a compact input note for this message.

    This embeds context-picker selections directly in the reconstructed user
    message content so they remain in conversation history across turns.
    """
    details = getattr(message, "details", None) or {}
    hints = sanitize_runtime_context_hints(details.get("context_hints"))
    if not hints:
        return ""

    lines = ["[User-selected context hints for this message:"]
    for hint in hints:
        hint_type = hint.get("type", "")
        name = hint.get("name", "")
        hint_id = hint.get("id", "")

        if hint_type == "tool":
            lines.append(f"- tool: {name} (tool_category={hint_id})")
        elif hint_type == "library":
            lines.append(f"- library: {name} (library_id={hint_id})")
        elif hint_type == "folder":
            lines.append(f"- folder: {name} (data_source_id={hint_id})")
        elif hint_type == "document":
            lines.append(f"- document: {name} (document_id={hint_id})")
        else:
            lines.append(f"- {hint_type or 'item'}: {name} (id={hint_id})")

    lines.append(
        "These IDs are internal for tool calls only; never expose IDs in user-facing text.]"
    )
    return "\n".join(lines)


def user_message_from_db(message) -> tuple:
    """
    Build input items from a stored user Message.

    If message.response_output has stored items, use those.
    Otherwise, create a simple text message from message.text.

    Note: Uploaded files are NOT context-stuffed or passed to vision.
    Files are indexed in the chat's uploads library (via link_chat_files_to_library)
    and accessed via Q&A tools (rag_search, get_document_text, view_library_files).
    This avoids context length issues with large files and gives the model control
    over how to process the files.

    Returns:
        Tuple of (items, file_ids):
        - items: List of input items for the message
        - file_ids: List of OpenAI file_id strings (always empty now - no Code Interpreter files)
    """
    if message.response_output:
        return _sanitize_input_items(message.response_output), []

    # Build message content
    text = message.text or ""

    # Build file upload description with document IDs (important for tool usage)
    # The model needs document IDs to call tools like view_library_files, get_document_text, etc.
    # Include paused status explicitly because paused documents are readable directly
    # but not searchable via semantic library tools until manually embedded.
    file_descriptions = []
    if hasattr(message, "files") and message.files.exists():
        for f in message.files.select_related("document").all():
            if f.document_id:
                description = f"{f.filename} (document_id={f.document_id}"
                if f.document and f.document.status == "PAUSED":
                    description += (
                        ", status=PAUSED, semantic_search=unavailable_until_embedded"
                    )
                description += ")"
                file_descriptions.append(description)
            else:
                file_descriptions.append(f.filename)

    context_hints_note = _format_context_hints_for_input(message)

    # If no user text provided, describe the uploaded files
    if not text and file_descriptions:
        text = f"User uploaded these files: {', '.join(file_descriptions)}"
    # If user did provide text but also has files, append file info so model knows the IDs
    elif text and file_descriptions:
        text = f"{text}\n\n[Uploaded files: {', '.join(file_descriptions)}]"

    if context_hints_note:
        if text:
            text = f"{text}\n\n{context_hints_note}"
        else:
            text = context_hints_note

    if not text:
        return [], []

    return [{"role": "user", "content": text}], []


def _get_vision_items_for_message(
    message, include_images=True, include_pdfs=True, use_file_ids=False
) -> tuple:
    """
    Get vision content items (images, PDFs) for a message in Responses API format.

    Args:
        message: Message model instance with files
        include_images: Whether to include images
        include_pdfs: Whether to include PDFs
        use_file_ids: If True, upload files to OpenAI Files API and use file_id references.
                      This is required for Code Interpreter to access the files.
                      If False, use inline base64 data URLs (for vision-only models).

    Returns:
        Tuple of (items, file_ids):
        - items: List of input_image/input_file dicts for the content array
        - file_ids: List of OpenAI file_id strings for Code Interpreter container access

        Images are always passed inline (base64) since they work fine that way.
        When use_file_ids=True:
        - PDFs and other documents are uploaded to Files API
        - file_ids are collected for the container config
    """
    import base64

    items = []
    file_ids = []

    if not hasattr(message, "files") or not message.files.exists():
        return items, file_ids

    for chat_file in message.files.all():
        saved_file = chat_file.saved_file
        if not saved_file or not saved_file.file:
            continue

        filename_lower = chat_file.filename.lower()

        try:
            # Check if image
            is_image = (
                saved_file.content_type and saved_file.content_type.startswith("image/")
            ) or filename_lower.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )

            # Check if PDF
            is_pdf = (
                saved_file.content_type == "application/pdf"
                or filename_lower.endswith(".pdf")
            )

            # Check if other Code Interpreter supported file
            is_code_interpreter_file = filename_lower.endswith(
                CODE_INTERPRETER_SUPPORTED_EXTENSIONS
            )

            if include_images and is_image:
                # Images are always passed inline (base64) - they work fine
                with saved_file.file.open("rb") as f:
                    file_bytes = f.read()
                mime_type = saved_file.content_type or "image/png"
                b64_data = base64.b64encode(file_bytes).decode("utf-8")
                items.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{b64_data}",
                        "detail": "high",
                    }
                )
            elif use_file_ids and (is_pdf or is_code_interpreter_file):
                # Upload to OpenAI Files API for Code Interpreter access
                # Check if already uploaded
                if saved_file.openai_file_id:
                    file_id = saved_file.openai_file_id
                else:
                    # Upload to OpenAI Files API
                    from django.conf import settings

                    from openai import AzureOpenAI

                    api_version = settings.AZURE_AI_SERVICES_VERSION
                    if not api_version or api_version in ("v1", "v1/"):
                        api_version = "2025-03-01-preview"
                    client = AzureOpenAI(
                        api_key=settings.AZURE_AI_SERVICES_KEY,
                        azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
                        api_version=api_version,
                    )
                    filename = saved_file.file.name.split("/")[-1]
                    with saved_file.file.open("rb") as f:
                        response = client.files.create(
                            file=(filename, f),
                            purpose="assistants",
                        )
                    file_id = response.id
                    # Cache on SavedFile for future use
                    saved_file.openai_file_id = file_id
                    saved_file.save(update_fields=["openai_file_id"])

                if file_id:
                    file_ids.append(file_id)
                    logger.debug(
                        "Adding file for Code Interpreter",
                        filename=chat_file.filename,
                        file_id=file_id,
                    )

                    # Only context-stuff supported file types to avoid API errors
                    if filename_lower.endswith(CONTEXT_STUFFING_EXTENSIONS):
                        # Add as input_file so model receives the content directly
                        items.append(
                            {
                                "type": "input_file",
                                "file_id": file_id,
                            }
                        )
                    else:
                        # For unsupported types (like .docx), we don't stuff content directly.
                        # The file is still available to Code Interpreter via file_ids.
                        # We add a text notice so the model knows it exists.
                        items.append(
                            {
                                "type": "input_text",
                                "text": f"\n[File '{chat_file.filename}' uploaded to Code Interpreter storage (ID: {file_id})]",
                            }
                        )
                else:
                    logger.warning(
                        "Failed to get file_id for file, skipping",
                        filename=chat_file.filename,
                        saved_file_id=saved_file.id,
                    )
            elif include_pdfs and is_pdf:
                # Use inline base64 for vision-only models (no Code Interpreter)
                with saved_file.file.open("rb") as f:
                    file_bytes = f.read()
                if not file_bytes:
                    logger.warning(
                        "PDF file is empty",
                        filename=chat_file.filename,
                        saved_file_id=saved_file.id,
                        file_path=saved_file.file.path if saved_file.file else None,
                    )
                    continue
                b64_data = base64.b64encode(file_bytes).decode("utf-8")
                logger.debug(
                    "Adding PDF as inline base64 for vision",
                    filename=chat_file.filename,
                    file_size_bytes=len(file_bytes),
                    b64_length=len(b64_data),
                )
                items.append(
                    {
                        "type": "input_file",
                        "file_data": f"data:application/pdf;base64,{b64_data}",
                        "filename": chat_file.filename,
                    }
                )
        except Exception as e:
            logger.warning(
                "Failed to load file for vision content",
                filename=chat_file.filename,
                error=str(e),
            )

    return items, file_ids


def bot_message_from_db(message) -> list:
    """
    Build input items from a stored bot Message.

    Returns the stored response_output items which may include:
    - Assistant message with content
    - Encrypted reasoning items (for reasoning models)
    """
    if message.response_output:
        return _sanitize_input_items(message.response_output)
    # Fallback: create simple assistant message from text
    if message.text:
        return _sanitize_input_items([{"role": "assistant", "content": message.text}])
    return []


def build_conversation_input(
    chat,
    skip_empty: bool = True,
) -> list:
    """
    Build the full input array for a Responses API request from a Chat's messages.

    This reconstructs conversation history from stored response_output fields,
    allowing us to use store=false while maintaining conversation state.

    Note: System prompt (instructions) should be passed separately to the API,
    not included in the input items.

    Note: Uploaded files are NOT passed to vision/Code Interpreter context.
    Files are indexed in the chat's uploads library and accessed via Q&A tools.
    This avoids context length issues with large files.

    Args:
        chat: Chat model instance
        skip_empty: If True, skip messages with no content

    Returns:
        List of input items for the Responses API
    """
    items = []

    # Get all messages in order (excluding the empty bot response being generated)
    messages = chat.messages.order_by("date_created")

    raw_compacted_items = getattr(chat, "compacted_input_items", None)
    compacted_items = (
        raw_compacted_items if isinstance(raw_compacted_items, list) else []
    )
    compacted_through_id = getattr(chat, "compacted_through_message_id", None)
    if compacted_items and compacted_through_id and hasattr(messages, "filter"):
        items.extend(copy.deepcopy(compacted_items))
        messages = messages.filter(id__gt=compacted_through_id)

    for message in messages:
        if message.is_bot:
            msg_items = bot_message_from_db(message)
        else:
            msg_items, _ = user_message_from_db(message)

        # Skip empty messages if requested
        if skip_empty and not msg_items:
            continue

        # If this message contains a compaction item, older assistant/tool
        # history has already been summarized by the API. Drop anything
        # accumulated so far and continue from the compacted state onward.
        if any(
            isinstance(item, dict) and item.get("type") == "compaction"
            for item in msg_items
        ):
            items = list(msg_items)
        else:
            items.extend(msg_items)

    # Remove trailing empty assistant message if present (the one being generated)
    if items and isinstance(items[-1], dict):
        last_item = items[-1]
        if last_item.get("role") == "assistant":
            content = last_item.get("content", "")
            if not content or (isinstance(content, str) and not content.strip()):
                items.pop()

    return items


def _auto_enable_hinted_skills(chat):
    """Return accessible context-hinted skills without persisting them.

    Kept as a backward-compatible shim for tests and older call sites.
    Hinted skills are available for the current turn only and must not mutate
    ``enabled_skills``.
    """
    from chat_next.prompts import get_effective_available_skills

    enabled_ids = set(chat.settings.enabled_skills.values_list("pk", flat=True))
    return [
        skill
        for skill in get_effective_available_skills(
            chat.settings,
            chat=chat,
            user=chat.user,
        )
        if skill.pk not in enabled_ids
    ]


def _build_context_hints_prompt(chat) -> str:
    """
    Build a prompt section from user-selected context hints.

    Reads ``context_hints`` from the latest user message's details field.
    Each hint has a type (tool, library, folder, document, skill) and an
    id/name.  Returns a prompt string instructing the model to prioritise
    those items.
    """
    last_user_msg = chat.messages.filter(is_bot=False).order_by("-date_created").first()
    if not last_user_msg:
        return ""

    hints = sanitize_runtime_context_hints(
        (last_user_msg.details or {}).get("context_hints")
    )
    if not hints:
        return ""

    parts = ["\n---\nCTX HINTS:\n"]

    skills = [h for h in hints if h.get("type") == "skill"]
    tools = [h for h in hints if h.get("type") == "tool"]
    libraries = [h for h in hints if h.get("type") == "library"]
    folders = [h for h in hints if h.get("type") == "folder"]
    documents = [h for h in hints if h.get("type") == "document"]

    # --- Skill hints: mandatory load_skill_instructions calls ---
    if skills:
        from chat_next.models import Skill

        # Resolve PK-based hint IDs to accessible skill objects.
        skill_pks = [int(h["id"]) for h in skills if str(h.get("id", "")).isdigit()]
        skill_objs = {
            s.pk: s
            for s in Skill.objects.get_accessible(chat.user).filter(
                pk__in=skill_pks,
            )
        }
        resolved = []
        for h in skills:
            skill_id = int(h["id"]) if str(h.get("id", "")).isdigit() else None
            s = skill_objs.get(skill_id) if skill_id is not None else None
            if s:
                display = str(h.get("name") or s.display_name or "").strip()
                resolved.append((skill_id, display or f"Skill {skill_id}"))
        if resolved:
            for skill_id, display in resolved:
                parts.append(
                    "- Load skill first: "
                    f"{display} [id={skill_id}] via `load_skill_instructions` "
                    f"with `skill_id={skill_id}`.\n"
                )

    if tools:
        names = ", ".join(f"{h.get('name', '')}[{h.get('id', '')}]" for h in tools)
        parts.append(f"- Tools first: {names}\n")
        hinted_tool_ids = {h.get("id") for h in tools}
        if "local_document_processing" in hinted_tool_ids:
            parts.append(
                "- Document processing: `prompt_documents` for per-file outputs, `prompt_document_chunks` for one huge document, default `gpt-5.4-nano`, keep them map-only.\n"
            )

    if libraries:
        items = ", ".join(f"{h.get('name', '')}[{h.get('id', '')}]" for h in libraries)
        parts.append(f"- Libraries: {items}\n")

    if folders:
        items = ", ".join(f"{h.get('name', '')}[{h.get('id', '')}]" for h in folders)
        parts.append(f"- Folders: {items}\n")

    if documents:
        items = ", ".join(f"{h.get('name', '')}[{h.get('id', '')}]" for h in documents)
        parts.append(f"- Documents: {items}\n")

    parts.append("- Use hinted resources first; never expose internal IDs.\n")

    parts.append("---\n")
    return "".join(parts)


def _build_known_resource_ids_prompt(chat) -> str:
    """Build a compact runtime note with the most useful library/folder IDs."""
    from librarian.models import DataSource, Library

    from chat_next.prompts import known_resource_ids_prompt

    corporate_library = (
        Library.objects.filter(is_default_library=True).order_by("id").first()
    )
    personal_library = getattr(chat.user, "personal_library", None)
    current_chat_data_source = (
        DataSource.objects.filter(chat_next=chat).order_by("id").first()
        or DataSource.objects.filter(chat=chat).order_by("id").first()
    )

    return known_resource_ids_prompt(
        corporate_library_id=getattr(corporate_library, "id", None),
        chat_files_library_id=getattr(personal_library, "id", None),
        current_chat_data_source_id=getattr(current_chat_data_source, "id", None),
    )


def build_system_prompt(chat) -> str:
    """
    Build the system/developer instructions for a chat.

    Combines:
    - Model-specific prefix/suffix instructions
    - Context awareness (model identity, context window, truncation policy, usage)
    - Current date/time
    - Saved user personalization/profile information
    - Tool-specific usage instructions (for enabled tools)
    - User's custom system prompt

    Args:
        chat: Chat model instance

    Returns:
        System prompt string
    """
    from chat_next._llm.models import get_model
    from chat_next.prompts import (
        context_awareness_prompt,
        current_time_prompt,
        get_effective_available_skills,
        get_effective_enabled_tools,
        get_tool_prompts,
        skill_metadata_prompt,
        source_transparency_prompt,
    )

    model_id = chat.settings.chat_model
    model = get_model(model_id)

    # Determine current context usage from the last bot message, if any
    context_pct = None
    last_bot = chat.messages.filter(is_bot=True).order_by("-date_created").first()
    if last_bot:
        usage_info = last_bot.context_usage
        if usage_info:
            context_pct = usage_info.get("percentage")

    stable_prompt_parts = [
        model.system_prompt_prefix,
        source_transparency_prompt(),
    ]

    user_display_name = (
        chat.settings.user_display_name.strip()
        if (chat.settings.user_display_name or "").strip()
        else getattr(chat.user, "full_name", "").strip()
    )
    personalization_parts = []
    if chat.settings.send_name_to_model and user_display_name:
        personalization_parts.append(f"- User name: {user_display_name}\n")
    if (chat.settings.job_description or "").strip():
        personalization_parts.append(
            f"- Job description: {chat.settings.job_description.strip()}\n"
        )
    if (chat.settings.global_instructions or "").strip():
        personalization_parts.append(
            f"- Global instructions: {chat.settings.global_instructions.strip()}\n"
        )

    if personalization_parts:
        personalization_prompt = (
            "\n---\nUSER PERSONALIZATION (applies across chats):\n"
            + "".join(personalization_parts)
            + "Use this context to tailor responses when helpful, but do not mention or quote these instructions unless the user asks.\n---\n"
        )
    else:
        personalization_prompt = ""

    # Add tool-specific instructions for tools already active in the prompt.
    # Skill-loaded tool guidance is appended dynamically later in the tool loop.
    enabled_tools = get_effective_enabled_tools(chat.settings, chat=chat)
    tool_prompts = get_tool_prompts(enabled_tools)
    if tool_prompts:
        stable_prompt_parts.append(tool_prompts)

    # Add skill metadata (compact name + description for all available skills)
    # including one-turn context-hinted skills.
    available_skills = get_effective_available_skills(
        chat.settings,
        chat=chat,
        user=chat.user,
    )
    skills_prompt = skill_metadata_prompt(available_skills)
    if skills_prompt:
        stable_prompt_parts.append(skills_prompt)

    if chat.settings.chat_system_prompt:
        stable_prompt_parts.append(chat.settings.chat_system_prompt)

    # Add user-selected context hints from the latest user message
    context_hint_prompt = _build_context_hints_prompt(chat)
    known_resource_ids_prompt_text = _build_known_resource_ids_prompt(chat)

    prompt_so_far = "".join(stable_prompt_parts)
    restore_client = type(
        "PromptRestoreClient",
        (),
        {
            "tools": list(enabled_tools),
            "unlocked_local_skill_tools": False,
        },
    )()
    prompt_so_far, _ = restore_chat_loaded_skill_state(
        chat=chat,
        client=restore_client,
        instructions=prompt_so_far,
    )

    dynamic_prompt_parts = [
        context_awareness_prompt(model.model_id, model.max_tokens_in, context_pct),
        current_time_prompt(),
    ]

    if known_resource_ids_prompt_text:
        dynamic_prompt_parts.append(known_resource_ids_prompt_text)

    if personalization_prompt:
        dynamic_prompt_parts.append(personalization_prompt)

    if context_hint_prompt:
        dynamic_prompt_parts.append(context_hint_prompt)

    if model.system_prompt_suffix:
        dynamic_prompt_parts.append(model.system_prompt_suffix)

    return prompt_so_far + "".join(dynamic_prompt_parts)


def _normalized_function_call_signature(item: dict) -> tuple[str, str]:
    """Return a stable (name, arguments_json) signature for local function-call caching."""
    name = (item or {}).get("name") or ""
    raw_arguments = (item or {}).get("arguments", "{}")
    try:
        if isinstance(raw_arguments, str):
            parsed = json.loads(raw_arguments)
        else:
            parsed = raw_arguments
        normalized_args = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
    except Exception:
        normalized_args = str(raw_arguments)
    return name, normalized_args


def should_proactively_compact_tool_continuation(
    *,
    context_management_mode: str,
    usage: dict | None,
    tool_output_items: list,
    model_id: str,
) -> tuple[bool, dict]:
    """Return whether pending tool outputs should trigger proactive compaction.

    This preserves the exact heuristic used for tool-loop proactive compaction:
    - compact mode only
    - requires response usage from the just-completed model turn
    - estimates tool output tokens from serialized output length at ~3 chars/token

    Returns ``(should_compact, metrics)`` where ``metrics`` contains the
    calculated input/output/estimated tool token counts for logging or tests.
    """
    metrics = {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_tool_tokens": 0,
    }

    if context_management_mode != "compact" or not usage:
        return False, metrics

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    tool_output_chars = sum(
        len(str(item.get("output", "")))
        for item in (tool_output_items or [])
        if isinstance(item, dict)
    )
    estimated_tool_tokens = tool_output_chars // 3

    metrics.update(
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_tool_tokens": estimated_tool_tokens,
        }
    )

    should_compact = should_compact_context(
        input_tokens + estimated_tool_tokens,
        output_tokens,
        model_id,
    )
    return should_compact, metrics


def estimate_tool_continuation_usage(
    usage: dict | None,
    tool_output_items: list,
) -> dict | None:
    """Return an estimated next-turn usage after adding tool outputs.

    This is a UI-oriented heuristic used for context-window display updates
    between model turns. Callers should pass already-sanitized tool outputs so
    inline base64 blobs do not distort the estimate.
    """
    if not usage:
        return None

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    tool_output_chars = sum(
        len(str(item.get("output", "")))
        for item in (tool_output_items or [])
        if isinstance(item, dict)
    )
    estimated_tool_tokens = tool_output_chars // 3

    estimated_usage = copy.deepcopy(usage)
    estimated_usage["input_tokens"] = input_tokens + estimated_tool_tokens
    estimated_usage["output_tokens"] = output_tokens
    estimated_usage["estimated_tool_tokens"] = estimated_tool_tokens
    return estimated_usage


def estimate_compacted_conversation_usage(compacted_items: list) -> dict | None:
    """Estimate context usage immediately after a compaction completes.

    The compact endpoint returns a rewritten conversation state, but not the
    exact next-turn context size. For the live UI, estimate it from the
    serialized compacted items so the context indicator visibly drops as soon
    as compaction finishes.
    """
    if not compacted_items:
        return None

    try:
        serialized_items = json.dumps(
            _make_json_serializable(compacted_items),
            ensure_ascii=False,
        )
    except Exception:
        serialized_items = str(compacted_items)

    estimated_input_tokens = max(len(serialized_items) // 3, 1)
    return {
        "input_tokens": estimated_input_tokens,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "estimated_compacted_tokens": estimated_input_tokens,
    }


def _sanitize_function_call_output_for_storage(output_item: dict) -> dict:
    """Strip bulky inline vision payloads from stored function-call outputs.

    The live tool continuation still needs the original `input_image` / `input_file`
    items, but persisting raw data URLs or inline PDF base64 into message history
    makes compaction heuristics wildly overestimate context size.
    """
    if not isinstance(output_item, dict):
        return output_item

    sanitized_item = copy.deepcopy(output_item)
    output = sanitized_item.get("output")
    if not isinstance(output, list):
        return sanitized_item

    sanitized_output = []
    for content_item in output:
        if not isinstance(content_item, dict):
            sanitized_output.append(content_item)
            continue

        sanitized_content_item = copy.deepcopy(content_item)

        image_url = sanitized_content_item.get("image_url")
        if (
            sanitized_content_item.get("type") == "input_image"
            and isinstance(image_url, str)
            and image_url.startswith("data:")
        ):
            sanitized_content_item.pop("image_url", None)
            sanitized_content_item["image_url_omitted"] = True

        file_data = sanitized_content_item.get("file_data")
        if (
            sanitized_content_item.get("type") == "input_file"
            and isinstance(file_data, str)
            and file_data.startswith("data:")
        ):
            sanitized_content_item.pop("file_data", None)
            sanitized_content_item["file_data_omitted"] = True

        sanitized_output.append(sanitized_content_item)

    sanitized_item["output"] = sanitized_output
    return sanitized_item


def _unwrap_tool_result_payload(output):
    """Return the innermost payload from nested ``success/result`` wrappers."""
    payload = output
    max_depth = 4

    while (
        max_depth > 0
        and isinstance(payload, dict)
        and payload.get("success") is True
        and "result" in payload
    ):
        payload = payload.get("result")
        max_depth -= 1

    return payload


def _parse_function_call_arguments(arguments) -> dict:
    """Best-effort parse of function-call arguments into a dict."""
    if isinstance(arguments, dict):
        return arguments

    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def _normalize_loaded_skill_state(loaded_skill_state: dict | None = None) -> dict:
    """Return a stable structure for per-turn loaded-skill state."""

    def _unique_strings(values) -> list[str]:
        unique = []
        for value in values or []:
            normalized = str(value or "").strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    state = loaded_skill_state if isinstance(loaded_skill_state, dict) else {}
    return {
        "tool_ids": _unique_strings(state.get("tool_ids")),
        "skill_names": _unique_strings(state.get("skill_names")),
        "instruction_blocks": _unique_strings(state.get("instruction_blocks")),
        "tool_prompt_ids": _unique_strings(state.get("tool_prompt_ids")),
    }


def merge_loaded_skill_states(*states: dict | None) -> dict:
    """Merge multiple normalized loaded-skill states with stable ordering."""
    merged = _normalize_loaded_skill_state()
    for state in states:
        normalized = _normalize_loaded_skill_state(state)
        for key in merged:
            for value in normalized[key]:
                if value not in merged[key]:
                    merged[key].append(value)
    return merged


def get_chat_loaded_skill_state(chat) -> dict:
    """Return normalized persisted loaded-skill state for a chat."""
    if chat is None:
        return _normalize_loaded_skill_state()
    return _normalize_loaded_skill_state(getattr(chat, "loaded_skill_state", None))


def persist_chat_loaded_skill_state(chat, loaded_skill_state: dict | None) -> dict:
    """Merge loaded-skill state into the chat-scoped persisted runtime state."""
    merged = merge_loaded_skill_states(
        getattr(chat, "loaded_skill_state", None),
        loaded_skill_state,
    )
    if getattr(chat, "loaded_skill_state", None) != merged:
        chat.loaded_skill_state = merged
        chat.save(update_fields=["loaded_skill_state"])
    return merged


def _collect_loaded_skill_tool_ids(skill_payload: dict | None) -> list[str]:
    """Extract valid tool-category IDs from a loaded-skill payload."""
    if not isinstance(skill_payload, dict):
        return []

    tool_ids: list[str] = []

    for raw_tool_id in skill_payload.get("required_tools") or []:
        normalized_tool_id = str(raw_tool_id or "").strip()
        if (
            is_valid_tool_context_hint_id(normalized_tool_id)
            and normalized_tool_id not in tool_ids
        ):
            tool_ids.append(normalized_tool_id)

    for hint in sanitize_runtime_context_hints(
        skill_payload.get("context_hints") or []
    ):
        if hint.get("type") != "tool" or not hint.get("id"):
            continue
        tool_id = hint["id"]
        if tool_id not in tool_ids:
            tool_ids.append(tool_id)

    return tool_ids


def _format_loaded_skill_resource_hints(skill_payload: dict | None) -> str:
    """Format non-tool context hints from a loaded skill for prompt salience."""
    if not isinstance(skill_payload, dict):
        return ""

    lines: list[str] = []
    for hint in sanitize_runtime_context_hints(
        skill_payload.get("context_hints") or []
    ):
        hint_type = hint.get("type")
        hint_id = hint.get("id")
        if hint_type == "tool" or not hint_id:
            continue

        name = str(hint.get("name") or "").strip() or "Untitled"
        if hint_type == "library":
            lines.append(f"- library: {name}[{hint_id}]")
        elif hint_type == "folder":
            lines.append(f"- folder: {name}[{hint_id}]")
        elif hint_type == "document":
            lines.append(f"- document: {name}[{hint_id}]")

    if not lines:
        return ""

    return (
        "Skill resource hints:\n"
        + "\n".join(lines)
        + "\nUse these first. Never expose internal IDs."
    )


def _append_instruction_block(instructions: str | None, block: str) -> str:
    """Append an instruction block once, preserving existing prompt text."""
    if not block:
        return instructions or ""

    existing = instructions or ""
    if block in existing:
        return existing

    if not existing:
        return block

    return existing.rstrip() + "\n\n" + block.strip()


def _format_loaded_skill_instructions_for_prompt(
    skill_label: str,
    skill_payload: dict | None,
) -> str:
    """Format loaded skill instructions into a high-salience prompt block."""
    if not isinstance(skill_payload, dict):
        return ""

    skill_instructions = str(skill_payload.get("instructions") or "").strip()
    context_hints_note = str(skill_payload.get("context_hints_note") or "").strip()
    required_tool_ids = _collect_loaded_skill_tool_ids(skill_payload)
    resource_hints_block = _format_loaded_skill_resource_hints(skill_payload)

    if (
        not skill_instructions
        and not context_hints_note
        and not required_tool_ids
        and not resource_hints_block
    ):
        return ""

    parts = ["---\nLOADED SKILL:\n"]
    if skill_label:
        parts.append(f"{skill_label}\n")
    parts.append("Already loaded for this turn; do not reload unless the user asks.\n")

    if required_tool_ids:
        parts.append("Unlocked tools: " + ", ".join(required_tool_ids) + "\n")

    if skill_instructions:
        parts.append("\n")
        parts.append(skill_instructions)
        parts.append("\n")

    if context_hints_note:
        parts.append("\n")
        parts.append(context_hints_note)
        parts.append("\n")

    if resource_hints_block:
        parts.append("\n")
        parts.append(resource_hints_block)
        parts.append("\n")

    parts.append("---")
    return "".join(parts)


def _restore_loaded_skill_state(
    *,
    client,
    instructions: str | None,
    loaded_skill_state: dict | None,
) -> tuple[str, dict]:
    """Restore dynamically loaded skill tools/instructions onto a client."""
    from chat_next.models import TOOL_CATEGORY_SKILLS
    from chat_next.prompts import get_tool_prompts

    state = _normalize_loaded_skill_state(loaded_skill_state)
    updated_instructions = instructions or ""

    for tool_id in state["tool_ids"]:
        if tool_id not in client.tools:
            client.tools.append(tool_id)

    if TOOL_CATEGORY_SKILLS in state["tool_ids"]:
        client.unlocked_local_skill_tools = True

    for block in state["instruction_blocks"]:
        updated_instructions = _append_instruction_block(updated_instructions, block)

    if state["tool_prompt_ids"]:
        tool_prompt_block = get_tool_prompts(state["tool_prompt_ids"])
        if tool_prompt_block:
            updated_instructions = _append_instruction_block(
                updated_instructions,
                tool_prompt_block,
            )

    return updated_instructions, state


def restore_chat_loaded_skill_state(
    *,
    chat,
    client,
    instructions: str | None,
    message_loaded_skill_state: dict | None = None,
) -> tuple[str, dict]:
    """Restore merged chat-scoped and message-scoped loaded skill state."""
    merged_state = merge_loaded_skill_states(
        get_chat_loaded_skill_state(chat),
        message_loaded_skill_state,
    )
    return _restore_loaded_skill_state(
        client=client,
        instructions=instructions,
        loaded_skill_state=merged_state,
    )


def _activate_loaded_skill_output(
    *,
    client,
    instructions: str | None,
    loaded_skill_state: dict | None,
    tool_arguments,
    tool_output,
) -> tuple[str, dict]:
    """Apply a successful ``load_skill_instructions`` result to the active turn."""
    from chat_next.prompts import get_tool_prompts

    state = _normalize_loaded_skill_state(loaded_skill_state)
    updated_instructions = instructions or ""
    skill_payload = _unwrap_tool_result_payload(tool_output)
    if not isinstance(skill_payload, dict):
        return updated_instructions, state

    skill_id = _parse_function_call_arguments(tool_arguments).get("skill_id")
    skill_display_name = str(skill_payload.get("display_name") or "").strip()
    skill_label = (
        f"{skill_display_name} [id={skill_id}]"
        if skill_display_name and skill_id is not None
        else (f"id={skill_id}" if skill_id is not None else skill_display_name)
    )
    if skill_label and skill_label not in state["skill_names"]:
        state["skill_names"].append(skill_label)

    for tool_id in _collect_loaded_skill_tool_ids(skill_payload):
        if tool_id not in state["tool_ids"]:
            state["tool_ids"].append(tool_id)

    updated_instructions, state = _restore_loaded_skill_state(
        client=client,
        instructions=updated_instructions,
        loaded_skill_state=state,
    )

    instruction_block = _format_loaded_skill_instructions_for_prompt(
        skill_label,
        skill_payload,
    )
    if instruction_block and instruction_block not in state["instruction_blocks"]:
        state["instruction_blocks"].append(instruction_block)
        updated_instructions = _append_instruction_block(
            updated_instructions,
            instruction_block,
        )

    new_prompt_tool_ids = [
        tool_id
        for tool_id in state["tool_ids"]
        if tool_id not in state["tool_prompt_ids"]
    ]
    if new_prompt_tool_ids:
        state["tool_prompt_ids"].extend(new_prompt_tool_ids)
        tool_prompt_block = get_tool_prompts(new_prompt_tool_ids)
        if tool_prompt_block:
            updated_instructions = _append_instruction_block(
                updated_instructions,
                tool_prompt_block,
            )

    return updated_instructions, state


def extract_output_items(response: Response, include_reasoning: bool = True) -> list:
    """
    Extract output items from a Response object for storage.

    For reasoning models, this includes encrypted reasoning content.
    We store the raw items so they can be passed back as input.

    Args:
        response: The completed Response object
        include_reasoning: Whether to include reasoning items

    Returns:
        List of output items suitable for storage and reuse as input
    """
    if not response or not response.output:
        return []

    items = []
    for item in response.output:
        # Convert to dict for JSON storage
        item_dict = item.model_dump() if hasattr(item, "model_dump") else dict(item)

        # Log function_call items for debugging
        item_type = item_dict.get("type", "")
        if item_type == "function_call":
            logger.info(
                "extract_output_items: function_call item",
                item_keys=list(item_dict.keys()),
                call_id=item_dict.get("call_id"),
                id=item_dict.get("id"),
                name=item_dict.get("name"),
            )

        # Some item types require their 'id' field to be preserved when passed back
        # as input. code_interpreter_call items need the id for the API to match
        # the call with its outputs. Only remove 'id' from message items.
        if item_type == "message":
            item_dict.pop("id", None)

        # Note: web_search_call items are stored but filtered out when building
        # input for subsequent requests (see _sanitize_input_items)

        # Include the item (message, reasoning, etc.)
        items.append(item_dict)

    return items


# API Client
# ============================================================================


class ResponsesAPIClient:
    """
    Direct client for OpenAI Responses API.

    By default, uses store=True to enable response caching via previous_response_id.
    Responses are stored on Azure's side for 30 days. When previous_response_id is
    provided, the API uses cached input context, significantly reducing costs.

    Falls back to manually rebuilding input items when:
    - previous_response_id is not available (first message or data lost)
    - The referenced response has expired (30-day limit)
    """

    def __init__(
        self,
        api_key: str = None,
        model: str = DEFAULT_CHAT_MODEL_ID,
        reasoning: bool = False,
        reasoning_effort: str = None,
        reasoning_summary: str = "auto",
        temperature: float = None,
        verbosity: str = None,
        timeout: float = 120.0,
        tools: list = None,
        previous_response_id: str = None,
        code_interpreter_container_id: str = None,
        auto_approve_tools: list = None,
        user=None,
        chat=None,
    ):
        self.api_key = api_key or settings.AZURE_AI_SERVICES_KEY
        self.model = model
        self.reasoning = reasoning
        self.reasoning_effort = reasoning_effort
        self.reasoning_summary = reasoning_summary
        self.temperature = temperature
        self.verbosity = verbosity
        self.timeout = timeout
        # List of tool type strings to enable (e.g., ["web_search_preview", "code_interpreter"])
        self.tools = tools or []
        # Previous response ID for chaining (enables input caching)
        self.previous_response_id = previous_response_id
        # Known container_id to attempt reuse for code interpreter (per chat)
        self.code_interpreter_container_id = code_interpreter_container_id or None
        # File IDs dynamically added by load_library_files tool for Code Interpreter
        # Note: This is NOT pre-populated from uploads - files are added via tools
        self.code_interpreter_file_ids = []
        # Tools that are auto-approved for the local approval flow.
        self.auto_approve_tools = auto_approve_tools or []
        # User and chat context for local function tools
        self.user = user
        self.chat = chat
        # Hidden local skill-management tools stay locked until a loaded skill
        # explicitly unlocks them during the tool loop.
        self.unlocked_local_skill_tools = self._should_unlock_skill_tools()

        # Get deployment name for Azure (Azure uses deployment names, not model IDs)
        model_config = get_model(model)
        self._deployment_name = model_config.deployment_name

        # Azure OpenAI Responses API requires 2025-03-01-preview or later
        api_version = settings.AZURE_AI_SERVICES_VERSION
        if not api_version or api_version in ("v1", "v1/"):
            api_version = "2025-03-01-preview"
        self._client = AsyncAzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
            api_version=api_version,
            timeout=self.timeout,
        )

        # Token usage tracking (populated after streaming completes)
        self.last_usage: Optional[TokenUsage] = None
        # Raw output items from last response (for storage)
        self.last_output_items: Optional[list] = None
        # Tool calls from last response (for cost tracking)
        self.last_tool_calls: Optional[list] = None
        # File citations from code interpreter (container files to download)
        self.last_file_citations: Optional[list] = None
        # Response ID from last response (for chaining via previous_response_id)
        self.last_response_id: Optional[str] = None
        # Container ID from code interpreter (for container reuse)
        self.last_container_id: Optional[str] = None
        # Number of unique Code Interpreter sessions in last response (for billing)
        self.last_code_interpreter_sessions: int = 0

    def _should_unlock_skill_tools(self) -> bool:
        """Hidden skill-management tools start locked until a skill loads."""
        return False

    async def _build_request_params(
        self, input_items: list, instructions: str = None, **kwargs
    ) -> dict:
        """Build the request parameters for the Responses API.

        When previous_response_id is set, we chain to the previous response
        and only pass the new user input (last item in input_items).
        This optimizes caching since Azure stores responses for 30 days.

        Falls back to full input rebuild if previous_response_id is not available.
        """
        # Use deployment name for Azure
        model_name = self._deployment_name

        # Determine if we're chaining to a previous response
        using_previous_response = bool(self.previous_response_id)

        if using_previous_response:
            sanitized_items, is_function_continuation = _extract_chained_input_items(
                input_items
            )
            if is_function_continuation:
                # Log details of function_call_output items for debugging
                for item in sanitized_items:
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "function_call_output"
                    ):
                        logger.info(
                            "Function call continuation: sending function_call_output",
                            call_id=item.get("call_id"),
                            output_type=type(item.get("output")).__name__
                            if item.get("output")
                            else None,
                        )
            logger.debug(
                "Using previous_response_id for chaining",
                previous_response_id=self.previous_response_id,
                new_items_count=len(sanitized_items),
                is_function_continuation=is_function_continuation,
            )
        else:
            # Full rebuild: sanitize and send all input items
            sanitized_items = _sanitize_input_items(input_items)

        params = {
            "model": model_name,
            "store": True,  # Enable response storage for caching optimization
        }

        # The Responses API accepts previous_response_id without an input payload,
        # but rejects an empty input array when there is no previous response to
        # chain from. Only send the input field when there are actual items.
        if sanitized_items:
            params["input"] = sanitized_items

        # Respect the user's context-management preference.
        context_mgmt = get_context_management_mode(self)

        if context_mgmt == "compact":
            # Compact mode uses explicit between-message compaction via the
            # /responses/compact endpoint before the next turn is sent.
            # Also enable the API's own server-side compaction as a best-effort
            # safety net during long responses.
            params["context_management"] = [
                {
                    "type": "compaction",
                    "compact_threshold": get_compaction_threshold_tokens(self.model),
                }
            ]
            # Keep truncation disabled so we do not silently truncate within a
            # response when the user selected compact mode.
            params["truncation"] = "disabled"
        elif context_mgmt == "truncate":
            params["truncation"] = "auto"
        else:
            params["truncation"] = "disabled"

        # Add previous_response_id if available (enables input caching)
        if using_previous_response:
            params["previous_response_id"] = self.previous_response_id

        # Intentionally do not set prompt_cache_key.
        # Rely on provider-managed prefix hashing for routing so all turns use a
        # consistent cache strategy and can benefit from shared static prefixes.
        #
        # Previous behavior kept here for easy rollback:
        # Uncomment if we decide to force per-chat routing on chained turns.
        # NOTE: This is currently commented out because mixing strategies caused
        # turn-to-turn cache misses in real traffic (e.g., turn 2 miss after turn 1 hit).
        # if using_previous_response and self.chat and hasattr(self.chat, "id"):
        #     params["prompt_cache_key"] = str(self.chat.id)

        # Add tools if any are enabled
        if self.tools:
            # Lazy import to avoid circular dependency
            from chat_next.models import LOCAL_TOOL_CATEGORIES
            from chat_next.tools import TOOL_REGISTRY

            tools_config = []
            # Collect enabled local tool categories
            enabled_local_categories = [
                t for t in self.tools if t in LOCAL_TOOL_CATEGORIES
            ]

            for tool_type in self.tools:
                if tool_type == "code_interpreter":
                    # Code interpreter requires a container configuration.
                    # Note: We do not explicitly pass the container ID here because the API
                    # rejects 'tools[x].container.id' as an unknown parameter.
                    # Reuse is handled implicitly via 'previous_response_id'.
                    container_cfg = {"type": "auto"}
                    # Add file_ids if any were loaded via load_library_files tool
                    if self.code_interpreter_file_ids:
                        container_cfg["file_ids"] = self.code_interpreter_file_ids
                        logger.debug(
                            "Adding file_ids to Code Interpreter container",
                            file_ids=self.code_interpreter_file_ids,
                        )
                    tools_config.append(
                        {"type": "code_interpreter", "container": container_cfg}
                    )
                elif tool_type in LOCAL_TOOL_CATEGORIES:
                    # Skip - local tools are handled after the loop
                    pass
                else:
                    tools_config.append({"type": tool_type})

            # Add local function tools for all enabled categories
            if enabled_local_categories and self.user:
                local_tools = await sync_to_async(TOOL_REGISTRY.get_tools_config)(
                    self.user,
                    self.chat,
                    enabled_categories=enabled_local_categories,
                    unlocked_local_skill_tools=self.unlocked_local_skill_tools,
                )
                tools_config.extend(local_tools)
                logger.debug(
                    "Added local function tools",
                    count=len(local_tools),
                    enabled_categories=enabled_local_categories,
                    tool_names=[t.get("name") for t in local_tools],
                )
            elif enabled_local_categories and not self.user:
                logger.warning(
                    "Local function tools requested but no user context provided"
                )

            params["tools"] = tools_config

        # Add system/developer instructions
        if instructions:
            params["instructions"] = instructions

        # Add reasoning configuration for reasoning models
        if self.reasoning:
            reasoning_config = {}
            if self.reasoning_effort:
                reasoning_config["effort"] = self.reasoning_effort
            if self.reasoning_summary:
                reasoning_config["summary"] = self.reasoning_summary
            if reasoning_config:
                params["reasoning"] = reasoning_config

            # Request encrypted reasoning content for state management
            params["include"] = ["reasoning.encrypted_content"]

        # Add text configuration (e.g., verbosity for gpt-5 models)
        if self.verbosity:
            params["text"] = {"verbosity": self.verbosity}

        # Add temperature for non-reasoning models
        if self.temperature is not None and not self.reasoning:
            params["temperature"] = self.temperature

        # Merge any additional kwargs
        params.update(kwargs)

        return params

    async def compact_conversation(
        self, input_items: list, instructions: str = None
    ) -> tuple[list, dict]:
        """Compact a conversation into a smaller opaque history payload.

        Always sends the full sanitised items directly (no previous_response_id
        chaining) so the compact endpoint receives the complete conversation
        context it needs regardless of how this method is invoked.
        """
        model_name = self._deployment_name
        compact_url = (
            f"{settings.AZURE_AI_SERVICES_ENDPOINT.rstrip('/')}"
            "/openai/v1/responses/compact"
        )
        compact_headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        primary_items = _sanitize_input_items_for_compaction(input_items)
        fallback_items = _sanitize_input_items(input_items)

        async def _post_compaction_request(sanitized_items: list):
            body = {
                "model": model_name,
                "input": sanitized_items,
            }
            if instructions:
                body["instructions"] = instructions

            logger.info(
                "Compacting conversation",
                model=model_name,
                input_items_count=len(sanitized_items),
            )

            async with httpx.AsyncClient() as http_client:
                response = await http_client.post(
                    compact_url,
                    json=body,
                    headers=compact_headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            return response, sanitized_items

        try:
            response, sanitized_items = await _post_compaction_request(primary_items)
        except httpx.HTTPStatusError as exc:
            should_retry_legacy = (
                exc.response is not None
                and exc.response.status_code == 400
                and primary_items != fallback_items
            )
            if not should_retry_legacy:
                raise

            logger.warning(
                "Compaction request rejected; retrying with legacy sanitization",
                model=model_name,
                status_code=exc.response.status_code,
            )
            response, sanitized_items = await _post_compaction_request(fallback_items)
        data = response.json()

        compacted_items = data.get("output") or []
        usage_raw = data.get("usage") or {}
        input_details = usage_raw.get("input_tokens_details") or {}
        output_details = usage_raw.get("output_tokens_details") or {}
        usage_dict = {
            "input_tokens": usage_raw.get("input_tokens", 0) or 0,
            "output_tokens": usage_raw.get("output_tokens", 0) or 0,
            "cached_tokens": input_details.get("cached_tokens", 0) or 0,
            "reasoning_tokens": output_details.get("reasoning_tokens", 0) or 0,
        }

        logger.info(
            "Conversation compacted",
            original_items=len(sanitized_items),
            compacted_items=len(compacted_items),
            input_tokens=usage_dict.get("input_tokens", 0),
            output_tokens=usage_dict.get("output_tokens", 0),
        )

        self.previous_response_id = None
        return compacted_items, usage_dict

    async def stream_chat(
        self, input_items: list, instructions: str = None, **kwargs
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Stream a chat response using the Responses API.
        Wraps the implementation with retry logic for expired/invalid container IDs,
        stale previous_response_id references, and context window overflow
        (compact-and-retry).
        """
        attempt = 0
        max_attempts = 2

        while attempt < max_attempts:
            try:
                async for chunk in self._stream_chat_impl(
                    input_items, instructions, **kwargs
                ):
                    yield chunk
                return
            except APIError as e:
                attempt += 1
                error_str = str(e).lower()
                # Check directly for "container" (generic check as exact error message varies)
                is_container_error = "container" in error_str

                if (
                    attempt < max_attempts
                    and self.code_interpreter_container_id
                    and is_container_error
                ):
                    logger.warning(
                        "Container invalid/expired, retrying with auto",
                        error=str(e),
                        container_id=self.code_interpreter_container_id,
                    )
                    self.code_interpreter_container_id = None
                    continue

                # Safety net: if the error is about missing tool output
                # (e.g., from a stale previous_response_id referencing a
                # response with unresolved function_calls), retry without
                # previous_response_id so the conversation is rebuilt from
                # stored messages instead.
                is_tool_output_error = "tool output" in error_str
                if (
                    attempt < max_attempts
                    and self.previous_response_id
                    and is_tool_output_error
                ):
                    logger.warning(
                        "Missing tool output error, retrying without "
                        "previous_response_id",
                        error=str(e),
                        previous_response_id=self.previous_response_id,
                    )
                    self.previous_response_id = None
                    continue

                # Context window errors are NOT retried here because during
                # tool-call continuations input_items only contains the
                # function_call_output items, not the full conversation.
                # stream_chat_for_htmx handles compaction at the right level
                # where it has access to the complete conversation history.
                raise
            except Exception:
                raise

    async def _stream_chat_impl(
        self, input_items: list, instructions: str = None, **kwargs
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Implementation of stream_chat.

        Args:
            input_items: List of input items (conversation history)
            instructions: System/developer instructions
            **kwargs: Additional parameters for the API

        Yields:
            StreamChunk objects with accumulated text and reasoning steps
        """
        params = await self._build_request_params(input_items, instructions, **kwargs)

        # Track state during streaming
        accumulated_text = ""
        # Track reasoning summaries by output_index -> {summary_index -> text}
        # Each reasoning output item has its own set of summaries
        reasoning_items: dict[int, dict[int, str]] = {}  # output_index -> summaries
        complete_reasoning_items: set[int] = set()  # output_indices that are complete
        has_started_text = False
        # Track tool calls by output_index
        tool_calls: dict[int, ToolCall] = {}

        # Track chronological order of events
        # Each entry: {'type': 'reasoning'|'tool_call', 'index': int, 'seq': int}
        event_order: list[dict] = []
        # Map from (type, index) to sequence number for updates
        event_seq_map: dict[tuple[str, int], int] = {}
        next_seq = 0

        def get_or_assign_seq(event_type: str, index: int) -> int:
            """Get existing sequence number or assign a new one."""
            nonlocal next_seq
            key = (event_type, index)
            if key not in event_seq_map:
                event_seq_map[key] = next_seq
                event_order.append(
                    {"type": event_type, "index": index, "seq": next_seq}
                )
                next_seq += 1
            return event_seq_map[key]

        def build_processing_steps() -> list[dict]:
            """Build chronologically ordered list of all processing steps."""
            steps = []
            for entry in event_order:
                if entry["type"] == "reasoning":
                    output_idx = entry["index"]
                    if output_idx in reasoning_items:
                        # Combine all summaries for this reasoning item
                        summaries = reasoning_items[output_idx]
                        combined_text = " ".join(
                            summaries[si] for si in sorted(summaries.keys())
                        )
                        steps.append(
                            {
                                "type": "reasoning",
                                "index": output_idx,
                                "text": combined_text,
                                "complete": output_idx in complete_reasoning_items,
                            }
                        )
                elif entry["type"] == "tool_call":
                    idx = entry["index"]
                    if idx in tool_calls:
                        tc = tool_calls[idx]
                        # Skip function_call - these are handled by stream_chat_for_htmx
                        # which executes them locally and tracks their processing steps
                        if tc.tool_type == "function_call":
                            continue
                        steps.append(
                            {
                                "type": "tool_call",
                                "tool_type": tc.tool_type,
                                "status": tc.status,
                                "query": tc.query,
                                "sources": tc.sources,
                                "details": tc.details,
                            }
                        )
            return steps

        try:
            async with self._client.responses.stream(**params) as stream:
                async for event in stream:
                    output_idx = getattr(event, "output_index", None)

                    # Handle tool call output item added (start of a tool call)
                    if isinstance(event, ResponseOutputItemAddedEvent):
                        item = event.item
                        item_type = getattr(item, "type", None)
                        logger.debug(
                            "OutputItemAdded",
                            output_index=event.output_index,
                            item_type=item_type,
                        )
                        if item_type == "function_call":
                            # Local function call requested by the model
                            get_or_assign_seq("tool_call", event.output_index)
                            tool_calls[event.output_index] = ToolCall(
                                tool_type="function_call",
                                status="in_progress",
                                details={
                                    "name": getattr(item, "name", ""),
                                    "call_id": getattr(item, "call_id", ""),
                                    "arguments": getattr(item, "arguments", ""),
                                },
                            )
                            yield StreamChunk(
                                text=accumulated_text,
                                reasoning_steps=self._build_reasoning_steps_from_items(
                                    reasoning_items, complete_reasoning_items
                                ),
                                tool_calls=list(tool_calls.values()),
                                processing_steps=build_processing_steps(),
                                is_reasoning=not has_started_text,
                            )
                        continue

                    # Handle tool call output item done (tool call completed)
                    if isinstance(event, ResponseOutputItemDoneEvent):
                        item = event.item
                        item_type = getattr(item, "type", None)
                        if item_type == "function_call":
                            # Function call completed (ready for local execution)
                            details = {
                                "name": getattr(item, "name", ""),
                                "call_id": getattr(item, "call_id", ""),
                                "arguments": getattr(item, "arguments", ""),
                            }
                            get_or_assign_seq("tool_call", event.output_index)

                            if event.output_index in tool_calls:
                                tool_calls[event.output_index].status = "pending"
                                tool_calls[event.output_index].details = details
                            else:
                                tool_calls[event.output_index] = ToolCall(
                                    tool_type="function_call",
                                    status="pending",
                                    details=details,
                                )

                            yield StreamChunk(
                                text=accumulated_text,
                                reasoning_steps=self._build_reasoning_steps_from_items(
                                    reasoning_items, complete_reasoning_items
                                ),
                                tool_calls=list(tool_calls.values()),
                                processing_steps=build_processing_steps(),
                                is_reasoning=not has_started_text,
                            )
                        continue

                    # Handle code interpreter events
                    if isinstance(event, ResponseCodeInterpreterCallInProgressEvent):
                        logger.debug(
                            "Code interpreter in progress",
                            output_index=event.output_index,
                            item_id=event.item_id,
                        )
                        get_or_assign_seq("tool_call", event.output_index)
                        tool_calls[event.output_index] = ToolCall(
                            tool_type="code_interpreter",
                            status="in_progress",
                            details={"code": ""},
                        )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    if isinstance(event, ResponseCodeInterpreterCallInterpretingEvent):
                        logger.debug(
                            "Code interpreter interpreting",
                            output_index=event.output_index,
                            item_id=event.item_id,
                        )
                        get_or_assign_seq("tool_call", event.output_index)
                        if event.output_index in tool_calls:
                            tool_calls[event.output_index].status = "interpreting"
                        else:
                            tool_calls[event.output_index] = ToolCall(
                                tool_type="code_interpreter",
                                status="interpreting",
                                details={"code": ""},
                            )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    if isinstance(event, ResponseCodeInterpreterCallCodeDeltaEvent):
                        get_or_assign_seq("tool_call", event.output_index)
                        if event.output_index in tool_calls:
                            # Accumulate code delta
                            current_code = tool_calls[event.output_index].details.get(
                                "code", ""
                            )
                            tool_calls[event.output_index].details["code"] = (
                                current_code + event.delta
                            )
                        else:
                            tool_calls[event.output_index] = ToolCall(
                                tool_type="code_interpreter",
                                status="interpreting",
                                details={"code": event.delta},
                            )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    if isinstance(event, ResponseCodeInterpreterCallCodeDoneEvent):
                        logger.debug(
                            "Code interpreter code done",
                            output_index=event.output_index,
                            code_length=len(event.code) if event.code else 0,
                        )
                        get_or_assign_seq("tool_call", event.output_index)
                        if event.output_index in tool_calls:
                            tool_calls[event.output_index].details["code"] = event.code
                        else:
                            tool_calls[event.output_index] = ToolCall(
                                tool_type="code_interpreter",
                                status="interpreting",
                                details={"code": event.code},
                            )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    if isinstance(event, ResponseCodeInterpreterCallCompletedEvent):
                        logger.debug(
                            "Code interpreter completed",
                            output_index=event.output_index,
                            item_id=event.item_id,
                        )
                        get_or_assign_seq("tool_call", event.output_index)
                        if event.output_index in tool_calls:
                            tool_calls[event.output_index].status = "completed"
                        else:
                            tool_calls[event.output_index] = ToolCall(
                                tool_type="code_interpreter",
                                status="completed",
                            )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    # Handle reasoning summary delta events
                    if isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
                        output_idx = event.output_index
                        summary_idx = event.summary_index
                        get_or_assign_seq("reasoning", output_idx)
                        if output_idx not in reasoning_items:
                            reasoning_items[output_idx] = {}
                        reasoning_items[output_idx][summary_idx] = (
                            reasoning_items[output_idx].get(summary_idx, "")
                            + event.delta
                        )
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    # Handle reasoning summary done events
                    if isinstance(event, ResponseReasoningSummaryTextDoneEvent):
                        output_idx = event.output_index
                        summary_idx = event.summary_index
                        get_or_assign_seq("reasoning", output_idx)
                        if output_idx not in reasoning_items:
                            reasoning_items[output_idx] = {}
                        reasoning_items[output_idx][summary_idx] = event.text
                        # Mark this reasoning item as complete when we get a done event
                        complete_reasoning_items.add(output_idx)
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=not has_started_text,
                        )
                        continue

                    # Handle text delta events
                    if isinstance(event, ResponseTextDeltaEvent):
                        has_started_text = True
                        accumulated_text += event.delta
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=False,
                        )
                        continue

                    # Handle text done events
                    if isinstance(event, ResponseTextDoneEvent):
                        has_started_text = True
                        accumulated_text = event.text
                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=False,
                        )
                        continue

                    # Handle completion event - extract usage and output items
                    if isinstance(event, ResponseCompletedEvent):
                        self.last_usage = TokenUsage.from_response(event.response)
                        self.last_output_items = extract_output_items(
                            event.response, include_reasoning=self.reasoning
                        )
                        # Extract file citations for code interpreter outputs
                        self.last_file_citations = extract_file_citations(
                            event.response
                        )
                        # Extract code interpreter outputs (images, logs)
                        code_interpreter_outputs = extract_code_interpreter_outputs(
                            event.response
                        )
                        # Extract container_id for downloading sandbox files
                        container_id = extract_container_id(event.response)
                        # Extract unique session count for accurate billing
                        unique_sessions = extract_unique_container_ids(event.response)
                        self.last_code_interpreter_sessions = len(unique_sessions)
                        # Store tool calls for cost tracking
                        self.last_tool_calls = list(tool_calls.values())
                        # Extract response_id for chaining via previous_response_id
                        response_id = getattr(event.response, "id", None)

                        # Log summary of tool calls collected
                        if tool_calls:
                            logger.debug(
                                "Stream completed with tool calls",
                                total_tool_calls=len(tool_calls),
                                queries=[tc.query for tc in tool_calls.values()],
                            )

                        if response_id:
                            logger.debug(
                                "Response completed with ID for caching",
                                response_id=response_id,
                            )

                        # Log cache metrics for monitoring
                        if self.last_usage:
                            cache_hit_rate = (
                                (
                                    self.last_usage.cached_tokens
                                    / self.last_usage.input_tokens
                                    * 100
                                )
                                if self.last_usage.input_tokens > 0
                                else 0
                            )
                            logger.info(
                                "OpenAI response token usage",
                                model=self.model,
                                input_tokens=self.last_usage.input_tokens,
                                cached_tokens=self.last_usage.cached_tokens,
                                output_tokens=self.last_usage.output_tokens,
                                reasoning_tokens=self.last_usage.reasoning_tokens,
                                cache_hit_rate_pct=round(cache_hit_rate, 1),
                                using_previous_response=bool(self.previous_response_id),
                                chat_id=str(self.chat.id) if self.chat else None,
                            )

                        yield StreamChunk(
                            text=accumulated_text,
                            reasoning_steps=self._build_reasoning_steps_from_items(
                                reasoning_items, complete_reasoning_items
                            ),
                            tool_calls=list(tool_calls.values()),
                            processing_steps=build_processing_steps(),
                            is_reasoning=False,
                            is_complete=True,
                            usage=self.last_usage.__dict__ if self.last_usage else None,
                            output_items=self.last_output_items,
                            file_citations=self.last_file_citations,
                            code_interpreter_outputs=code_interpreter_outputs,
                            container_id=container_id,
                            response_id=response_id,
                        )

        except Exception as e:
            logger.exception("Error streaming from Responses API", error=str(e))
            raise

    def _build_reasoning_steps(
        self,
        reasoning_summaries: dict[int, str],
        complete_step_indices: set[int],
    ) -> list[dict]:
        """Build list of reasoning steps in order, marking complete status."""
        return [
            {"index": idx, "text": text, "complete": idx in complete_step_indices}
            for idx, text in sorted(reasoning_summaries.items())
        ]

    def _build_reasoning_steps_from_items(
        self,
        reasoning_items: dict[int, dict[int, str]],
        complete_reasoning_items: set[int],
    ) -> list[dict]:
        """Build list of reasoning steps from items structure.

        Args:
            reasoning_items: output_index -> {summary_index -> text}
            complete_reasoning_items: set of output_indices that are complete
        """
        steps = []
        for output_idx in sorted(reasoning_items.keys()):
            summaries = reasoning_items[output_idx]
            # Combine all summaries for this reasoning item
            combined_text = " ".join(summaries[si] for si in sorted(summaries.keys()))
            steps.append(
                {
                    "index": output_idx,
                    "text": combined_text,
                    "complete": output_idx in complete_reasoning_items,
                }
            )
        return steps

    async def complete_chat(
        self, input_items: list, instructions: str = None, **kwargs
    ) -> tuple[str, TokenUsage, list, Optional[str]]:
        """
        Get a complete (non-streaming) chat response.

        Args:
            input_items: List of input items (conversation history)
            instructions: System/developer instructions

        Returns:
            Tuple of (response_text, token_usage, output_items, response_id)
        """
        attempt = 0
        max_attempts = 3

        response = None
        while attempt < max_attempts:
            try:
                params = await self._build_request_params(
                    input_items, instructions, **kwargs
                )
                params["stream"] = False

                response = await self._client.responses.create(**params)
                break
            except APIError as e:
                attempt += 1
                error_str = str(e).lower()
                is_container_error = "container" in error_str
                if (
                    attempt < max_attempts
                    and self.code_interpreter_container_id
                    and is_container_error
                ):
                    logger.warning(
                        "Container invalid/expired in complete_chat, retrying with auto",
                        error=str(e),
                    )
                    self.code_interpreter_container_id = None
                    continue

                # Safety net: retry without previous_response_id if the error
                # is about missing tool output (stale cached context).
                is_tool_output_error = "tool output" in error_str
                if (
                    attempt < max_attempts
                    and self.previous_response_id
                    and is_tool_output_error
                ):
                    logger.warning(
                        "Missing tool output error in complete_chat, "
                        "retrying without previous_response_id",
                        error=str(e),
                        previous_response_id=self.previous_response_id,
                    )
                    self.previous_response_id = None
                    continue

                # Context window exceeded: compact the conversation and retry.
                if attempt < max_attempts and _is_context_window_error(error_str):
                    context_mgmt = get_context_management_mode(self)
                    if context_mgmt == "compact":
                        logger.warning(
                            "Context window exceeded, compacting and retrying complete_chat",
                            error=str(e),
                            previous_response_id=self.previous_response_id,
                        )
                        self.previous_response_id = None
                        try:
                            (
                                input_items,
                                compaction_usage,
                            ) = await self.compact_conversation(
                                input_items,
                                instructions,
                            )
                            await sync_to_async(create_compaction_costs)(
                                compaction_usage,
                                self.model,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to compact after context window error in complete_chat"
                            )
                            raise e
                        continue

                raise

        # Extract text from response
        text = ""
        if response.output:
            for item in response.output:
                if item.type == "message":
                    for content in item.content:
                        if hasattr(content, "text"):
                            text += content.text

        usage = TokenUsage.from_response(response)
        output_items = extract_output_items(response, include_reasoning=self.reasoning)
        response_id = getattr(response, "id", None)
        container_id = extract_container_id(response)

        self.last_usage = usage
        self.last_output_items = output_items
        self.last_response_id = response_id
        self.last_container_id = container_id

        # Log cache metrics for monitoring (non-streaming)
        if usage:
            cache_hit_rate = (
                (usage.cached_tokens / usage.input_tokens * 100)
                if usage.input_tokens > 0
                else 0
            )
            logger.info(
                "OpenAI response token usage (complete)",
                model=self.model,
                input_tokens=usage.input_tokens,
                cached_tokens=usage.cached_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cache_hit_rate_pct=round(cache_hit_rate, 1),
                using_previous_response=bool(self.previous_response_id),
                chat_id=str(self.chat.id) if self.chat else None,
            )

        return text, usage, output_items, response_id


# ============================================================================
# File upload handling for Responses API
# ============================================================================


def upload_chat_files_to_openai(chat_files: list) -> tuple[list, list]:
    """
    Upload ChatFile objects to OpenAI Files API.

    Args:
        chat_files: List of ChatFile model instances

    Returns:
        Tuple of (file_ids, file_info) where:
        - file_ids: List of OpenAI file IDs (strings)
        - file_info: List of dicts with file metadata (filename, file_id, content_type)
    """
    from django.conf import settings

    from openai import AzureOpenAI

    file_ids = []
    file_info = []

    # Create OpenAI client for file uploads
    # Azure OpenAI Files API for Code Interpreter requires 2025-03-01-preview or later
    api_version = settings.AZURE_AI_SERVICES_VERSION
    if not api_version or api_version in ("v1", "v1/"):
        api_version = "2025-03-01-preview"

    client = AzureOpenAI(
        api_key=settings.AZURE_AI_SERVICES_KEY,
        azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
        api_version=api_version,
    )

    # Image extensions - these should be sent via vision, not Files API
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    for chat_file in chat_files:
        saved_file = chat_file.saved_file
        if not saved_file or not saved_file.file:
            logger.warning(
                "Skipping chat file with no saved file",
                chat_file_id=chat_file.id,
                filename=chat_file.filename,
            )
            continue

        # Skip image files - they're handled via vision, not Files API
        filename_lower = chat_file.filename.lower()
        if any(filename_lower.endswith(ext) for ext in image_extensions):
            logger.info(
                "Skipping image file for Files API (handled via vision)",
                filename=chat_file.filename,
            )
            continue

        try:
            # Check if already uploaded
            if saved_file.openai_file_id:
                file_id = saved_file.openai_file_id
            else:
                # Upload to OpenAI Files API
                filename = saved_file.file.name.split("/")[-1]
                with saved_file.file.open("rb") as f:
                    response = client.files.create(
                        file=(filename, f),
                        purpose="assistants",
                    )
                file_id = response.id
                # Cache on SavedFile for future use
                saved_file.openai_file_id = file_id
                saved_file.save(update_fields=["openai_file_id"])

            file_ids.append(file_id)
            file_info.append(
                {
                    "filename": chat_file.filename,
                    "file_id": file_id,
                    "content_type": saved_file.content_type
                    or "application/octet-stream",
                }
            )
            logger.info(
                "Uploaded chat file to OpenAI",
                filename=chat_file.filename,
                file_id=file_id,
            )
        except Exception as e:
            logger.error(
                "Exception uploading chat file to OpenAI",
                filename=chat_file.filename,
                error=str(e),
            )

    return file_ids, file_info


def build_file_upload_input(file_info: list, file_ids: list) -> list:
    """
    Build input items for a file upload message.

    Creates a user message that describes the uploaded files and includes
    a simple acknowledgment. For Code Interpreter, files are made available via
    the tool container's file_ids and do not need to be included as input_file
    items (which triggers context stuffing validation).

    Args:
        file_info: List of file info dicts from upload_chat_files_to_openai
        file_ids: List of OpenAI file IDs

    Returns:
        List of input items for the Responses API
    """
    if not file_info:
        return []

    # Build content array with file references
    content = []

    # Add text describing the files
    filenames = [f["filename"] for f in file_info]
    if len(filenames) == 1:
        text = f"I've uploaded a file: {filenames[0]}"
    else:
        text = f"I've uploaded {len(filenames)} files: {', '.join(filenames)}"
    content.append({"type": "input_text", "text": text})

    # Do NOT add input_file references here. Code Interpreter accesses files in
    # /mnt/data via the tool container configuration (file_ids). Including
    # input_file items attempts context stuffing and causes 400 errors for
    # non-supported extensions like .csv.

    return [{"role": "user", "content": content}]


async def process_file_upload(
    chat_files: list,
    chat,
    user_message,
    response_message,
    model_id: str = None,
) -> AsyncGenerator[dict, None]:
    """
    Process file uploads by uploading non-image files to OpenAI Files API and making
    a Responses API call.

    This establishes context for the files in the conversation history, creating a
    response_id that subsequent messages can chain from.

    For image files, an immediate Responses API call is made using vision content
    (inline base64) without going through the Files API.

    Args:
        chat_files: List of ChatFile model instances
        chat: Chat model instance
        user_message: The user Message model instance (for storing response_output)
        response_message: The bot Message model instance
        model_id: Model to use (defaults to chat.settings.chat_model)

    Yields:
        Dicts with streaming state compatible with htmx_stream:
        - {"text": "...", "is_uploading": True} during upload phase
        - {"text": "...", "output_items": [...], "response_id": "..."} when complete
    """
    from django.utils.translation import gettext as _

    # Categorize files: images, PDFs, and other (Code Interpreter) files
    def categorize_files():
        images = []
        pdfs = []
        other_files = []
        for cf in chat_files:
            saved = getattr(cf, "saved_file", None)
            if not saved or not saved.file:
                continue
            content_type = (saved.content_type or "").lower()
            filename_lower = cf.filename.lower()

            if content_type.startswith("image/") or filename_lower.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            ):
                images.append(cf)
            elif content_type == "application/pdf" or filename_lower.endswith(".pdf"):
                pdfs.append(cf)
            else:
                other_files.append(cf)
        return images, pdfs, other_files

    images, pdfs, other_files = await sync_to_async(categorize_files)()

    # Determine what needs to be done:
    # - Images: vision only (no Files API)
    # - PDFs: both vision AND Files API
    # - Other files: Files API only
    # has_vision_files = bool(images or pdfs)
    # has_files_api_files = bool(pdfs or other_files)

    # If only images (no PDFs, no other files), use simple vision-only path
    if images and not pdfs and not other_files:

        def build_image_input_items():
            import base64

            content = []
            text = (
                user_message.text or "Here is an image I uploaded. Please analyze it."
            )
            content.append({"type": "input_text", "text": text})

            for cf in images:
                saved = cf.saved_file
                try:
                    with saved.file.open("rb") as f:
                        file_bytes = f.read()
                    mime_type = saved.content_type or "image/png"
                    b64_data = base64.b64encode(file_bytes).decode("utf-8")
                    content.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{b64_data}",
                            "detail": "high",
                        }
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to read image file",
                        filename=cf.filename,
                        error=str(e),
                    )
            return [{"role": "user", "content": content}]

        input_items = await sync_to_async(build_image_input_items)()

        def get_chat_config_vision():
            from chat_next.prompts import get_effective_enabled_tools

            return (
                model_id or chat.settings.chat_model,
                get_effective_enabled_tools(chat.settings, chat=chat),
                chat.code_interpreter_container_id,
            )

        model, enabled_tools, container_id = await sync_to_async(
            get_chat_config_vision
        )()

        client = ResponsesAPIClient(
            model=model,
            tools=enabled_tools,
            previous_response_id=None,
            code_interpreter_container_id=container_id,
            code_interpreter_file_ids=None,
        )

        yield {"text": _("Processing image..."), "is_uploading": True}

        try:
            text, usage, output_items, response_id = await client.complete_chat(
                input_items, await sync_to_async(build_system_prompt)(chat)
            )

            def save_response_vision():
                response_message.text = text
                response_message.response_output = _make_json_serializable(output_items)
                response_message.response_id = response_id
                response_message.save(
                    update_fields=["text", "response_output", "response_id"]
                )

            await sync_to_async(save_response_vision)()

            if usage:
                await sync_to_async(usage.create_costs)(model)

            yield {
                "text": text,
                "output_items": output_items,
                "response_id": response_id,
                "is_complete": True,
            }
        except Exception as e:
            logger.exception("Error processing image upload", error=str(e))
            error_text = _(
                "**Error:** Failed to process uploaded image. Please try again.\n\n"
                "_(Error: %(error)s)_"
            ) % {"error": str(e)[:100]}
            yield {
                "text": error_text,
                "is_complete": True,
                "output_items": None,
            }
        return

    # For PDFs and/or other files: upload to Files API first.
    # Do not stream transient progress text here; status is shown by file widgets.

    # Upload PDFs and other files to Files API (not images)
    files_for_api = pdfs + other_files
    file_ids, file_info = await sync_to_async(upload_chat_files_to_openai)(
        files_for_api
    )

    if not file_ids and not images:
        # No files uploaded and no images to show
        yield {
            "text": _("**Error:** Failed to upload files. Please try again."),
            "is_complete": True,
            "output_items": None,
        }
        return

    # Phase 2: Make Responses API call with vision content for images/PDFs + file_ids for Code Interpreter

    # Build input with:
    # - Text description of uploaded files
    # - Vision content for images and PDFs (inline base64)
    # - file_ids go to the container config (not input)
    def build_mixed_input():
        import base64

        content = []

        # Add text describing files
        all_filenames = [cf.filename for cf in chat_files]
        if len(all_filenames) == 1:
            text = user_message.text or f"I've uploaded a file: {all_filenames[0]}"
        else:
            text = (
                user_message.text
                or f"I've uploaded {len(all_filenames)} files: {', '.join(all_filenames)}"
            )
        content.append({"type": "input_text", "text": text})

        # Add vision content for images
        for cf in images:
            saved = cf.saved_file
            try:
                with saved.file.open("rb") as f:
                    file_bytes = f.read()
                mime_type = saved.content_type or "image/png"
                b64_data = base64.b64encode(file_bytes).decode("utf-8")
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{b64_data}",
                        "detail": "high",
                    }
                )
            except Exception as e:
                logger.warning(
                    "Failed to read image file", filename=cf.filename, error=str(e)
                )

        # Add vision content for PDFs using file_id (already uploaded to Files API)
        # Per the API docs, we use {"type": "input_file", "file_id": "..."} for uploaded files
        for cf in pdfs:
            saved = cf.saved_file
            if saved and saved.openai_file_id:
                logger.debug(
                    "Adding PDF as vision content via file_id",
                    filename=cf.filename,
                    file_id=saved.openai_file_id,
                )
                content.append(
                    {
                        "type": "input_file",
                        "file_id": saved.openai_file_id,
                    }
                )
            else:
                logger.warning(
                    "PDF has no openai_file_id, skipping vision input",
                    filename=cf.filename,
                )

        return [{"role": "user", "content": content}]

    input_items = await sync_to_async(build_mixed_input)()

    # Note: We don't save input_items to user_message.response_output here because
    # it contains large base64 image/PDF data. The files are already associated
    # with the message via ChatFile records.

    # Get previous response_id if available (from earlier messages in the chat)
    def get_previous_response_id():
        last_bot_messages = chat.messages.filter(
            is_bot=True, response_id__isnull=False
        ).order_by("-date_created")[:1]
        if last_bot_messages.exists():
            return last_bot_messages[0].response_id
        return None

    previous_response_id = await sync_to_async(get_previous_response_id)()

    # Get chat options (sync DB operation for related field access)
    def get_chat_config():
        from chat_next.prompts import get_effective_enabled_tools

        return (
            model_id or chat.settings.chat_model,
            get_effective_enabled_tools(chat.settings, chat=chat),
            chat.code_interpreter_container_id,
        )

    model, enabled_tools, container_id = await sync_to_async(get_chat_config)()

    # Create client
    client = ResponsesAPIClient(
        model=model,
        tools=enabled_tools,
        previous_response_id=previous_response_id,
        code_interpreter_container_id=container_id,
        code_interpreter_file_ids=file_ids,
    )

    # Build system prompt (also accesses chat.settings)
    instructions = await sync_to_async(build_system_prompt)(chat)

    # Debug: Log what we're sending
    for item in input_items:
        if item.get("role") == "user" and isinstance(item.get("content"), list):
            for ci in item["content"]:
                ci_type = ci.get("type")
                if ci_type == "input_file":
                    logger.info(
                        "Sending input_file to API",
                        file_id=ci.get("file_id"),
                        filename=ci.get("filename"),
                    )
                elif ci_type == "input_text":
                    logger.info(
                        "Sending input_text to API", text_length=len(ci.get("text", ""))
                    )

    # Make API call (non-streaming for simplicity)
    try:
        text, usage, output_items, response_id = await client.complete_chat(
            input_items, instructions
        )

        # Store response on the message (sync DB operations)
        def save_response():
            response_message.text = text
            response_message.response_output = _make_json_serializable(output_items)
            response_message.response_id = response_id
            response_message.save(
                update_fields=["text", "response_output", "response_id"]
            )

            # Update chat container_id if we got one
            if client.last_container_id:
                chat.code_interpreter_container_id = client.last_container_id
                chat.save(update_fields=["code_interpreter_container_id"])

        await sync_to_async(save_response)()

        # Create costs
        if usage:
            await sync_to_async(usage.create_costs)(model)

        yield {
            "text": text,
            "output_items": output_items,
            "response_id": response_id,
            "is_complete": True,
        }

    except Exception as e:
        logger.exception("Error processing file upload", error=str(e))
        error_text = _(
            "**Error:** Failed to process uploaded files. Please try again.\n\n"
            "_(Error: %(error)s)_"
        ) % {"error": str(e)[:100]}
        yield {
            "text": error_text,
            "is_complete": True,
            "output_items": None,
        }


# ============================================================================
# Streaming wrapper for htmx_stream compatibility
# ============================================================================


async def stream_chat_for_htmx(
    client: ResponsesAPIClient,
    input_items: list,
    instructions: str = None,
    full_input_items: list = None,
    initial_processing_steps: list | None = None,
):
    """
    Async generator that wraps ResponsesAPIClient.stream_chat() for htmx_stream.

    Handles local function calling:
    - When the model requests a function_call, executes it locally
    - Sends the function_call_output back to continue the response
    - Loops until no more function calls are requested

    Yields dicts with 'text', 'reasoning_steps', 'tool_calls', 'processing_steps',
    'is_reasoning', and 'output_items' keys that htmx_stream understands.

    Args:
        client: ResponsesAPIClient instance
        input_items: List of input items (conversation history)
        instructions: System/developer instructions
        full_input_items: Optional full conversation history for compaction.
            When stream_chat_for_htmx is called from the approval resume flow,
            input_items contains only function_call_output items (chained via
            previous_response_id).  If compaction is needed, the full
            conversation must be provided here so the compact endpoint
            receives meaningful context.
        initial_processing_steps: Optional raw processing steps to seed before
            any streaming begins (used for between-message compaction).

    Yields:
        Dict with streaming state compatible with htmx_stream
    """
    # Lazy import to avoid circular dependency
    from chat_next.tools import (
        TOOL_REGISTRY,
        build_function_call_output,
        execute_tool_call,
    )

    def _extract_skill_action(result_output: dict | None) -> dict | None:
        """Extract normalized action metadata for skill create/edit results."""
        if not isinstance(result_output, dict):
            return None

        skill_id = result_output.get("skill_id")
        edit_link_token = result_output.get("edit_link_token")
        if not skill_id or not edit_link_token:
            return None

        label = (
            result_output.get("display_name_en")
            or result_output.get("display_name_fr")
            or "Open skill"
        )
        return {
            "skill_id": skill_id,
            "label": label,
            "edit_link_token": edit_link_token,
        }

    def _prepend_skill_action_link(text: str, skill_action: dict | None) -> str:
        """Ensure the final assistant message starts with the open-skill link."""
        if not skill_action:
            return text

        token = skill_action["edit_link_token"]
        existing_text = text or ""
        if existing_text.lstrip().startswith(token) or token in existing_text:
            return existing_text

        return f"{token}\n\n{existing_text}"

    # Track accumulated state across potential function call iterations
    all_output_items = []
    all_processing_steps = list(initial_processing_steps or [])
    current_response_id = None
    skill_action_to_surface = None
    loaded_skill_state = _normalize_loaded_skill_state()
    max_proactive_compactions_per_turn = 3
    max_reactive_compactions_per_turn = 4
    total_compaction_attempts = 0
    proactive_compaction_attempts = 0
    reactive_compaction_attempts = 0
    # Cache identical local function-call results within a single turn so the
    # model cannot repeatedly hammer the same local/public tool with the same
    # arguments during iterative tool use or approval resume.
    if not hasattr(client, "local_tool_result_cache"):
        client.local_tool_result_cache = {}
    local_tool_result_cache = client.local_tool_result_cache
    # Maximum function call iterations to prevent infinite loops
    # Configurable via chat options (default: 25)
    max_iterations = 25
    try:
        if (
            client
            and getattr(client, "chat", None)
            and hasattr(client.chat, "settings")
        ):
            configured_max = int(
                getattr(client.chat.settings, "chat_max_iterations", 25) or 25
            )
            # Guardrails: keep sane bounds even if DB value was manually edited
            max_iterations = max(1, min(configured_max, 200))
    except Exception:
        # Keep default if option is unavailable or invalid
        max_iterations = 25
    iteration = 0

    # Keep a reference to the original full conversation for compaction recovery.
    # During tool-call continuations, input_items gets replaced with just the
    # function_call_output items; if a context window error occurs at that point
    # we need the full conversation to pass to compact_conversation().
    # When called from the approval resume flow, full_input_items provides the
    # complete conversation (input_items is only function_call_output items).
    original_full_input_items = list(
        full_input_items if full_input_items is not None else input_items
    )

    def _append_compaction_processing_step() -> None:
        """Append a compaction step once without creating adjacent duplicates."""
        if all_processing_steps:
            last_step = all_processing_steps[-1]
            if (
                isinstance(last_step, dict)
                and last_step.get("type") == "tool_call"
                and last_step.get("tool_type") == "compaction"
            ):
                return
        all_processing_steps.append(copy.deepcopy(COMPACTION_PROCESSING_STEP))

    def _build_compaction_progress_update(
        *,
        last_chunk: StreamChunk | None,
        completed: bool,
        usage: dict | None = None,
    ) -> dict:
        processing_steps = list(all_processing_steps)
        if not completed:
            processing_steps.append(
                copy.deepcopy(COMPACTION_IN_PROGRESS_PROCESSING_STEP)
            )

        return {
            "text": last_chunk.text if last_chunk else "",
            "reasoning_steps": last_chunk.reasoning_steps if last_chunk else [],
            "tool_calls": [],
            "processing_steps": processing_steps,
            "is_reasoning": False,
            "output_items": None,
            "file_citations": None,
            "code_interpreter_outputs": None,
            "container_id": None,
            "response_id": None,
            "usage": usage,
        }

    async def _compact_and_reset(source: str, **log_fields) -> tuple[bool, dict | None]:
        """Compact the current conversation state and restart from the result."""
        nonlocal input_items
        nonlocal original_full_input_items
        nonlocal all_output_items
        nonlocal total_compaction_attempts
        nonlocal proactive_compaction_attempts
        nonlocal reactive_compaction_attempts

        if source == "proactive" and (
            proactive_compaction_attempts >= max_proactive_compactions_per_turn
        ):
            logger.warning(
                "Skipping proactive compaction: max proactive compactions reached for this response",
                proactive_compaction_attempts=proactive_compaction_attempts,
                max_proactive_compactions_per_turn=max_proactive_compactions_per_turn,
                iteration=iteration,
            )
            return False, None

        if source == "reactive" and (
            reactive_compaction_attempts >= max_reactive_compactions_per_turn
        ):
            logger.warning(
                "Skipping reactive compaction: max reactive compactions reached for this response",
                reactive_compaction_attempts=reactive_compaction_attempts,
                max_reactive_compactions_per_turn=max_reactive_compactions_per_turn,
                iteration=iteration,
            )
            return False, None

        total_compaction_attempts += 1
        if source == "reactive":
            reactive_compaction_attempts += 1
        elif source == "proactive":
            proactive_compaction_attempts += 1

        compact_input = original_full_input_items + all_output_items
        logger.warning(
            "Compacting conversation during tool loop",
            source=source,
            iteration=iteration,
            total_compaction_attempts=total_compaction_attempts,
            proactive_compaction_attempts=proactive_compaction_attempts,
            reactive_compaction_attempts=reactive_compaction_attempts,
            output_items_count=len(all_output_items),
            compact_input_count=len(compact_input),
            **log_fields,
        )

        compacted_items, compaction_usage = await client.compact_conversation(
            compact_input,
            instructions,
        )
        await sync_to_async(create_compaction_costs)(
            compaction_usage,
            client.model,
        )

        input_items = compacted_items
        original_full_input_items = list(compacted_items)
        all_output_items = []
        _append_compaction_processing_step()
        compacted_usage_estimate = estimate_compacted_conversation_usage(
            compacted_items
        )

        logger.info(
            "Compaction succeeded during tool loop",
            source=source,
            iteration=iteration,
            total_compaction_attempts=total_compaction_attempts,
            proactive_compaction_attempts=proactive_compaction_attempts,
            reactive_compaction_attempts=reactive_compaction_attempts,
            compacted_items=len(compacted_items),
        )

        return True, compacted_usage_estimate

    while iteration < max_iterations:
        iteration += 1
        function_calls_to_execute = []
        last_chunk = None

        # Stream the response — wrapping with context-window recovery so that
        # errors during tool-call continuations can be retried after compaction.
        try:
            async for chunk in client.stream_chat(input_items, instructions):
                last_chunk = chunk

                # Convert ToolCall objects to dicts for JSON serialization
                tool_calls_dicts = []
                for tc in chunk.tool_calls:
                    if isinstance(tc, ToolCall):
                        tool_calls_dicts.append(
                            {
                                "tool_type": tc.tool_type,
                                "status": tc.status,
                                "query": tc.query,
                                "details": tc.details,
                            }
                        )
                    else:
                        tool_calls_dicts.append(tc)

                # Combine accumulated processing steps with current chunk's steps
                combined_processing_steps = (
                    all_processing_steps + chunk.processing_steps
                )

                # For intermediate chunks, always yield streaming state
                if not chunk.is_complete:
                    yield {
                        "text": chunk.text,
                        "reasoning_steps": chunk.reasoning_steps,
                        "tool_calls": tool_calls_dicts,
                        "processing_steps": combined_processing_steps,
                        "is_reasoning": chunk.is_reasoning,
                        "output_items": None,
                        "file_citations": None,
                        "code_interpreter_outputs": None,
                        "container_id": None,
                        "response_id": None,
                    }
                    continue

                # Stream complete - check for function calls in output_items
                if chunk.output_items:
                    seen_call_ids = set()
                    seen_signatures = set()
                    for item in chunk.output_items:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "function_call"
                        ):
                            call_id = item.get("call_id") or item.get("id")
                            signature = _normalized_function_call_signature(item)

                            # Some responses can contain duplicate function_call items.
                            # Ignore exact duplicates to avoid repeated execution/display.
                            if call_id and call_id in seen_call_ids:
                                logger.info(
                                    "Skipping duplicate function_call by call_id",
                                    call_id=call_id,
                                    name=item.get("name"),
                                )
                                continue
                            if signature in seen_signatures:
                                logger.info(
                                    "Skipping duplicate function_call by signature",
                                    name=item.get("name"),
                                    call_id=call_id,
                                )
                                continue

                            if call_id:
                                seen_call_ids.add(call_id)
                            seen_signatures.add(signature)

                            logger.info(
                                "Found function_call in output_items",
                                item_keys=list(item.keys()),
                                call_id=item.get("call_id"),
                                id=item.get("id"),
                                name=item.get("name"),
                            )
                            function_calls_to_execute.append(item)

                # Store response info
                if chunk.response_id:
                    current_response_id = chunk.response_id
                    # Update client's previous_response_id for chaining
                    client.previous_response_id = chunk.response_id

                # Collect all output items
                if chunk.output_items:
                    all_output_items.extend(chunk.output_items)

        except (APIError, BadRequestError, APIStatusError) as e:
            error_str = str(e).lower()
            if _is_context_window_error(error_str):
                context_mgmt = get_context_management_mode(client)
                if context_mgmt == "compact":
                    try:
                        yield _build_compaction_progress_update(
                            last_chunk=last_chunk,
                            completed=False,
                        )

                        compacted, compacted_usage_estimate = await _compact_and_reset(
                            "reactive",
                            error=str(e),
                        )
                        if not compacted:
                            raise e
                        yield _build_compaction_progress_update(
                            last_chunk=last_chunk,
                            completed=True,
                            usage=compacted_usage_estimate,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to compact after context window error in tool loop"
                        )
                        raise e
                    continue
            raise

        # If no function calls, we're done - yield final chunk with all accumulated output
        if not function_calls_to_execute:
            if last_chunk:
                tool_calls_dicts = []
                for tc in last_chunk.tool_calls:
                    if isinstance(tc, ToolCall):
                        tool_calls_dicts.append(
                            {
                                "tool_type": tc.tool_type,
                                "status": tc.status,
                                "query": tc.query,
                                "details": tc.details,
                            }
                        )
                    else:
                        tool_calls_dicts.append(tc)

                # Combine all accumulated steps with final chunk's steps
                final_processing_steps = (
                    all_processing_steps + last_chunk.processing_steps
                )

                # Use the usage from the response - input_tokens already includes full context
                final_usage = last_chunk.usage.copy() if last_chunk.usage else {}

                yield {
                    "text": _prepend_skill_action_link(
                        last_chunk.text,
                        skill_action_to_surface,
                    ),
                    "reasoning_steps": last_chunk.reasoning_steps,
                    "tool_calls": tool_calls_dicts,
                    "processing_steps": final_processing_steps,
                    "is_reasoning": last_chunk.is_reasoning,
                    "output_items": all_output_items,
                    "file_citations": (
                        last_chunk.file_citations if last_chunk.is_complete else None
                    ),
                    "code_interpreter_outputs": (
                        last_chunk.code_interpreter_outputs
                        if last_chunk.is_complete
                        else None
                    ),
                    "container_id": (
                        last_chunk.container_id if last_chunk.is_complete else None
                    ),
                    "response_id": current_response_id,
                    "usage": final_usage if last_chunk.is_complete else None,
                }
            return  # Done!

        # Save processing steps from this iteration before function execution
        if last_chunk and last_chunk.processing_steps:
            all_processing_steps.extend(last_chunk.processing_steps)

        # Log ALL function calls we need to handle
        logger.info(
            "Processing function calls",
            iteration=iteration,
            total_function_calls=len(function_calls_to_execute),
            function_calls=[
                {
                    "name": fc.get("name"),
                    "call_id": fc.get("call_id"),
                    "id": fc.get("id"),
                }
                for fc in function_calls_to_execute
            ],
            current_response_id=current_response_id,
        )

        # Separate function calls into auto-approved vs approval-needed
        # Execute all auto-approved calls first, then pause for approval if needed
        auto_approve_tools = []
        if client.chat and hasattr(client.chat, "settings"):
            auto_approve_tools = client.chat.settings.chat_auto_approve_tools or []

        auto_approved_calls = []
        approval_needed_calls = []
        pending_tool_call = None
        pending_tool = None
        call_metadata_by_id = {}

        def _set_call_metadata(call_id: str | None, **metadata) -> None:
            if not call_id:
                return
            call_metadata_by_id.setdefault(call_id, {}).update(metadata)

        # At the iteration limit, force non-cached calls to require approval.
        # This prevents the loop from breaking with unanswered tool calls in
        # OpenAI's response chain, which would cause 400 errors on follow-up.
        at_iteration_limit = iteration >= max_iterations

        for fc in function_calls_to_execute:
            tool = TOOL_REGISTRY.get(fc.get("name"))
            signature = _normalized_function_call_signature(fc)
            call_id = fc.get("call_id") or fc.get("id")
            if signature in local_tool_result_cache:
                # Already executed with same args — auto-approve to return cached result
                auto_approved_calls.append(fc)
                _set_call_metadata(call_id, approval_source=APPROVAL_SOURCE_CACHE)
                continue

            risk_review = None
            if tool and tool.is_external_tool:
                risk_review = await sync_to_async(review_external_tool_call)(
                    function_call=fc,
                    tool=tool,
                )
                fc["risk_review"] = risk_review
                fc["pii_flagged"] = bool(risk_review.get("pii_flagged"))
                _set_call_metadata(call_id, risk_review=risk_review)

            if at_iteration_limit:
                # At max iterations: force approval to keep the response chain valid.
                approval_needed_calls.append(fc)
                _set_call_metadata(call_id, approval_source=APPROVAL_SOURCE_MANUAL)
                if pending_tool_call is None:
                    pending_tool_call = fc
                    pending_tool = tool
                continue
            elif tool and tool.requires_approval:
                if risk_review and risk_review.get("flagged"):
                    approval_needed_calls.append(fc)
                    _set_call_metadata(call_id, approval_source=APPROVAL_SOURCE_MANUAL)
                    if pending_tool_call is None:
                        pending_tool_call = fc
                        pending_tool = tool
                    continue

                policy_decision = await evaluate_approval_policy(
                    tool=tool,
                    user=client.user,
                    chat=client.chat,
                    function_call=fc,
                )
                if policy_decision.auto_approve:
                    auto_approved_calls.append(fc)
                    _set_call_metadata(
                        call_id,
                        approval_source=policy_decision.approval_source,
                        approval_rule=policy_decision.matched_rule,
                    )
                elif tool.allow_auto_approve and _tool_is_user_auto_approved(
                    tool, auto_approve_tools
                ):
                    # Auto-approved - execute it
                    auto_approved_calls.append(fc)
                    _set_call_metadata(
                        call_id,
                        approval_source=APPROVAL_SOURCE_USER_ALLOWLIST,
                    )
                else:
                    # Needs manual approval
                    approval_needed_calls.append(fc)
                    _set_call_metadata(call_id, approval_source=APPROVAL_SOURCE_MANUAL)
                    if pending_tool_call is None:
                        # First tool needing manual approval - we'll pause for this one
                        pending_tool_call = fc
                        pending_tool = tool
            else:
                # Doesn't require approval - execute it
                auto_approved_calls.append(fc)

        logger.info(
            "Function call classification",
            auto_approved=[
                {
                    "name": fc.get("name"),
                    "approval_source": call_metadata_by_id.get(
                        fc.get("call_id") or fc.get("id"), {}
                    ).get("approval_source"),
                }
                for fc in auto_approved_calls
            ],
            approval_needed=[
                {
                    "name": fc.get("name"),
                    "approval_source": call_metadata_by_id.get(
                        fc.get("call_id") or fc.get("id"), {}
                    ).get("approval_source"),
                }
                for fc in approval_needed_calls
            ],
            pending_tool=pending_tool_call.get("name") if pending_tool_call else None,
        )

        # Estimate costs and check warning threshold for all pending
        # function calls.  This is the single place where the cost decision
        # is made — approval_stream simply reads the result.
        cost_warning, estimated_cost, formatted_cost = await sync_to_async(
            check_tool_cost_warning
        )(function_calls_to_execute, chat=client.chat)

        # When cost exceeds the threshold, pause ALL tool execution
        # (including auto-approved calls) so the user confirms the cost.
        # This ensures expensive tools get a cost gate regardless of whether
        # they require manual approval.
        if cost_warning:
            approval_needed_calls = function_calls_to_execute
            auto_approved_calls = []
            for fc in approval_needed_calls:
                call_id = fc.get("call_id") or fc.get("id")
                _set_call_metadata(call_id, approval_source=APPROVAL_SOURCE_MANUAL)
            if not pending_tool_call:
                pending_tool_call = approval_needed_calls[0]
                pending_tool = TOOL_REGISTRY.get(pending_tool_call.get("name"))

        # Execute all auto-approved calls BEFORE pausing for approval
        pre_executed_outputs = []
        executed_uncached_tool_output = False
        for fc in auto_approved_calls:
            call_id = fc.get("call_id") or fc.get("id")
            name = fc.get("name")
            arguments = fc.get("arguments", "{}")
            signature = _normalized_function_call_signature(fc)
            cached_result = local_tool_result_cache.get(signature)
            tool = TOOL_REGISTRY.get(name)
            call_metadata = call_metadata_by_id.get(call_id, {})
            approval_source = call_metadata.get("approval_source")
            approval_rule = call_metadata.get("approval_rule")
            risk_review = call_metadata.get("risk_review") or fc.get("risk_review")
            pii_flagged = bool(
                (risk_review or {}).get("pii_flagged") or fc.get("pii_flagged")
            )

            if cached_result is not None:
                output = copy.deepcopy(cached_result.get("output"))
                status = cached_result.get("status", "completed")
                logger.info(
                    "Reusing cached local function-call result",
                    function=name,
                    call_id=call_id,
                )
            else:
                # Only show new processing steps when we actually execute the tool.
                # Cache hits are continuation artifacts, not fresh tool activity.
                function_step = {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "in_progress",
                    "details": {
                        "name": name,
                        "arguments": arguments,
                        "tool_label": tool.display_name if tool else name,
                        "approval_source": approval_source,
                        "approval_rule": approval_rule,
                        "pii_flagged": pii_flagged,
                        "risk_review": risk_review,
                    },
                }

                yield {
                    "text": last_chunk.text if last_chunk else "",
                    "reasoning_steps": last_chunk.reasoning_steps if last_chunk else [],
                    "tool_calls": [],
                    "processing_steps": all_processing_steps + [function_step],
                    "is_reasoning": False,
                    "output_items": None,
                    "file_citations": None,
                    "code_interpreter_outputs": None,
                    "container_id": None,
                    "response_id": None,
                }

                # Execute the function
                result = await execute_tool_call(
                    tool_name=name,
                    arguments=arguments,
                    user=client.user,
                    chat=client.chat,
                    extra_context={"responses_client": client},
                )

                # Build the output item
                if result.get("success"):
                    output = result.get("result")
                    status = "completed"
                    executed_uncached_tool_output = True
                    if name == "load_skill_instructions":
                        instructions, loaded_skill_state = (
                            _activate_loaded_skill_output(
                                client=client,
                                instructions=instructions,
                                loaded_skill_state=loaded_skill_state,
                                tool_arguments=arguments,
                                tool_output=output,
                            )
                        )
                        if client.chat is not None:
                            loaded_skill_state = await sync_to_async(
                                persist_chat_loaded_skill_state
                            )(
                                client.chat,
                                loaded_skill_state,
                            )
                    if name in {
                        "create_skill",
                        "create_skill_from_preset",
                        "edit_skill",
                    }:
                        skill_action_to_surface = _extract_skill_action(
                            _unwrap_tool_result_payload(output)
                        )
                else:
                    output = {"error": result.get("error", "Unknown error")}
                    status = "failed"
                    executed_uncached_tool_output = True

                local_tool_result_cache[signature] = {
                    "output": copy.deepcopy(output),
                    "status": status,
                }

            output_item = build_function_call_output(call_id, output)
            pre_executed_outputs.append(output_item)
            all_output_items.append(
                _sanitize_function_call_output_for_storage(output_item)
            )

            # Only add a completed processing step for actual executions.
            if cached_result is None:
                all_processing_steps.append(
                    {
                        "type": "tool_call",
                        "tool_type": "function_call",
                        "status": status,
                        "details": {
                            "name": name,
                            "call_id": call_id,
                            "arguments": arguments,
                            "output": output,
                            "tool_label": tool.display_name if tool else name,
                            "approval_source": approval_source,
                            "approval_rule": approval_rule,
                            "pii_flagged": pii_flagged,
                            "risk_review": risk_review,
                        },
                    }
                )

                estimated_usage = estimate_tool_continuation_usage(
                    last_chunk.usage if last_chunk else None,
                    [
                        _sanitize_function_call_output_for_storage(item)
                        for item in pre_executed_outputs
                    ],
                )

                yield {
                    "text": last_chunk.text if last_chunk else "",
                    "reasoning_steps": last_chunk.reasoning_steps if last_chunk else [],
                    "tool_calls": [],
                    "processing_steps": all_processing_steps,
                    "is_reasoning": False,
                    "output_items": None,
                    "file_citations": None,
                    "code_interpreter_outputs": None,
                    "container_id": None,
                    "response_id": None,
                    "usage": estimated_usage,
                }

            logger.info(
                "Pre-executed auto-approved function call",
                function=name,
                call_id=call_id,
                cached=cached_result is not None,
            )

        # Now pause for approval if there are any approval-needed calls
        if approval_needed_calls:
            # Use the first one as the "primary" for approval_request_id (used for Button click)
            pending_tool_call = approval_needed_calls[0]
            pending_tool = TOOL_REGISTRY.get(pending_tool_call.get("name"))
            pending_call_id = pending_tool_call.get("call_id") or pending_tool_call.get(
                "id"
            )
            batch_external_service_names = sorted(
                {
                    str(fc_tool.external_service_name or fc_tool.display_name)
                    for fc in approval_needed_calls
                    for fc_tool in [TOOL_REGISTRY.get(fc.get("name"))]
                    if fc_tool and fc_tool.is_external_tool
                }
            )
            batch_contains_external_tools = bool(batch_external_service_names)
            approval_requested_at = timezone.now().isoformat()

            # Log what we're pausing with
            logger.info(
                "Pausing for local tool approval",
                pending_call_id=pending_call_id,
                pending_tool_name=pending_tool_call.get("name"),
                response_id_to_resume_from=current_response_id,
                pre_executed_count=len(pre_executed_outputs),
                pre_executed_call_ids=[
                    item.get("call_id") for item in pre_executed_outputs
                ],
                total_function_calls_in_response=len(function_calls_to_execute),
                total_approval_needed=len(approval_needed_calls),
                approval_needed_calls=[
                    {"name": fc.get("name"), "call_id": fc.get("call_id")}
                    for fc in approval_needed_calls
                ],
            )

            # Create a processing step for EACH approval-needed call
            # This shows all of them in the UI so user knows what they're approving
            # Only the LAST one gets the approval_request_id so buttons appear once at the end
            for i, fc in enumerate(approval_needed_calls):
                fc_call_id = fc.get("call_id") or fc.get("id")
                fc_tool = TOOL_REGISTRY.get(fc.get("name"))
                fc_metadata = call_metadata_by_id.get(fc_call_id, {})
                fc_risk_review = fc_metadata.get("risk_review") or fc.get("risk_review")
                fc_pii_flagged = bool(
                    (fc_risk_review or {}).get("pii_flagged") or fc.get("pii_flagged")
                )
                is_last = i == len(approval_needed_calls) - 1
                approval_step = {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {
                        "name": fc.get("name"),
                        "call_id": fc_call_id,
                        "arguments": fc.get("arguments", "{}"),
                        # Only the last step gets approval_request_id to show buttons
                        "approval_request_id": pending_call_id if is_last else None,
                        "tool_label": (fc_tool.display_name or fc_tool.name)
                        if fc_tool
                        else fc.get("name"),
                        "allow_auto_approve": fc_tool.allow_auto_approve
                        if fc_tool
                        else False,
                        "is_external_tool": bool(fc_tool and fc_tool.is_external_tool),
                        "external_service_name": (
                            str(fc_tool.external_service_name or "")
                            if fc_tool and fc_tool.is_external_tool
                            else ""
                        ),
                        "approval_requires_external_warning": batch_contains_external_tools,
                        "external_service_names": batch_external_service_names,
                        "pii_flagged": fc_pii_flagged,
                        "risk_review": fc_risk_review,
                        "max_iterations_reached": at_iteration_limit,
                        "approval_source": APPROVAL_SOURCE_MANUAL,
                        # Cost info so the frontend can show a cost warning
                        "cost_warning": cost_warning,
                        "formatted_cost": formatted_cost if cost_warning else None,
                    },
                }
                all_processing_steps.append(approval_step)

            tool_calls_dicts = []
            if last_chunk:
                for tc in last_chunk.tool_calls:
                    if isinstance(tc, ToolCall):
                        tool_calls_dicts.append(
                            {
                                "tool_type": tc.tool_type,
                                "status": tc.status,
                                "query": tc.query,
                                "details": tc.details,
                            }
                        )
                    else:
                        tool_calls_dicts.append(tc)

            yield {
                "text": last_chunk.text if last_chunk else "",
                "reasoning_steps": last_chunk.reasoning_steps if last_chunk else [],
                "tool_calls": tool_calls_dicts,
                "processing_steps": all_processing_steps,
                "is_reasoning": False,
                "output_items": all_output_items,
                "file_citations": None,
                "code_interpreter_outputs": None,
                "container_id": None,
                "response_id": current_response_id,
                "usage": last_chunk.usage if last_chunk else None,
                "pending_local_tool": {
                    # Primary tool info (for backward compat and approval_request_id)
                    "name": pending_tool_call.get("name"),
                    "call_id": pending_call_id,
                    "arguments": pending_tool_call.get("arguments", "{}"),
                    "tool_label": (pending_tool.display_name or pending_tool.name)
                    if pending_tool
                    else pending_tool_call.get("name"),
                    "allow_auto_approve": pending_tool.allow_auto_approve
                    if pending_tool
                    else False,
                    "approval_source": APPROVAL_SOURCE_MANUAL,
                    # Include pre-executed outputs so approval_stream can send them all
                    "pre_executed_outputs": pre_executed_outputs,
                    # Include all approval-needed calls for reference (approval_stream
                    # will actually use response_output to get the full function_call data)
                    "approval_needed_count": len(approval_needed_calls),
                    "approval_calls": approval_needed_calls,
                    "approval_requested_at": approval_requested_at,
                    "batch_contains_external_tools": batch_contains_external_tools,
                    "external_service_names": batch_external_service_names,
                    "is_external_tool": bool(
                        pending_tool and pending_tool.is_external_tool
                    ),
                    "external_service_name": (
                        str(pending_tool.external_service_name or "")
                        if pending_tool and pending_tool.is_external_tool
                        else ""
                    ),
                    "pii_flagged": bool(
                        (
                            call_metadata_by_id.get(pending_call_id, {}).get(
                                "risk_review", {}
                            )
                            or pending_tool_call.get("risk_review", {})
                        ).get("pii_flagged")
                    ),
                    "risk_review": call_metadata_by_id.get(pending_call_id, {}).get(
                        "risk_review"
                    )
                    or pending_tool_call.get("risk_review"),
                    # Persist dynamically loaded skill tools/instructions so a
                    # recreated client can resume with the same unlocked state.
                    "loaded_skill_state": loaded_skill_state,
                    # Flag so the UI/approval_stream knows this is an iteration-limit pause
                    "max_iterations_reached": at_iteration_limit,
                    # Pre-computed cost decision so approval_stream doesn't need to
                    # re-estimate or check thresholds.
                    "cost_warning": cost_warning,
                    "estimated_cost": str(estimated_cost) if estimated_cost else None,
                    "formatted_cost": formatted_cost,
                },
            }
            return

        # If we had any function calls to execute (all auto-approved), continue loop
        if pre_executed_outputs:
            # Proactive compaction: estimate whether sending tool results
            # would cross the same model-specific threshold used elsewhere.
            # Use sanitized outputs so inline vision payloads (base64 PDFs,
            # data-URL images) don't inflate the token estimate.
            sanitized_for_estimation = [
                _sanitize_function_call_output_for_storage(item)
                for item in pre_executed_outputs
            ]
            should_compact, proactive_metrics = (
                should_proactively_compact_tool_continuation(
                    context_management_mode=get_context_management_mode(client),
                    usage=last_chunk.usage if last_chunk else None,
                    tool_output_items=sanitized_for_estimation,
                    model_id=getattr(client, "model", DEFAULT_CHAT_MODEL_ID),
                )
            )
            allow_proactive_compaction = (
                executed_uncached_tool_output or proactive_compaction_attempts == 0
            )
            if should_compact:
                if allow_proactive_compaction:
                    try:
                        yield _build_compaction_progress_update(
                            last_chunk=last_chunk,
                            completed=False,
                        )

                        compacted, compacted_usage_estimate = await _compact_and_reset(
                            "proactive",
                            **proactive_metrics,
                        )
                        if not compacted:
                            logger.warning(
                                "Skipping proactive compaction for current tool continuation",
                                iteration=iteration,
                                total_compaction_attempts=total_compaction_attempts,
                                proactive_compaction_attempts=proactive_compaction_attempts,
                                reactive_compaction_attempts=reactive_compaction_attempts,
                                **proactive_metrics,
                            )
                        # Yield an intermediate update so the UI shows
                        # the compaction step immediately.
                        if compacted:
                            yield _build_compaction_progress_update(
                                last_chunk=last_chunk,
                                completed=True,
                                usage=compacted_usage_estimate,
                            )
                            continue
                    except Exception:
                        logger.exception(
                            "Proactive compaction failed, proceeding with original items"
                        )
                else:
                    logger.info(
                        "Skipping proactive compaction for cached-only tool continuation after earlier proactive compaction",
                        iteration=iteration,
                        total_compaction_attempts=total_compaction_attempts,
                        proactive_compaction_attempts=proactive_compaction_attempts,
                        reactive_compaction_attempts=reactive_compaction_attempts,
                        **proactive_metrics,
                    )

            input_items = pre_executed_outputs
            continue

        # If we get here with no pre_executed_outputs and no pending_tool_call,
        # it means function_calls_to_execute was empty (shouldn't happen in normal flow)
        logger.warning(
            "No function calls executed and no approval pending",
            function_calls=len(function_calls_to_execute),
        )
        input_items = []

    # Safety net: if we exit the while loop without returning, yield what we have.
    # With the iteration-limit approval gate above, this should rarely fire.
    if iteration >= max_iterations:
        logger.warning(
            "Max tool-call iterations reached (safety net)",
            max_iterations=max_iterations,
            total_output_items=len(all_output_items),
        )
        yield {
            "text": last_chunk.text if last_chunk and last_chunk.text else "",
            "reasoning_steps": last_chunk.reasoning_steps if last_chunk else [],
            "tool_calls": [],
            "processing_steps": all_processing_steps,
            "is_reasoning": False,
            "output_items": all_output_items,
            "file_citations": None,
            "code_interpreter_outputs": None,
            "container_id": None,
            "response_id": current_response_id,
            "usage": last_chunk.usage if last_chunk else None,
        }
