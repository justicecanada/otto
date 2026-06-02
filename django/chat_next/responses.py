import json
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from asgiref.sync import async_to_sync, sync_to_async
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars, get_contextvars

from otto.utils.decorators import permission_required

from chat_next._llm import (
    COMPACTION_PROCESSING_STEP,
    ResponsesAPIClient,
    _sanitize_function_call_output_for_storage,
    build_conversation_input,
    build_system_prompt,
    create_compaction_costs,
    estimate_tool_continuation_usage,
    get_context_management_mode,
    get_model,
    restore_chat_loaded_skill_state,
    should_compact_context,
    should_proactively_compact_tool_continuation,
    stream_chat_for_htmx,
)
from chat_next._tools.approval import APPROVAL_SOURCE_MANUAL
from chat_next.error_messages import (
    build_context_window_error_message,
    is_context_window_error,
)
from chat_next.models import Message
from chat_next.prompts import get_effective_enabled_tools
from chat_next.utils import (
    _clear_terminal_approval_state,
    htmx_stream,
)

logger = get_logger(__name__)


def _resolve_auto_approve_tool_id(*, tool_id: str = "", tool_label: str = "") -> str:
    """Resolve approval request metadata to the canonical local tool ID."""
    from chat_next.tools import TOOL_REGISTRY

    tool_id = (tool_id or "").strip()
    tool_label = (tool_label or "").strip()

    if tool_id and TOOL_REGISTRY.get(tool_id):
        return tool_id

    if tool_label:
        for registered_tool_id in TOOL_REGISTRY.list_tool_names():
            tool = TOOL_REGISTRY.get(registered_tool_id)
            if not tool:
                continue
            display_name = str(tool.display_name or "").strip()
            if display_name == tool_label:
                return registered_tool_id

    return tool_id or tool_label


batch_size = getattr(settings, "PER_DOC_BATCH_SIZE", 5)


def _get_response_model_settings(response_message):
    chat_settings = response_message.chat.settings
    model_overrides = (response_message.details or {}).get("model_overrides") or {}

    return {
        "model_id": model_overrides.get("chat_model") or chat_settings.chat_model,
        "temperature": chat_settings.chat_temperature,
        "reasoning_effort": model_overrides.get("chat_reasoning_effort")
        or chat_settings.chat_reasoning_effort,
        "verbosity": model_overrides.get("chat_verbosity")
        or chat_settings.chat_verbosity,
    }


