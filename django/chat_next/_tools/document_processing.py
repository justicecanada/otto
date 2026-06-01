from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from structlog import get_logger

from chat_next._llm.models import (
    DEFAULT_CHAT_MODEL_ID,
    get_supported_reasoning_efforts,
    normalize_reasoning_effort,
)
from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._tools.qa_libraries import (
    _compute_page_boundaries,
    _get_page_count_from_text,
    _get_page_for_offset,
    _map_pages_to_char_offsets,
)
from chat_next._tools.utils import (
    EST_CHARS_PER_TOKEN,
    ESTIMATED_OUTPUT_TOKENS,
    TOKENS_PER_CHUNK,
    _calculate_cost_for_units,
    _get_model_id,
)
from chat_next.models import TOOL_CATEGORY_DOCUMENT_PROCESSING

logger = get_logger(__name__)


PROMPT_DOCUMENT_MODEL_CHOICES = {
    "gpt-5.4-mini": "Use only for genuinely nuanced extraction, ambiguous interpretation, or higher-quality writing within each document or chunk.",
    "gpt-5.4-nano": "Default choice for most batch processing tasks, especially extraction, classification, format conversion, simple rewrites, and straightforward summaries.",
}

PROMPT_DOCUMENT_REASONING_CHOICES = ("default", "low", "medium", "high")
MAX_PROMPT_DOCUMENTS = 10
MAX_PROMPT_DOCUMENT_RANGE_INPUTS = 50
DEFAULT_PROMPT_DOCUMENT_CHUNK_TARGET_CHARS = 400_000


def _get_prompt_documents_model_id(arguments: dict, chat=None) -> str | None:
    """Resolve the model used for prompt_documents cost and execution."""
    requested_model = arguments.get("llm_model")
    if requested_model:
        return requested_model

    model_id = _get_model_id(chat)
    return model_id or DEFAULT_CHAT_MODEL_ID


def _get_default_prompt_documents_reasoning(model_id: str | None) -> str | None:
    """Return the lightest supported reasoning effort for the selected model."""
    supported = get_supported_reasoning_efforts(model_id)
    if not supported:
        return None
    return supported[0]


def _resolve_prompt_documents_reasoning(
    model_id: str | None, reasoning_effort: str | None
) -> str | None:
    """Resolve a user-facing reasoning choice to a supported model value."""
    if not reasoning_effort or reasoning_effort == "default":
        return _get_default_prompt_documents_reasoning(model_id)
    return normalize_reasoning_effort(model_id, reasoning_effort)


def _validate_prompt_documents_model_choice(model_id: str | None) -> str | None:
    if not model_id:
        return None
    if model_id not in PROMPT_DOCUMENT_MODEL_CHOICES:
        allowed = ", ".join(PROMPT_DOCUMENT_MODEL_CHOICES)
        return (
            "Invalid llm_model. Choose one of: "
            f"{allowed}, or null to inherit the chat's current model."
        )
    return None


def _validate_prompt_documents_reasoning_choice(
    reasoning_effort: str | None,
) -> str | None:
    if not reasoning_effort:
        return None
    if reasoning_effort not in PROMPT_DOCUMENT_REASONING_CHOICES:
        allowed = ", ".join(PROMPT_DOCUMENT_REASONING_CHOICES)
        return f"Invalid reasoning_effort. Choose one of: {allowed}."
    return None


def _validate_prompt_documents_truncate_chars(truncate_chars) -> str | None:
    if truncate_chars in (None, ""):
        return None
    if not isinstance(truncate_chars, int):
        return (
            "Invalid truncate_chars. Provide an integer number of characters, or null."
        )
    if truncate_chars <= 0:
        return "Invalid truncate_chars. It must be greater than 0 when provided."
    return None


