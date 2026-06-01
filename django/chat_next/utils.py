import asyncio
import copy
import html
import json
import re
import time
import uuid
from typing import AsyncGenerator, Generator

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

import markdown
import tiktoken
from asgiref.sync import sync_to_async
from data_fetcher import cache_within_request
from structlog import get_logger

from chat_next._llm.code_interpreter import (  # noqa: F401
    download_code_interpreter_images,
    download_container_files,
    download_sandbox_files,
    replace_sandbox_urls,
)
from chat_next._llm.models import DEFAULT_CHAT_MODEL_ID
from chat_next.error_messages import (
    build_context_window_error_message,
    is_context_window_error,
)
from chat_next.models import Chat, Message

logger = get_logger(__name__)
PLACEHOLDER_CHAT_TITLES = {
    "",
    "untitled chat",
    "conversation sans titre",
}
CHAT_TITLE_GENERATION_MODEL_ID = "gpt-5.4-nano"
MIN_CHAT_TEXT_LENGTH_FOR_TITLE = 25
CHAT_TITLE_PREFIX_STRIP_RE = re.compile(
    r"^(?:please|pls|can you|could you|would you|will you|help me(?:\s+to)?|i need(?:\s+help)?(?:\s+with)?|need(?:\s+help)?(?:\s+with)?|j'ai besoin(?:\s+d['’]aide)?(?:\s+pour)?|peux-tu|pouvez-vous|s['’]il te plaît|s['’]il vous plaît|aide-moi(?:\s+à)?|aidez-moi(?:\s+à)?|merci(?:\s+de)?)\s+",
    re.IGNORECASE,
)
CHAT_TITLE_ACTION_STRIP_RE = re.compile(
    r"^(?:summari[sz]e|draft|write|create|generate|translate|improve|review|analy[sz]e|explain|compare|show me|tell me about|give me|make|build|résume(?:r)?|rédige(?:r)?|tradui(?:s|re)|explique(?:r)?|analyse(?:r)?|compare(?:r)?)\s+(?:me\s+)?(?:a|an|the|my|some|this|that|un|une|des|le|la|les|mon|ma|mes)?\s*",
    re.IGNORECASE,
)
TRIVIAL_CHAT_TITLE_TOPICS = {"hi", "hello", "hey", "bonjour", "salut"}
FRENCH_REASONING_SUFFIX = " (raisonne exclusivement en anglais)"
PROCESSING_STEPS_TRANSLATIONS_KEY = "processing_steps_translations"
SSE_KEEPALIVE_INTERVAL_SECONDS = 10
SSE_RENDER_THROTTLE_MESSAGE_LENGTH = 8000
SSE_RENDER_THROTTLE_INTERVAL_SECONDS = 0.12
SSE_LARGE_PROCESSING_OOB_PAYLOAD_LENGTH = 6000
SSE_LARGE_PROCESSING_OOB_INTERVAL_SECONDS = 0.35

# Markdown instance
md = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "tables", "extra"], tab_length=2
)


