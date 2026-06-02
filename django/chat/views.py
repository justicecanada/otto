import json
import re
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import OttoStatus
from otto.priorities import LOW
from otto.utils.common import check_url_allowed, generate_mailto
from otto.utils.decorators import (
    budget_required,
    otto_user_required,
    permission_required,
)
from otto.views import feedback_message

# ruff: noqa # Do not remove - used in urls.py
from chat._views.pin_chat import (
    pin_chat,
    unpin_chat,
)
from chat.forms import ChatOptionsForm, ChatRenameForm, PresetForm, UploadForm
from chat.models import (
    AnswerSource,
    Chat,
    ChatFile,
    ChatOptions,
    Message,
    Preset,
    create_chat_data_source,
)
from chat.utils import (
    annotate_pending_titles,
    bad_url,
    change_mode_to_chat_qa,
    create_library_shared_notification,
    create_preset_shared_notification,
    copy_options,
    enqueue_chat_title_generation,
    fix_source_links,
    generate_prompt,
    get_chat_history_sections,
    get_model_name,
    highlight_claims,
    is_placeholder_chat_title,
    label_section_index,
    options_match,
    update_qa_library_for_chat_uploads,
    wrap_llm_response,
)
from librarian.forms import LibraryUsersForm
from librarian.models import Library

app_name = "chat"
logger = get_logger(__name__)
User = get_user_model()


new_chat_with_ai = lambda request: new_chat(request, mode="chat")
new_translate = lambda request: new_chat(request, mode="translate")
new_summarize = lambda request: new_chat(request, mode="summarize")
new_document_qa = lambda request: new_chat(request, mode="document_qa")
new_qa = lambda request: new_chat(request, mode="qa")


DEBUG_STREAM_SCENARIOS = {
    "long_markdown_table": {
        "label": _("Debug long markdown table"),
        "message": _(
            "[DEBUG] Long markdown table stream for frontend rendering stress testing"
        ),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_markdown_table": "1",
            "simulate_chunk_delay": "0.03",
            "simulate_token_chunk_size": "24",
            "simulate_table_rows": "240",
            "simulate_table_columns": "6",
            "simulate_table_cell_length": "48",
        },
    },
    "legacy_silent_25": {
        "label": _("Debug legacy silent gap (25s, no keepalive)"),
        "message": _(
            "[DEBUG] Legacy silent-gap test with a 25-second pause and no keepalive comments"
        ),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_chunk_delay": "25",
            "simulate_disable_keepalive": "1",
        },
    },
    "legacy_silent_90": {
        "label": _("Debug legacy silent gap (90s, no keepalive)"),
        "message": _(
            "[DEBUG] Legacy silent-gap test with a 90-second pause and no keepalive comments"
        ),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_chunk_delay": "90",
            "simulate_disable_keepalive": "1",
        },
    },
    "keepalive_12": {
        "label": _("Debug keepalive test (12s gap)"),
        "message": _("[DEBUG] Keepalive test with a 12-second silent gap"),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_chunk_delay": "12",
        },
    },
    "keepalive_25": {
        "label": _("Debug keepalive test (25s gap, 2 chunks)"),
        "message": _(
            "[DEBUG] Keepalive stress test with a 25-second silent gap and 2 chunks"
        ),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_chunk_delay": "25",
            "simulate_chunk_count": "2",
        },
    },
    "keepalive_90": {
        "label": _("Debug keepalive test (90s gap)"),
        "message": _("[DEBUG] Keepalive test with a 90-second silent gap"),
        "query_params": {
            "simulate_slow_stream": "1",
            "simulate_chunk_delay": "90",
        },
    },
}


@otto_user_required
def new_chat(request, mode=None):
    """
    Create a new chat and render it directly (avoiding redirect)
    """

    empty_chat = Chat.objects.create(user=request.user, mode=mode)

    logger.info("New chat created.", chat_id=empty_chat.id, mode=mode)
    new_url = reverse("chat:chat", args=[empty_chat.id])
    redirect_url = new_url

    start_tour = request.GET.get("start_tour") == "true"
    if start_tour:
        # Reset settings to Otto default
        if get_language() == "fr":
            preset = Preset.objects.get(french_default=True)
        else:
            preset = Preset.objects.get(english_default=True)
        empty_chat.loaded_preset = preset
        empty_chat.save()
        # Update the chat options with the preset options
        copy_options(preset.options, empty_chat.options)
        redirect_url += "?start_tour=true"
    elif request.GET.get("open_library"):
        open_library = request.GET.get("open_library")
        open_data_source = request.GET.get("open_data_source")
        if open_data_source:
            redirect_url += (
                f"?open_library={open_library}&open_data_source={open_data_source}"
            )
        else:
            redirect_url += f"?open_library={open_library}"
    elif request.GET.get("open_preset"):
        open_preset = request.GET.get("open_preset")
        redirect_url += f"?open_preset={open_preset}"

    # Call chat view directly instead of redirecting
    # Store URL to push in request so chat view can add it to context
    request._push_url = redirect_url
    return chat(request, empty_chat.id)