def _parse_optional_non_negative_int(value, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be 0 or greater")
    return value


def _parse_optional_positive_int(value, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _normalize_range_label(label) -> str | None:
    if label in (None, ""):
        return None
    if not isinstance(label, str):
        raise ValueError("label must be a string when provided")
    normalized = label.strip()
    if not normalized:
        return None
    if len(normalized) > 120:
        raise ValueError("label must be 120 characters or fewer")
    return normalized


def _normalize_range_input(item: dict, index: int) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"inputs[{index}] must be an object")

    document_id = item.get("document_id")
    if not isinstance(document_id, int) or document_id <= 0:
        raise ValueError(f"inputs[{index}].document_id must be a positive integer")

    start_char = _parse_optional_non_negative_int(
        item.get("start_char"), f"inputs[{index}].start_char"
    )
    end_char = _parse_optional_non_negative_int(
        item.get("end_char"), f"inputs[{index}].end_char"
    )
    start_page = _parse_optional_positive_int(
        item.get("start_page"), f"inputs[{index}].start_page"
    )
    end_page = _parse_optional_positive_int(
        item.get("end_page"), f"inputs[{index}].end_page"
    )
    overlap_chars = _parse_optional_non_negative_int(
        item.get("overlap_chars"), f"inputs[{index}].overlap_chars"
    )
    label = _normalize_range_label(item.get("label"))

    has_char_range = start_char is not None or end_char is not None
    has_page_range = start_page is not None or end_page is not None

    if has_char_range and has_page_range:
        raise ValueError(
            f"inputs[{index}] cannot mix character and page range arguments"
        )

    if end_char is not None and start_char is not None and end_char <= start_char:
        raise ValueError(f"inputs[{index}].end_char must be greater than start_char")

    if end_page is not None and start_page is not None and end_page < start_page:
        raise ValueError(f"inputs[{index}].end_page cannot be earlier than start_page")

    return {
        "document_id": document_id,
        "start_char": start_char,
        "end_char": end_char,
        "start_page": start_page,
        "end_page": end_page,
        "label": label,
        "overlap_chars": overlap_chars,
    }


def _resolve_prompt_document_chunk_target_chars(target_chars) -> int:
    if target_chars in (None, ""):
        return DEFAULT_PROMPT_DOCUMENT_CHUNK_TARGET_CHARS
    if not isinstance(target_chars, int) or target_chars <= 0:
        raise ValueError("target_chars must be a positive integer")
    return target_chars


def _ordered_output_document_ids(completed: list[dict]) -> list[int]:
    return [
        output_document_id
        for item in completed
        if (output_document_id := item.get("output_document_id")) is not None
    ]


def _is_tool_generated_output(doc, document_model) -> bool:
    """Return True only for documents explicitly marked as generated outputs.

    For backward compatibility with older rows, fall back to the prior
    attachment-based heuristic only when provenance is still unknown.
    """

    if doc.provenance == document_model.PROVENANCE_GENERATED_OUTPUT:
        return True

    if doc.provenance != document_model.PROVENANCE_UNKNOWN:
        return False

    has_bot_attachment = doc.chat_next_files.filter(message__is_bot=True).exists()
    has_user_attachment = doc.chat_next_files.filter(message__is_bot=False).exists()
    is_linked_source_document = doc.chat_next_messages.exists()
    return (
        has_bot_attachment and not has_user_attachment and not is_linked_source_document
    )


def _build_pending_task_label(filename: str, job_input: dict) -> str:
    label = job_input.get("label")
    if label:
        return f"Processing: {filename} ({label})"

    start_page = job_input.get("start_page")
    end_page = job_input.get("end_page")
    if start_page is not None or end_page is not None:
        return f"Processing: {filename} (pages {start_page or '?'}-{end_page or '?'})"

    start_char = job_input.get("start_char")
    end_char = job_input.get("end_char")
    if start_char is not None or end_char is not None:
        return f"Processing: {filename} (chars {start_char or 0}-{end_char or 'end'})"

    return f"Processing: {filename}"


def _build_completed_result(
    job_input: dict, source_filename: str, task_result: dict
) -> dict:
    completed = {
        "document_id": job_input["document_id"],
        "filename": source_filename,
        "status": "completed",
        "output_filename": task_result.get("filename"),
        "output_document_id": task_result.get("document_id"),
        "truncate_chars": task_result.get("truncate_chars"),
        "input_truncated": task_result.get("input_truncated", False),
        "input_chars_used": task_result.get("input_chars_used"),
        "source_document_id": task_result.get(
            "source_document_id", job_input["document_id"]
        ),
        "source_filename": task_result.get("source_filename", source_filename),
        "label": task_result.get("label") or job_input.get("label"),
        "start_char": task_result.get("start_char"),
        "end_char": task_result.get("end_char"),
        "start_page": task_result.get("start_page"),
        "end_page": task_result.get("end_page"),
        "overlap_chars": task_result.get("overlap_chars"),
        "model_used": task_result.get("model_used"),
    }
    return completed


def _document_processing_result_sort_key(result: dict) -> tuple:
    """Return a stable source-order sort key for document-processing results."""

    primary_document_id = result.get("source_document_id", result.get("document_id"))
    start_char = result.get("start_char")
    start_page = result.get("start_page")
    label = result.get("label") or ""
    output_filename = result.get("output_filename") or result.get("filename") or ""

    return (
        primary_document_id if primary_document_id is not None else 10**12,
        start_char if start_char is not None else 10**12,
        start_page if start_page is not None else 10**12,
        label,
        output_filename,
    )


async def _run_document_processing_batch(
    *,
    context: ToolContext,
    job_inputs: list[dict],
    prompt: str | None,
    template_doc_id,
    llm_model,
    reasoning_effort,
    truncate_chars,
    include_generated_outputs: bool,
):
    from asgiref.sync import sync_to_async
    from structlog.contextvars import get_contextvars

    from librarian.models import Document

    from chat_next.tasks import prompt_document_next

    user = context.user
    chat = context.chat

    if model_error := _validate_prompt_documents_model_choice(llm_model):
        return {"error": model_error}

    if reasoning_error := _validate_prompt_documents_reasoning_choice(reasoning_effort):
        return {"error": reasoning_error}

    if truncate_error := _validate_prompt_documents_truncate_chars(truncate_chars):
        return {"error": truncate_error}

    if not job_inputs:
        return {
            "error": "No documents provided. Please specify which document(s) to process."
        }

    if not prompt:
        return {"error": "A prompt must be provided for document processing."}

    request_context = get_contextvars()
    message_id = request_context.get("message_next_id")
    user_id = request_context.get("user_id")
    cost_group_id = request_context.get("cost_group_id")
    if not message_id:
        return {"error": "No message context available for file attachment."}

    model_name = _get_prompt_documents_model_id(
        {"llm_model": llm_model},
        chat,
    )
    resolved_reasoning_effort = _resolve_prompt_documents_reasoning(
        model_name, reasoning_effort
    )
    document_ids = sorted({item["document_id"] for item in job_inputs})

    @sync_to_async
    def validate_and_submit_tasks():
        import time as _time

        from django.db.models import Q

        submitted = []
        errors = []

        ids_awaiting = set(
            Document.objects.filter(
                id__in=document_ids,
                status__in=("PENDING", "INIT", "PROCESSING"),
            )
            .filter(Q(extracted_text__isnull=True) | Q(extracted_text=""))
            .values_list("id", flat=True)
        )
        if ids_awaiting:
            logger.info(
                "Waiting for text extraction before prompting",
                document_ids=sorted(ids_awaiting),
            )
            deadline = _time.monotonic() + 60
            while ids_awaiting and _time.monotonic() < deadline:
                _time.sleep(2)
                ids_awaiting = set(
                    Document.objects.filter(
                        id__in=list(ids_awaiting),
                        status__in=("PENDING", "INIT", "PROCESSING"),
                    )
                    .filter(Q(extracted_text__isnull=True) | Q(extracted_text=""))
                    .values_list("id", flat=True)
                )

        template_text = None
        if template_doc_id:
            try:
                template_doc = Document.objects.get(id=template_doc_id)
                template_text = template_doc.extracted_text or ""
            except Document.DoesNotExist:
                pass

        for job_input in job_inputs:
            doc_id = job_input["document_id"]
            try:
                doc = Document.objects.get(id=doc_id)

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

                doc_text = doc.extracted_text or ""
                if not doc_text.strip():
                    errors.append(
                        {
                            "document_id": doc_id,
                            "filename": doc.filename,
                            "status": "error",
                            "error": "Document has no extracted text. It may still be processing.",
                        }
                    )
                    continue

                if not include_generated_outputs and _is_tool_generated_output(
                    doc, Document
                ):
                    errors.append(
                        {
                            "document_id": doc_id,
                            "filename": doc.filename,
                            "status": "skipped",
                            "error": (
                                "Skipped tool-generated output file to avoid recursive processing. "
                                "Use the original uploaded documents, or set "
                                "include_generated_outputs=true to process generated outputs intentionally."
                            ),
                        }
                    )
                    continue

                task_kwargs = {
                    "model_name": model_name,
                    "reasoning_effort": resolved_reasoning_effort,
                    "truncate_chars": truncate_chars,
                    "template_text": template_text,
                    "original_filename": doc.filename,
                    "start_char": job_input.get("start_char"),
                    "end_char": job_input.get("end_char"),
                    "start_page": job_input.get("start_page"),
                    "end_page": job_input.get("end_page"),
                    "range_label": job_input.get("label"),
                    "overlap_chars": job_input.get("overlap_chars"),
                }
                if user_id is not None:
                    task_kwargs["user_id"] = user_id
                if cost_group_id is not None:
                    task_kwargs["cost_group_id"] = cost_group_id

                task = prompt_document_next.apply_async(
                    args=[
                        doc_id,
                        prompt,
                        str(message_id),
                        str(chat.id) if chat else "",
                    ],
                    kwargs=task_kwargs,
                    priority=5,
                )
                submitted.append((job_input, doc.filename, task))

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
                    "Error submitting document processing job", document_id=doc_id
                )
                errors.append(
                    {"document_id": doc_id, "status": "error", "error": str(e)}
                )

        if submitted and message_id:
            try:
                from chat_next.models import Message as Msg

                msg = Msg.objects.get(id=message_id)
                msg.details["pending_tasks"] = [
                    {
                        "task_id": task.id,
                        "label": _build_pending_task_label(filename, job_input),
                    }
                    for job_input, filename, task in submitted
                ]
                msg.save(update_fields=["details"])
            except Exception:
                pass

        return submitted, errors

    submitted, errors = await validate_and_submit_tasks()

    if not submitted:
        return {"results": list(errors), "completed": []}

    import asyncio
    import time

    results = list(errors)
    pending = {
        task.id: (job_input, filename, task) for job_input, filename, task in submitted
    }
    total = len(submitted)
    completed_count = 0
    poll_started_at = time.monotonic()
    max_poll_seconds = 300

    while pending:
        if time.monotonic() - poll_started_at > max_poll_seconds:
            for task_id, (job_input, filename, _) in pending.items():
                results.append(
                    {
                        "document_id": job_input["document_id"],
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

        await asyncio.sleep(2)
        done_ids = []
        for task_id, (job_input, filename, task) in pending.items():
            is_ready = False
            if hasattr(task, "ready"):
                try:
                    is_ready = bool(task.ready())
                except Exception:
                    is_ready = False
            else:
                is_ready = True

            if is_ready:
                done_ids.append(task_id)
                completed_count += 1
                try:
                    task_result = task.get(timeout=10)
                    if task_result.get("success"):
                        results.append(
                            _build_completed_result(job_input, filename, task_result)
                        )
                    else:
                        results.append(
                            {
                                "document_id": job_input["document_id"],
                                "filename": filename,
                                "status": "error",
                                "error": task_result.get("error", "Unknown error"),
                            }
                        )
                except Exception as e:
                    results.append(
                        {
                            "document_id": job_input["document_id"],
                            "filename": filename,
                            "status": "error",
                            "error": f"Processing failed: {e}",
                        }
                    )
                logger.info(
                    "Document processing progress",
                    completed=completed_count,
                    total=total,
                    filename=filename,
                )

        for task_id in done_ids:
            del pending[task_id]

    results = sorted(results, key=_document_processing_result_sort_key)
    completed = [r for r in results if r.get("status") == "completed"]
    return {"results": results, "completed": completed}


def estimate_document_processing_cost(arguments: dict, chat=None) -> str | None:
    """Estimate LLM input and output cost for batch document processing.
    (user prompt + document text per document + estimated output tokens per document),
    Returns a formatted cost string (e.g. "0.15") or None when cost is zero.
    """
    from otto.utils.common import cad_cost

    from librarian.models import Document

    model_id = _get_prompt_documents_model_id(arguments, chat)
    if not model_id:
        return None

    document_ids = arguments.get("document_ids", [])
    if not document_ids:
        return None

    total_cost = Decimal("0")
    truncate_chars = arguments.get("truncate_chars")

    # Cost of the user's prompt (sent to each LLM worker as the instruction)
    prompt = arguments.get("prompt") or ""
    prompt_text = prompt
    if prompt_text:
        prompt_tokens = len(prompt_text) // EST_CHARS_PER_TOKEN
        total_cost += _calculate_cost_for_units(
            f"{model_id}-in", prompt_tokens * len(document_ids)
        )

    # Cost of each document's text sent to the LLM + estimated output
    for doc_id in document_ids:
        try:
            doc = Document.objects.get(id=doc_id)
            if doc.extracted_text:
                char_count = len(doc.extracted_text)
                if truncate_chars:
                    char_count = min(char_count, truncate_chars)
                token_count = char_count // EST_CHARS_PER_TOKEN
            elif doc.num_chunks:
                token_count = doc.num_chunks * TOKENS_PER_CHUNK
                if truncate_chars:
                    token_count = min(
                        token_count, truncate_chars // EST_CHARS_PER_TOKEN
                    )
            else:
                continue
            # Input cost: document text sent to LLM
            total_cost += _calculate_cost_for_units(f"{model_id}-in", token_count)
            # Output cost: estimated response tokens
            total_cost += _calculate_cost_for_units(
                f"{model_id}-out", ESTIMATED_OUTPUT_TOKENS
            )
        except Document.DoesNotExist:
            continue

    if total_cost == 0:
        return None

    return f"{cad_cost(total_cost):.2f}"


def estimate_document_range_processing_cost(arguments: dict, chat=None) -> str | None:
    """Estimate cost for range-based document processing jobs."""
    from otto.utils.common import cad_cost

    from librarian.models import Document

    model_id = _get_prompt_documents_model_id(arguments, chat)
    if not model_id:
        return None

    inputs = arguments.get("inputs", [])
    if not inputs:
        return None

    total_cost = Decimal("0")
    prompt = arguments.get("prompt") or ""
    if prompt:
        prompt_tokens = len(prompt) // EST_CHARS_PER_TOKEN
        total_cost += _calculate_cost_for_units(
            f"{model_id}-in", prompt_tokens * len(inputs)
        )

    for index, raw_input in enumerate(inputs):
        try:
            item = _normalize_range_input(raw_input, index)
        except ValueError:
            return None

        try:
            doc = Document.objects.get(id=item["document_id"])
        except Document.DoesNotExist:
            continue

        if doc.extracted_text:
            text = doc.extracted_text
            if item.get("start_page") is not None or item.get("end_page") is not None:
                page_map = _map_pages_to_char_offsets(text)
                if page_map:
                    sorted_pages = sorted(page_map.keys())
                    start_page = item.get("start_page") or sorted_pages[0]
                    end_page = item.get("end_page") or sorted_pages[-1]
                    if start_page in page_map and end_page in page_map:
                        char_count = max(
                            0, page_map[end_page][1] - page_map[start_page][0]
                        )
                    else:
                        char_count = len(text)
                else:
                    char_count = len(text)
            else:
                start_char = item.get("start_char") or 0
                end_char = item.get("end_char") or len(text)
                char_count = max(
                    0, min(end_char, len(text)) - min(start_char, len(text))
                )
                if char_count == 0:
                    char_count = len(text)
            token_count = char_count // EST_CHARS_PER_TOKEN
        elif doc.num_chunks:
            token_count = doc.num_chunks * TOKENS_PER_CHUNK
        else:
            continue

        total_cost += _calculate_cost_for_units(f"{model_id}-in", token_count)
        total_cost += _calculate_cost_for_units(
            f"{model_id}-out", ESTIMATED_OUTPUT_TOKENS
        )

    if total_cost == 0:
        return None

    return f"{cad_cost(total_cost):.2f}"


def estimate_document_chunk_processing_cost(arguments: dict, chat=None) -> str | None:
    """Estimate cost for one-document chunk processing with internally planned ranges."""
    from otto.utils.common import cad_cost

    from librarian.models import Document

    model_id = _get_prompt_documents_model_id(arguments, chat)
    if not model_id:
        return None

    document_id = arguments.get("document_id")
    target_chars = arguments.get("target_chars")
    overlap_chars = arguments.get("overlap_chars", 0)
    if not isinstance(document_id, int) or document_id <= 0:
        return None
    try:
        target_chars = _resolve_prompt_document_chunk_target_chars(target_chars)
    except ValueError:
        return None
    if not isinstance(overlap_chars, int) or overlap_chars < 0:
        return None
    if overlap_chars >= target_chars:
        return None

    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        return None

    if doc.extracted_text:
        total_chars = len(doc.extracted_text)
    elif doc.num_chunks:
        total_chars = doc.num_chunks * TOKENS_PER_CHUNK * EST_CHARS_PER_TOKEN
    else:
        return None

    if total_chars <= 0:
        return None

    step_chars = max(1, target_chars - overlap_chars)
    estimated_chunk_count = max(
        1, (max(total_chars - target_chars, 0) + step_chars - 1) // step_chars + 1
    )
    estimated_input_chars = (
        total_chars + max(0, estimated_chunk_count - 1) * overlap_chars
    )

    total_cost = Decimal("0")

    prompt = arguments.get("prompt") or ""
    if prompt:
        prompt_tokens = len(prompt) // EST_CHARS_PER_TOKEN
        total_cost += _calculate_cost_for_units(
            f"{model_id}-in", prompt_tokens * estimated_chunk_count
        )

    total_cost += _calculate_cost_for_units(
        f"{model_id}-in", estimated_input_chars // EST_CHARS_PER_TOKEN
    )
    total_cost += _calculate_cost_for_units(
        f"{model_id}-out", ESTIMATED_OUTPUT_TOKENS * estimated_chunk_count
    )

    if total_cost == 0:
        return None

    return f"{cad_cost(total_cost):.2f}"


async def prompt_documents(arguments: dict, context: ToolContext) -> dict:
    """
    Process documents with an LLM prompt in the background via Celery tasks.

    Each document is processed independently. Results are saved as markdown
    ChatFiles attached to the bot message and added to the chat_files library.

    Args:
        arguments: dict with:
            - document_ids: List of document IDs to process
            - prompt: The instructions to apply to each document
            - template_doc_id: Optional template document ID (or null)
            - llm_model: Optional explicit model override for this batch
            - reasoning_effort: Optional reasoning override for this batch
            - truncate_chars: Optional character limit per document

    Returns:
        dict with status information about the submitted tasks
    """
    arguments = dict(arguments)
    # Backward compatibility: older tool schemas may still send this field.
    # Ignore it so stale calls don't fail or encourage inline re-summarization.
    arguments.pop("include_output_text", None)

    document_ids = arguments.get("document_ids", [])
    prompt = arguments.get("prompt")  # Can be None
    template_doc_id = arguments.get("template_doc_id")  # Can be None
    llm_model = arguments.get("llm_model")  # Can be None
    reasoning_effort = arguments.get("reasoning_effort")  # Can be None
    truncate_chars = arguments.get("truncate_chars")  # Can be None
    include_generated_outputs = bool(arguments.get("include_generated_outputs", False))

    if not document_ids:
        return {
            "error": "No document IDs provided. Please specify which documents to process."
        }

    if len(document_ids) > MAX_PROMPT_DOCUMENTS:
        return {"error": "Maximum of 10 documents can be processed at once."}

    batch_result = await _run_document_processing_batch(
        context=context,
        job_inputs=[{"document_id": doc_id} for doc_id in document_ids],
        prompt=prompt,
        template_doc_id=template_doc_id,
        llm_model=llm_model,
        reasoning_effort=reasoning_effort,
        truncate_chars=truncate_chars,
        include_generated_outputs=include_generated_outputs,
    )
    if batch_result.get("error"):
        return {"error": batch_result["error"]}

    results = batch_result["results"]
    completed = batch_result["completed"]
    if not completed:
        return {
            "success": False,
            "message": "No documents could be processed.",
            "processing_scope": (
                {
                    "truncate_chars": truncate_chars,
                    "user_notice": (
                        f"Only the first {truncate_chars} characters of each document were processed."
                        if truncate_chars
                        else None
                    ),
                }
                if truncate_chars
                else None
            ),
            "files": results,
        }

    return {
        "success": True,
        "message": (
            f"Processed {len(completed)} document(s). "
            f"The output files have been attached to this message and indexed for Q&A."
            + (
                f" Only the first {truncate_chars} characters of each document were processed."
                if truncate_chars
                else ""
            )
        ),
        "processing_scope": (
            {
                "truncate_chars": truncate_chars,
                "user_notice": f"Only the first {truncate_chars} characters of each document were processed.",
            }
            if truncate_chars
            else None
        ),
        "output_document_ids": _ordered_output_document_ids(completed),
        "files": results,
    }


async def prompt_document_ranges(arguments: dict, context: ToolContext) -> dict:
    """Process explicit document ranges into downloadable markdown artifacts."""
    arguments = dict(arguments)
    arguments.pop("include_output_text", None)

    inputs = arguments.get("inputs", [])
    prompt = arguments.get("prompt")
    template_doc_id = arguments.get("template_doc_id")
    llm_model = arguments.get("llm_model")
    reasoning_effort = arguments.get("reasoning_effort")
    include_generated_outputs = bool(arguments.get("include_generated_outputs", False))

    if not inputs:
        return {
            "error": "No range inputs provided. Please specify at least one document range."
        }

    if len(inputs) > MAX_PROMPT_DOCUMENT_RANGE_INPUTS:
        return {"error": "Maximum of 50 document ranges can be processed at once."}

    try:
        normalized_inputs = [
            _normalize_range_input(item, index) for index, item in enumerate(inputs)
        ]
    except ValueError as exc:
        return {"error": str(exc)}

    batch_result = await _run_document_processing_batch(
        context=context,
        job_inputs=normalized_inputs,
        prompt=prompt,
        template_doc_id=template_doc_id,
        llm_model=llm_model,
        reasoning_effort=reasoning_effort,
        truncate_chars=None,
        include_generated_outputs=include_generated_outputs,
    )
    if batch_result.get("error"):
        return {"error": batch_result["error"]}

    results = batch_result["results"]
    completed = batch_result["completed"]
    distinct_document_ids = sorted({item["document_id"] for item in normalized_inputs})
    processing_scope = {
        "range_count": len(normalized_inputs),
        "source_document_count": len(distinct_document_ids),
    }

    if not completed:
        return {
            "success": False,
            "message": "No document ranges could be processed.",
            "processing_scope": processing_scope,
            "files": results,
        }

    return {
        "success": True,
        "message": (
            f"Processed {len(completed)} document range(s). "
            "The chunk output files have been attached to this message and indexed for Q&A."
        ),
        "processing_scope": processing_scope,
        "files": results,
    }


async def prompt_document_chunks(arguments: dict, context: ToolContext) -> dict:
    """Plan and process one large document into chunk-level intermediate artifacts."""
    arguments = dict(arguments)
    arguments.pop("include_output_text", None)

    document_id = arguments.get("document_id")
    target_chars = arguments.get("target_chars")
    overlap_chars = arguments.get("overlap_chars", 0)
    prefer_page_boundaries = bool(arguments.get("prefer_page_boundaries", True))
    prompt = arguments.get("prompt")
    template_doc_id = arguments.get("template_doc_id")
    llm_model = arguments.get("llm_model")
    reasoning_effort = arguments.get("reasoning_effort")
    include_generated_outputs = bool(arguments.get("include_generated_outputs", False))

    try:
        resolved_target_chars = _resolve_prompt_document_chunk_target_chars(
            target_chars
        )
    except ValueError as exc:
        return {"error": str(exc)}

    chunk_plan = await plan_document_chunks(
        {
            "document_id": document_id,
            "target_chars": resolved_target_chars,
            "overlap_chars": overlap_chars,
            "prefer_page_boundaries": prefer_page_boundaries,
        },
        context,
    )
    if chunk_plan.get("error"):
        return {"error": chunk_plan["error"]}

    planned_inputs = chunk_plan.get("prompt_document_ranges_inputs") or []
    if not planned_inputs:
        return {
            "success": False,
            "message": "No document chunks could be planned.",
            "processing_scope": {
                "document_id": document_id,
                "range_count": 0,
                "target_chars": resolved_target_chars,
                "overlap_chars": overlap_chars,
                "prefer_page_boundaries": prefer_page_boundaries,
            },
            "files": [],
        }

    if len(planned_inputs) > MAX_PROMPT_DOCUMENT_RANGE_INPUTS:
        return {
            "error": (
                "Chunk plan would create too many chunk jobs. "
                f"This tool supports at most {MAX_PROMPT_DOCUMENT_RANGE_INPUTS} chunks at once; "
                "increase target_chars or narrow the requested scope."
            )
        }

    batch_result = await _run_document_processing_batch(
        context=context,
        job_inputs=planned_inputs,
        prompt=prompt,
        template_doc_id=template_doc_id,
        llm_model=llm_model,
        reasoning_effort=reasoning_effort,
        truncate_chars=None,
        include_generated_outputs=include_generated_outputs,
    )
    if batch_result.get("error"):
        return {"error": batch_result["error"]}

    results = batch_result["results"]
    completed = batch_result["completed"]
    processing_scope = {
        "document_id": chunk_plan["document_id"],
        "source_filename": chunk_plan.get("source_filename"),
        "range_count": len(planned_inputs),
        "target_chars": resolved_target_chars,
        "overlap_chars": overlap_chars,
        "prefer_page_boundaries": prefer_page_boundaries,
        "total_chars": chunk_plan.get("total_chars"),
        "total_pages": chunk_plan.get("total_pages"),
        "estimated_chunk_count": chunk_plan.get("estimated_chunk_count"),
    }
    output_document_ids = _ordered_output_document_ids(completed)

    if not completed:
        return {
            "success": False,
            "message": "No document chunks could be processed.",
            "processing_scope": processing_scope,
            "files": results,
        }

    return {
        "success": True,
        "message": (
            f"Processed {len(completed)} chunk(s) from one document. "
            "The chunk output files have been attached to this message and indexed for Q&A. "
            "These are intermediate chunk artifacts only; inspect them yourself for any final synthesis or comparison."
        ),
        "processing_scope": processing_scope,
        "output_document_ids": output_document_ids,
        "recommended_followup": {
            "tool": "get_document_text",
            "arguments": {"document_ids": output_document_ids},
            "note": (
                "If you need to inspect the chunk outputs inline, read these output_document_ids "
                "in one ordered get_document_text call rather than one call per chunk."
            ),
        },
        "files": results,
    }


async def plan_document_chunks(arguments: dict, context: ToolContext) -> dict:
    """Plan overlap-aware character ranges for chunking a large document."""
    from asgiref.sync import sync_to_async

    from librarian.models import Document

    document_id = arguments.get("document_id")
    target_chars = arguments.get("target_chars")
    overlap_chars = arguments.get("overlap_chars", 0)
    prefer_page_boundaries = bool(arguments.get("prefer_page_boundaries", True))

    if not isinstance(document_id, int) or document_id <= 0:
        return {"error": "document_id must be a positive integer"}
    try:
        target_chars = _resolve_prompt_document_chunk_target_chars(target_chars)
    except ValueError as exc:
        return {"error": str(exc)}
    if not isinstance(overlap_chars, int) or overlap_chars < 0:
        return {"error": "overlap_chars must be 0 or greater"}
    if overlap_chars >= target_chars:
        return {"error": "overlap_chars must be smaller than target_chars"}

    user = context.user

    @sync_to_async
    def build_plan():
        try:
            doc = Document.objects.select_related(
                "data_source", "data_source__library"
            ).get(id=document_id)
        except Document.DoesNotExist:
            return (
                None,
                f"Document {document_id} not found. Verify the ID using list_documents.",
            )

        library = doc.data_source.library if doc.data_source else None
        if not library:
            return None, "Document is not part of a library"

        can_access = (
            library.is_public
            or library.created_by == user
            or library.user_roles.filter(user=user).exists()
        )
        if not can_access:
            return None, "You don't have permission to access this document"

        full_text = doc.extracted_text or ""
        if not full_text.strip():
            return None, "Document has no extracted text. It may still be processing."

        total_chars = len(full_text)
        total_pages = _get_page_count_from_text(full_text)
        page_map = (
            _map_pages_to_char_offsets(full_text) if prefer_page_boundaries else {}
        )
        page_boundaries = _compute_page_boundaries(full_text)
        page_end_offsets = sorted({end for _, end in page_map.values()})

        ranges = []
        prompt_document_ranges_inputs = []
        start_char = 0
        chunk_index = 1
        snap_window = max(target_chars // 4, 1)

        while start_char < total_chars:
            desired_end = min(total_chars, start_char + target_chars)
            end_char = desired_end

            if prefer_page_boundaries and page_end_offsets:
                forward_candidates = [
                    offset
                    for offset in page_end_offsets
                    if desired_end
                    <= offset
                    <= min(total_chars, desired_end + snap_window)
                ]
                backward_candidates = [
                    offset
                    for offset in page_end_offsets
                    if start_char < offset <= desired_end
                ]
                if forward_candidates:
                    end_char = forward_candidates[0]
                elif backward_candidates:
                    end_char = backward_candidates[-1]

            if end_char <= start_char:
                end_char = min(total_chars, start_char + target_chars)
            if end_char <= start_char:
                break

            start_page = _get_page_for_offset(page_boundaries, start_char)
            end_page = _get_page_for_offset(
                page_boundaries, max(start_char, end_char - 1)
            )
            label = f"chunk_{chunk_index:03d}"
            char_range_input = {
                "document_id": doc.id,
                "start_char": start_char,
                "end_char": end_char,
                "label": label,
                "overlap_chars": overlap_chars,
            }
            ranges.append(
                {
                    "document_id": doc.id,
                    "label": label,
                    "start_char": start_char,
                    "end_char": end_char,
                    "start_page": start_page,
                    "end_page": end_page,
                    "overlap_chars": overlap_chars,
                }
            )
            prompt_document_ranges_inputs.append(char_range_input)

            next_start = max(0, end_char - overlap_chars)
            if next_start <= start_char:
                next_start = end_char
            start_char = next_start
            chunk_index += 1

        return (
            {
                "document_id": doc.id,
                "source_filename": doc.filename,
                "total_chars": total_chars,
                "total_pages": total_pages,
                "target_chars": target_chars,
                "overlap_chars": overlap_chars,
                "prefer_page_boundaries": prefer_page_boundaries,
                "estimated_chunk_count": len(ranges),
                "ranges": ranges,
                "prompt_document_ranges_inputs": prompt_document_ranges_inputs,
            },
            None,
        )

    result, error = await build_plan()
    if error:
        return {"error": error}
    return result


_model_list = "; ".join(
    f'"{model_id}": {guidance}'
    for model_id, guidance in PROMPT_DOCUMENT_MODEL_CHOICES.items()
)

TOOL_REGISTRY.register(
    OttoTool(
        name="prompt_documents",
        description=(
            "Process whole documents with an LLM prompt and save downloadable output files."
            # "Each source document is processed independently in parallel; this tool never combines multiple source documents into one model context. "
            # "Use it when the unit of work is an entire document, such as summarizing each file, extracting the same fields from each file, classifying each file, or rewriting each file to a template. "
            # "It is also useful as a delegated parallel subworkflow: apply the same prompt to many whole documents, keep the main context window clean, and optionally route the batch to a cheaper model like gpt-5.4-nano. "
            # "For a single document where the user wants the answer directly in chat, prefer get_document_text so the response can stream normally. "
            # "Use list_documents, rag_search, or retrieve_url_content to get document IDs. "
            # "Documents from retrieve_url_content can be passed directly — this tool waits for text extraction to complete. "
            # "Returns output filenames and document IDs so you can reference them later. "
            # "If you later need to inspect those generated outputs inline, prefer a single ordered get_document_text call using document_ids instead of many one-document reads. "
            # "Do not avoid this tool merely because the user asked a question instead of explicitly asking for files, but keep its contract narrow: it creates per-document artifacts only. "
            # "If the user ultimately needs a cross-document comparison or synthesis, first use this tool to generate structured per-document outputs, then inspect those generated outputs yourself in a later step. Never ask prompt_documents to compare, rank, or synthesize across source documents. "
            # "After successful processing, keep the user-facing response brief and file-first unless the user explicitly asks for inline text. "
            # "Interpret requests such as 'summarize each file' as instructions to generate per-document output files, "
            # "not as a requirement to paste every summary into the chat response.\n\n"
            # "A custom prompt is required; there are no built-in presets. "
            # "Model selection: set llm_model explicitly for this batch. By default, use gpt-5.4-nano for document-processing calls instead of inheriting the chat's current model. "
            # "Prefer gpt-5.4-nano for almost all document-processing jobs, including extraction, classification, format conversion, templated rewrites, and simple summaries. "
            # "Use gpt-5.4-mini only when the task truly needs materially stronger reasoning, nuanced interpretation, or noticeably better writing within each document, or when the user or loaded skill instructions explicitly require it. "
            # f"Available overrides: {_model_list} "
            # "For reasoning_effort, use default for the lightest supported reasoning on the chosen model, or choose low, medium, or high when the task needs extra care.\n\n"
            # "If you set truncate_chars, only the first N characters of each document will be processed. "
            # "Use this for front-matter tasks such as titles, authors, or dates. "
            # "When truncate_chars is used, you must tell the user that only the first N characters were processed.\n\n"
            # "By default, tool-generated output files are skipped to prevent recursive processing (summary of summaries). "
            # "Set include_generated_outputs=true only when you intentionally want to process generated outputs.\n\n"
            # "For template-based processing (e.g. filling a template with content from documents), "
            # "pass template_doc_id with the ID of the template document."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of document IDs to process.",
                    "maximum": 10,
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Required instructions to apply to each document (e.g. extraction, summarization, classification)."
                        # "Use a direct task description such as extraction, summarization, classification, or rewriting instructions."
                    ),
                },
                "template_doc_id": {
                    "type": ["integer", "null"],
                    "description": "Optional Document ID of a template to use for context.",
                },
                "llm_model": {
                    "type": ["string", "null"],
                    "enum": [None, *PROMPT_DOCUMENT_MODEL_CHOICES.keys()],
                    "description": (
                        "Optional model override for this batch only."
                        # "Default to gpt-5.4-nano for routine document-processing calls; use null only when you intentionally want to inherit the chat's current model. "
                        # "Reserve gpt-5.4-mini for genuinely harder reasoning/interpretation or meaningfully better writing tasks within each document. "
                        # f"Choices: {_model_list}"
                    ),
                    "default": "gpt-5.4-nano",
                },
                "reasoning_effort": {
                    "type": ["string", "null"],
                    "enum": [None, *PROMPT_DOCUMENT_REASONING_CHOICES],
                    "description": (
                        "Optional reasoning level for this batch. Choose lightest supported reasoning on the chosen model by default."
                        # "Use default for the lightest supported reasoning on the chosen model, "
                        # "or choose low, medium, or high."
                    ),
                },
                "truncate_chars": {
                    "type": ["integer", "null"],
                    "description": (
                        "Optional character limit per document. "
                        # "When provided, only the first N characters of each document are processed. "
                        "Use this for front-matter extraction tasks (e.g. titles, early-page metadata)."
                        # "If used, you must tell the user "
                        # "that only the first N characters were processed."
                    ),
                },
                "include_generated_outputs": {
                    "type": "boolean",
                    "description": (
                        "Whether to include documents that were generated by prior tool runs "
                        "(attached to bot messages)."
                        # "Default false to avoid recursive processing."
                    ),
                    "default": False,
                },
            },
            "required": [
                "document_ids",
                "prompt",
                "template_doc_id",
                "llm_model",
                "reasoning_effort",
                "truncate_chars",
                "include_generated_outputs",
            ],
            "additionalProperties": False,
        },
        execute=prompt_documents,
        category=TOOL_CATEGORY_DOCUMENT_PROCESSING,
        requires_user=True,
        requires_chat=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        requires_approval=False,
        approval_label=_("Batch document processing"),
        estimate_cost=estimate_document_processing_cost,
    )
)