def make_json_serializable(value):
    """Recursively convert lazy translation proxies into JSON-safe values."""
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, dict):
        return {
            make_json_serializable(key): make_json_serializable(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [make_json_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [make_json_serializable(item) for item in value]
    return value


def _render_stream_message_actions_oob(
    message: Message, captured_usage: dict
) -> tuple[str | None, str]:
    """Render an out-of-band message actions update for transient usage.

    Runs in sync context so model helpers can safely access ORM-backed relations.
    Returns `(context_usage_json, html)`.
    """
    transient_context_usage = message.get_context_usage_display_for_usage(
        captured_usage
    )
    context_usage_json = (
        json.dumps(transient_context_usage, sort_keys=True)
        if transient_context_usage
        else None
    )
    message._transient_context_usage = transient_context_usage
    message._transient_usage = captured_usage
    try:
        html_output = render_to_string(
            "chat_next/components/message_actions.html",
            {
                "message": message,
                "read_only": False,
                "actions_swap_oob": True,
            },
        )
    finally:
        if hasattr(message, "_transient_context_usage"):
            delattr(message, "_transient_context_usage")
        if hasattr(message, "_transient_usage"):
            delattr(message, "_transient_usage")

    return context_usage_json, html_output


def is_placeholder_chat_title(title: str) -> bool:
    """Return True when title is an auto-placeholder and should be replaced."""
    return (title or "").strip().lower() in PLACEHOLDER_CHAT_TITLES


def enqueue_chat_title_generation(chat_id, language=None, timeout=600):
    """Queue chat title generation once per chat within a short lock window."""
    from django.db import transaction

    from otto.priorities import HIGH

    lock_key = f"chat_title_generation_{chat_id}"
    if not cache.add(lock_key, True, timeout=timeout):
        return False

    def _dispatch():
        try:
            from chat_next.tasks import generate_chat_title_task

            generate_chat_title_task.apply_async(
                args=[str(chat_id), language],
                priority=HIGH,
            )
        except Exception:
            cache.delete(lock_key)
            logger.exception("Failed to queue chat title generation", chat_id=chat_id)

    transaction.on_commit(_dispatch)
    return True


def annotate_pending_titles(chats, language=None):
    """
    For each chat in *chats*, set ``is_title_pending`` and display title, and
    enqueue async title generation when appropriate.
    Callers are responsible for setting ``current_chat`` separately.
    """
    for chat_obj in chats:
        if is_placeholder_chat_title(chat_obj.title):
            should_enqueue = (getattr(chat_obj, "message_count", 0) or 0) > 0
            chat_obj.is_title_pending = should_enqueue
            chat_obj.title = _("Untitled chat")
            if should_enqueue:
                enqueue_chat_title_generation(chat_obj.id, language=language)
        else:
            chat_obj.is_title_pending = False


def link_chat_files_to_library(files, message, data_source):
    """
    Link ChatFile objects to the library as Documents, reusing existing documents
    by matching data_source, filename, and file hash.

    Args:
        files: List of ChatFile objects
        message: Message object to associate with the documents
        data_source: DataSource to link the documents to
    """
    from librarian.models import Document

    for file in files:
        # Skip if already linked to a document
        if hasattr(file, "document") and file.document:
            continue

        # Check for existing document by data_source, filename, and file hash
        existing_document = Document.objects.filter(
            data_source=data_source,
            filename=file.filename,
            saved_file__sha256_hash=file.saved_file.sha256_hash,
        ).first()

        if existing_document:
            if existing_document.provenance == Document.PROVENANCE_UNKNOWN:
                existing_document.provenance = Document.PROVENANCE_USER_UPLOAD
                existing_document.save(update_fields=["provenance"])
            # Reuse existing document
            if existing_document.status == "ERROR":
                # Retry processing if it previously failed
                existing_document.process()
            existing_document.chat_next_messages.add(message)
            file.document = existing_document
            file.save()

            # If this is a container document (ZIP, MSG, EML), also associate child documents
            # Use parent_document relationship to find all children (works recursively for nested containers)
            child_documents = existing_document.child_documents.all()
            if child_documents.exists():
                for child_doc in child_documents:
                    child_doc.chat_next_messages.add(message)
        else:
            # Create new document
            document = Document.objects.create(
                data_source=data_source,
                saved_file=file.saved_file,
                filename=file.filename,
                provenance=Document.PROVENANCE_GENERATED_OUTPUT
                if message.is_bot
                else Document.PROVENANCE_USER_UPLOAD,
            )
            document.chat_next_messages.add(message)
            file.document = document
            file.save()
            # Queue processing
            document.process()


def num_tokens_from_string(
    string: str, model: str = DEFAULT_CHAT_MODEL_ID, enc_type: str = "o200k_base"
) -> int:
    """Returns the number of tokens in a text string."""
    string = string or ""
    try:
        encoding = tiktoken.get_encoding(enc_type)
        num_tokens = len(encoding.encode(string))
    except Exception:
        # Estimate the number of tokens using a simple heuristic (1 token = 4 chars)
        num_tokens = len(string) // 4
    return num_tokens


def replace_image_urls(text: str, url_mappings: list) -> str:
    """
    Replace temporary image URLs in message text with permanent file URLs.
    If the content is not referenced in the text, append it to the end.

    Args:
        text: The message text containing image URLs
        url_mappings: List of tuples (original_url, new_url)

    Returns:
        The text with URLs replaced or appended
    """
    if not url_mappings:
        return text

    # Ensure text is string
    result = text or ""

    for original_url, new_url in url_mappings:
        if original_url in result:
            result = result.replace(original_url, new_url)
        else:
            # Append image if not referenced
            result += f"\n\n![Generated Image]({new_url})"

    return result


def sanitize_unresolved_sandbox_links(text: str) -> str:
    """Remove unresolved sandbox: links from assistant text.

    This is a safety net for non-code-interpreter tool responses where the
    model may still emit markdown links like [file](sandbox:/mnt/data/file.md).
    Those links are not user-downloadable, so we keep readable text and strip
    unusable URLs.
    """
    if not text or "sandbox:" not in text:
        return text

    result = text

    # Preserve link text while removing unusable sandbox target.
    result = re.sub(r"\[(.*?)\]\(sandbox:[^\)]+\)", r"\1", result)

    # Remove markdown image tags with sandbox URLs.
    result = re.sub(r"!\[(.*?)\]\(sandbox:[^\)]+\)", r"", result)

    # Remove any remaining bare sandbox URLs.
    result = re.sub(r"sandbox:[^\s\)\"\']+", "", result)

    # Clean up excessive blank lines introduced by removals.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def wrap_llm_response(llm_response_str):
    return f'<div class="markdown-text" data-md="{html.escape(json.dumps(str(llm_response_str)))}"></div>'


def _is_unresolved_approval_step(step) -> bool:
    """Return True when a persisted processing step still represents pending approval."""
    if not isinstance(step, dict):
        return False

    if step.get("status") == "waiting_approval":
        return True

    return bool(
        step.get("is_approval_request")
        and step.get("approval_status") not in {"approved", "denied"}
    )


def _clear_terminal_approval_state(details: dict | None) -> dict:
    """Remove stale approval state so refreshes do not resurrect approval buttons."""
    cleaned_details = dict(details or {})
    cleaned_details.pop("pending_local_tool", None)

    for key in ("processing_steps", "raw_processing_steps"):
        steps = cleaned_details.get(key)
        if not isinstance(steps, list):
            continue

        filtered_steps = [
            step for step in steps if not _is_unresolved_approval_step(step)
        ]
        if filtered_steps:
            cleaned_details[key] = filtered_steps
        else:
            cleaned_details.pop(key, None)

    return cleaned_details


def _has_complete_reasoning_title(text: str) -> bool:
    """Return True if reasoning text has a complete, parseable title."""
    if not text:
        return False

    normalized = text.strip()
    if not normalized:
        return False

    if "\n" in normalized:
        return True

    if re.match(r"^\*\*[^*\n]+\*\*", normalized):
        return True

    if re.match(r"^#{1,6}\s+\S", normalized):
        return True

    return False


def _split_reasoning_text(text: str) -> list[tuple[str, str]]:
    """Split reasoning text into one or more (title, details) tuples.

    Handles normal newline-based formatting and packed inline markdown like:
    **Step 1**details**Step 2**more details
    """
    if not text:
        return []

    normalized = text.strip().replace("\r\n", "\n")
    if not normalized:
        return []

    inline_headers = list(re.finditer(r"\*\*([^*\n][^*\n]*?)\*\*", normalized))
    if inline_headers and inline_headers[0].start() == 0:
        has_more_than_one_header = len(inline_headers) > 1
        has_inline_body_after_first_header = inline_headers[0].end() < len(normalized)

        if has_more_than_one_header or has_inline_body_after_first_header:
            split_steps = []
            for i, match in enumerate(inline_headers):
                title = match.group(1).strip()
                if not title:
                    continue

                body_start = match.end()
                body_end = (
                    inline_headers[i + 1].start()
                    if i + 1 < len(inline_headers)
                    else len(normalized)
                )
                details = normalized[body_start:body_end].strip()
                details = details.lstrip(":;- ").strip()
                split_steps.append((title, details))

            if split_steps:
                return split_steps

    lines = normalized.split("\n")
    first_line = lines[0].strip()
    remaining_lines = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    markdown_header = re.match(r"^#{1,6}\s*(.+)$", first_line)
    if markdown_header:
        title = markdown_header.group(1).strip()
        if title:
            return [(title, remaining_lines)]

    bold_header = re.match(r"^\*\*(.+?)\*\*(.*)$", first_line)
    if bold_header:
        title = bold_header.group(1).strip()
        inline_details = bold_header.group(2).strip()
        details = (
            f"{inline_details}\n{remaining_lines}".strip()
            if inline_details and remaining_lines
            else inline_details or remaining_lines
        )
        if title:
            return [(title, details)]

    return [(first_line, remaining_lines)]


def format_markdown_code_block(content: str, language: str = "") -> str:
    """Wrap content in a fenced markdown code block.

    Uses a fence length longer than any run of backticks in the content so the
    block remains parseable even when the content itself contains markdown
    fences.
    """
    content = "" if content is None else str(content)
    language = (language or "").strip()

    longest_backtick_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", content)),
        default=0,
    )
    fence = "`" * max(3, longest_backtick_run + 1)
    opening_fence = f"{fence}{language}" if language else fence
    return f"{opening_fence}\n{content}\n{fence}"


def is_reasoning_display_step(step: dict | None) -> bool:
    """Return True when a formatted processing-step row represents reasoning."""
    return isinstance(step, dict) and step.get("status") is None and "title" in step


def strip_reasoning_suffix(title: str) -> str:
    """Remove the French English-only suffix from a reasoning title."""
    if title and title.endswith(FRENCH_REASONING_SUFFIX):
        return title[: -len(FRENCH_REASONING_SUFFIX)]
    return title


def get_tool_label(tool_name: str, fallback: str = None) -> str:
    """Resolve a human-readable tool label from the local tool registry."""
    if not tool_name:
        return fallback or ""

    try:
        from chat_next._tools.base import get_tool_display_name
        from chat_next.tools import TOOL_REGISTRY

        tool = TOOL_REGISTRY.get(tool_name)
        if tool and tool.display_name:
            return str(tool.display_name)
        return fallback or get_tool_display_name(tool_name)
    except Exception:
        pass

    return fallback or tool_name


def _build_tool_title(template: str, tool_label: str) -> tuple[str, str]:
    """Return plain-text and safe-HTML tool titles for display rows."""
    plain_title = template.format(function=tool_label)
    html_title = template.format(function=f"<em>{html.escape(tool_label)}</em>")
    return plain_title, html_title


def get_processing_steps_translation(
    details: dict | None, language: str = None
) -> list:
    """Return translated processing steps for language when available."""
    language = (language or get_language() or "")[:2]
    if not details or not language:
        return None

    translations = details.get(PROCESSING_STEPS_TRANSLATIONS_KEY) or {}
    translation = translations.get(language)
    if not isinstance(translation, dict):
        return None

    steps = translation.get("steps")
    if translation.get("status") == "complete" and isinstance(steps, list):
        return steps
    return None


def get_base_display_processing_steps(message_or_details, language: str = None) -> list:
    """Return current-language processing steps before reasoning-only overlays."""
    details = (
        (message_or_details.details if hasattr(message_or_details, "details") else None)
        or message_or_details
        or {}
    )

    raw_processing_steps = details.get("raw_processing_steps")
    if isinstance(raw_processing_steps, list) and raw_processing_steps:
        return format_processing_steps(raw_processing_steps, language=language)

    processing_steps = details.get("processing_steps")
    if processing_steps:
        return processing_steps

    return (
        (details.get("query_info") or [])
        + (details.get("reasoning_steps") or [])
        + (details.get("tool_calls") or [])
    )


def get_display_processing_steps(message_or_details, language: str = None) -> list:
    """Return the localized processing-step list for display/reload."""
    details = (
        (message_or_details.details if hasattr(message_or_details, "details") else None)
        or message_or_details
        or {}
    )

    base_steps = get_base_display_processing_steps(details, language=language)
    translated_steps = get_processing_steps_translation(details, language=language)
    if translated_steps:
        if details.get("raw_processing_steps"):
            translated_reasoning_steps = collect_reasoning_steps_for_translation(
                translated_steps
            )
            return apply_reasoning_step_translations(
                base_steps, translated_reasoning_steps
            )
        return translated_steps

    return base_steps


def collect_reasoning_steps_for_translation(processing_steps: list) -> list[dict]:
    """Extract reasoning rows for translation, preserving original indices."""
    reasoning_steps = []
    for index, step in enumerate(processing_steps or []):
        if not is_reasoning_display_step(step):
            continue
        reasoning_steps.append(
            {
                "index": index,
                "title": strip_reasoning_suffix(step.get("title", "") or ""),
                "details": step.get("details", "") or "",
            }
        )
    return reasoning_steps


def apply_reasoning_step_translations(
    processing_steps: list, translated_reasoning_steps: list[dict]
) -> list:
    """Merge translated reasoning rows back into a cloned processing-step list."""
    translated_processing_steps = copy.deepcopy(processing_steps or [])

    for item in translated_reasoning_steps or []:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue

        if index < 0 or index >= len(translated_processing_steps):
            continue

        step = translated_processing_steps[index]
        if not is_reasoning_display_step(step):
            continue

        if "title" in item and item.get("title") is not None:
            step["title"] = item.get("title")
        if "details" in item and item.get("details") is not None:
            step["details"] = item.get("details")

    return translated_processing_steps


def translate_reasoning_processing_steps(
    processing_steps: list, target_language: str = "fr"
) -> list:
    """Translate only reasoning step titles/details and preserve tool rows."""
    from translate.utils import translate_text_azure

    reasoning_steps = collect_reasoning_steps_for_translation(processing_steps)
    if not reasoning_steps:
        return copy.deepcopy(processing_steps or [])

    translated_reasoning_steps = []
    for step in reasoning_steps:
        translated_reasoning_steps.append(
            {
                "index": step["index"],
                "title": (
                    translate_text_azure(step.get("title", ""), "en", target_language)
                    if step.get("title")
                    else ""
                ),
                "details": (
                    translate_text_azure(step.get("details", ""), "en", target_language)
                    if step.get("details")
                    else ""
                ),
            }
        )

    return apply_reasoning_step_translations(
        processing_steps, translated_reasoning_steps
    )


def format_processing_steps(processing_steps: list, language: str = None) -> list:
    """
    Format processing steps from API into display format for the widget.

    Processing steps come in chronological order and can be either reasoning steps
    or tool calls. This function formats them for display while preserving order.

    Args:
        processing_steps: List of step dicts with 'type' ('reasoning' or 'tool_call')
        language: Current user language code for localization

    Returns:
        List of dicts with 'title', 'details', and 'status' keys for display.
    """
    from django.utils.translation import gettext as _

    from chat_next._llm.openai_responses import ToolCall
    from chat_next._tools.approval_display import render_tool_approval_input_html

    if not processing_steps:
        return []

    formatted = []
    french_suffix = FRENCH_REASONING_SUFFIX if language == "fr" else ""
    for step in processing_steps:
        step_type = step.get("type")
        approval_info = {}

        if step_type == "reasoning":
            # Format reasoning step
            text = step.get("text", "")
            is_complete = step.get("complete", True)
            if not text:
                continue

            # Skip incomplete steps unless we have a complete, parseable title.
            if not is_complete and not _has_complete_reasoning_title(text):
                continue

            for title, details in _split_reasoning_text(text):
                if not title:
                    continue

                formatted.append(
                    {
                        "title": title + french_suffix,
                        "details": details,
                        # Reasoning steps don't have status - they're displayed without status indicator
                    }
                )

        elif step_type == "tool_call":
            # Format tool call step
            tool_type = step.get("tool_type", "unknown")
            status = step.get("status", "in_progress")
            query = step.get("query")

            # Create a ToolCall to use is_billable() check
            tool_call = ToolCall(
                tool_type=tool_type,
                status=status,
                query=query,
                details=step.get("details", {}),
            )

            # Initialize variables for tool outputs (captured later by type)
            tool_output = None
            approval_input_html = ""

            # Skip non-billable tool calls (e.g., container events without data)
            # Exception: function_call and compaction steps are valuable
            # to display even if not billable
            if (
                tool_type not in ("function", "function_call", "compaction")
                and not tool_call.is_billable()
            ):
                continue

            # Format based on tool type
            if tool_type == "code_interpreter":
                # Format code interpreter with appropriate status messages
                code = tool_call.details.get("code", "")
                title_html = None
                if status == "in_progress":
                    title = _("Code interpreter: Generating code...")
                    details = format_markdown_code_block(code, "python") if code else ""
                elif status == "interpreting":
                    title = _("Code interpreter: Running...")
                    details = format_markdown_code_block(code, "python") if code else ""
                else:  # completed
                    title = _("Code interpreter")
                    details = format_markdown_code_block(code, "python") if code else ""
            elif tool_type == "compaction":
                # Context compaction step — shown when the conversation was
                # compacted mid-response to stay within the context window.
                if status == "in_progress":
                    tool_label = tool_call.details.get(
                        "tool_label", _("Compacting conversation...")
                    )
                else:
                    tool_label = tool_call.details.get(
                        "tool_label", _("Compacted conversation")
                    )
                title = tool_label
                title_html = None
                details = ""
            elif tool_type in ("function", "function_call"):
                # Local function calling tools (e.g., rag_search, list_libraries)
                details_dict = tool_call.details
                func_name = details_dict.get("name", "function")
                arguments = details_dict.get("arguments", "")
                tool_output = details_dict.get("output")
                allow_auto_approve = details_dict.get("allow_auto_approve", False)
                pii_flagged = bool(details_dict.get("pii_flagged", False))
                risk_review = details_dict.get("risk_review") or {}
                # approval_request_id is explicitly set only for steps that should show approval buttons
                # When None, this step shows "Approval required" but no buttons (part of a batch)
                approval_request_id = details_dict.get("approval_request_id")
                # For non-approval cases (completed steps), use call_id as fallback
                if approval_request_id is None and status != "waiting_approval":
                    approval_request_id = details_dict.get("call_id")
                # tool_label is the human-readable display name (set from OttoTool.display_name)
                tool_label = get_tool_label(
                    func_name,
                    fallback=details_dict.get("tool_label") or func_name,
                )

                # Format arguments as details (only if non-empty)
                details = ""
                if arguments and arguments != "{}":
                    try:
                        if isinstance(arguments, str):
                            arg_data = json.loads(arguments)
                        else:
                            arg_data = arguments
                        # Only show if there are actual arguments
                        if arg_data:
                            formatted_args = json.dumps(arg_data, indent=2)
                            details = format_markdown_code_block(formatted_args, "json")
                    except Exception:
                        details = format_markdown_code_block(arguments)

                if status == "waiting_approval":
                    approval_input_html = render_tool_approval_input_html(
                        func_name,
                        arguments,
                    )

                # Cost estimation: check_tool_cost_warning() stores the
                # result as "formatted_cost" (e.g. "0.15") and a boolean
                # "cost_warning" on the raw step details.
                estimated_cost = (
                    details_dict.get("formatted_cost")
                    if details_dict.get("cost_warning")
                    else None
                )

                title_html = None

                if status == "waiting_approval":
                    if details_dict.get("max_iterations_reached"):
                        iteration_limit_note = _(
                            "Otto paused before running another tool step because this chat reached its maximum tool iterations. Approve to continue, or increase Maximum tool iterations in Advanced settings for longer tool workflows."
                        )
                        title, title_html = _build_tool_title(
                            _("Iteration limit reached: {function}"),
                            tool_label,
                        )
                        details = (
                            f"{iteration_limit_note}\n\n{details}"
                            if details
                            else iteration_limit_note
                        )
                    else:
                        title, title_html = _build_tool_title(
                            _("Approval required: {function}"),
                            tool_label,
                        )
                    # Only mark as approval_request if we have a valid approval_request_id
                    # This allows showing multiple waiting_approval steps with only ONE
                    # having the approval buttons (the one with approval_request_id set)
                    if approval_request_id:
                        approval_info = {
                            "is_approval_request": True,
                            "approval_request_id": approval_request_id,
                            "tool_name": func_name,
                            "tool_label": tool_label,
                            "allow_auto_approve": bool(allow_auto_approve),
                            "estimated_cost": estimated_cost,
                            "approval_requires_external_warning": bool(
                                details_dict.get(
                                    "approval_requires_external_warning", False
                                )
                            ),
                            "external_service_name": details_dict.get(
                                "external_service_name", ""
                            ),
                            "external_service_names": details_dict.get(
                                "external_service_names", []
                            ),
                            "pii_flagged": pii_flagged,
                            "risk_review": risk_review,
                        }
                elif status == "in_progress":
                    title, title_html = _build_tool_title(
                        _("Using tool: {function}..."),
                        tool_label,
                    )
                elif status == "failed":
                    title, title_html = _build_tool_title(
                        _("Tool call failed: {function}"),
                        tool_label,
                    )
                    error_output = details_dict.get("output", {})
                    if isinstance(error_output, dict) and error_output.get("error"):
                        details = error_output.get("error")
                else:
                    title, title_html = _build_tool_title(
                        _("Used tool: {function}"),
                        tool_label,
                    )
            else:
                title = _("Using tool: {tool_type}").format(tool_type=tool_type)
                details = ""
                tool_output = None
                title_html = None

            step_data = {
                "title": title,
                "details": details,
                "tool_type": tool_type,
                "status": "complete"
                if status not in ("in_progress", "searching", "waiting_approval")
                else status,  # Pass 'waiting_approval' status through
            }

            if tool_type in ("function", "function_call"):
                approval_source = details_dict.get("approval_source")
                if approval_source:
                    step_data["approval_source"] = approval_source
                if pii_flagged:
                    step_data["pii_flagged"] = pii_flagged
                if risk_review:
                    step_data["risk_review"] = risk_review

            if title_html:
                step_data["title_html"] = title_html

            if approval_input_html:
                step_data["approval_input_html"] = approval_input_html

            # Add tool output for inspection (not displayed inline, but available)
            if tool_type in ("function", "function_call"):
                if tool_output is not None:
                    step_data["output"] = tool_output

            # Add approval info if present
            if approval_info:
                step_data.update(approval_info)

            formatted.append(step_data)

    return formatted


def format_tool_calls(tool_calls: list, language: str = None) -> list:
    """
    Format tool calls from API into display format for the processing steps widget.

    Args:
        tool_calls: List of tool call dicts with keys like 'tool_type', 'status', 'query'
        language: Current user language code for localization

    Returns:
        List of dicts with 'title', 'details', and 'status' keys for display.
    """
    from django.utils.translation import gettext as _

    from chat_next._llm.openai_responses import ToolCall

    if not tool_calls:
        return []

    formatted = []
    for tc in tool_calls:
        # Normalize to ToolCall for consistent handling
        if isinstance(tc, dict):
            tool_call = ToolCall(
                tool_type=tc.get("tool_type", "unknown"),
                status=tc.get("status", "in_progress"),
                query=tc.get("query"),
                details=tc.get("details", {}),
            )
        else:
            tool_call = tc

        # Skip non-billable tool calls (e.g., container events without data)
        # These shouldn't be shown to users as they're not real actions
        if not tool_call.is_billable():
            continue

        # Format based on tool type
        if tool_call.tool_type == "code_interpreter":
            code = tool_call.details.get("code", "")
            title_html = None
            if tool_call.status == "in_progress":
                title = _("Code interpreter: Generating code...")
                details = format_markdown_code_block(code, "python") if code else ""
            elif tool_call.status == "interpreting":
                title = _("Code interpreter: Running...")
                details = format_markdown_code_block(code, "python") if code else ""
            else:  # completed
                title = _("Code interpreter")
                details = format_markdown_code_block(code, "python") if code else ""
        elif tool_call.tool_type in ("function", "function_call"):
            func_name = tool_call.details.get("name", "function")
            tool_label = get_tool_label(
                func_name,
                fallback=tool_call.details.get("tool_label") or func_name,
            )
            title, title_html = _build_tool_title(
                _("Using tool: {function}"),
                tool_label,
            )
            details = ""
        else:
            title = _("Using tool: {tool_type}").format(tool_type=tool_call.tool_type)
            details = ""
            title_html = None

        step_data = {
            "title": title,
            "details": details,
            "tool_type": tool_call.tool_type,
            "status": "complete"
            if tool_call.status not in ("in_progress", "searching")
            else "in_progress",
        }
        if title_html:
            step_data["title_html"] = title_html

        formatted.append(step_data)

    return formatted


def format_reasoning_steps(
    reasoning_steps: list, include_incomplete: bool = False, language: str = None
) -> list:
    """
    Format reasoning steps from API into display format.
    Each step from API has 'index', 'text', and optionally 'complete'.
    We extract title and details from the text for better display.

    Args:
        reasoning_steps: List of step dicts from API
        include_incomplete: If False (default), only include complete steps OR
                          incomplete steps that have a complete first line (title).
                          If True, include all steps.
        language: Current user language code. If 'fr', appends a note that
                  reasoning is in English only.

    Returns list of dicts with 'title' and 'details' keys.
    """
    if not reasoning_steps:
        return []

    formatted_steps = []
    # Note for French users that reasoning is only in English
    french_suffix = FRENCH_REASONING_SUFFIX if language == "fr" else ""

    for step in reasoning_steps:
        is_complete = step.get("complete", True)
        text = step.get("text", "")
        if not text:
            continue

        # Skip incomplete steps unless:
        # - explicitly requested via include_incomplete
        # - or we have a complete title line (show title early, even if details still streaming)
        if (
            not include_incomplete
            and not is_complete
            and not _has_complete_reasoning_title(text)
        ):
            continue

        for title, details in _split_reasoning_text(text):
            if not title:
                continue
            formatted_steps.append(
                {
                    "title": title + french_suffix,
                    "details": details,
                }
            )

    return formatted_steps


async def stream_to_replacer(response_stream, attribute=None):
    response = ""
    try:
        async for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
    except Exception:
        for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
            await asyncio.sleep(0)  # This will allow other async tasks to run


def close_md_code_blocks(text):
    # Close any open code blocks
    if text.count("```") % 2 == 1:
        text += "\n```"
    elif text.count("`") % 2 == 1:
        text += "`"
    return text


def get_model_name(chat_settings, model_key=None):
    """
    Get the model used for the chat message.
    """
    from chat_next._llm.models import get_chat_model_choices

    model_key = model_key or chat_settings.chat_model

    # chat_model_choices is a list of tuples
    # (model_key, model_description (including some stuff in parens))
    model_name = [
        model[1] for model in get_chat_model_choices() if model[0] == model_key
    ]
    if model_name:
        return model_name[0].split("(")[0].strip()
    else:
        return ""


async def htmx_stream(
    chat: Chat,
    message_id: int,
    response_generator: Generator = None,
    response_replacer: AsyncGenerator = None,
    response_str: str = "",
    wrap_markdown: bool = True,
    dots: bool = False,
    source_nodes: list = None,
    remove_stop: bool = False,
    oob_html: str = None,
    query_info: list = None,
    cost_callback=None,
    output_items_callback=None,
    keepalive_interval_seconds: float | None = SSE_KEEPALIVE_INTERVAL_SECONDS,
) -> AsyncGenerator:
    """
    Formats responses into HTTP Server-Sent Events (SSE) for HTMX streaming.
    This function is a generator that yields SSE strings (lines starting with "data: ").

    There are 3 ways to use this function:
    1. response_generator: A custom generator that yields response chunks.
       Each chunk will be *appended* to the previous chunk.
    2. response_replacer: A custom generator that yields complete response strings.
       Unlike response_generator, each response will *replace* the previous response.
    3. response_str: A static response string.

    If dots is True, typing dots will be added to the end of the response.

    The function typically expects markdown responses from LLM, but can also handle
    HTML responses from other sources. Set wrap_markdown=False for plain HTML output.

    By default, the response will be saved as a Message object in the database after
    the response is finished. Set save_message=False to disable this behavior.

    query_info: Optional list of query info events to display in the reasoning widget.
                Each event should have 'title' and optional 'details' keys.
                Example: [{"title": "Optimizing search terms", "details": "query text"}]

    cost_callback: Optional callable that returns USD cost. Used to track costs
                   when using ResponsesAPIClient.

    output_items_callback: Optional callable to receive output_items from streaming
                           response. Called with the output_items list when streaming
                           completes. Used to store response_output for conversation state.

    The response_replacer can yield dicts with special keys:
    - {"progress_events": [...]} - Events to add to the reasoning widget
    - {"source_nodes": [...]} - Source nodes to save with the message
    - {"text": "..."} - Text content (can be combined with above)
    - {"output_items": [...]} - Output items from completed response (for storage)
    """

    # Helper function to format a string as an SSE message
    def sse_string(
        message: str,
        wrap_markdown=True,
        dots=False,
        remove_stop=False,
        oob_html=None,
    ) -> str:
        sse_joiner = "\ndata: "
        if wrap_markdown:
            message = wrap_llm_response(message)
        if dots:
            message += dots
        out_string = "data: "
        out_string += sse_joiner.join(message.split("\n"))

        if oob_html:
            # Add out-of-band HTML (e.g., accordion updates)
            out_string += sse_joiner + sse_joiner.join(oob_html.split("\n"))

        if remove_stop:
            out_string += sse_joiner + "<div hx-swap-oob='true' id='stop-button'></div>"
        out_string += "\n\n"  # End of SSE message
        return out_string

    ##############################
    # Start of the main function #
    ##############################
    # Initialize mutable defaults
    if source_nodes is None:
        source_nodes = []
    else:
        source_nodes = list(source_nodes)  # Make a copy to avoid mutating the original

    is_untitled_chat = is_placeholder_chat_title(chat.title)
    full_message = ""
    reasoning_steps = []  # Track structured reasoning steps from API
    tool_call_steps = []  # Track formatted tool call steps for display
    stream_message = await sync_to_async(Message.objects.get)(id=message_id)
    has_reasoning = (
        False  # Track if we've ever had reasoning (persists after reasoning ends)
    )
    dots_html = '<div class="typing"><span></span><span></span><span></span></div>'

    # If query_info is provided, show the reasoning widget
    if query_info:
        has_reasoning = True

    stop_warning_message = _(
        "Response stopped early. Costs may still be incurred after stopping."
    )
    generation_stopped = False
    if dots:
        dots = dots_html

    # Track output_items from streaming response (for conversation state)
    captured_output_items = None
    # Track pending local tool approval payload
    captured_pending_local_tool = None
    # Track file_citations from code interpreter (container files to download)
    captured_file_citations = None
    # Track code_interpreter_outputs (images from matplotlib, etc.)
    captured_code_interpreter_outputs = None
    # Track container_id for downloading sandbox files
    captured_container_id = None
    # Track token usage for context display and compaction decision
    captured_usage = None
    # Track response_id for chaining via previous_response_id (caching optimization)
    captured_response_id = None
    # Track chronologically ordered processing steps for display and storage
    processing_steps = []
    # Track raw processing steps for API chaining (before formatting)
    raw_processing_steps = []
    # Track last sent processing steps JSON to avoid redundant OOB updates
    last_processing_steps_json = ""
    # Track the last rendered transient context usage widget state
    last_actions_context_usage_json = None
    # Track when text streaming has started - once true, stop processing_steps OOB updates
    # This optimizes streaming by not re-rendering the widget during text generation
    text_started = False
    last_stream_yield_at = 0.0
    last_stream_render_at = 0.0
    last_large_processing_oob_at = 0.0
    min_reasoning_yield_interval = 0.12
    pending_response_task = None
    context = None

    try:
        # A previous run for the same message id may have left a stop flag in the
        # shared cache (especially in tests where DB ids can be reused across test
        # cases). Clear any stale value before starting a fresh stream so a new
        # response is not aborted immediately.
        cache.delete(f"stop_response_{message_id}")

        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])

        # Stream the response text
        response_iter = response_replacer.__aiter__()
        oob_html_sent = False
        while True:
            if pending_response_task is None:
                pending_response_task = asyncio.create_task(response_iter.__anext__())

            try:
                if keepalive_interval_seconds and keepalive_interval_seconds > 0:
                    done, _pending_tasks = await asyncio.wait(
                        {pending_response_task},
                        timeout=keepalive_interval_seconds,
                    )

                    if not done:
                        # Emit a harmless SSE comment so reverse proxies / browsers do not
                        # treat long model-thinking gaps as a dead connection.
                        yield ": keepalive\n\n"
                        continue

                    response = pending_response_task.result()
                else:
                    response = await pending_response_task
            except StopAsyncIteration:
                pending_response_task = None
                break

            pending_response_task = None
            if response is None:
                continue

            # Handle both dict responses (with reasoning_steps) and string responses
            # Extract text content and check for batch boundary
            if isinstance(response, dict):
                # Check for progress_events (transient events for reasoning widget)
                # These REPLACE previous progress_events (like reasoning_steps from API)
                progress_events = response.get("progress_events", [])
                if progress_events:
                    query_info = progress_events
                    has_reasoning = True

                # Check for source_nodes from the generator
                response_source_nodes = response.get("source_nodes", [])
                if response_source_nodes:
                    source_nodes = source_nodes + response_source_nodes

                # Capture output_items for conversation state storage
                if response.get("output_items"):
                    captured_output_items = response["output_items"]

                # Capture pending local tool approval payload
                if response.get("pending_local_tool"):
                    captured_pending_local_tool = response["pending_local_tool"]
                    logger.info(
                        "htmx_stream: Captured pending_local_tool",
                        pending_local_tool=captured_pending_local_tool,
                        call_id=captured_pending_local_tool.get("call_id"),
                    )

                # Capture file_citations for code interpreter files
                if response.get("file_citations"):
                    captured_file_citations = response["file_citations"]
                    logger.info(
                        "htmx_stream: Captured file_citations",
                        count=len(captured_file_citations),
                        citations=captured_file_citations,
                    )

                # Capture code interpreter outputs (images)
                if response.get("code_interpreter_outputs"):
                    captured_code_interpreter_outputs = response[
                        "code_interpreter_outputs"
                    ]
                    logger.info(
                        "htmx_stream: Captured code_interpreter_outputs",
                        count=len(captured_code_interpreter_outputs),
                        outputs=captured_code_interpreter_outputs,
                    )

                # Capture container_id for downloading sandbox files
                if response.get("container_id"):
                    captured_container_id = response["container_id"]
                    logger.info(
                        "htmx_stream: Captured container_id",
                        container_id=captured_container_id,
                    )

                # Capture response_id for chaining (caching optimization)
                if response.get("response_id"):
                    captured_response_id = response["response_id"]
                    logger.debug(
                        "htmx_stream: Captured response_id",
                        response_id=captured_response_id,
                    )

                # Capture token usage for context display and compaction decision
                if response.get("usage"):
                    captured_usage = response["usage"]
                    logger.debug(
                        "htmx_stream: Captured usage",
                        input_tokens=captured_usage.get("input_tokens"),
                        output_tokens=captured_usage.get("output_tokens"),
                    )

                # New format from Responses API with reasoning
                text_response = response.get("text", "")
                api_reasoning_steps = response.get("reasoning_steps", [])
                api_tool_calls = response.get("tool_calls", [])
                api_processing_steps = response.get("processing_steps", [])
                is_reasoning = response.get("is_reasoning", False)
                current_pending_local_tool = bool(response.get("pending_local_tool"))
                is_batch_boundary = text_response == "<|batchboundary|>"
                has_waiting_approval = any(
                    step.get("status") == "waiting_approval"
                    for step in api_processing_steps
                    if step.get("type") == "tool_call"
                )
            else:
                # Original string format
                text_response = response
                api_reasoning_steps = []
                api_tool_calls = []
                api_processing_steps = []
                is_reasoning = False
                current_pending_local_tool = False
                is_batch_boundary = response.endswith("<|batchboundary|>")
                has_waiting_approval = False

            if not is_batch_boundary:
                if remove_stop or not cache.get(f"stop_response_{message_id}", False):
                    full_message = text_response
                    # Handle unified processing_steps (preferred - chronological order)
                    if api_processing_steps:
                        # Keep raw format for API chaining
                        raw_processing_steps = api_processing_steps
                        processing_steps = format_processing_steps(
                            api_processing_steps, language=get_language()
                        )
                        has_reasoning = True
                    else:
                        # Fallback: handle separate tool_calls and reasoning_steps
                        # (for backwards compatibility with non-Responses API sources)
                        if api_tool_calls:
                            tool_call_steps = format_tool_calls(
                                api_tool_calls, language=get_language()
                            )
                            has_reasoning = True
                        if api_reasoning_steps:
                            reasoning_steps = format_reasoning_steps(
                                api_reasoning_steps, language=get_language()
                            )
                            has_reasoning = True
                        # Combine for backwards compat (reasoning first, then tools)
                        if api_reasoning_steps or api_tool_calls:
                            processing_steps = reasoning_steps + tool_call_steps
                    if is_reasoning:
                        has_reasoning = True
                        full_message = ""  # Keep empty to trigger indicator
                elif not generation_stopped:
                    generation_stopped = True
                    if wrap_markdown:
                        full_message = close_md_code_blocks(full_message)
                        stop_warning_message = f"\n\n_{stop_warning_message}_"
                    else:
                        stop_warning_message = f"<p><em>{stop_warning_message}</em></p>"
                    full_message = f"{full_message}{stop_warning_message}"
                    # Break immediately to stop consuming the generator.
                    # This prevents further tool execution, API calls, and
                    # stale response_id/output_items from being captured.
                    break
            elif generation_stopped:
                break

            # Prepare display message - reasoning will be handled separately via data-reasoning
            display_message = full_message

            # Check if we're in reasoning phase
            is_currently_reasoning = is_reasoning

            provisional_reasoning_steps = []
            if display_message and (has_waiting_approval or current_pending_local_tool):
                provisional_reasoning_steps = format_reasoning_steps(
                    [{"text": display_message, "complete": True}],
                    language=get_language(),
                )
                if provisional_reasoning_steps:
                    has_reasoning = True
                    display_message = ""

            # Avoid overwhelming client with markdown rendering:
            # slow down yields if the message is large
            length = len(display_message)
            yield_every = length // 2000 + 1

            # Yield text with basic chunking. Reasoning-only updates are further throttled
            # below to avoid excessive OOB swaps and front-end re-renders.
            should_yield = length < 1000 or length % yield_every == 0

            # Precompute processing-step change state so we can force a yield when
            # tool state changes (e.g., local function calls) even after text started.
            processing_has_tool_steps = any(
                step.get("status") is not None for step in processing_steps
            )
            streaming_processing_steps = list(processing_steps)
            if provisional_reasoning_steps:
                first_tool_index = next(
                    (
                        index
                        for index, step in enumerate(streaming_processing_steps)
                        if step.get("status") is not None
                    ),
                    len(streaming_processing_steps),
                )
                streaming_processing_steps = (
                    streaming_processing_steps[:first_tool_index]
                    + provisional_reasoning_steps
                    + streaming_processing_steps[first_tool_index:]
                )

            all_events = (query_info or []) + streaming_processing_steps
            streaming_events = [
                {k: v for k, v in step.items() if k != "output"} for step in all_events
            ]
            reasoning_json = json.dumps(streaming_events, ensure_ascii=False)
            processing_steps_changed = reasoning_json != last_processing_steps_json
            large_processing_oob = (
                processing_has_tool_steps
                and processing_steps_changed
                and len(reasoning_json) >= SSE_LARGE_PROCESSING_OOB_PAYLOAD_LENGTH
            )
            should_send_processing_oob_now = processing_steps_changed

            if large_processing_oob:
                now = time.monotonic()
                should_send_processing_oob_now = (
                    last_large_processing_oob_at == 0.0
                    or now - last_large_processing_oob_at
                    >= SSE_LARGE_PROCESSING_OOB_INTERVAL_SECONDS
                )

            # If tool progress changed, force an immediate SSE update so users can
            # see the tool action right away (even when message text is already long).
            # For very large processing payloads, cap how often we resend the OOB
            # reasoning snapshot so the browser does not have to reprocess huge
            # code blocks dozens of times per second.
            if processing_has_tool_steps and should_send_processing_oob_now:
                should_yield = True

            # While only processing steps are changing (no response text yet),
            # limit update frequency to keep streaming responsive.
            if should_yield and has_reasoning and not display_message:
                # Do not throttle when tool-state steps changed and we're actually
                # sending the update now; users should still see long-running tool
                # starts immediately (e.g., transcription/batch jobs).
                if not (processing_has_tool_steps and should_send_processing_oob_now):
                    now = time.monotonic()
                    if now - last_stream_yield_at < min_reasoning_yield_interval:
                        should_yield = False

            should_throttle_render = (
                should_yield
                and display_message
                and not is_currently_reasoning
                and length >= SSE_RENDER_THROTTLE_MESSAGE_LENGTH
                and not (processing_has_tool_steps and processing_steps_changed)
            )

            if should_throttle_render:
                now = time.monotonic()
                should_yield = (
                    last_stream_render_at == 0.0
                    or now - last_stream_render_at
                    >= SSE_RENDER_THROTTLE_INTERVAL_SECONDS
                )

            if should_yield:
                if should_throttle_render:
                    last_stream_render_at = time.monotonic()
                # Build complete HTML - message content only, reasoning data goes via OOB swap
                stream_html = ""

                # Build OOB update for reasoning data element (outside SSE swap area)
                # Only send when processing_steps actually change to avoid redundant updates
                # that cause choppy streaming during text generation
                reasoning_oob = ""

                # Track when text streaming starts.
                if display_message and not text_started:
                    text_started = True

                # Send processing-step updates:
                # - before text starts (normal reasoning phase), and
                # - after text starts when tool-state steps are changing.
                allow_reasoning_oob = not text_started or processing_has_tool_steps
                if has_reasoning and allow_reasoning_oob:
                    # Only send OOB update if processing_steps have changed
                    if should_send_processing_oob_now:
                        last_processing_steps_json = reasoning_json
                        if large_processing_oob:
                            last_large_processing_oob_at = time.monotonic()
                        # OOB swap updates the hidden data element, JS reads it and updates the widget
                        reasoning_oob = (
                            f'<div id="reasoning-data-{message_id}" hx-swap-oob="true" '
                            f'style="display:none" '
                            f'data-is-reasoning="{"true" if is_currently_reasoning else "false"}" '
                            f'data-reasoning="{html.escape(reasoning_json)}"></div>'
                        )

                actions_oob = ""
                if captured_usage:
                    context_usage_json, rendered_actions_oob = await sync_to_async(
                        _render_stream_message_actions_oob
                    )(stream_message, captured_usage)
                    if context_usage_json != last_actions_context_usage_json:
                        last_actions_context_usage_json = context_usage_json
                        actions_oob = rendered_actions_oob

                # Add the message content (will be wrapped in markdown if wrap_markdown=True)
                # Note: We need to handle this carefully - if wrap_markdown is True, only wrap
                # the display_message part, not the reasoning widget
                if wrap_markdown and display_message:
                    stream_html += wrap_llm_response(display_message)
                elif display_message:
                    stream_html += display_message

                # Show typing dots whenever reasoning is active and no response text yet,
                # except when the user is being asked to approve/deny (approval UI replaces dots).
                # This ensures dots remain visible throughout all gaps between processing steps.
                if not display_message and has_reasoning and not has_waiting_approval:
                    stream_html = dots_html
                # Add dots if needed (outside the markdown wrapper)
                # But not if we're already showing dots for reasoning phase
                elif dots and not generation_stopped:
                    stream_html += dots

                # Combine reasoning OOB with other OOB HTML
                combined_oob = reasoning_oob
                if actions_oob:
                    combined_oob = (
                        (combined_oob + "\n" + actions_oob)
                        if combined_oob
                        else actions_oob
                    )
                if not oob_html_sent and oob_html:
                    combined_oob = (
                        (combined_oob + "\n" + oob_html) if combined_oob else oob_html
                    )

                yield sse_string(
                    stream_html,
                    wrap_markdown=False,  # Already wrapped above if needed
                    dots=False,  # Already added above if needed
                    remove_stop=remove_stop or generation_stopped,
                    oob_html=combined_oob if combined_oob else None,
                )
                last_stream_yield_at = time.monotonic()
                oob_html_sent = True
            await asyncio.sleep(0.01)

        # If generation was stopped (user interrupted or sent new message),
        # clear captured state to prevent stale response_id/output_items from
        # being saved. A stale response_id that references a response with
        # unresolved function_calls would cause "No tool output found" errors
        # on the next turn.
        if generation_stopped:
            captured_output_items = []
            captured_response_id = None
            captured_pending_local_tool = None
            captured_file_citations = None
            captured_code_interpreter_outputs = None
            captured_container_id = None
            captured_usage = None
            raw_processing_steps = []

        # Calculate costs via callback
        if cost_callback and not generation_stopped:
            usd_cost = await sync_to_async(cost_callback)()
        else:
            usd_cost = 0.0

        message = await sync_to_async(Message.objects.get)(id=message_id)
        persisted_message_text = message.text or ""

        if captured_pending_local_tool:
            # Approval pauses can include provisional assistant narration from an
            # unfinished segment. Keep showing only the last committed message
            # text until the response actually completes after approval.
            full_message = persisted_message_text

        # Save the message text without thinking embedded
        finished_at = timezone.now()
        message.seconds_elapsed = (finished_at - message.date_created).total_seconds()
        # Store per-message cost so the UI can display it next to the message.
        message.usd_cost = usd_cost

        # If generation was stopped, explicitly clear response state to prevent
        # the next turn from chaining to an incomplete response.
        if generation_stopped:
            message.response_output = []
            message.response_id = ""

        # If this response is not ending in a fresh approval pause, scrub any
        # stale unresolved approval markers before persisting updated details.
        # This keeps reloads from resurrecting approval buttons when a resumed
        # stream returns no replacement processing steps.
        if not captured_pending_local_tool:
            message.details = _clear_terminal_approval_state(message.details)

        # Store processing steps in message details (chronologically ordered, for display on reload)
        if processing_steps:
            message.details["processing_steps"] = make_json_serializable(
                processing_steps
            )
        # Store raw processing steps for API chaining (approval resume, etc.)
        if raw_processing_steps:
            message.details["raw_processing_steps"] = make_json_serializable(
                raw_processing_steps
            )
        # Store token usage for context display and compaction decision
        if captured_usage:
            message.details["usage"] = make_json_serializable(captured_usage)

        # Store response output items for conversation state
        if captured_output_items is not None:
            message.response_output = make_json_serializable(captured_output_items)
        elif output_items_callback:
            message.response_output = make_json_serializable(
                await sync_to_async(output_items_callback)()
            )

        # Store pending local tool approval details (if any)
        if captured_pending_local_tool:
            message.details["pending_local_tool"] = make_json_serializable(
                captured_pending_local_tool
            )
            logger.info(
                "htmx_stream: Storing pending_local_tool in message.details",
                call_id=captured_pending_local_tool.get("call_id"),
                response_id=captured_response_id,
            )

        # Store response_id for chaining via previous_response_id (caching optimization)
        # This allows subsequent messages to use previous_response_id which enables
        # Azure's input caching, significantly reducing costs.
        if captured_response_id:
            message.response_id = captured_response_id

        # Save container_id to the chat for reuse in future turns
        if (
            captured_container_id
            and chat.code_interpreter_container_id != captured_container_id
        ):
            chat.code_interpreter_container_id = captured_container_id
            await sync_to_async(chat.save)(
                update_fields=["code_interpreter_container_id"]
            )
            logger.info(
                "htmx_stream: Saved code_interpreter_container_id to chat",
                container_id=captured_container_id,
                chat_id=chat.id,
            )

        # Download container files from code interpreter and attach to message.
        # Only treat sandbox URLs as downloadable when this response actually
        # involved code interpreter activity.
        code_interpreter_activity = bool(
            captured_container_id
            or captured_file_citations
            or captured_code_interpreter_outputs
        )

        # Prefer container_id from the current response; fallback to chat-level
        # stored container_id only for genuine code interpreter responses.
        container_id = captured_container_id
        if not container_id and code_interpreter_activity:
            container_id = chat.code_interpreter_container_id

        has_files_to_download = (
            captured_file_citations
            or (
                "sandbox:" in full_message
                and container_id
                and code_interpreter_activity
            )
            or captured_code_interpreter_outputs
        )

        # Re-yield current content while files are downloading so the client keeps
        # the latest message text visible without adding extra bot-output status UI.
        if has_files_to_download:
            yield sse_string(
                (
                    wrap_llm_response(full_message)
                    if wrap_markdown and full_message
                    else full_message
                ),
                wrap_markdown=False,
                remove_stop=True,
            )

        if captured_file_citations:
            logger.info(
                "Downloading container files from code interpreter",
                file_count=len(captured_file_citations),
            )
            # Get container_id from the first citation that has one (if not already set)
            if not container_id:
                for citation in captured_file_citations:
                    if citation.get("container_id"):
                        container_id = citation.get("container_id")
                        break

            await sync_to_async(download_container_files)(
                captured_file_citations, message
            )
            # Replace sandbox:// URLs with actual file URLs
            full_message = await sync_to_async(replace_sandbox_urls)(
                full_message, message
            )

        # Check for sandbox: URLs that weren't in annotations and download them
        # This handles cases where the model returns sandbox:/mnt/data/file.png
        # without a proper file citation annotation
        if "sandbox:" in full_message and container_id and code_interpreter_activity:
            logger.info(
                "Checking for additional sandbox files not in annotations",
                container_id=container_id,
            )
            await sync_to_async(download_sandbox_files)(
                full_message, container_id, message
            )
            # Replace any newly downloaded files' URLs
            full_message = await sync_to_async(replace_sandbox_urls)(
                full_message, message
            )

        # Safety net: non-code-interpreter responses should not expose
        # sandbox:/mnt links because they are not directly downloadable.
        if "sandbox:" in full_message and not code_interpreter_activity:
            logger.info(
                "Removing unresolved sandbox links from non-code-interpreter response",
                message_id=message.id,
            )
            full_message = sanitize_unresolved_sandbox_links(full_message)

        # Download code interpreter image outputs (matplotlib plots, etc.)
        if captured_code_interpreter_outputs:
            logger.info(
                "Downloading code interpreter images",
                image_count=len(
                    [
                        o
                        for o in captured_code_interpreter_outputs
                        if o.get("type") == "image_url"
                    ]
                ),
            )
            url_mappings = await sync_to_async(download_code_interpreter_images)(
                captured_code_interpreter_outputs, message
            )
            # Replace temporary image URLs with permanent file URLs
            full_message = replace_image_urls(full_message, url_mappings)

        # Save just the text response (reasoning will be displayed separately)
        message.text = full_message
        message.text = full_message

        await sync_to_async(message.save)()

        if is_untitled_chat:
            message_count = await sync_to_async(chat.messages.count)()
            if message_count >= 4:
                await sync_to_async(enqueue_chat_title_generation)(
                    chat.id, language=get_language()
                )

        # Refresh message from DB to get the saved text
        if raw_processing_steps or captured_pending_local_tool:
            from chat_next.approval_logging import (
                sync_external_tool_approval_logs_from_processing_steps,
            )

            await sync_to_async(sync_external_tool_approval_logs_from_processing_steps)(
                message=message,
                user=chat.user,
                raw_processing_steps=raw_processing_steps,
                pending_local_tool=captured_pending_local_tool,
                recorded_at=finished_at,
            )

        await sync_to_async(message.refresh_from_db)()

        # Check for pending background tasks (e.g. translation, document processing)
        has_pending_tasks = bool(
            message.details and message.details.get("pending_tasks")
        )

        # Combine query_info and processing_steps for display
        # Use local processing_steps variable instead of message.details because
        # it's guaranteed to have the latest formatted data
        all_events = (query_info or []) + processing_steps

        # Check if we are waiting for approval
        waiting_for_approval = False
        if all_events:
            last_event = all_events[-1]
            if last_event.get("status") == "waiting_approval":
                waiting_for_approval = True

        # Also check processing_steps directly in case it wasn't combined properly
        if not waiting_for_approval and processing_steps:
            last_step = processing_steps[-1]
            if last_step.get("status") == "waiting_approval":
                waiting_for_approval = True

        context = {
            "message": message,
            "swap_oob": True,
            "update_cost_bar": True,
            "plain_message_text": full_message,
            "waiting_for_approval": waiting_for_approval,
            "has_pending_tasks": has_pending_tasks,
            "pending_tasks": (
                message.details.get("pending_tasks", []) if has_pending_tasks else []
            ),
            "task_status_summary": (
                {
                    "completed": 0,
                    "total": len(message.details.get("pending_tasks", [])),
                }
                if has_pending_tasks
                else None
            ),
            # Pass JSON-encoded events for script tag
            "reasoning_steps_json": (
                json.dumps(all_events, ensure_ascii=False) if all_events else None
            ),
        }

    except Exception as e:
        try:
            message = await sync_to_async(Message.objects.get)(id=message_id)
        except Message.DoesNotExist:
            logger.warning("The message does not exist", message_id=message_id)
            return

        error_id = str(uuid.uuid4())[:7]
        if is_context_window_error(e):
            full_message = build_context_window_error_message(chat, message, error_id)
        else:
            from otto.utils.common import generate_ai_error_summary

            full_message = await sync_to_async(generate_ai_error_summary)(e, error_id)

        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=message.id,
            chat_id=chat.id,
        )
        message.text = full_message
        message.details = _clear_terminal_approval_state(message.details)
        # Clear response_output on error - partial output items (especially incomplete
        # reasoning) will cause "reasoning item provided without required following item"
        # errors when trying to continue the conversation
        message.response_output = []  # Empty list, not None (NOT NULL constraint)
        message.response_id = ""  # Empty string, not None (NOT NULL constraint)
        await sync_to_async(message.save)()
        context = {
            "message": message,
            "swap_oob": True,
            "plain_message_text": full_message,
        }
    finally:
        if pending_response_task and not pending_response_task.done():
            pending_response_task.cancel()

        try:
            if context:
                context["message"].json = json.dumps(str(context["message"].text))

                yield sse_string(
                    await sync_to_async(render_to_string)(
                        "chat_next/components/chat_message.html", context
                    ),
                    wrap_markdown=False,
                    remove_stop=True,
                )
        except Exception:
            logger.exception("Error rendering final chat_next message")

        # Send "done" event to signal client to close the SSE connection.
        # The client uses sse-close="done" attribute to listen for this event.
        yield "event: done\ndata: complete\n\n"