def _create_costs_once_per_response(
    message_id: int,
    client: ResponsesAPIClient,
    model_id: str,
    *,
    reuse_container: bool,
) -> float:
    """Create costs idempotently for the latest streamed response segment.

    Cost rows are keyed by the latest response_id seen by the client
    (tracked on client.previous_response_id by stream_chat_for_htmx).
    If we've already billed that response_id for this message, skip creating
    new Cost rows and return the currently persisted message total.
    """
    from chat_next._llm import create_tool_costs

    message = Message.objects.get(id=message_id)
    details = message.details or {}

    raw_billed_ids = details.get("billed_response_ids") or []
    billed_response_ids = [rid for rid in raw_billed_ids if isinstance(rid, str)]

    response_id = getattr(client, "previous_response_id", None)
    context = get_contextvars()

    logger.info(
        "chat_next_cost_stream_start",
        message_id=message_id,
        response_id=response_id,
        billed_ids_count=len(billed_response_ids),
        existing_message_usd_cost=float(message.usd_cost or 0.0),
        has_usage=bool(client.last_usage),
        tool_call_count=len(client.last_tool_calls or []),
        code_interpreter_sessions=client.last_code_interpreter_sessions,
        reuse_container=bool(reuse_container),
        context_feature=context.get("feature"),
        context_user_id=context.get("user_id"),
        context_cost_group_id=context.get("cost_group_id"),
        context_message_next_id=context.get("message_next_id"),
    )

    if response_id and response_id in billed_response_ids:
        logger.info(
            "Skipping duplicate chat_next cost creation for response",
            message_id=message_id,
            response_id=response_id,
            existing_message_usd_cost=float(message.usd_cost or 0.0),
        )
        return float(message.usd_cost or 0.0)

    token_cost_usd = 0.0
    if client.last_usage:
        token_cost_usd = float(client.last_usage.create_costs(model_id) or 0.0)

    tool_cost_usd = 0.0
    if client.last_tool_calls:
        tool_cost_usd = float(
            create_tool_costs(
                client.last_tool_calls,
                reuse_container=reuse_container,
                code_interpreter_sessions=client.last_code_interpreter_sessions,
            )
            or 0.0
        )

    if response_id:
        billed_response_ids.append(response_id)
        # Keep only the latest 50 response IDs per message.
        details["billed_response_ids"] = billed_response_ids[-50:]
        message.details = details
        message.save(update_fields=["details"])

    # Cost.objects.new() recomputes message.usd_cost; refresh and return latest total.
    message.refresh_from_db()
    total_message_cost = float(message.usd_cost or 0.0)

    logger.info(
        "chat_next_cost_stream_complete",
        message_id=message_id,
        response_id=response_id,
        token_cost_usd=token_cost_usd,
        tool_cost_usd=tool_cost_usd,
        segment_cost_usd=token_cost_usd + tool_cost_usd,
        total_message_usd_cost=total_message_cost,
        billed_ids_count=len((message.details or {}).get("billed_response_ids") or []),
    )

    return total_message_cost


def _record_compaction_state(
    *,
    chat,
    user_message,
    compacted_items: list,
):
    """Persist compacted conversation state for future conversation rebuilds."""
    if not user_message or not compacted_items:
        return

    chat.compacted_input_items = compacted_items
    chat.compacted_through_message = user_message
    chat.save(update_fields=["compacted_input_items", "compacted_through_message"])


def _compact_chat_history_if_needed(
    *,
    chat,
    response_message,
    user_message,
    input_items: list,
    instructions: str,
    client: ResponsesAPIClient,
    model_id: str,
):
    """Proactively compact history between messages when compact mode is selected."""
    if not user_message:
        return input_items, False

    if get_context_management_mode(chat) != "compact":
        return input_items, False

    last_bot_message = (
        chat.messages.filter(is_bot=True)
        .exclude(id=response_message.id)
        .order_by("-date_created")
        .first()
    )
    if not last_bot_message:
        return input_items, False

    usage = (last_bot_message.details or {}).get("usage") or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    if not should_compact_context(input_tokens, output_tokens, model_id):
        return input_items, False

    compacted_items, compaction_usage = async_to_sync(client.compact_conversation)(
        input_items,
        instructions,
    )
    if not compacted_items:
        return input_items, False

    create_compaction_costs(compaction_usage, model_id)

    _record_compaction_state(
        chat=chat,
        user_message=user_message,
        compacted_items=compacted_items,
    )
    return compacted_items, True


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def otto_response(request, message_id=None):
    """
    Stream a response to the user's message. Thin wrapper around chat_response
    that also handles error catching and testing hooks.
    """
    # Test hook: simulate SSE error for testing error handling UI
    # Usage: Add ?simulate_sse_error=1 to the SSE URL
    if request.GET.get("simulate_sse_error") == "1":
        raise Exception("Simulated SSE error for testing")

    response_message = Message.objects.get(id=message_id)

    try:
        chat = response_message.chat

        # For costing and logging. Contextvars are accessible anytime during the request
        # including in async functions (i.e. htmx_stream) and Celery tasks.
        # Use message_next_id (not message_id) to avoid FK collision with chat.Message
        user_id = request.user.id
        active_cost_group = request.user.get_active_cost_group(request)
        cost_group_id = active_cost_group.id if active_cost_group else None
        bind_contextvars(
            message_next_id=message_id,
            feature="chat_next",
            user_id=user_id,
            cost_group_id=cost_group_id,
        )

        return chat_response(
            chat,
            response_message,
            request=request,
        )

    except Exception as e:
        return error_response(chat, response_message, e)