TOOL_REGISTRY.register(
    OttoTool(
        name="prompt_document_chunks",
        description=(
            "Plan and process one large document into chunk-level markdown output files."
            # "This is a map-stage helper only: it creates one intermediate result per chunk and does NOT synthesize, compare, or reduce the chunks for you. "
            # "Use it when one document is too large to process as a whole (>=400K characters) and you want reusable chunk artifacts without manually spelling out every range. "
            # "It internally plans overlap-aware chunks, then applies the same prompt to each chunk separately. "
            # "After it finishes, inspect or compare the generated chunk files yourself. "
            # "Use large chunks (about 400,000 characters) for high-level summarization or extraction so you get as few chunk artifacts as practical; pass a smaller target_chars only when you intentionally need finer granularity. "
            # "By default, explicitly set llm_model to gpt-5.4-nano for this tool; switch to gpt-5.4-mini only when the chunk task genuinely needs materially stronger reasoning or writing quality, or when the user or loaded skill instructions require it. "
            # "If you need to read the chunk outputs inline, use the returned output_document_ids in one ordered get_document_text call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "Document ID of the source document.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Required instructions to apply independently to each chunk.",
                },
                "target_chars": {
                    "type": "integer",
                    "description": "Target chunk size in characters.",
                    # "Prefer a large value such as 400000 for high-level outcomes and fewer chunk artifacts; use a smaller value only when you intentionally need finer granularity.",
                },
                "overlap_chars": {
                    "type": "integer",
                    "description": "Optional overlap between chunks in characters.",
                },
                "prefer_page_boundaries": {
                    "type": "boolean",
                    "description": "Whether to prefer snapping chunk ends to page boundaries when available.",
                },
                "template_doc_id": {
                    "type": ["integer", "null"],
                    "description": "Optional document ID of a template to use for context.",
                },
                "llm_model": {
                    "type": ["string", "null"],
                    "enum": [None, *PROMPT_DOCUMENT_MODEL_CHOICES.keys()],
                    "description": (
                        "Optional model override for this batch only."
                        # "Default to gpt-5.4-nano for routine chunk processing; use null only when you intentionally want to inherit the chat's current model. "
                        # f"Choices: {_model_list}"
                    ),
                    "default": "gpt-5.4-nano",
                },
                "reasoning_effort": {
                    "type": ["string", "null"],
                    "enum": [None, *PROMPT_DOCUMENT_REASONING_CHOICES],
                    "description": "Optional reasoning level for this batch.",
                },
                "include_generated_outputs": {
                    "type": "boolean",
                    "description": "Whether to allow processing of generated-output documents.",
                    "default": False,
                },
            },
            "required": [
                "document_id",
                "prompt",
                "target_chars",
                "overlap_chars",
                "prefer_page_boundaries",
                "template_doc_id",
                "llm_model",
                "reasoning_effort",
                "include_generated_outputs",
            ],
            "additionalProperties": False,
        },
        execute=prompt_document_chunks,
        category=TOOL_CATEGORY_DOCUMENT_PROCESSING,
        requires_user=True,
        requires_chat=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        requires_approval=False,
        approval_label=_("Process document chunks"),
        estimate_cost=estimate_document_chunk_processing_cost,
    )
)