def _sanitize_chat_title(raw_title: str) -> str:
    """Strip markdown formatting, quotes, and truncate chat titles."""
    title = raw_title.strip()

    # Remove code blocks (```...```) - take content inside if short
    title = re.sub(r"```[\s\S]*?```", "", title)
    # Remove inline backticks
    title = re.sub(r"`([^`]*)`", r"\1", title)
    # Remove markdown headers
    title = re.sub(r"^#{1,6}\s+", "", title)
    # Remove bold/italic markers
    title = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", title)
    title = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", title)

    # Strip surrounding quotes (single or double)
    title = title.strip()
    if (title.startswith('"') and title.endswith('"')) or (
        title.startswith("'") and title.endswith("'")
    ):
        title = title[1:-1]

    # Collapse whitespace and take only the first line
    title = title.split("\n")[0].strip()
    title = re.sub(r"\s+", " ", title)

    # Truncate to reasonable length (max 80 chars)
    if len(title) > 80:
        title = title[:77] + "..."

    # Fallback if empty after sanitization
    if not title:
        title = _("Untitled chat")

    return title


def _normalize_chat_title_comparison_text(text: str) -> str:
    """Normalize text for title similarity checks."""
    normalized = _sanitize_chat_title(text or "")
    normalized = re.sub(r"[^\w\s]", " ", normalized.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_chat_title_topic(text: str) -> str:
    """Extract a compact topic from the first user message without repeating it verbatim."""
    if not text:
        return ""

    topic = html.unescape(text)
    topic = re.sub(r"```[\s\S]*?```", " ", topic)
    topic = re.sub(r"`([^`]*)`", r"\1", topic)
    topic = re.sub(r"https?://\S+", " ", topic)
    topic = topic.split("\n")[0]
    topic = re.split(r"(?<=[.!?])\s+", topic, maxsplit=1)[0]
    topic = re.sub(r"\s+", " ", topic).strip(" \t\r\n'\"“”‘’.,:;!?-–—")

    previous = None
    while topic and topic != previous:
        previous = topic
        topic = CHAT_TITLE_PREFIX_STRIP_RE.sub("", topic).strip()
        topic = CHAT_TITLE_ACTION_STRIP_RE.sub("", topic).strip()

    topic = topic.strip(" \t\r\n'\"“”‘’.,:;!?-–—")
    topic = re.sub(r"\s+", " ", topic)

    if not topic:
        return ""

    normalized_topic = _normalize_chat_title_comparison_text(topic)
    if normalized_topic in TRIVIAL_CHAT_TITLE_TOPICS:
        return ""

    words = topic.split()
    if len(words) > 6:
        topic = " ".join(words[:6])

    return topic


def _build_fallback_chat_title(first_user_message_text: str | None) -> str:
    """Build a non-verbatim fallback title from the first user message."""
    topic = _extract_chat_title_topic(first_user_message_text or "")
    if not topic:
        return _("Chat")
    return _sanitize_chat_title(_("About %(topic)s") % {"topic": topic})


def _is_verbatim_first_message_title(
    title: str, first_user_message_text: str | None
) -> bool:
    """Return True when the generated title is just the first user message repeated."""
    if not title or not first_user_message_text:
        return False

    return _normalize_chat_title_comparison_text(
        title
    ) == _normalize_chat_title_comparison_text(first_user_message_text)


def title_chat(chat_id, force_title=True):
    """
    Generate a title for a chat using the Responses API.

    Uses a simple non-streaming request to generate a concise title.
    """
    import asyncio

    from structlog import get_logger

    from chat_next._llm import ResponsesAPIClient

    logger = get_logger(__name__)

    chat = Chat.objects.select_related("user").get(id=chat_id)
    chat_messages = chat.messages.order_by("date_created")

    title_is_placeholder = is_placeholder_chat_title(chat.title)

    # Skip if already has a non-placeholder title (unless forcing).
    # For placeholder titles, try titling as soon as we have substantive content.
    if not force_title and not title_is_placeholder:
        return chat.title

    chat_messages_text = []
    first_user_message_text = None
    for message in chat_messages[:7]:
        role_label = "Assistant" if message.is_bot else "User"
        if message.text:
            chat_messages_text.append(f"{role_label}: {message.text}")
            if not message.is_bot and first_user_message_text is None:
                first_user_message_text = message.text
        elif message.files.exists():
            chat_messages_text.append(f"{role_label}: Message with files")
            # Directly fetch filenames to avoid heavy joins
            filenames = message.files.values_list("filename", flat=True)
            for filename in filenames:
                chat_messages_text.append(filename)
    chat_text = "\n".join([message[:500] for message in chat_messages_text])
    if len(chat_text) < 3:
        return _("Untitled chat")
    if not force_title and len(chat_text) < MIN_CHAT_TEXT_LENGTH_FOR_TITLE:
        return chat.title or _("Untitled chat")
    chat_text = chat_text[:2000]

    instructions = (
        "Write a concise chat title (1-5 words) that summarizes the overall topic. "
        "Prefer a compact topic label or noun phrase. "
        "Do NOT copy or lightly trim the first user message. "
        "Do NOT reuse the opening request verbatim. "
        "Do NOT start with filler like 'Help with', 'Question about', or 'Request for'. "
        "Examples: 'DG meeting notes', 'Budget follow-up', 'Python script debugging', 'Leave policy summary'. "
        "Respond with ONLY the plain text title. "
        "Do NOT use quotes, markdown, code blocks, backticks, bullet points, or any formatting. "
        "Do NOT include explanations or extra text."
    )

    try:
        # Use a fast, cheap model for title generation
        model_id = CHAT_TITLE_GENERATION_MODEL_ID

        client = ResponsesAPIClient(
            model=model_id,
            reasoning=False,
        )

        # Simple non-streaming request
        async def _generate_title():
            text, usage, _, _ = await client.complete_chat(
                input_items=[{"role": "user", "content": chat_text}],
                instructions=instructions,
            )
            # Don't track costs here - title generation is low cost and we're in a mixed async/sync context
            # Cost tracking would require sync_to_async wrapper which adds complexity
            return text

        # Handle async execution properly depending on context
        try:
            # Check if we're in an event loop
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            # No event loop running, safe to use asyncio.run()
            generated_title = asyncio.run(_generate_title())
        else:
            # We're inside an event loop, need to create a new thread
            import concurrent.futures

            def run_in_new_loop():
                return asyncio.run(_generate_title())

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop)
                generated_title = future.result(timeout=30)

        generated_title = _sanitize_chat_title(generated_title)
        if _is_verbatim_first_message_title(generated_title, first_user_message_text):
            generated_title = _build_fallback_chat_title(first_user_message_text)
    except Exception as e:
        logger.exception("Error generating chat title", chat_id=chat_id, error=str(e))
        generated_title = _("Untitled chat")

    chat.title = generated_title
    chat.save()
    return generated_title