def chat_response(
    chat,
    response_message,
    request=None,
):
    # If the user only uploaded files (no prompt text), avoid sending those
    # files to the model automatically (context stuffing / vision costs).
    # Instead, acknowledge the upload and let subsequent user prompts drive
    # tool-based retrieval from the chat files library.
    user_msg = response_message.parent
    if user_msg and user_msg.sorted_files and not (user_msg.text or "").strip():
        chat_files = list(user_msg.files.all())
        filenames = [cf.filename for cf in chat_files]

        if len(filenames) == 1:
            upload_text = _(
                "Uploaded 1 file: %(filename)s. It's now being added to this chat's uploads. "
                "It will appear in Q&A search after embedding finishes; large files may pause for manual embedding first."
            ) % {"filename": filenames[0]}
        else:
            preview = ", ".join(filenames[:3])
            if len(filenames) > 3:
                preview = preview + _(" …")
            upload_text = _(
                "Uploaded %(count)s files: %(filenames)s. They're now being added to this chat's uploads. "
                "They will appear in Q&A search after embedding finishes; large files may pause for manual embedding first."
            ) % {"count": len(filenames), "filenames": preview}

        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                response_str=upload_text,
                wrap_markdown=True,
                dots=False,
                remove_stop=True,
            ),
            content_type="text/event-stream",
        )

    # Get model configuration
    model_settings = _get_response_model_settings(response_message)
    model_id = model_settings["model_id"]
    model_config = get_model(model_id)
    temperature = model_settings["temperature"]
    reasoning_effort = model_settings["reasoning_effort"]
    verbosity = model_settings["verbosity"]
    is_reasoning = getattr(model_config, "reasoning", False)

    # Get enabled tools (defaults to empty list for new chats)
    enabled_tools = get_effective_enabled_tools(
        chat.settings,
        chat=chat,
        user=chat.user,
    )

    auto_approve_tools = chat.settings.chat_auto_approve_tools or []

    # Look up the last bot message's response_id for chaining (caching optimization)
    # If available, we use previous_response_id to let Azure handle context caching
    # Responses are stored for 30 days, so this may fail for old conversations
    previous_response_id = None
    code_interpreter_container_id = None
    last_bot_message = (
        chat.messages.filter(is_bot=True)
        .exclude(id=response_message.id)
        .order_by("-date_created")
        .first()
    )
    if last_bot_message and last_bot_message.response_id:
        previous_response_id = last_bot_message.response_id
        logger.debug(
            "Using previous_response_id for caching",
            previous_response_id=previous_response_id,
            message_id=last_bot_message.id,
        )

    # Attempt to reuse an existing code interpreter container for this chat
    if chat.code_interpreter_container_id:
        code_interpreter_container_id = chat.code_interpreter_container_id
        logger.debug(
            "Reusing code interpreter container",
            container_id=code_interpreter_container_id,
            chat_id=chat.id,
        )

    # Build conversation input and system prompt
    # Note: Files are NOT passed to vision/Code Interpreter - they're accessed via Q&A tools
    input_items = build_conversation_input(chat)
    instructions = build_system_prompt(chat)

    # Create Responses API client with user/chat context for local function tools.
    # Context handling is user-controlled:
    # - compact: server-side compaction between messages, truncation disabled
    # - truncate: API auto-truncation within the current response
    # - error: fail fast and show an explicit context-window error
    client = ResponsesAPIClient(
        model=model_id,
        reasoning=is_reasoning,
        reasoning_effort=reasoning_effort if is_reasoning else None,
        temperature=temperature if not is_reasoning else None,
        verbosity=verbosity,
        tools=enabled_tools,
        previous_response_id=previous_response_id,
        code_interpreter_container_id=code_interpreter_container_id,
        auto_approve_tools=auto_approve_tools,
        user=chat.user,
        chat=chat,
    )

    instructions, __ = restore_chat_loaded_skill_state(
        chat=chat,
        client=client,
        instructions=instructions,
    )

    initial_processing_steps = []

    try:
        input_items, was_compacted = _compact_chat_history_if_needed(
            chat=chat,
            response_message=response_message,
            user_message=user_msg,
            input_items=input_items,
            instructions=instructions,
            client=client,
            model_id=model_id,
        )
        if was_compacted:
            initial_processing_steps = [COMPACTION_PROCESSING_STEP]
    except Exception:
        logger.exception(
            "Failed to compact chat history before sending request",
            chat_id=chat.id,
            response_message_id=response_message.id,
        )

    # Create cost callback that uses the client's last_usage and tool calls
    def cost_callback():
        # When using container reuse, avoid re-charging code-interpreter session costs.
        sid = code_interpreter_container_id
        is_reused = sid and client.code_interpreter_container_id == sid
        return _create_costs_once_per_response(
            response_message.id,
            client,
            model_id,
            reuse_container=is_reused,
        )

    # Stream the response
    response_replacer = stream_chat_for_htmx(
        client,
        input_items,
        instructions,
        initial_processing_steps=initial_processing_steps,
    )

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_replacer=response_replacer,
            cost_callback=cost_callback,
        ),
        content_type="text/event-stream",
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def stop_response(request, message_id):
    """
    Stop the response to the user's message.
    """
    cache.set(f"stop_response_{message_id}", True, timeout=60)

    return HttpResponse(200)