@permission_required("chat.access_preset", objectgetter(Preset, "preset_id"))
def new_chat_from_preset(request, preset_id):
    """Create a new chat pre-loaded from a Preset.

    Intended for shareable org links like:
    /chat/preset/1234

    This flow intentionally skips the AI Assistant tour gating for the initial
    request (it does not mark the tour as completed).
    """

    preset = get_object_or_404(
        Preset.objects.select_related("options", "options__qa_library"), id=preset_id
    )

    chat_obj = Chat.objects.create(user=request.user, mode=preset.options.mode)

    # Ensure ChatOptions/DataSource exist (mirrors the insurance code in chat())
    try:
        chat_obj.options
        ChatOptions.objects.check_and_update_models(chat_obj.options)
    except Chat.options.RelatedObjectDoesNotExist:
        existing = ChatOptions.objects.filter(chat=chat_obj).first()
        if existing:
            chat_obj.options = existing
        else:
            chat_obj.options = ChatOptions.objects.from_defaults(chat=chat_obj)
            chat_obj.save()
    try:
        chat_obj.data_source
    except:
        chat_obj.data_source = create_chat_data_source(request.user, chat=chat_obj)
        chat_obj.save()

    chat_obj.loaded_preset = preset
    chat_obj.save(update_fields=["loaded_preset"])

    # Copy preset options onto the new chat. This will also reset any
    # inaccessible Q&A library to the user's personal library.
    copy_options(preset.options, chat_obj.options, user=request.user, chat=chat_obj)

    new_url = reverse("chat:chat", args=[chat_obj.id])
    new_url += f"?skip_tour=true&from_preset={preset.id}"

    # Call chat view directly instead of redirecting
    # Store URL to push in request so chat view can add it to context
    request._push_url = new_url
    return chat(request, chat_obj.id)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def delete_chat(request, chat_id, current_chat=None):
    # HTMX delete route
    # Delete chat
    chat = Chat.objects.get(id=chat_id)

    chat.delete()
    logger.info("Chat was deleted.", chat_id=chat_id)

    # Is this the currently open chat? If so, redirect away
    if current_chat == "True":
        response = HttpResponse()
        response["HX-Redirect"] = reverse("chat:new_chat")
        return response
    return HttpResponse(status=200)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def download_glossary(request, chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    glossary_saved_file = getattr(chat.options, "translate_glossary", None)
    if not glossary_saved_file:
        return HttpResponse(status=404)

    # Use the stored filename if available, otherwise fall back to the file path basename
    filename = (
        chat.options.translate_glossary_filename
        or glossary_saved_file.file.name.split("/")[-1]
    )

    response = FileResponse(
        glossary_saved_file.file.open("rb"),
        as_attachment=True,
        filename=filename,
    )
    return response


def delete_all_chats(request):
    # NOTE: Cannot use bulk QuerySet.delete() here because Chat.delete() has a
    # custom method that calls DataSource.delete(), which in turn stops celery
    # tasks and removes documents from the vector store. QuerySet.delete()
    # would bypass both the custom Chat.delete() and DataSource.delete() methods,
    # leaving orphaned vector store data and zombie celery tasks.
    for chat in Chat.objects.filter(user=request.user):
        chat.delete()

    logger.info("all chats deleted")

    response = HttpResponse()
    response["HX-Redirect"] = reverse("chat:new_chat")
    return response


@otto_user_required
def chat(request, chat_id):
    """
    Get the chat based on the provided chat ID.
    Returns read-only view if user does not have access.
    """
    logger.info("Chat session retrieved.", chat_id=chat_id)
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="chat", user_id=user_id, cost_group_id=cost_group_id)

    # Prefetch user's groups once to avoid repeated permission check queries
    # This prevents multiple queries to check is_admin, is_operations_admin, etc.
    if hasattr(request.user, "groups"):
        # Force evaluation of groups queryset to cache it
        list(request.user.groups.all())

    chat = (
        Chat.objects.filter(id=chat_id)
        .select_related(
            "user",
            "loaded_preset",
            "loaded_preset__options",
            "options",
            "options__qa_library",
        )
        .prefetch_related(
            "options__qa_data_sources",
            "options__qa_documents",
            "loaded_preset__options__qa_data_sources",
            "loaded_preset__options__qa_documents",
        )
        .first()
    )

    if not chat:
        return new_chat(request)
    Chat.objects.filter(id=chat_id).update(accessed_at=timezone.now())

    # Get chat messages ready
    # Prefetch parent messages with their file counts annotated
    parent_prefetch = Prefetch(
        "parent",
        queryset=Message.objects.annotate(num_files_count=Count("files")),
    )

    chat_messages = list(
        Message.objects.filter(chat=chat)
        .order_by("date_created")
        .annotate(num_files_count=Count("files"))  # Annotate file count to avoid N+1
        .prefetch_related(
            parent_prefetch,  # Prefetch parent with file count
            "answersource_set",
            "files__saved_file",  # Prefetch files and their SavedFile references
        )
    )
    # Highlight a specific matched message if requested
    highlight_message_id = request.GET.get("highlight_message") or None
    anchor_message_id = request.GET.get("anchor_message") or None
    if not highlight_message_id and anchor_message_id:
        highlight_message_id = anchor_message_id

    for message in chat_messages:
        if message.is_bot:
            message.json = json.dumps(message.text)
        else:
            message.text = message.text.strip()
        # Mark the target message for the template/JS to act on
        if highlight_message_id and str(message.id) == str(highlight_message_id):
            message.details = {**(message.details or {}), "flash_highlight": True}

    if not request.user.has_perm("chat.access_chat", chat):
        context = {
            "chat": chat,
            "chat_messages": chat_messages,
            "hide_breadcrumbs": True,
            "read_only": True,
            "chat_author": chat.user,
        }
        return render(request, "chat/chat_readonly.html", context=context)

    # Insurance code to ensure we have ChatOptions, DataSource, and Personal Library
    try:
        chat.options
        # Check for deprecated models and update them
        ChatOptions.objects.check_and_update_models(chat.options)
    except Chat.options.RelatedObjectDoesNotExist:
        # Options row may exist in DB but not be loaded (e.g. select_related miss).
        # Try to fetch it before creating, to avoid unique constraint violations.
        existing = ChatOptions.objects.filter(chat=chat).first()
        if existing:
            chat.options = existing
            ChatOptions.objects.check_and_update_models(chat.options)
        else:
            chat.options = ChatOptions.objects.from_defaults(chat=chat)
            chat.save()
    try:
        chat.data_source
    except:
        chat.data_source = create_chat_data_source(request.user, chat=chat)
        chat.save()
    # END INSURANCE CODE

    mode = chat.options.mode

    # Get sidebar chat history list.
    # Don't show empty chats - these will be deleted automatically later.
    # The current chat is always shown, even if it's empty.
    user_chats = (
        Chat.objects.filter(user=request.user)
        .annotate(message_count=Count("messages"))
        .filter(Q(message_count__gt=0) | Q(pk=chat.id))
        .order_by("-last_modification_date")
    )

    # Convert to list once so downstream iterations don't trigger repeated queries
    user_chats_list = list(user_chats)

    # Title chats in sidebar if necessary & set default labels
    for user_chat in user_chats_list:
        user_chat.current_chat = user_chat.id == chat.id
    annotate_pending_titles(user_chats_list, language=request.LANGUAGE_CODE)

    # If arriving with ?search=, pre-render a filtered sidebar to avoid flicker
    search = (request.GET.get("search", "") or "").strip()
    if search:
        base_qs = Chat.objects.filter(user=request.user, messages__isnull=False)
        filtered = (
            base_qs.filter(
                Q(title__icontains=search) | Q(messages__text__icontains=search)
            )
            .distinct()
            .select_related("options")
            .annotate(
                message_count=Count("messages")
            )  # Annotate to avoid N+1 in templates
            .order_by("-last_modification_date")
        )
        # Prefetch matched messages to avoid N+1 query
        filtered = filtered.prefetch_related(
            Prefetch(
                "messages",
                queryset=Message.objects.filter(text__icontains=search).order_by(
                    "date_created", "id"
                ),
                to_attr="matched_messages",
            )
        )
        # Attach deterministic matched message snippet and id
        for c in filtered:
            # Use prefetched messages instead of separate query
            matched = c.matched_messages[0] if c.matched_messages else None
            if matched:
                text = matched.text or ""
                lower_text = text.lower()
                idx = lower_text.find(search.lower()) if search else -1
                if idx != -1:
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(search) + 40)
                    snippet = text[start:end].strip()
                    if start > 0:
                        snippet = "…" + snippet
                    if end < len(text):
                        snippet = snippet + "…"
                else:
                    snippet = (text[:80] + ("…" if len(text) > 80 else "")).strip()
                c.snippet = snippet
                c.matched_message_id = matched.id
        annotate_pending_titles(list(filtered), language=request.LANGUAGE_CODE)
        sidebar_sections = get_chat_history_sections(filtered)
    else:
        sidebar_sections = get_chat_history_sections(user_chats_list)

    awaiting_response = request.GET.get("awaiting_response") == "True"

    # When a chat is created from outside Otto, we want to emulate the behaviour
    # of creating a new message - which returns an "awaiting_response" bot message
    last_message = chat_messages[-1] if chat_messages else None
    if (
        awaiting_response
        and last_message
        and last_message.is_bot
        and not last_message.text
    ):
        response_init_message = {
            "is_bot": True,
            "awaiting_response": True,
            "id": last_message.id,
            "date_created": last_message.date_created + timezone.timedelta(seconds=1),
        }
        chat_messages = [chat_messages[0], response_init_message]

    if not chat.options.qa_library or not request.user.has_perm(
        "librarian.view_library", chat.options.qa_library
    ):
        # The copy_options function fixes these issues
        copy_options(chat.options, chat.options)

    form = ChatOptionsForm(instance=chat.options, user=request.user)

    # Calculate if current options differ from loaded preset
    preset_dirty = False
    if chat.loaded_preset:
        preset_dirty = not options_match(chat.options, chat.loaded_preset.options)

    # Determine upload limits (in bytes) for the current user from OttoStatus singleton
    otto_status = OttoStatus.objects.singleton()
    chat_max = otto_status.chat_max_bytes_for(request.user)
    librarian_max = otto_status.librarian_max_bytes_for(request.user)
    azure_translation_max = settings.AZURE_TRANSLATION_DOCUMENT_SIZE_LIMIT

    context = {
        "active_app": "chat",
        "chat": chat,
        "options_form": form,
        "preset_dirty": preset_dirty,
        "prompt": chat.options.prompt,
        "chat_messages": chat_messages,
        "hide_breadcrumbs": True,
        "user_chats": user_chats_list,
        "mode": mode,
        "chat_history_sections": sidebar_sections,
        "has_tour": True,
        "tour_name": _("AI Assistant"),
        "force_tour": (not request.user.ai_assistant_tour_completed)
        and not (request.GET.get("skip_tour") == "true"),
        "tour_skippable": (
            request.user.is_admin or request.user.ai_assistant_tour_completed
        )
        or (request.GET.get("skip_tour") == "true"),
        "start_tour": request.GET.get("start_tour") == "true",
        "upload_form": UploadForm(prefix="chat"),
        "highlight_message_id": highlight_message_id,
        "search": search,
        # Values (in bytes) or None for no limit. Templates will convert None to JS Number.MAX_VALUE
        "chat_max_upload_size": chat_max,
        "librarian_max_upload_size": librarian_max,
        "azure_translation_max_upload_size": azure_translation_max,
        "show_debug_tools": settings.CHAT_DEBUG_STREAM_TESTS_ENABLED
        and request.user.is_admin,
        "debug_stream_scenarios": DEBUG_STREAM_SCENARIOS,
    }

    # If arriving from a preset link, pass the preset info for the welcome message
    from_preset_id = request.GET.get("from_preset")
    if from_preset_id:
        try:
            from_preset = Preset.objects.get(id=from_preset_id)
            context["from_preset"] = from_preset
        except Preset.DoesNotExist:
            pass

    # If called from new_chat views, push URL to browser without redirect
    if hasattr(request, "_push_url"):
        context["push_url"] = request._push_url

    return render(request, "chat/chat.html", context=context)