def create_batches(iterable, n=1):
    length = len(iterable)
    for ndx in range(0, length, n):
        yield iterable[ndx : min(ndx + n, length)]


def bad_url(render_markdown=False):
    out = _("Sorry, that URL isn't allowed. Otto can only access sites ending in:")
    out += "\n\n"
    out += "\n".join([f"* `{url}`" for url in settings.ALLOWED_FETCH_URLS]) + "\n\n"
    out += (
        _("(e.g., `justice.gc.ca` or `www.tbs-sct.canada.ca` are also allowed)")
        + "\n\n"
    )
    out += _("As a workaround, you can save the content to a file and upload it here.")

    if render_markdown:
        out = md.convert(out)
    return out


@cache_within_request
def fix_source_links(text, source_document_url):
    """
    Fix internal links in the text by merging them with the source document URL
    """

    def is_external_link(link):
        """
        Check if the link starts with "http"
        """
        return link.startswith("http")

    def is_anchor(link):
        """
        Check if the link starts with a "#"
        """
        return link.startswith("#")

    def merge_link_with_source(link, source_document_url):
        """
        Merge the link with the source document URL based on conditions
        """
        if link.startswith("/"):
            first_subdirectory = link.split("/")[1]
            # If the first subdirectory of the internal link is in the source url, it is merged at that point
            if "/" + first_subdirectory in source_document_url:
                source_document_url = source_document_url.split(
                    "/" + first_subdirectory
                )[0]
            # makes sure that we don't have double slashes in the URL
            elif source_document_url.endswith("/"):
                source_document_url = source_document_url[:-1]
        # makes sure we don't have a slash missing in the URL
        elif not source_document_url.endswith("/") and not is_anchor(link):
            source_document_url += "/"

        return source_document_url + link

    def remove_link(text, link_tuple):
        """
        Replace the link with plain text of the link text: "[text](url)" -> "text"
        """
        return text.replace(f"[{link_tuple[0]}]({link_tuple[1]})", f"{link_tuple[0]}")

    # Capture both the url and the text in a tuple, i.e., ('[text]', 'url')
    links = re.findall(r"\[(.*?)\]\((.*?)\)", text)

    # Check if there are internal links and merge them with the source URL
    for link_tuple in links:
        try:
            # The URL itself is the second group
            link = link_tuple[1]
            if not is_external_link(link):
                if source_document_url:
                    # Sometimes the internal link is followed by a space and some text like the name of the page
                    # e.g. (/wiki/Grapheme "Grapheme")
                    link = link.split(" ")[0]
                    # Merge the link with the source document URL
                    modified_link = merge_link_with_source(link, source_document_url)
                    text = text.replace(link, modified_link)
                else:
                    # makes sure we don't have unusable links in the text
                    text = remove_link(text, link_tuple)
        except Exception:
            continue

    return text