def error_response(chat, response_message, error_message=None):
    """
    Send an error message to the user.
    """
    error_id = str(uuid.uuid4())[:7]

    # Check if error_message is an Exception instance
    if isinstance(error_message, Exception):
        if is_context_window_error(error_message):
            response_str = build_context_window_error_message(
                chat, response_message, error_id
            )
        else:
            from otto.utils.common import generate_ai_error_summary

            # Use AI to generate a user-friendly summary
            response_str = generate_ai_error_summary(error_message, error_id)
        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=response_message.id,
            chat_id=chat.id,
        )
    else:
        # Traditional error message handling (for string messages)
        response_str = _("There was an error processing your request.")
        if error_message and settings.DEBUG:
            response_str += f"\n\n```\n{error_message}\n```\n\n"
        # Translatable string extracted for xgettext compatibility
        error_id_label = _("Error ID:")
        response_str += f" _({error_id_label} {error_id})_"
        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=response_message.id,
            chat_id=chat.id,
        )

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_str=response_str,
        ),
        content_type="text/event-stream",
    )


@require_GET
@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def handle_approval(request, message_id=None):
    """
    Handle manual approval for local tool calls.

    This endpoint returns HTML that sets up the streaming infrastructure,
    which then auto-connects to the approval_stream endpoint.
    """
    import html

    from django.shortcuts import render

    message = Message.objects.get(id=message_id)
    approved_str = request.GET.get("approved", "false").lower()
    cost_cancelled_str = request.GET.get("cost_cancelled", "false").lower()

    # Pre-serialize processing steps as JSON for template
    processing_steps_json = ""
    if message.details.get("processing_steps"):
        processing_steps_json = html.escape(
            json.dumps(message.details["processing_steps"], ensure_ascii=False)
        )

    # Return HTML that replaces the message content with streaming setup
    return render(
        request,
        "chat_next/components/approval_streaming.html",
        {
            "message": message,
            "approved": approved_str,
            "cost_cancelled": cost_cancelled_str,
            "processing_steps_json": processing_steps_json,
        },
    )