@require_POST
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def debug_stream_test(request, chat_id):
    """Create a debug streaming message for admin-only browser verification."""

    if not settings.CHAT_DEBUG_STREAM_TESTS_ENABLED:
        raise Http404()
    if not request.user.is_admin:
        return HttpResponse(status=403)

    scenario = request.POST.get("scenario")
    config = DEBUG_STREAM_SCENARIOS.get(scenario)
    if not config:
        return HttpResponse(status=400)

    chat = Chat.objects.get(id=chat_id)
    logger.info(
        "Creating debug stream test message.",
        chat_id=chat_id,
        scenario=scenario,
        user_id=request.user.id,
    )

    user_message = Message.objects.create(
        chat=chat,
        text=config["message"],
        is_bot=False,
        mode="chat",
    )
    user_message.is_new_user_message = True

    response_message = Message.objects.create(
        chat=chat,
        is_bot=True,
        mode="chat",
        parent=user_message,
        text="",
        bot_name=str(_("Debug")),
    )

    response_context = {
        "is_bot": True,
        "awaiting_response": True,
        "id": response_message.id,
        "date_created": response_message.date_created + timezone.timedelta(seconds=1),
        "bot_name": str(_("Debug")),
        "stream_query": urlencode(config["query_params"]),
    }

    context = {
        "chat_messages": [user_message, response_context],
        "mode": "chat",
    }

    response = HttpResponse()
    response.write(
        render_to_string(
            "chat/components/chat_messages.html",
            context,
            request=request,
        )
    )
    response.write("<script>scrollToBottom(false, true);</script>")
    return response


@require_POST
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@budget_required
def chat_message(request, chat_id):
    """
    Post a user message to the chat and initiate a streaming response
    """
    # The user must match the chat
    chat = Chat.objects.get(id=chat_id)
    # Create the user's message in database
    user_message_text = request.POST.get("user-message", "").strip()
    mode = chat.options.mode

    logger.debug(
        "User message received.",
        chat_id=chat_id,
        user_message=f"{user_message_text[:100]}{'...' if len(user_message_text) > 100 else ''}",
        mode=mode,
    )

    # Stop the previous bot response message, if necessary
    last_bot_message = (
        Message.objects.filter(chat=chat, is_bot=True)
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )
    if last_bot_message:
        cache.set(f"stop_response_{last_bot_message}", True, timeout=60)

    # Quick-add URL to library (Change mode to QA and data source to current Chat if so)
    entered_url = False
    allowed_url = False
    url_validator = URLValidator()
    try:
        url_validator(user_message_text)
        entered_url = True
        allowed_url = check_url_allowed(user_message_text)
    except ValidationError:
        pass

    user_message = Message.objects.create(
        chat=chat, text=user_message_text, is_bot=False, mode=mode
    )
    # Don't enqueue title task here - inline titling at end of streaming handles
    # current chat. Sidebar rendering will enqueue if user navigates away early.
    user_message.is_new_user_message = True

    if entered_url and not allowed_url:
        # Just respond with the error message.
        response_message = Message.objects.create(
            chat=chat, is_bot=True, mode=mode, parent=user_message, text=bad_url()
        )
        response_message.json = json.dumps(response_message.text)
    else:
        bot_name = get_model_name(chat.options)
        response_message = Message.objects.create(
            chat=chat,
            is_bot=True,
            mode=mode,
            parent=user_message,
            text="",
            bot_name=bot_name,
        )
        # This tells the frontend to display the 3 dots and initiate the streaming response
        response_message = {
            "is_bot": True,
            "awaiting_response": True,
            "id": response_message.id,
            "date_created": response_message.date_created
            + timezone.timedelta(seconds=1),
            "bot_name": bot_name,
        }

    context = {
        "chat_messages": [
            user_message,
            response_message,
        ],
        "mode": mode,
    }
    response = HttpResponse()
    response.write(render_to_string("chat/components/chat_messages.html", context))
    if entered_url and allowed_url and (mode == "chat" or mode == "qa"):
        response.write(change_mode_to_chat_qa(chat))
    return response


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def delete_message(request, message_id):
    """
    Delete a message from the chat
    """
    message = Message.objects.get(id=message_id)
    chat = message.chat
    logger.info("Deleting chat message.", message_id=message_id, chat_id=chat.id)
    message.delete()
    return HttpResponse()


def cost_warning(request, message_id):
    """
    Continue a message after user approves
    """
    message = Message.objects.get(id=message_id, is_bot=True)
    cost_approved = request.GET.get("cost_approved", "false") == "true"
    if cost_approved:
        # Clear the persisted warning text before restarting the SSE response.
        # Otherwise, if the long-running approved stream disconnects before the
        # final response is saved, SSE recovery can mistake the old warning text
        # for a completed answer and render it again without approval buttons.
        message.text = ""
        message.save(update_fields=["text"])
        message.awaiting_response = True
    else:
        message.text = _("Request cancelled.")
        message.awaiting_response = False
        message.json = json.dumps(message.text)
        message.save()

    context = {
        "message": message,
        "mode": message.mode,
        "cost_approved": cost_approved,
    }
    html = render_to_string("chat/components/chat_message.html", context)
    return HttpResponse(html)