@cache_within_request
def label_section_index(last_modification_date):
    last_modification_date = last_modification_date.date()
    todays_date = timezone.now().date()
    if last_modification_date > todays_date - timezone.timedelta(days=1):
        return 1
    elif last_modification_date > todays_date - timezone.timedelta(days=2):
        return 2
    elif last_modification_date > todays_date - timezone.timedelta(days=7):
        return 3
    elif last_modification_date > todays_date - timezone.timedelta(days=30):
        return 4
    else:
        return 5


def get_chat_history_sections(user_chats):
    """
    Group the chat history into sections formatted as [{"label": "(string)", "chats": [list..]}]
    """
    chat_history_sections = [
        {"label": _("Pinned chats"), "chats": []},
        {"label": _("Today"), "chats": []},
        {"label": _("Yesterday"), "chats": []},
        {"label": _("Last 7 days"), "chats": []},
        {"label": _("Last 30 days"), "chats": []},
        {"label": _("Older"), "chats": []},
    ]

    for user_chat in user_chats:
        if user_chat.pinned:
            chat_history_sections[0]["chats"].append(user_chat)
            continue
        section_index = label_section_index(user_chat.last_modification_date)
        chat_history_sections[section_index]["chats"].append(user_chat)

    return chat_history_sections


# MIME types supported for vision models
IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
}