@require_GET
@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def handle_approval_all(request, message_id=None):
    """
    Handle "Approve all" for local tool calls.

    This endpoint adds the tool to the auto_approve list AND approves the current call.
    Subsequent calls to this tool within this chat will be auto-approved.
    """
    import html

    from django.shortcuts import render

    message = Message.objects.get(id=message_id)
    chat = message.chat
    tool_id = request.GET.get("tool_id", "")
    tool_label = request.GET.get("tool_label", "")
    resolved_tool_id = _resolve_auto_approve_tool_id(
        tool_id=tool_id,
        tool_label=tool_label,
    )

    # Add tool to auto_approve list if not already there
    auto_approve_tools = chat.settings.chat_auto_approve_tools or []
    if resolved_tool_id and resolved_tool_id not in auto_approve_tools:
        auto_approve_tools.append(resolved_tool_id)
        chat.settings.chat_auto_approve_tools = auto_approve_tools
        chat.settings.save()

    # Pre-serialize processing steps as JSON for template
    processing_steps_json = ""
    if message.details.get("processing_steps"):
        processing_steps_json = html.escape(
            json.dumps(message.details["processing_steps"], ensure_ascii=False)
        )

    # Return HTML that replaces the message content with streaming setup
    return render(
        request,
        "chat_next/components/approval_streaming.html",
        {
            "message": message,
            "approved": "true",  # Approve all always approves
            "processing_steps_json": processing_steps_json,
        },
    )