@require_POST
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def save_upload(request, chat_id):
    """
    Handles the form submission after JS upload
    """
    chat = Chat.objects.get(id=chat_id)
    form = UploadForm(request.POST, request.FILES, prefix="chat")
    if not form.is_valid():
        logger.error("File upload error.", errors=form.errors)
        messages.error(request, _("There was an error uploading your files."))
        response = HttpResponse()
        response.write(
            render_to_string(
                "chat/components/chat_upload_message.html",
                context={
                    "swap_upload_message": True,
                    "upload_form": UploadForm(prefix="chat"),
                    "chat": chat,
                    "csrf_token": request.POST.get("csrfmiddlewaretoken"),
                },
                request=request,
            )
        )
        return response

    # If in Chat mode, switch to Q&A mode for file uploads
    if chat.options.mode == "chat":
        chat.options.mode = "qa"
        chat.save()
    logger.info("File upload initiated.", chat_id=chat_id, mode=chat.options.mode)
    user_message = Message.objects.create(
        chat=chat, text="", is_bot=False, mode=chat.options.mode
    )
    # Don't enqueue title task here - inline titling handles current chat
    saved_files = form.save()
    for saved_file in saved_files:
        ChatFile.objects.create(
            message_id=user_message.id,
            filename=saved_file["filename"],
            saved_file=saved_file["saved_file"],
        )

    # Create bot response and trigger processing for Q&A/Summarize/Translate
    response = HttpResponse()
    bot_name = get_model_name(chat.options) if chat.options.mode != "qa" else ""
    response_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        mode=chat.options.mode,
        parent=user_message,
        bot_name=bot_name,
    )

    # Update Q&A library settings to "Chat Uploads" and swap accordion
    logger.debug("File upload - updating Q&A settings accordion")
    response.write(update_qa_library_for_chat_uploads(chat))
    response_init_message = {
        "is_bot": True,
        "awaiting_response": True,
        "id": response_message.id,
        "date_created": user_message.date_created + timezone.timedelta(seconds=1),
        "bot_name": bot_name,
    }
    context = {
        "chat_messages": [
            user_message,
            response_init_message,
        ],
        "mode": chat.options.mode,
        # You can't really stop file translations or QA uploads, so don't show the button
        "hide_stop_button": chat.options.mode in ["translate", "qa"],
    }
    response.write(
        render_to_string(
            "chat/components/chat_messages.html", context=context, request=request
        )
    )
    response.write(
        render_to_string(
            "chat/components/chat_upload_message.html",
            context={
                "swap_upload_message": True,
                "upload_form": UploadForm(prefix="chat"),
                "chat": chat,
                "csrf_token": request.POST.get("csrfmiddlewaretoken"),
            },
            request=request,
        )
    )
    response.write("<script>scrollToBottom(false, true);</script>")
    return response