# File types supported in Chat mode (vision models)
# Images and PDFs can be passed directly to vision-capable models
CHAT_MODE_SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".pdf",
}

from chat_next._llm.constants import CODE_INTERPRETER_SUPPORTED_EXTENSIONS  # noqa: E402

# All file types supported for upload (vision + code interpreter)
ALL_SUPPORTED_EXTENSIONS = (
    CHAT_MODE_SUPPORTED_EXTENSIONS | CODE_INTERPRETER_SUPPORTED_EXTENSIONS
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def is_image_file(chat_file):
    """Check if a ChatFile represents an image based on content_type or filename."""
    if chat_file.saved_file and chat_file.saved_file.content_type:
        return chat_file.saved_file.content_type.lower() in IMAGE_MIME_TYPES

    # Fallback to filename extension
    filename_lower = chat_file.filename.lower()
    return any(
        filename_lower.endswith(ext)
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]
    )


def is_pdf_file(chat_file):
    """Check if a ChatFile represents a PDF based on content_type or filename."""
    if chat_file.saved_file and chat_file.saved_file.content_type:
        return chat_file.saved_file.content_type.lower() == "application/pdf"
    return chat_file.filename.lower().endswith(".pdf")


def is_image_filename(filename):
    """Check if a filename has an image extension."""
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in IMAGE_EXTENSIONS)