@require_GET
@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def approval_stream(request, message_id=None):
    """
    Stream the response after approval. Called by the SSE connection in approval_streaming.html.
    """
    message = Message.objects.get(id=message_id)
    chat = message.chat

    # Bind request-scoped cost attribution for approval continuation requests.
    # Without this, costs created after approval can be recorded with feature=None.
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(
        message_next_id=message.id,
        feature="chat_next",
        user_id=user_id,
        cost_group_id=cost_group_id,
    )

    # Parse approved status
    approved_str = request.GET.get("approved", "false").lower()
    approved = approved_str == "true"
    cost_cancelled = request.GET.get("cost_cancelled", "false").lower() == "true"

    # If the user cancelled from the cost warning, stop entirely without
    # feeding a denial back to the API (which would cause the model to
    # continue generating a response).
    if cost_cancelled:
        try:
            message.details = _clear_terminal_approval_state(message.details)
            message.save(update_fields=["details"])
        except Exception:
            pass
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                message.id,
                response_str=str(_("Request cancelled.")),
            ),
            content_type="text/event-stream",
        )

    pending_local_tool = (message.details or {}).get("pending_local_tool")

    # Guard: if the approval state has been cleared (e.g., user sent a new
    # message which interrupted the pending approval), return a graceful
    # error instead of attempting a broken API call.
    has_response_output = bool(message.response_output)
    has_response_id = bool(message.response_id)
    if not pending_local_tool and not has_response_output and not has_response_id:
        logger.warning(
            "Approval stream called but approval state was already cleared",
            message_id=message_id,
        )
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                message.id,
                response_str=str(
                    _(
                        "This approval is no longer available. "
                        "The conversation has moved on."
                    )
                ),
            ),
            content_type="text/event-stream",
        )

    if not pending_local_tool:
        logger.warning(
            "Approval stream called without a pending local tool",
            message_id=message_id,
        )
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                message.id,
                response_str=str(
                    _(
                        "This approval is no longer available. "
                        "The conversation has moved on."
                    )
                ),
            ),
            content_type="text/event-stream",
        )

    # Use function_call items from response_output when available;
    # otherwise fall back to the approval call metadata captured in pending_local_tool.
    function_calls_for_approval = []
    if message.response_output:
        for item in message.response_output:
            if isinstance(item, dict) and item.get("type") == "function_call":
                function_calls_for_approval.append(item)
    if not function_calls_for_approval:
        fallback_calls = pending_local_tool.get("approval_calls", [])
        function_calls_for_approval = [
            item
            for item in fallback_calls
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]

    from chat_next.approval_logging import mark_external_tool_approval_decision

    mark_external_tool_approval_decision(
        message=message,
        user=request.user,
        function_calls=function_calls_for_approval,
        approved=approved,
        decided_at=timezone.now(),
        approval_request_id=pending_local_tool.get("call_id", ""),
        fallback_displayed_at=pending_local_tool.get("approval_requested_at"),
    )

    # Clear pending approval marker to avoid reuse once we're actually resuming.
    try:
        message.details = _clear_terminal_approval_state(message.details)
        message.save(update_fields=["details"])
    except Exception:
        pass

    # Recover settings
    model_settings = _get_response_model_settings(message)
    model_id = model_settings["model_id"]
    model_config = get_model(model_id)
    temperature = model_settings["temperature"]
    reasoning_effort = model_settings["reasoning_effort"]
    verbosity = model_settings["verbosity"]
    is_reasoning = getattr(model_config, "reasoning", False)
    enabled_tools = get_effective_enabled_tools(
        chat.settings,
        chat=chat,
        user=chat.user,
    )
    auto_approve_tools = chat.settings.chat_auto_approve_tools or []

    # Initialize client with previous_response_id to enable session chaining
    client = ResponsesAPIClient(
        model=model_id,
        reasoning=is_reasoning,
        reasoning_effort=reasoning_effort if is_reasoning else None,
        temperature=temperature if not is_reasoning else None,
        verbosity=verbosity,
        previous_response_id=message.response_id,
        code_interpreter_container_id=chat.code_interpreter_container_id,
        tools=enabled_tools,
        auto_approve_tools=auto_approve_tools,
        user=chat.user,
        chat=chat,
    )

    # When the user denies at the iteration limit, strip local function tools
    # so the API cannot generate new function_call items. This forces the model
    # to respond with text using whatever information it already has.
    max_iterations_reached = pending_local_tool.get("max_iterations_reached", False)
    if max_iterations_reached and not approved:
        from chat_next.models import LOCAL_TOOL_CATEGORIES

        client.tools = [t for t in client.tools if t not in LOCAL_TOOL_CATEGORIES]

    # Get previous raw processing steps to prepend to stream (for API chaining)
    previous_raw_steps = message.details.get("raw_processing_steps", [])

    # Build the full conversation and system prompt so stream_chat_for_htmx can
    # compact the context if the continuation exceeds the context window.
    # Without this, the approval flow only passes function_call_output items
    # which are insufficient for meaningful compaction.
    approval_full_input_items = build_conversation_input(chat)
    approval_instructions = build_system_prompt(chat)
    loaded_skill_state = pending_local_tool.get("loaded_skill_state")
    approval_instructions, loaded_skill_state = restore_chat_loaded_skill_state(
        chat=chat,
        client=client,
        instructions=approval_instructions,
        message_loaded_skill_state=loaded_skill_state,
    )

    # Create the generator for the new stream segment
    async def inner_gen():
        from structlog import get_logger

        from chat_next.tools import (
            TOOL_REGISTRY,
            build_function_call_output,
            execute_tool_call,
        )

        logger = get_logger(__name__)

        # Get pre-executed outputs from auto-approved calls (if any)
        pre_executed_outputs = pending_local_tool.get("pre_executed_outputs", [])
        pre_executed_call_ids = {item.get("call_id") for item in pre_executed_outputs}

        # Find call_ids that already have outputs from earlier iterations.
        # response_output accumulates across iterations, so function_calls from
        # previous iterations may already have a corresponding function_call_output.
        already_completed_call_ids = set()
        if message.response_output:
            for item in message.response_output:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "function_call_output"
                ):
                    completed_id = item.get("call_id")
                    if completed_id:
                        already_completed_call_ids.add(completed_id)

        # Find function_calls from the stored response_output that still need outputs.
        # Skip calls that were already completed in earlier iterations.
        all_function_calls = []
        seen_call_ids = set()
        for item in function_calls_for_approval:
            call_id = item.get("call_id") or item.get("id")
            name = item.get("name") or ""

            if call_id and call_id in seen_call_ids:
                logger.info(
                    "Skipping duplicate function_call in approval resume (call_id)",
                    call_id=call_id,
                    name=name,
                )
                continue

            if call_id and call_id in already_completed_call_ids:
                logger.info(
                    "Skipping already-completed function_call in approval resume",
                    call_id=call_id,
                    name=name,
                )
                continue

            if call_id:
                seen_call_ids.add(call_id)

            all_function_calls.append(item)

        logger.info(
            "Resuming after local tool approval",
            approved=approved,
            pending_tool_name=pending_local_tool.get("name"),
            pending_call_id=pending_local_tool.get("call_id"),
            previous_response_id=message.response_id,
            pre_executed_count=len(pre_executed_outputs),
            pre_executed_call_ids=list(pre_executed_call_ids),
            total_function_calls_in_response=len(all_function_calls),
            all_call_ids=[fc.get("call_id") for fc in all_function_calls],
        )

        # Execute ALL function calls that weren't pre-executed
        # The user's approval applies to ALL pending approval-required tools
        all_outputs = list(pre_executed_outputs)
        tool_result_steps = []

        for fc in all_function_calls:
            fc_call_id = fc.get("call_id") or fc.get("id")
            fc_name = fc.get("name")
            fc_arguments = fc.get("arguments", "{}")

            # Skip if already pre-executed
            if fc_call_id in pre_executed_call_ids:
                logger.debug(
                    "Skipping pre-executed function call",
                    name=fc_name,
                    call_id=fc_call_id,
                )
                continue

            logger.info(
                "Executing function call",
                name=fc_name,
                call_id=fc_call_id,
                approved=approved,
            )

            # Resolve display label for the processing step
            fc_tool = TOOL_REGISTRY.get(fc_name)
            fc_tool_label = fc_tool.display_name if fc_tool else fc_name

            # Yield "in_progress" step BEFORE execution so the user sees
            # the processing step immediately
            in_progress_step = {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "in_progress",
                "details": {
                    "name": fc_name,
                    "arguments": fc_arguments,
                    "tool_label": fc_tool_label,
                    "approval_source": APPROVAL_SOURCE_MANUAL,
                },
            }
            yield {
                "text": "",
                "processing_steps": tool_result_steps + [in_progress_step],
                "is_reasoning": False,
            }

            if approved:
                result = await execute_tool_call(
                    tool_name=fc_name,
                    arguments=fc_arguments,
                    user=chat.user,
                    chat=chat,
                )
            else:
                if max_iterations_reached:
                    result = {
                        "success": False,
                        "error": (
                            "The user denied this tool call because the "
                            "iteration limit was reached. Do not call any "
                            "more tools during this response. Answer using "
                            "the information you already have, or tell the "
                            "user what additional steps would be needed."
                        ),
                    }
                else:
                    result = {"success": False, "error": "User denied approval."}

            output = (
                result.get("result")
                if result.get("success")
                else {"error": result.get("error")}
            )
            output_item = build_function_call_output(fc_call_id, output)
            all_outputs.append(output_item)

            tool_result_steps.append(
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "completed",
                    "details": {
                        "name": fc_name,
                        "call_id": fc_call_id,
                        "arguments": fc_arguments,
                        "approved": approved,
                        "output": output,
                        "tool_label": fc_tool_label,
                        "approval_source": APPROVAL_SOURCE_MANUAL,
                    },
                }
            )

            estimated_usage = None
            if approved:
                paused_usage = (message.details or {}).get("usage") or {}
                estimated_usage = estimate_tool_continuation_usage(
                    paused_usage,
                    [
                        _sanitize_function_call_output_for_storage(item)
                        for item in all_outputs
                    ],
                )

            yield {
                "text": "",
                "processing_steps": tool_result_steps,
                "is_reasoning": False,
                "usage": estimated_usage,
            }

        logger.info(
            "Sending function_call_outputs to continue response",
            total_outputs=len(all_outputs),
            output_call_ids=[item.get("call_id") for item in all_outputs],
            previous_response_id=client.previous_response_id,
        )

        manual_continuation_input_items = all_outputs
        manual_initial_processing_steps = []

        if approved:
            paused_usage = (message.details or {}).get("usage") or {}
            sanitized_for_estimation = [
                _sanitize_function_call_output_for_storage(item) for item in all_outputs
            ]
            should_compact, proactive_metrics = (
                should_proactively_compact_tool_continuation(
                    context_management_mode=get_context_management_mode(client),
                    usage=paused_usage,
                    tool_output_items=sanitized_for_estimation,
                    model_id=model_id,
                )
            )
            if should_compact:
                compact_input = approval_full_input_items + all_outputs
                logger.info(
                    "Proactively compacting manual approval continuation",
                    message_id=message.id,
                    output_items_count=len(all_outputs),
                    compact_input_count=len(compact_input),
                    **proactive_metrics,
                )
                compacted_items, compaction_usage = await client.compact_conversation(
                    compact_input,
                    approval_instructions,
                )
                await sync_to_async(create_compaction_costs)(
                    compaction_usage,
                    model_id,
                )
                manual_continuation_input_items = compacted_items
                manual_initial_processing_steps = [COMPACTION_PROCESSING_STEP]

        if not manual_continuation_input_items and not client.previous_response_id:
            logger.warning(
                "Approval resume had no continuation outputs and no previous_response_id; rebuilding from full conversation",
                message_id=message.id,
                approved=approved,
                total_function_calls=len(all_function_calls),
                pre_executed_count=len(pre_executed_outputs),
            )
            manual_continuation_input_items = approval_full_input_items

        # Only attach outputs for the approval-required calls to response_output.
        # Pre-executed outputs were already captured before the approval pause.
        # Sanitize to strip bulky inline vision payloads from stored history.
        manual_output_items = [
            _sanitize_function_call_output_for_storage(item)
            for item in all_outputs
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") not in pre_executed_call_ids
            )
        ]

        async for chunk in stream_chat_for_htmx(
            client,
            input_items=manual_continuation_input_items,
            instructions=approval_instructions,
            full_input_items=approval_full_input_items,
            initial_processing_steps=manual_initial_processing_steps,
        ):
            chunk_steps = chunk.get("processing_steps", [])
            chunk["processing_steps"] = tool_result_steps + chunk_steps

            if manual_output_items:
                existing_items = chunk.get("output_items") or []
                chunk["output_items"] = manual_output_items + existing_items
            yield chunk

    # Create cost callback that combines previous cost with new continuation cost
    def cost_callback():
        return _create_costs_once_per_response(
            message.id,
            client,
            model_id,
            reuse_container=True,  # Container is reused via previous_response_id
        )

    # Define a replacer that preserves previous content and prepends it
    async def combined_generator():
        initial_text = message.text or ""
        initial_items = message.response_output or []

        updated_previous_steps = []
        for step in previous_raw_steps:
            if step.get("status") == "waiting_approval":
                continue
            else:
                updated_previous_steps.append(step)

        async for chunk in inner_gen():
            # The API returns accumulated text, so we prepend initial_text
            # chunk["text"] is already the full text from the new stream
            chunk["text"] = initial_text + chunk.get("text", "")

            # Prepend previous raw processing steps to the new raw ones
            # This ensures format_processing_steps() gets consistent raw format
            new_raw_steps = chunk.get("processing_steps", [])
            chunk["processing_steps"] = updated_previous_steps + new_raw_steps

            # Merge output items if present (completion)
            new_items = chunk.get("output_items", [])
            if new_items:
                chunk["output_items"] = initial_items + new_items

            yield chunk

    return StreamingHttpResponse(
        htmx_stream(
            chat,
            message.id,
            response_replacer=combined_generator(),
            cost_callback=cost_callback,
        ),
        content_type="text/event-stream",
    )