@permission_required("chat.access_file", objectgetter(ChatFile, "file_id"))
def download_file(request, file_id):
    logger.info("Downloading chat file.", file_id=file_id)
    file_obj = get_object_or_404(ChatFile, pk=file_id)
    file = file_obj.saved_file.file
    # Download the file, don't display it
    return FileResponse(
        file,
        as_attachment=True,
        filename=file_obj.filename,
        content_type=file_obj.saved_file.content_type,
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def thumbs_feedback(request: HttpRequest, message_id: int, feedback: str):
    try:
        feedback = int(feedback)  # cast to integer
        logger.info(
            "Providing chat feedback.",
            message_id=message_id,
            feedback=feedback,
        )
        message = Message.objects.get(id=message_id)
        message.feedback = message.get_toggled_feedback(feedback)
        message.save(update_fields=["feedback"])
    except Exception as e:
        logger.exception(
            f"An error occurred while providing thumbs up/down feedback.:{e}",
            message_id=message_id,
        )

    if feedback == -1:
        return feedback_message(request, message_id)

    return HttpResponse()


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def rerun_prompt(request, message_id):
    """
    Rerun a user prompt by creating a new user message with the same text
    and initiating a new bot response.
    """
    try:
        original_message = Message.objects.get(id=message_id, is_bot=False)

        chat = original_message.chat
        mode = chat.options.mode
        logger.info(
            "Rerunning chat prompt.",
            message_id=message_id,
            chat_id=chat.id,
            mode=mode,
        )

        original_message_id = original_message.id
        later_messages = list(
            Message.objects.filter(chat=chat, id__gt=original_message_id)
        )
        removed_message_ids = []
        for message in later_messages:
            if message.is_bot:
                cache.set(f"stop_response_{message.id}", True, timeout=60)
            removed_message_ids.append(message.id)
        if removed_message_ids:
            Message.objects.filter(id__in=removed_message_ids).delete()

        now = timezone.now()
        original_message.date_created = now
        original_message.save(update_fields=["date_created"])
        # Don't enqueue title task here - inline titling handles current chat
        chat.last_modification_date = now
        chat.save(update_fields=["last_modification_date"])

        bot_name = get_model_name(chat.options) if not chat.options.mode == "qa" else ""
        response_message = Message.objects.create(
            chat=chat,
            text="",
            is_bot=True,
            mode=mode,
            parent=original_message,
            bot_name=bot_name,
        )

        response_context = {
            "is_bot": True,
            "awaiting_response": True,
            "id": response_message.id,
            "date_created": response_message.date_created
            + timezone.timedelta(seconds=1),
            "bot_name": bot_name,
        }

        context = {
            "chat_messages": [original_message, response_context],
            "mode": mode,
        }

        html = render_to_string(
            "chat/components/chat_messages.html", context, request=request
        )

        if removed_message_ids:
            html += "".join(
                f"<div id='message_{message_id}' hx-swap-oob='delete'></div>"
                for message_id in removed_message_ids
            )

        return HttpResponse(html)

    except Exception:
        logger.exception(
            "An error occurred while rerunning chat prompt.", message_id=message_id
        )
        return HttpResponse(status=500)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_options(request, chat_id, action=None, preset_id=None):
    from django.contrib import messages

    """
    Save and load chat options.
    """

    chat = Chat.objects.select_related(
        "options", "loaded_preset", "loaded_preset__options"
    ).get(id=chat_id)
    # if we are loading a preset, check if the user has access to it
    if preset_id and not request.user.has_perm(
        "chat.access_preset", Preset.objects.get(id=preset_id)
    ):
        return HttpResponse(status=403)

    if action == "load_preset":
        logger.info(
            "Loading chat options from a preset.",
            chat_id=chat_id,
            preset=preset_id,
        )

        if not preset_id:
            preset = Preset.objects.get_global_default()
        else:
            preset = Preset.objects.get(id=int(preset_id))
        if not preset:
            return HttpResponse(status=500)

        chat.loaded_preset = preset
        chat.save()
        messages.success(
            request,
            _("Preset loaded successfully."),
        )

        # Update the chat options with the preset options
        copy_options(preset.options, chat.options)

        chat_options_form = ChatOptionsForm(instance=chat.options, user=request.user)

        return render(
            request,
            "chat/components/chat_options_accordion.html",
            {
                "options_form": chat_options_form,
                "preset_loaded": "true",
                "preset_dirty": False,
                "prompt": preset.options.prompt,
                "chat": chat,
            },
        )
    elif action == "create_preset":
        if request.method == "POST":
            form = PresetForm(data=request.POST, user=request.user)

            if form.is_valid():
                is_new_preset = not preset_id
                if preset_id:
                    preset = get_object_or_404(Preset, id=preset_id)
                    replace_with_settings = request.POST.get(
                        "replace_with_settings", False
                    )
                else:
                    # Create a new Preset object
                    preset = Preset()
                    preset.options = ChatOptions.objects.create()
                    preset.owner = request.user
                    preset_id = preset.id
                    replace_with_settings = True

                # save the current chat settings
                if replace_with_settings:
                    # copy the options from the chat to the preset
                    copy_options(chat.options, preset.options)
                    preset.options.prompt = request.POST.get("prompt", "")
                    preset.options.save()

                english_title = form.cleaned_data["name_en"]
                french_title = form.cleaned_data["name_fr"]

                # Set the fields based on the selected tab
                preset.name_en = english_title
                preset.name_fr = french_title
                preset.description_en = form.cleaned_data["description_en"]
                preset.description_fr = form.cleaned_data["description_fr"]

                preset.sharing_option = form.cleaned_data.get("sharing_option", None)

                accessible_to_data = form.cleaned_data.get("accessible_to", {})
                editable_by_data = form.cleaned_data.get("editable_by", {})
                accessible_to_users = (
                    accessible_to_data.get("users", [])
                    if isinstance(accessible_to_data, dict)
                    else accessible_to_data
                )
                accessible_to_teams = (
                    accessible_to_data.get("teams", [])
                    if isinstance(accessible_to_data, dict)
                    else []
                )
                editable_by_users = (
                    editable_by_data.get("users", [])
                    if isinstance(editable_by_data, dict)
                    else editable_by_data
                )
                editable_by_teams = (
                    editable_by_data.get("teams", [])
                    if isinstance(editable_by_data, dict)
                    else []
                )

                previous_accessible_ids = (
                    set(preset.accessible_to.values_list("id", flat=True))
                    if preset_id
                    else set()
                )
                previous_editable_ids = (
                    set(preset.editable_by.values_list("id", flat=True))
                    if preset_id
                    else set()
                )

                preset.save()

                # clear the accessible_to field if the user changes the sharing option to private
                if preset.sharing_option == "private" and len(accessible_to_users) > 0:
                    accessible_to_users = []
                    accessible_to_teams = []

                preset.accessible_to.set(accessible_to_users)
                preset.editable_by.set(editable_by_users)
                preset.accessible_to_teams.set(accessible_to_teams)
                preset.editable_by_teams.set(editable_by_teams)

                # Creating a brand-new preset should set it as the current chat preset.
                # Editing an existing preset should not change which preset is loaded.
                if is_new_preset:
                    chat.loaded_preset = preset
                    chat.save(update_fields=["loaded_preset"])
                elif chat.loaded_preset_id == preset.id:
                    # Keep currently loaded preset metadata in sync for the response
                    # so the preset header reflects updates like a renamed title.
                    chat.loaded_preset = preset

                current_accessible_ids = set(
                    preset.accessible_to.values_list("id", flat=True)
                )
                current_editable_ids = set(
                    preset.editable_by.values_list("id", flat=True)
                )
                newly_shared_user_ids = (
                    current_accessible_ids - previous_accessible_ids
                ) | (current_editable_ids - previous_editable_ids)

                if newly_shared_user_ids:
                    shared_users = User.objects.filter(
                        id__in=newly_shared_user_ids
                    ).exclude(id=request.user.id)
                    for shared_user in shared_users:
                        create_preset_shared_notification(
                            shared_user,
                            preset,
                            request.user,
                            can_edit=shared_user.id in current_editable_ids,
                        )

                messages.success(
                    request,
                    _("Preset saved successfully."),
                )

                if request.POST.get("make_default", False) == "True":
                    request.user.default_preset = preset
                    request.user.save()

                # If this is a brand-new public preset, apply library access changes
                # then show the edit form so the user can see and copy the shareable link
                if is_new_preset and preset.sharing_option == "everyone":
                    # Apply library access logic (make library public if applicable)
                    library = preset.options.qa_library
                    if (
                        library
                        and not library.is_public
                        and request.user.has_perm(
                            "librarian.manage_library_users", library
                        )
                    ):
                        library.is_public = True
                        library.save()
                        messages.info(
                            request,
                            _(
                                "The attached Q&A library has been made publicly viewable."
                            ),
                        )

                    edit_form = PresetForm(instance=preset, user=request.user)
                    return render(
                        request,
                        "chat/modals/presets/presets_form.html",
                        {
                            "form": edit_form,
                            "preset_id": str(preset.id),
                            "chat_id": chat_id,
                            "can_delete": request.user.has_perm(
                                "chat.delete_preset", preset
                            ),
                            "is_public": True,
                            "is_global_default": preset.global_default,
                        },
                    )

                # Show the user any relevant messages about changes to library privacy
                return library_access(
                    request, preset, preset.options.qa_library, action, chat=chat
                )

        return HttpResponse(status=500)
    elif action == "update_preset":
        preset = get_object_or_404(Preset, id=preset_id)
        previous_accessible_ids = set(preset.accessible_to.values_list("id", flat=True))
        previous_editable_ids = set(preset.editable_by.values_list("id", flat=True))
        old_library = preset.options.qa_library
        copy_options(chat.options, preset.options)
        preset.options.prompt = request.POST.get("prompt", "")
        preset.options.save()
        messages.success(
            request,
            _("Preset updated successfully."),
        )

        current_accessible_ids = set(preset.accessible_to.values_list("id", flat=True))
        current_editable_ids = set(preset.editable_by.values_list("id", flat=True))
        newly_shared_user_ids = (current_accessible_ids - previous_accessible_ids) | (
            current_editable_ids - previous_editable_ids
        )

        if newly_shared_user_ids:
            shared_users = User.objects.filter(id__in=newly_shared_user_ids).exclude(
                id=request.user.id
            )
            for shared_user in shared_users:
                create_preset_shared_notification(
                    shared_user,
                    preset,
                    request.user,
                    can_edit=shared_user.id in current_editable_ids,
                )

        # Show the user any relevant messages about changes to library privacy
        return library_access(
            request,
            preset,
            preset.options.qa_library,
            action,
            (old_library if old_library != preset.options.qa_library else None),
            chat=chat,
        )
    elif action == "delete_preset":
        # Bulk-clear loaded_preset for all user's chats referencing this preset
        Chat.objects.filter(user=request.user, loaded_preset_id=int(preset_id)).update(
            loaded_preset=None
        )
        preset = get_object_or_404(Preset, id=preset_id)
        preset.delete()
        messages.success(
            request,
            _("Preset deleted successfully."),
        )
        return redirect("chat:get_presets", chat_id=chat_id)
    elif request.method == "POST":
        chat_options = chat.options
        post_data = request.POST.copy()

        # Ensure existing filename is preserved in POST data if it exists
        if (
            chat_options.translate_glossary_filename
            and "translate_glossary_filename" not in post_data
        ):
            post_data["translate_glossary_filename"] = (
                chat_options.translate_glossary_filename
            )

        # Handle file removal for translate_glossary
        glossary_removed = False
        if request.GET.get("remove_glossary") == "1":
            glossary_saved_file = chat_options.translate_glossary
            chat_options.translate_glossary = None
            chat_options.save(update_fields=["translate_glossary"])
            glossary_saved_file.safe_delete()
            glossary_removed = True

        # Process translate_glossary file BEFORE form validation
        glossary_error = None
        glossary_uploaded = False
        glossary_file = request.FILES.get("translate_glossary")
        if glossary_file:
            import csv
            from io import TextIOWrapper

            from librarian.models import SavedFile
            from librarian.utils.process_engine import generate_hash

            try:
                wrapper = TextIOWrapper(glossary_file, encoding="utf-8")
                reader = csv.reader(wrapper)
                for idx, row in enumerate(reader, 1):
                    if len(row) != 2 or not row[0].strip() or not row[1].strip():
                        glossary_error = _(
                            "Glossary file is invalid:\nEach row must consist of 'English term, French term' and no cell can be empty.\nError on line: "
                        ) + str(idx)
                        break
                wrapper.detach()

                # If validation passed, create/get SavedFile
                if not glossary_error:
                    file_hash = generate_hash(glossary_file)
                    saved_file = SavedFile.objects.filter(sha256_hash=file_hash).first()
                    if not saved_file:
                        saved_file = SavedFile.objects.create(
                            file=glossary_file,
                            sha256_hash=file_hash,
                            content_type=glossary_file.content_type or "text/csv",
                        )

                    # Set the SavedFile reference and filename on the instance BEFORE form validation
                    chat_options.translate_glossary = saved_file
                    chat_options.translate_glossary_filename = glossary_file.name
                    # Also add the filename to the POST data so the hidden field gets it
                    post_data["translate_glossary_filename"] = glossary_file.name
                    # Mark that we successfully uploaded a glossary
                    glossary_uploaded = True
                    # Remove the file from request.FILES so form doesn't try to process it
                    del request.FILES["translate_glossary"]

            except Exception as e:
                glossary_error = _(f"Glossary file could not be read: {str(e)}")
                print(e)

        if glossary_error:
            messages.error(request, glossary_error)
            # Remove the invalid file from request.FILES so it is not saved
            if "translate_glossary" in request.FILES:
                del request.FILES["translate_glossary"]
            # Remove the file from the model instance as well
            if getattr(chat_options, "translate_glossary", None):
                chat_options.translate_glossary = None
                chat_options.save(update_fields=["translate_glossary"])
            # Create a fresh form so the upload field is empty
            fresh_form = ChatOptionsForm(instance=chat_options, user=request.user)
            return render(
                request,
                "chat/components/glossary_upload_fragment.html",
                {"options_form": fresh_form, "chat": chat, "swap": True},
            )

        # Now validate the form (without the file field)
        chat_options_form = ChatOptionsForm(
            post_data, request.FILES, instance=chat_options, user=request.user
        )
        # Check for errors and print them to console
        if not chat_options_form.is_valid():
            logger.error(chat_options_form.errors)
            return HttpResponse(status=500)
        chat_options_form.save()

        # HTMX: If glossary was uploaded or removed, return only the fragment
        if glossary_uploaded or glossary_removed:
            return render(
                request,
                "chat/components/glossary_upload_fragment.html",
                {"options_form": chat_options_form, "chat": chat, "swap": True},
            )

        # Calculate if options now differ from loaded preset and return updated header
        preset_dirty = False
        if chat.loaded_preset:
            # Refresh from database to get latest saved values
            chat.options.refresh_from_db()
            chat.loaded_preset.options.refresh_from_db()
            preset_dirty = not options_match(chat.options, chat.loaded_preset.options)
            return render(
                request,
                "chat/components/preset_header.html",
                {"chat": chat, "preset_dirty": preset_dirty, "swap": True},
            )

        # Return a simple success response if no preset loaded
        return HttpResponse(status=200)

    else:
        return HttpResponse(status=500)


def library_access(request, preset, library, action, old_library=None, chat=None):
    # Helper function (never called directly by user) to change librarian permissions
    # when a user changes an associated preset, then display relevant messages

    message = ""

    # If there's a library attached to the preset, and it's not already public,
    # and the user has the necessary permissions, change its privacy
    if (
        library
        and not library.is_public
        and request.user.has_perm("librarian.manage_library_users", library)
    ):
        if preset.sharing_option == "everyone":
            library.is_public = True
            library.save()
            message = f"""
                {_("This preset is accessible to all users.")}
                {_("By saving it, you have made the attached Q&A library")} ({library}) {_("publicly viewable.")}
                """
        elif preset.sharing_option == "others":
            # Only make note of people who've had the preset shared with them,
            # but who *don't* already have access to the attached library
            # (in this case, generally people who were *just* added to the preset)
            shared_users = User.objects.filter(
                Q(accessible_presets=preset) | Q(editable_presets=preset)
            ).distinct()
            potential_new_viewers = list(
                shared_users.filter(~Q(library_roles__library=library))
            )
            user_form = LibraryUsersForm(
                library=library,
                data={
                    "viewers": shared_users.union(
                        User.objects.filter(
                            library_roles__library=library,
                            library_roles__role="viewer",
                        )
                    )
                },
            )
            for field in ["admins", "contributors"]:
                user_form.data[field] = user_form.fields[field].initial
            if user_form.is_valid():
                user_form.save()
                if potential_new_viewers:
                    message = f"""
                        {_("The following users have been granted access to the Q&A library")} ({library}):
                        {", ".join(user.full_name for user in potential_new_viewers)}
                        """
                    # Send notifications for newly added library viewers
                    for new_viewer in potential_new_viewers:
                        if new_viewer.id != request.user.id:
                            create_library_shared_notification(
                                new_viewer,
                                library,
                                request.user,
                                role="viewer",
                            )

    # Warn the user if they *don't* have the necessary permissions to add viewers
    # to the library. Note that this only happens if the user saved new preset "metadata",
    # or updated it *and changed the qa_library*
    elif (old_library or action == "create_preset") and not (
        library.is_public
        or request.user.has_perm("librarian.manage_library_users", library)
    ):
        if library.is_personal_library:
            message = _(
                "This preset uses your personal Q&A library. Other users will have their own personal library selected when they load the preset."
            ) + _(
                "\nIf this isn't what you want, you can create a library via 'Manage libraries' and then update this preset."
            )
        elif preset.sharing_option != "private":
            message = f"""
            {_("Other users may not be able to see the attached Q&A library")} ({library})
            {_("unless a library administrator has granted them access.")}
            """

    # If the user has changed qa_library for the preset and DOES have management permissions
    # for the old one, remind them that all of the permissions are still there
    if old_library and request.user.has_perm(
        "librarian.manage_library_users", old_library
    ):
        message += f"""
        {_("Note: This action does NOT change permissions for the previously attached Q&A library")} ({old_library}).
        """

    # If there's a message, end it with a reminder to use the librarian modal
    # Otherwise, we send a flag that immediately closes the modal
    if message and not library.is_personal_library:
        message += _(
            " To adjust permissions on your libraries, use the 'Manage Libraries' button."
        )

    preset_dirty = False
    if chat and chat.loaded_preset:
        chat.options.refresh_from_db()
        chat.loaded_preset.options.refresh_from_db()
        preset_dirty = not options_match(chat.options, chat.loaded_preset.options)

    return render(
        request,
        "chat/modals/presets/library_check.html",
        {
            "keep_open": message != "",
            "message": message.strip(),
            "chat": chat,
            "preset_loaded": chat.loaded_preset if chat else None,
            "preset_dirty": preset_dirty,
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_list_item(request, chat_id, current_chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = chat.id == current_chat_id

    return render(
        request,
        "chat/components/chat_list_item.html",
        {
            "chat": chat,
            "current_chat_id": current_chat_id,
            "section_index": label_section_index(chat.last_modification_date),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def rename_chat(request, chat_id, current_chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = chat_id == current_chat_id

    if request.method == "POST":
        # Only proceed if this POST originated from the inline rename form.
        if request.POST.get("rename_intent") != "1":
            return render(
                request,
                "chat/components/chat_list_item.html",
                {
                    "chat": chat,
                    "current_chat_id": current_chat_id,
                    "section_index": label_section_index(chat.last_modification_date),
                },
            )
        chat_rename_form = ChatRenameForm(request.POST)
        if chat_rename_form.is_valid():
            chat.title = chat_rename_form.cleaned_data["title"]
            # we keep the old last change date since the button will still be displayed in the old section until the next reload
            old_last_modification_date = chat.last_modification_date
            chat.last_modification_date = timezone.now()
            chat.save()

            context = {
                "chat": chat,
                "current_chat_id": current_chat_id,
                "section_index": label_section_index(old_last_modification_date),
            }
            return render(request, "chat/components/chat_list_item.html", context)
        else:
            return render(
                request,
                "chat/components/chat_list_item_title_edit.html",
                {
                    "form": chat_rename_form,
                    "chat": chat,
                    "current_chat_id": current_chat_id,
                    "section_index": label_section_index(chat.last_modification_date),
                },
            )

    chat_rename_form = ChatRenameForm(data={"title": chat.title})
    return render(
        request,
        "chat/components/chat_list_item_title_edit.html",
        {
            "form": chat_rename_form,
            "chat": chat,
            "current_chat_id": current_chat_id,
            "section_index": label_section_index(chat.last_modification_date),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def share_chat(request, chat_id):
    chat = get_object_or_404(Chat, id=chat_id)

    shareable_url = request.build_absolute_uri(reverse("chat:chat", args=[chat.id]))

    return JsonResponse({"success": True, "chat_url": shareable_url})


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def get_message_html(request, message_id):
    """
    Return the rendered HTML for a bot message.
    Used for SSE error recovery when the client falls behind processing SSE events
    but the server has already completed and saved the response.

    Returns JSON with:
    - html: The rendered message HTML (if message is complete)
    - complete: Boolean indicating if the message has content
    """
    message = get_object_or_404(Message, id=message_id)

    # Check if message has content (i.e., streaming completed and was saved)
    # A message is complete if it has non-empty text
    is_complete = bool(message.text and message.text.strip())

    if is_complete:
        # Get reasoning steps from message details for display
        all_events = (message.details.get("query_info") or []) + (
            message.details.get("reasoning_steps") or []
        )

        context = {
            "message": message,
            "swap_oob": True,
            "update_cost_bar": True,
            "plain_message_text": message.text,
            "reasoning_steps_json": (
                json.dumps(all_events, ensure_ascii=False) if all_events else None
            ),
        }
        # Set message.json from the raw text
        context["message"].json = json.dumps(str(context["message"].text))

        html = render_to_string(
            "chat/components/chat_message.html", context, request=request
        )
        return JsonResponse({"html": html, "complete": True})
    else:
        return JsonResponse({"html": "", "complete": False})


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def message_sources(request, message_id, highlight=False):
    # When called via the URL for highlights, ?highlight=true will make this True.
    highlight = request.GET.get("highlight", "false").lower() == "true" or highlight
    message = Message.objects.get(id=message_id)
    already_highlighted = message.claims_list != []

    def replace_page_tags(match):
        page_number = match.group(1)
        return f"**_Page {page_number}_**\n"

    sources = []
    for source in (
        AnswerSource.objects.prefetch_related(
            "document",
            "document__data_source",
            "document__data_source__library",
            "message",
        )
        .filter(message_id=message_id)
        .order_by("group_number")
    ):
        source_text = str(source.node_text)

        already_processed = source.processed_text is not None
        needs_processing = (
            highlight and not already_highlighted
        ) or not already_processed

        source_text = re.sub(r"<page_(\d+)>", replace_page_tags, source_text)
        source_text = re.sub(r"</page_\d+>", "", source_text)

        if needs_processing:
            if highlight:
                claims_list = source.message.claims_list
                if not claims_list:
                    source.message.update_claims_list()
                    claims_list = source.message.claims_list
                source_text = highlight_claims(claims_list, source_text)

            if source.document:
                source_text = fix_source_links(source_text, source.document.url)

            source.processed_text = source_text
            source.save(update_fields=["processed_text"])
            source_text = wrap_llm_response(source_text)
        else:
            source_text = wrap_llm_response(source_text)

        source_dict = {
            "citation": source.citation,
            "document": source.document,
            "node_text": source_text,
            "group_number": source.group_number,
        }

        sources.append(source_dict)

    return render(
        request,
        "chat/modals/sources_modal_inner.html",
        {
            "message_id": message_id,
            "sources": sources,
            "highlighted": highlight or already_highlighted,
            "is_per_doc": message.details.get("is_per_doc", False),
            "is_granular": message.details.get("is_granular", False),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def get_presets(request, chat_id):
    # If user has no default preset, set it to the global default (based on language)
    if not request.user.default_preset:
        request.user.default_preset = Preset.objects.get_global_default()
        request.user.save()
    presets = Preset.objects.get_accessible_presets(request.user, get_language())
    is_otto_admin = request.user.groups.filter(name=settings.OTTO_ADMIN_GROUP).exists()

    # Precompute sharing and language for each preset for use in the template
    for preset in presets:
        if preset.sharing_option == "others" and preset.owner != request.user:
            preset.sharing_option = "shared_with_me"
        preset.can_edit = (
            preset.owner_id == request.user.id
            or (preset.owner_id is None and is_otto_admin)
            or any(u.id == request.user.id for u in preset.editable_by.all())
        )
        # Language detection (crude, based on name)
        if preset.name_en.lower().endswith("(english)"):
            language = "en"
        elif preset.name_en.lower().endswith("(french)"):
            language = "fr"
        else:
            language = ""
        preset.language = language
    return render(
        request,
        "chat/modals/presets/card_list.html",
        {
            "presets": presets,
            "chat_id": chat_id,
            "chat": Chat.objects.select_related("loaded_preset").get(id=chat_id),
            "user": request.user,
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def save_preset(request, chat_id):
    chat = Chat.objects.select_related("loaded_preset").get(id=chat_id)
    # check if chat.loaded_preset is set
    if chat.loaded_preset and request.user.has_perm(
        "chat.edit_preset", chat.loaded_preset
    ):
        preset = Preset.objects.get(id=chat.loaded_preset.id)
        context = {
            "chat_id": chat_id,
            "preset": preset,
            "is_user_default": request.user.default_preset == preset,
            "is_public": preset.sharing_option == "everyone",
            "is_shared": preset.sharing_option == "others",
            "is_global_default": preset.global_default,
        }
        if context["is_user_default"]:
            context["confirm_message"] = _(
                "This preset is set as your default for new chats. Are you sure you want to overwrite it?"
            )
        if context["is_shared"]:
            context["confirm_message"] = _(
                "This preset is shared with other users. Are you sure you want to overwrite it?"
            )
        if context["is_public"]:
            context["confirm_message"] = _(
                "WARNING: This preset is shared with all Otto users. Are you sure you want to overwrite it?"
            )
        if context["is_global_default"]:
            context["confirm_message"] = _(
                "DANGER: This preset is set as the default for all Otto users. Are you sure you want to overwrite it?"
            )
        return render(
            request,
            "chat/modals/presets/save_preset_user_choice.html",
            context,
        )
    else:
        form = PresetForm(user=request.user)
        return render(
            request,
            "chat/modals/presets/presets_form.html",
            {"form": form, "chat_id": chat_id},
        )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def open_preset_form(request, chat_id):
    form = PresetForm(user=request.user)
    return render(
        request,
        "chat/modals/presets/presets_form.html",
        {"form": form, "chat_id": chat_id},
    )


@permission_required("chat.edit_preset", objectgetter(Preset, "preset_id"))
def edit_preset(request, chat_id, preset_id):
    preset = get_object_or_404(
        Preset.objects.select_related("options", "options__qa_library"), id=preset_id
    )
    form = PresetForm(instance=preset, user=request.user)

    library = preset.options.qa_library

    return render(
        request,
        "chat/modals/presets/presets_form.html",
        {
            "form": form,
            "preset_id": preset_id,
            "chat_id": chat_id,
            "can_delete": request.user.has_perm("chat.delete_preset", preset),
            "is_public": preset.sharing_option == "everyone",
            "is_global_default": preset.global_default,
        },
    )


@permission_required("chat.access_preset", objectgetter(Preset, "preset_id"))
def set_preset_default(request, chat_id: str, preset_id: int):
    try:
        selected_preset = Preset.objects.get(id=preset_id)
        old_default_preset = Preset.objects.filter(default_for=request.user).first()
        request.user.default_preset = selected_preset
        request.user.save()
        messages.success(request, _("Default preset updated."), extra_tags="unique")

        # Add the "default" styling to the selected preset
        selected_preset.default = True
        context = {
            "preset": selected_preset,
            "chat_id": chat_id,
            "swap": True,
        }
        response_str = render_to_string(
            "chat/modals/presets/default_icon.html", context, request
        )

        # Remove the "default" styling from the old default preset
        if old_default_preset:
            old_default_preset.default = False
            context.update({"preset": old_default_preset})
            response_str += render_to_string(
                "chat/modals/presets/default_icon.html", context, request
            )

        return HttpResponse(response_str)

    except ValueError as e:
        from otto.utils.common import generate_ai_error_summary

        error_id = str(uuid.uuid4())[:7]
        response_str = generate_ai_error_summary(e, error_id)
        logger.error(
            f"Error setting default preset:",
            chat_id=chat_id,
            preset_id=preset_id,
            error_id=error_id,
            error=e,
        )
        messages.error(request, response_str)
        return HttpResponse(status=500)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def update_qa_options_from_librarian(request, chat_id, library_id):
    # (See librarian/scripts.js)
    chat = Chat.objects.select_related("options", "options__qa_library").get(id=chat_id)
    original_library = chat.options.qa_library
    library = Library.objects.filter(id=library_id).first()
    # If library doesn't exist, or user doesn't have access to it, reset to default library
    if not library or not request.user.has_perm("librarian.view_library", library):
        library = Library.objects.get_default_library()
    chat.options.qa_library = library
    if library != original_library:
        chat.options.qa_data_sources.clear()
        chat.options.qa_documents.clear()
        chat.options.qa_mode = "rag"
        chat.options.qa_scope = "all"
        chat.options.qa_process_mode = "combined_docs"
    chat.options.save()
    # Now return the updated chat options form for swapping
    return render(
        request,
        "chat/components/chat_options_accordion.html",
        {
            "options_form": ChatOptionsForm(instance=chat.options, user=request.user),
            "preset_loaded": "false",
            "trigger_library_change": "true" if library != original_library else None,
            "chat": chat,
        },
    )


@require_POST
def generate_prompt_view(request):
    from structlog.contextvars import bind_contextvars

    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="chat", user_id=user_id, cost_group_id=cost_group_id)

    user_input = request.POST.get("user_input", "")
    output_text, cost = generate_prompt(user_input)
    return render(
        request,
        "chat/modals/prompt_generator_result.html",
        {"user_input": user_input, "output_text": output_text, "cost": cost},
    )


def email_author(request, chat_id):
    chat = get_object_or_404(Chat, pk=chat_id)
    chat_link = request.build_absolute_uri(reverse("chat:chat", args=[chat_id]))
    subject = (
        f"Sharing link for Otto chat | Lien de partage pour le chat Otto: {chat.title}"
    )
    body = (
        "Le message français suit l'anglais.\n---\n"
        "You are receiving this email because you are the author of the following Otto chat:"
        f"\n{chat.title}"
        "\n\nThis link was shared with me, but I don't believe I should have access to it."
        "\n\nACTION REQUIRED: Please open chat using the link below, and delete it if it contains sensitive information."
        f"\n\n{chat_link}"
        "\n\n---\n\n"
        "Vous recevez ce courriel parce que vous êtes l'auteur du chat Otto suivant :"
        f"\n{chat.title}"
        "\n\nCe lien m'a été partagé, mais je ne crois pas que je devrais y avoir accès."
        "\n\nACTION REQUISE : Veuillez ouvrir le chat en utilisant le lien ci-dessous, et le supprimer s'il contient des informations sensibles."
        f"\n\n{chat_link}"
    )
    mailto_link = generate_mailto(to=chat.user.email, subject=subject, body=body)
    return HttpResponse(f"<a href='{mailto_link}'>mailto link</a>")


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def send_message_outlook(request, message_id):
    """
    Outlook web compose deeplink (or mailto fallback) for a chat message
    and redirect the user to it.
    """
    message = get_object_or_404(Message, pk=message_id)
    # Use chat title as subject when available
    chat_title = (message.chat.title or "").strip()
    subject = f"{_('From Otto:')} {chat_title}" if chat_title else f"{_('From Otto:')}"

    # Build view-in-Otto link and prepend it to the plain-text body
    chat_url = request.build_absolute_uri(reverse("chat:chat", args=[message.chat.id]))
    view_link = f"{chat_url}#message_{message.id}"
    body_text = (message.text or "").strip()
    body = f"View in Otto: {view_link}\n\n{body_text}"

    mailto = generate_mailto(to="", subject=subject, body=body)
    # Return a tiny page that opens the mailto link, then attempts to close
    # the temporary tab/window that initiated it.
    mailto_js = json.dumps(mailto)
    html = (
        "<html><head><meta charset='utf-8'></head><body>"
        "<script>"
        f"const mailto = {mailto_js};"
        "window.location.href = mailto;"
        "setTimeout(() => { window.close(); }, 250);"
        "</script>"
        f"<a href='{mailto}'>Open mail client</a>"
        "</body></html>"
    )
    return HttpResponse(html)


def search_chats(request):
    """Simple HTMX endpoint returning chat history sections filtered by title only.
    Does not trigger rename, pin/unpin or any write actions. Returns a partial
    rendering of the chat history list grouped into sections. When search is
    empty, it restores the full interactive list.
    """
    query = (request.GET.get("search", "") or "").strip()
    active_chat_id = request.GET.get("current_chat_id") or None

    if query:
        # When searching, only show chats with messages that match the query
        qs = (
            Chat.objects.filter(user=request.user, messages__isnull=False)
            .filter(Q(title__icontains=query) | Q(messages__text__icontains=query))
            .distinct()
            .annotate(message_count=Count("messages"))
            .prefetch_related(
                Prefetch(
                    "messages",
                    queryset=Message.objects.filter(text__icontains=query).order_by(
                        "date_created", "id"
                    ),
                    to_attr="matched_messages",
                )
            )
            .order_by("-last_modification_date")
        )
    else:
        # When clearing search (empty query), restore the full list like in the main chat view
        # Don't show empty chats except for the current one
        if active_chat_id:
            qs = (
                Chat.objects.filter(user=request.user)
                .annotate(message_count=Count("messages"))
                .filter(Q(message_count__gt=0) | Q(pk=active_chat_id))
                .order_by("-last_modification_date")
            )
        else:
            qs = (
                Chat.objects.filter(user=request.user)
                .annotate(message_count=Count("messages"))
                .filter(message_count__gt=0)
                .order_by("-last_modification_date")
            )

    # For chats with message matches, append a concise snippet and store the
    # earliest matching message id so anchors and highlights are deterministic
    if query:
        for chat in qs:
            matched = chat.matched_messages[0] if chat.matched_messages else None
            if matched:
                # Build a short snippet around the first occurrence
                text = matched.text or ""
                lower_text = text.lower()
                idx = lower_text.find(query.lower()) if query else -1
                if idx != -1:
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(query) + 40)
                    snippet = text[start:end].strip()
                    if start > 0:
                        snippet = "…" + snippet
                    if end < len(text):
                        snippet = snippet + "…"
                else:
                    # Fallback: simple head snippet
                    snippet = (text[:80] + ("…" if len(text) > 80 else "")).strip()

                # Expose snippet separately (don't alter title)
                chat.snippet = snippet
                chat.matched_message_id = matched.id

    # Set titles for untitled chats (similar to main chat view)
    for chat in qs:
        chat.current_chat = str(chat.id) == str(active_chat_id)
    annotate_pending_titles(qs, language=request.LANGUAGE_CODE)

    sections = get_chat_history_sections(qs)
    return render(
        request,
        "chat/components/chat_history_list_container.html",
        {
            "chat_history_sections": sections,
            "search": query,
            "current_chat_id": active_chat_id,
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "current_chat_id"))
def refresh_chat_titles(request, current_chat_id):
    """Return OOB chat-list-item swaps for chats whose async titles are now ready."""
    chat_ids = request.GET.getlist("chat_ids")
    if not chat_ids:
        return HttpResponse("")

    search = (request.GET.get("search", "") or "").strip()

    chats = Chat.objects.filter(user=request.user, id__in=chat_ids).order_by(
        "-last_modification_date"
    )

    response_html = ""
    for chat in chats:
        # Title is still pending (Celery task hasn't completed yet); skip
        if is_placeholder_chat_title(chat.title):
            continue

        # Title has been resolved; render the updated chat item
        chat.current_chat = str(chat.id) == str(current_chat_id)
        chat.is_title_pending = False
        response_html += render_to_string(
            "chat/components/chat_list_item.html",
            {
                "chat": chat,
                "current_chat_id": current_chat_id,
                "section_index": label_section_index(chat.last_modification_date),
                "search": search,
                "swap_oob": True,
            },
            request=request,
        )

    return HttpResponse(response_html)