def is_pdf_filename(filename):
    """Check if a filename has a PDF extension."""
    return filename.lower().endswith(".pdf")


def is_vision_supported_file(filename):
    """Check if a filename has an extension supported by vision models in Chat mode."""
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in CHAT_MODE_SUPPORTED_EXTENSIONS)


def is_upload_supported_file(filename):
    """Check if a filename has an extension supported for upload (vision or code interpreter)."""
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in ALL_SUPPORTED_EXTENSIONS)


def swap_glossary_columns(file):
    """
    Given a glossary CSV file, swap the first two columns and return a new file-like object.
    """
    import csv
    import io

    file.seek(0)
    reader = csv.reader(io.StringIO(file.read().decode("utf-8")))
    output = io.StringIO()
    writer = csv.writer(output)

    for row in reader:
        if len(row) >= 2:
            row[0], row[1] = row[1], row[0]
        writer.writerow(row)

    output.seek(0)
    return io.BytesIO(output.read().encode("utf-8"))


def escape_ol_name(name):
    """
    Escape periods after leading numbers to prevent Markdown ordered list interpretation.
    """
    if name and name[0].isdigit() and ". " in name:
        return name.replace(". ", r"\. ", 1)
    return name


async def stream_library_updates(user_message, adding_url=False):
    """
    Async generator to stream status while documents are processing,
    then emit a completion message summarizing duplicates, errors,
    and new documents.
    """
    from librarian.models import Document
    from librarian.views import IN_PROGRESS_STATUSES

    def _in_progress_count():
        return Document.objects.filter(
            chat_next_messages=user_message, status__in=IN_PROGRESS_STATUSES
        ).count()

    processing_count = await sync_to_async(_in_progress_count)()
    # Translatable strings (extracted outside f-strings for xgettext compatibility)
    adding_msg = _("Adding to library")
    still_processing_msg = _("file(s) still processing")
    while processing_count:
        if adding_url:
            yield adding_msg + "..."
        else:
            yield f"{adding_msg}... ({processing_count} {still_processing_msg})"
        await asyncio.sleep(0.5)
        processing_count = await sync_to_async(_in_progress_count)()

    def _build_completion_message():
        # All sync ORM work in one block
        msg_docs = Document.objects.filter(chat_next_messages=user_message)

        # Exclude container documents (ZIP files, etc.) that are not queryable in RAG
        # These documents exist for metadata but shouldn't be counted
        msg_docs_queryable = msg_docs.exclude(is_container=True)

        error_documents = list(msg_docs_queryable.filter(status="ERROR"))
        paused_documents = list(msg_docs_queryable.filter(status="PAUSED"))

        # Duplicate SUCCESS docs: associated to this message and at least one other message
        # We need to count ALL messages, not just within the filtered queryset
        duplicate_success = []
        for doc in msg_docs_queryable.filter(status="SUCCESS"):
            if doc.chat_next_messages.count() > 1:
                duplicate_success.append(doc)

        # New SUCCESS docs: associated only to this message
        total_success = msg_docs_queryable.filter(status="SUCCESS").count()
        num_completed_documents = total_success - len(duplicate_success)

        parts = []

        if duplicate_success:
            names = [doc.filename for doc in duplicate_success if doc.filename]
            if names:
                escaped_names = [escape_ol_name(name) for name in names]
                parts.append(
                    _("The following document(s) already exist in the library:")
                    + "\n\n - "
                    + "\n\n - ".join(escaped_names)
                )

        if error_documents:
            errors = []
            for doc in error_documents:
                # Format: **filename** error message _(Error ID: xxx)_
                details = doc.status_details or _("Unknown error")
                # Check if there's an Error ID at the end and format it italic
                error_id_match = re.search(r"\(Error ID:?\s*([a-f0-9]+)\)\s*$", details)
                if error_id_match:
                    # Remove the Error ID from details and add it back formatted
                    details_without_id = details[: error_id_match.start()].strip()
                    error_id = error_id_match.group(1)
                    # Translatable string extracted for xgettext compatibility
                    error_id_label = _("Error ID:")
                    errors.append(
                        f"**{doc.filename}** {details_without_id} _({error_id_label} {error_id})_"
                    )
                else:
                    errors.append(f"**{doc.filename}** {details}")
            escaped_errors = [escape_ol_name(error) for error in errors]
            parts.append(
                _("Error processing the following document(s):")
                + "\n\n - "
                + "\n\n - ".join(escaped_errors)
            )

        if paused_documents:
            names = [doc.filename for doc in paused_documents if doc.filename]
            if names:
                escaped_names = [escape_ol_name(name) for name in names]
                parts.append(
                    _(
                        "The following large document(s) are paused for manual embedding and won't be searchable yet:"
                    )
                    + "\n\n - "
                    + "\n\n - ".join(escaped_names)
                )

        if adding_url and not error_documents:
            parts.append(_("URL added to library."))
        elif num_completed_documents > 0:
            parts.append(
                f"{num_completed_documents} " + _("new document(s) added to library.")
            )
        elif (
            not error_documents
            and not duplicate_success
            and num_completed_documents == 0
        ):
            parts.append(_("All documents already exist in the library."))

        return "\n\n".join(parts)

    completion_message = await sync_to_async(_build_completion_message)()
    yield completion_message
