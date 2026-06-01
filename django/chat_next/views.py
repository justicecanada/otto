import json

from django.utils.translation import gettext as _
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.conf import settings

from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import OttoStatus
from otto.utils.common import generate_mailto
from otto.utils.decorators import (
    budget_required,
    otto_user_required,
    permission_required,
)
from otto.views import feedback_message

# ruff: noqa # Do not remove - used in urls.py
from chat_next._views.pin_chat import (
    pin_chat,
    unpin_chat,
)
from chat_next.forms import (
    ChatModelSelectorForm,
    ChatRenameForm,
    UploadForm,
    get_chat_model_selector_summary,
)
from chat_next.message_attachments import ensure_message_attachment_for_document
from chat_next.models import (
    Chat,
    ChatFile,
    ChatSettings,
    Message,
    create_chat_data_source,
)
from chat_next.utils import (
    PROCESSING_STEPS_TRANSLATIONS_KEY,
    annotate_pending_titles,
    format_markdown_code_block,
    get_base_display_processing_steps,
    get_display_processing_steps,
    get_chat_history_sections,
    get_model_name,
    label_section_index,
    link_chat_files_to_library,
    translate_reasoning_processing_steps,
    wrap_llm_response,
)

app_name = "chat"
logger = get_logger(__name__)
User = get_user_model()


def _request_language_code(request):
    return (getattr(request, "LANGUAGE_CODE", None) or get_language() or "")[:2]


def _build_message_render_context(
    request, message, *, swap_oob=True, update_cost_bar=True
):
    processing_steps = get_display_processing_steps(
        message, language=_request_language_code(request)
    )
    if message.details and message.details.get("processing_steps"):
        all_events = (message.details.get("query_info") or []) + processing_steps
    else:
        all_events = processing_steps

    context = {
        "message": message,
        "swap_oob": swap_oob,
        "update_cost_bar": update_cost_bar,
        "plain_message_text": message.text,
        "reasoning_steps_json": (
            json.dumps(all_events, ensure_ascii=False) if all_events else None
        ),
    }
    context["message"].json = json.dumps(str(context["message"].text))
    return context


def _render_reasoning_section(request, message):
    context = _build_message_render_context(
        request, message, swap_oob=False, update_cost_bar=False
    )
    return render_to_string(
        "chat_next/components/reasoning_section.html",
        context,
        request=request,
    )


def _get_selected_model_overrides(request, chat):
    model_selector_form = ChatModelSelectorForm(
        request.POST,
        instance=chat.settings,
        prefix="chat-model-selector",
    )
    if model_selector_form.is_valid():
        selected_model = model_selector_form.cleaned_data["chat_model"]
        selected_reasoning_effort = model_selector_form.cleaned_data[
            "chat_reasoning_effort"
        ]
        selected_verbosity = model_selector_form.cleaned_data["chat_verbosity"]
    else:
        logger.warning(
            "Invalid chat model selector values in prompt submission; using defaults.",
            chat_id=chat.id,
            errors=model_selector_form.errors,
        )
        selected_model = chat.settings.chat_model
        selected_reasoning_effort = chat.settings.chat_reasoning_effort
        selected_verbosity = chat.settings.chat_verbosity

    return {
        "chat_model": selected_model,
        "chat_reasoning_effort": selected_reasoning_effort,
        "chat_verbosity": selected_verbosity,
    }


@permission_required("otto.can_access_chat_next")
def new_chat(request):
    """
    Create a new chat and render it directly (avoiding redirect).
    """

    empty_chat = Chat.objects.create(user=request.user)

    logger.info("New chat created.", chat_id=empty_chat.id)
    new_url = reverse("chat_next:chat", args=[empty_chat.id])
    open_skill = request.GET.get("open_skill")

    start_tour = request.GET.get("start_tour") == "true"
    query_params = []
    if start_tour:
        query_params.append("start_tour=true")
    if open_skill:
        query_params.append(f"open_skill={open_skill}")
    if query_params:
        new_url += "?" + "&".join(query_params)

    request._push_url = new_url
    return chat(request, empty_chat.id)


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
        response["HX-Redirect"] = reverse("chat_next:new_chat")
        return response
    return HttpResponse(status=200)


@permission_required("otto.can_access_chat_next")
def delete_all_chats(request):
    for chat in Chat.objects.filter(user=request.user):
        chat.delete()

    logger.info("all chats deleted")

    response = HttpResponse()
    response["HX-Redirect"] = reverse("chat_next:new_chat")
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
    bind_contextvars(feature="chat_next", user_id=user_id, cost_group_id=cost_group_id)

    # Prefetch user's groups once to avoid repeated permission check queries
    # This prevents multiple queries to check is_admin, is_operations_admin, etc.
    if hasattr(request.user, "groups"):
        # Force evaluation of groups queryset to cache it
        list(request.user.groups.all())

    chat = Chat.objects.filter(id=chat_id).select_related("user").first()

    if not chat:
        return new_chat(request)
    Chat.objects.filter(id=chat_id).update(accessed_at=timezone.now())

    # Get chat messages ready
    # Prefetch parent messages with their file counts annotated
    parent_prefetch = Prefetch(
        "parent",
        queryset=Message.objects.annotate(num_files_count=Count("files")),
    )

    chat_messages = (
        Message.objects.filter(chat=chat)
        .order_by("date_created")
        .annotate(num_files_count=Count("files"))  # Annotate file count to avoid N+1
        .prefetch_related(
            parent_prefetch,  # Prefetch parent with file count
            "files__saved_file",  # Prefetch files and their SavedFile references
            "files__document",  # Prefetch documents for status checking
        )
    )
    # Highlight a specific matched message if requested
    highlight_message_id = request.GET.get("highlight_message") or None
    anchor_message_id = request.GET.get("anchor_message") or None
    if not highlight_message_id and anchor_message_id:
        highlight_message_id = anchor_message_id

    previous_bot_total_tokens = None
    for message in chat_messages:
        if message.is_bot:
            message.json = json.dumps(message.text)
            message._previous_bot_total_tokens = previous_bot_total_tokens
            previous_bot_total_tokens = message.get_context_total_tokens()
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
        return render(
            request,
            "chat_next/chat_readonly.html",
            context=context,
        )

    # Ensure DataSource exists
    try:
        chat.data_source
    except:
        chat.data_source = create_chat_data_source(request.user, chat=chat)
        chat.save()

    # Get sidebar chat history list.
    # Don't show empty chats - these will be deleted automatically later.
    # The current chat is always shown, even if it's empty.
    user_chats = (
        Chat.objects.filter(user=request.user)
        .annotate(message_count=Count("messages"))
        .filter(Q(message_count__gt=0) | Q(pk=chat.id))
        .order_by("-last_modification_date")
    )

    # Convert to list once so downstream iterations don't trigger repeated queries.
    user_chats_list = list(user_chats)

    # Mark current chat in the sidebar list; placeholder titling is applied
    # only to the list that is actually rendered below.
    for user_chat in user_chats_list:
        user_chat.current_chat = user_chat.id == chat.id

    # If arriving with ?search=, pre-render a filtered sidebar to avoid flicker
    search = (request.GET.get("search", "") or "").strip()
    if search:
        base_qs = Chat.objects.filter(user=request.user, messages__isnull=False)
        filtered = (
            base_qs.filter(
                Q(title__icontains=search) | Q(messages__text__icontains=search)
            )
            .distinct()
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
            c.current_chat = c.id == chat.id
        annotate_pending_titles(filtered, language=_request_language_code(request))
        sidebar_sections = get_chat_history_sections(filtered)
    else:
        annotate_pending_titles(
            user_chats_list, language=_request_language_code(request)
        )
        sidebar_sections = get_chat_history_sections(user_chats_list)

    # Get or create user-level ChatSettings
    user_settings, _created = ChatSettings.objects.get_or_create_for_user(request.user)

    # Determine upload limits (in bytes) for the current user from OttoStatus singleton
    otto_status = OttoStatus.objects.singleton()
    chat_max = otto_status.chat_max_bytes_for(request.user)
    librarian_max = otto_status.librarian_max_bytes_for(request.user)
    azure_translation_max = settings.AZURE_TRANSLATION_DOCUMENT_SIZE_LIMIT

    context = {
        "active_app": "chat_next",
        "chat": chat,
        "chat_settings": user_settings,
        "model_selector_form": ChatModelSelectorForm(
            instance=user_settings,
            prefix="chat-model-selector",
        ),
        "model_selector_summary": get_chat_model_selector_summary(
            user_settings.chat_model,
            user_settings.chat_reasoning_effort,
        ),
        "chat_messages": chat_messages,
        "hide_breadcrumbs": True,
        "user_chats": user_chats_list,
        "chat_history_sections": sidebar_sections,
        "has_tour": True,
        "tour_name": _("AI Assistant"),
        "force_tour": (not request.user.chat_next_tour_completed)
        and not (request.GET.get("skip_tour") == "true"),
        "tour_skippable": (
            request.user.is_admin or request.user.chat_next_tour_completed
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
        "mode": "chat",  # chat_next only has chat mode
        "pinned_messages": [],  # No pinned messages in chat_next yet
    }

    if hasattr(request, "_push_url"):
        context["push_url"] = request._push_url

    return render(request, "chat_next/chat.html", context=context)


@require_POST
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@budget_required
def chat_message(request, chat_id):
    """
    Post a user message to the chat and initiate a streaming response.
    Also handles file uploads when files are included with the message.
    """
    # The user must match the chat
    chat = Chat.objects.get(id=chat_id)
    # Create the user's message in database
    user_message_text = request.POST.get("user-message", "").strip()
    logger.debug(
        "User message received.",
        chat_id=chat_id,
        user_message=f"{user_message_text[:100]}{'...' if len(user_message_text) > 100 else ''}",
    )

    # Stop the previous bot response message, if necessary
    chat_bot_messages = Message.objects.filter(chat=chat, is_bot=True).order_by("id")
    if chat_bot_messages.exists():
        last_bot = chat_bot_messages.last()
        cache.set(f"stop_response_{last_bot.id}", True, timeout=60)
        # Clear response_id and response_output on the interrupted message.
        # A stale response_id that references a response with unresolved
        # function_calls would cause "No tool output found" errors when the
        # next turn tries to chain to it via previous_response_id.
        needs_cleanup = False
        # if last_bot.response_id:
        #     last_bot.response_id = ""
        #     needs_cleanup = True
        # if last_bot.response_output:
        #     last_bot.response_output = []
        #     needs_cleanup = True
        # # Clear pending approval state so it's not accidentally reused
        # if last_bot.details and last_bot.details.get("pending_local_tool"):
        #     last_bot.details.pop("pending_local_tool", None)
        #     needs_cleanup = True
        if needs_cleanup:
            last_bot.save(update_fields=["response_id", "response_output", "details"])

    user_message = Message.objects.create(
        chat=chat, text=user_message_text, is_bot=False
    )
    user_message.is_new_user_message = True

    # Store context hints (tools, libraries, documents selected by user)
    context_hints_raw = request.POST.get("context-hints", "").strip()
    if context_hints_raw:
        try:
            context_hints = json.loads(context_hints_raw)
            if context_hints:
                user_message.details["context_hints"] = context_hints
                user_message.save(update_fields=["details"])
        except (json.JSONDecodeError, TypeError):
            pass

    model_overrides = _get_selected_model_overrides(request, chat)

    # Handle file uploads if present (e.g., files submitted with prompt text)
    # Note: File-only uploads are now auto-submitted via save_upload endpoint
    form = UploadForm(request.POST, request.FILES, prefix="chat")
    uploaded_chat_files = []
    if form.is_valid():
        saved_files = form.save()
        # Note: We no longer reject files based on format - the Q&A library
        # can process many file types that direct LLM vision/code-interpreter cannot.
        for saved_file in saved_files:
            chat_file = ChatFile.objects.create(
                message_id=user_message.id,
                filename=saved_file["filename"],
                saved_file=saved_file["saved_file"],
            )
            uploaded_chat_files.append(chat_file)
        if uploaded_chat_files:
            logger.info(
                "Files attached to message.",
                chat_id=chat_id,
                message_id=user_message.id,
                num_files=len(uploaded_chat_files),
            )

            # Immediately link uploaded files into the user's personal library
            # under this chat's DataSource so they are available for tool-based retrieval.
            try:
                data_source = chat.data_source
            except Exception:
                chat.data_source = create_chat_data_source(request.user, chat=chat)
                chat.save(update_fields=["data_source"])
                data_source = chat.data_source

            link_chat_files_to_library(uploaded_chat_files, user_message, data_source)

    bot_name = get_model_name(chat.settings, model_key=model_overrides["chat_model"])
    response_message = Message.objects.create(
        chat=chat,
        is_bot=True,
        parent=user_message,
        text="",
        bot_name=bot_name,
        details={"model_overrides": model_overrides},
    )
    # This tells the frontend to display the 3 dots and initiate the streaming response
    response_message = {
        "is_bot": True,
        "awaiting_response": True,
        "id": response_message.id,
        "date_created": response_message.date_created + timezone.timedelta(seconds=1),
        "bot_name": bot_name,
    }

    context = {
        "chat_messages": [
            user_message,
            response_message,
        ],
    }
    response = HttpResponse()
    response.write(
        render_to_string(
            "chat_next/components/chat_messages.html",
            context,
            request=request,
        )
    )

    # Clear the prompt upload area via OOB swap
    response.write(
        render_to_string(
            "chat_next/components/prompt_upload_area_reset.html",
            context={"chat": chat, "upload_form": UploadForm(prefix="chat")},
            request=request,
        )
    )

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


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def edit_message(request, message_id):
    """
    GET:  Render the user message with the inline editor (when ?editing=1) or
          in normal read mode (for cancel).
    POST: Save the edited text, delete all later messages, and create a new
          bot response — same flow as rerun_prompt.
    """
    try:
        message = Message.objects.get(id=message_id, is_bot=False)
    except Message.DoesNotExist:
        return HttpResponse(status=404)

    chat = message.chat

    # GET: just render the message text with the text editor
    if request.method == "GET":
        editing = request.GET.get("editing") == "1"
        return render(
            request,
            "chat_next/components/chat_message.html",
            {"message": message, "editing_message": editing},
        )

    # POST: save the edited text and rerun the prompt
    new_text = request.POST.get("user-message", "").strip()
    if not new_text:
        return HttpResponse(status=400)

    try:
        logger.info(
            "Editing chat message and rerunning prompt.",
            message_id=message_id,
            chat_id=chat.id,
        )

        later_messages = list(Message.objects.filter(chat=chat, id__gt=message.id))
        removed_message_ids = []
        for msg in later_messages:
            if msg.is_bot:
                cache.set(f"stop_response_{msg.id}", True, timeout=60)
            removed_message_ids.append(msg.id)
        if removed_message_ids:
            Message.objects.filter(id__in=removed_message_ids).delete()

        now = timezone.now()
        message.text = new_text
        message.date_created = now
        message.save(update_fields=["text", "date_created"])
        chat.last_modification_date = now
        chat.save(update_fields=["last_modification_date"])

        model_overrides = _get_selected_model_overrides(request, chat)
        bot_name = get_model_name(
            chat.settings, model_key=model_overrides["chat_model"]
        )
        response_message = Message.objects.create(
            chat=chat,
            text="",
            is_bot=True,
            parent=message,
            bot_name=bot_name,
            details={"model_overrides": model_overrides},
        )

        response_context = {
            "is_bot": True,
            "awaiting_response": True,
            "id": response_message.id,
            "date_created": response_message.date_created
            + timezone.timedelta(seconds=1),
            "bot_name": bot_name,
        }

        context = {"chat_messages": [message, response_context]}
        html = render_to_string(
            "chat_next/components/chat_messages.html",
            context,
            request=request,
        )

        if removed_message_ids:
            html += "".join(
                f"<div id='message_{mid}' hx-swap-oob='delete'></div>"
                for mid in removed_message_ids
            )

        return HttpResponse(html)

    except Exception:
        logger.exception(
            "An error occurred while editing chat message.", message_id=message_id
        )
        return HttpResponse(status=500)


@require_POST
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def save_upload(request, chat_id):
    """
    Handles the form submission after JS upload.
    Creates a user message with files attached and links them to the Q&A library.
    Does NOT create a bot response message - files are processed asynchronously
    and status is shown via polling in the user message.
    """
    chat = Chat.objects.get(id=chat_id)
    form = UploadForm(request.POST, request.FILES, prefix="chat")
    if not form.is_valid():
        logger.error("File upload error.", errors=form.errors)
        messages.error(request, _("There was an error uploading your files."))
        response = HttpResponse()
        response.write(
            render_to_string(
                "chat_next/components/prompt_upload_message.html",
                context={"hidden": True},
                request=request,
            )
        )
        response.write(
            render_to_string(
                "chat_next/components/prompt_upload_area_reset.html",
                context={"chat": chat, "upload_form": UploadForm(prefix="chat")},
                request=request,
            )
        )
        return response

    saved_files = form.save()
    # Note: We no longer reject files based on format - the Q&A library
    # can process many file types that direct LLM vision/code-interpreter cannot.

    logger.info("File upload initiated.", chat_id=chat_id, num_files=len(saved_files))
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)
    uploaded_chat_files = []
    for saved_file in saved_files:
        chat_file = ChatFile.objects.create(
            message_id=user_message.id,
            filename=saved_file["filename"],
            saved_file=saved_file["saved_file"],
        )
        uploaded_chat_files.append(chat_file)

    # Link files to the Q&A library for tool-based retrieval
    if uploaded_chat_files:
        try:
            data_source = chat.data_source
        except Exception:
            chat.data_source = create_chat_data_source(request.user, chat=chat)
            chat.save(update_fields=["data_source"])
            data_source = chat.data_source

        link_chat_files_to_library(uploaded_chat_files, user_message, data_source)
        logger.info(
            "Files linked to Q&A library.",
            chat_id=chat_id,
            message_id=user_message.id,
            num_files=len(uploaded_chat_files),
        )

    # Build the response with just the user message (no bot response)
    # The user message will poll for library processing status
    response = HttpResponse()
    context = {
        "chat_messages": [user_message],
    }
    response.write(
        render_to_string(
            "chat_next/components/chat_messages.html",
            context=context,
            request=request,
        )
    )

    # Reset the prompt upload form via OOB swap so new uploads work
    response.write(
        render_to_string(
            "chat_next/components/prompt_upload_area_reset.html",
            context={"chat": chat, "upload_form": UploadForm(prefix="chat")},
            request=request,
        )
    )
    return response


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def library_status(request, message_id):
    """
    Return the current processing status of files linked to this message.
    Returns the message_files template with updated file cards and status icons.
    Used for polling from the user message to show file processing progress.

    Also handles child documents from containers (ZIP, MSG, EML) - creates
    ChatFile objects for children and includes them in the status.
    """
    from librarian.models import Document
    from librarian.views import IN_PROGRESS_STATUSES

    message = Message.objects.get(id=message_id)

    # Link any unlinked files to the Q&A library (triggers document processing)
    unlinked_files = list(
        ChatFile.objects.filter(message=message, document__isnull=True)
        .exclude(saved_file__isnull=True)
        .select_related("saved_file")
    )
    if unlinked_files:
        try:
            data_source = message.chat.data_source
        except Exception:
            data_source = create_chat_data_source(request.user, chat=message.chat)
            message.chat.data_source = data_source
            message.chat.save(update_fields=["data_source"])
        link_chat_files_to_library(unlinked_files, message, data_source)

    # Get documents directly linked to this message
    directly_linked = Document.objects.filter(chat_next_messages=message)

    # Get child documents whose parent is linked to this message (for ZIP/MSG/EML extraction)
    # This catches children even if they weren't directly linked via chat_next_messages
    child_docs_of_linked = Document.objects.filter(
        parent_document__chat_next_messages=message
    )

    # Combine both querysets, excluding containers, and select related fields
    linked_docs = (
        (directly_linked | child_docs_of_linked)
        .exclude(is_container=True)
        .select_related("saved_file", "parent_document")
        .distinct()
    )

    # Ensure we have ChatFile objects for all linked documents (including children)
    existing_doc_ids = set(
        ChatFile.objects.filter(message=message).values_list("document_id", flat=True)
    )

    for doc in linked_docs:
        if doc.id not in existing_doc_ids:
            ensure_message_attachment_for_document(message, doc)

    # Re-fetch message with all files including newly created ones
    message = Message.objects.prefetch_related(
        Prefetch(
            "files",
            queryset=ChatFile.objects.select_related(
                "document", "saved_file", "document__parent_document"
            ),
        )
    ).get(id=message_id)

    # Calculate status summary
    total_docs = linked_docs.count()
    processing_count = linked_docs.filter(status__in=IN_PROGRESS_STATUSES).count()
    success_count = linked_docs.filter(status="SUCCESS").count()
    error_count = linked_docs.filter(status="ERROR").count()
    stopped_count = linked_docs.filter(status="BLOCKED").count()
    paused_count = linked_docs.filter(status="PAUSED").count()

    # Also check for files without documents yet (still being linked)
    files_without_docs = message.files.filter(document__isnull=True).count()

    files_processing = processing_count > 0 or files_without_docs > 0

    # Status summary for display
    status_summary = None
    if total_docs > 0 or files_without_docs > 0:
        total_for_display = total_docs + files_without_docs
        processed_for_display = (
            success_count + error_count + stopped_count + paused_count
        )
        if files_processing:
            status_summary = {
                "processing": True,
                "processed": processed_for_display,
                "total": total_for_display,
                "paused_count": paused_count,
            }
        else:
            status_summary = {
                "processing": False,
                "success_count": success_count,
                "error_count": error_count,
                "stopped_count": stopped_count,
                "paused_count": paused_count,
                "total": total_for_display,
            }

    # Get the data source ID for the "See details" link
    data_source_id = None
    try:
        data_source_id = message.chat.data_source.id
    except Exception:
        pass

    return HttpResponse(
        render_to_string(
            "chat_next/components/message_files.html",
            {
                "message": message,
                "show_library_status": True,
                "files_processing": files_processing,
                "status_summary": status_summary,
                "data_source_id": data_source_id,
            },
            request=request,
        )
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def bot_task_status(request, message_id):
    """
    Poll the status of background Celery tasks (adding to library) for a bot message.
    Returns updated message_files.html with progress or final state.

    Uses message.details["pending_tasks"] to track which tasks are still running.
    When all tasks complete, removes pending_tasks from details so polling stops.
    """
    from celery.result import AsyncResult

    message = Message.objects.prefetch_related(
        Prefetch(
            "files",
            queryset=ChatFile.objects.select_related("saved_file", "document").order_by(
                "id"
            ),
        )
    ).get(id=message_id)

    details = message.details or {}
    pending_tasks = details.get("pending_tasks", [])

    # Check each task's status
    still_pending = []
    for task_info in pending_tasks:
        task_id = task_info.get("task_id")
        if task_id:
            result = AsyncResult(task_id)
            if not result.ready():
                still_pending.append(task_info)

    total_tasks = len(pending_tasks)
    completed_count = total_tasks - len(still_pending)
    has_pending = len(still_pending) > 0

    # If all tasks are done, clean up pending_tasks from details
    if not has_pending and pending_tasks:
        details.pop("pending_tasks", None)
        message.details = details
        message.save(update_fields=["details"])
        # Re-fetch to get any newly created ChatFiles
        message = Message.objects.prefetch_related(
            Prefetch(
                "files",
                queryset=ChatFile.objects.select_related(
                    "saved_file", "document"
                ).order_by("id"),
            )
        ).get(id=message_id)

    task_status_summary = {
        "completed": completed_count,
        "total": total_tasks,
    }

    return HttpResponse(
        render_to_string(
            "chat_next/components/message_files.html",
            {
                "message": message,
                "has_pending_tasks": has_pending,
                "pending_tasks": still_pending,
                "task_status_summary": task_status_summary,
            },
            request=request,
        )
    )


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


@permission_required("chat.access_file", objectgetter(ChatFile, "file_id"))
def inline_file(request, file_id):
    """Serve a file inline (Content-Disposition: inline) for preview in <img>/<iframe>."""
    import mimetypes

    file_obj = get_object_or_404(ChatFile, pk=file_id)
    file = file_obj.saved_file.file
    ct = file_obj.saved_file.content_type or ""
    if not ct or ct == "?":
        ct = mimetypes.guess_type(file_obj.filename)[0] or "application/octet-stream"
    return FileResponse(
        file,
        as_attachment=False,
        filename=file_obj.filename,
        content_type=ct,
    )


@permission_required("chat.access_file", objectgetter(ChatFile, "file_id"))
def preview_file(request, file_id):
    """
    Return an HTML fragment with preview content for a ChatFile.
    Loaded via HTMX into the file preview panel.
    """
    import json as json_module

    file_obj = get_object_or_404(
        ChatFile.objects.select_related("saved_file", "document"), pk=file_id
    )
    import mimetypes

    stored_ct = (
        (file_obj.saved_file.content_type or "").lower() if file_obj.saved_file else ""
    )
    if not stored_ct or stored_ct == "?":
        stored_ct = (mimetypes.guess_type(file_obj.filename)[0] or "").lower()
    content_type = stored_ct
    download_url = reverse("chat_next:download_file", args=[file_obj.id])
    inline_url = reverse("chat_next:inline_file", args=[file_obj.id])

    # Determine preview type and load content as needed
    preview_type = "unsupported"
    file_content = ""
    file_content_json = ""

    if content_type == "text/markdown" or file_obj.filename.lower().endswith(".md"):
        preview_type = "markdown"
        file_content = _read_file_text(file_obj)
        # JSON-encode for data-md attribute (same pattern as message.json)
        # Django template auto-escaping handles HTML-safe encoding
        file_content_json = json_module.dumps(file_content)

    elif content_type.startswith("image/"):
        preview_type = "image"

    elif content_type in (
        "text/plain",
        "text/csv",
    ) or file_obj.filename.lower().endswith(
        (".txt", ".csv", ".log", ".json", ".xml", ".yaml", ".yml")
    ):
        preview_type = "text"
        file_content = _read_file_text(file_obj)

    elif content_type == "application/pdf" or file_obj.filename.lower().endswith(
        ".pdf"
    ):
        preview_type = "pdf"

    elif (
        content_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or file_obj.filename.lower().endswith(".docx")
    ):
        preview_type = "docx"

    elif content_type == "text/html" or file_obj.filename.lower().endswith(
        (".html", ".htm")
    ):
        preview_type = "html"
        file_content = _read_file_text(file_obj)
        # Django auto-escaping handles srcdoc attribute escaping;
        # browser decodes HTML entities in srcdoc, then renders the HTML.

    return HttpResponse(
        render_to_string(
            "chat_next/components/file_preview_content.html",
            {
                "file": file_obj,
                "preview_type": preview_type,
                "file_content": file_content,
                "file_content_json": file_content_json,
                "inline_url": inline_url,
                "download_url": download_url,
            },
            request=request,
        )
    )


def _read_file_text(file_obj, max_bytes=2 * 1024 * 1024):
    """Read text content from a ChatFile's saved_file, up to max_bytes."""
    try:
        f = file_obj.saved_file.file
        f.open("rb")
        raw = f.read(max_bytes)
        f.close()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Failed to read file for preview.", file_id=file_obj.id)
        return ""


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
        message.save()
    except Exception as e:
        logger.exception(
            f"An error occurred while providing thumbs up/down feedback.:{e}",
            message_id=message_id,
        )

    if feedback == -1:
        return feedback_message(request, message_id, is_chat_next=True)

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
        logger.info(
            "Rerunning chat prompt.",
            message_id=message_id,
            chat_id=chat.id,
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
        chat.last_modification_date = now
        chat.save(update_fields=["last_modification_date"])

        model_overrides = _get_selected_model_overrides(request, chat)
        bot_name = get_model_name(
            chat.settings, model_key=model_overrides["chat_model"]
        )
        response_message = Message.objects.create(
            chat=chat,
            text="",
            is_bot=True,
            parent=original_message,
            bot_name=bot_name,
            details={"model_overrides": model_overrides},
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
        }

        html = render_to_string(
            "chat_next/components/chat_messages.html",
            context,
            request=request,
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
def chat_list_item(request, chat_id, current_chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = chat.id == current_chat_id

    return render(
        request,
        "chat_next/components/chat_list_item.html",
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
                "chat_next/components/chat_list_item.html",
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
            return render(
                request,
                "chat_next/components/chat_list_item.html",
                context,
            )
        else:
            return render(
                request,
                "chat_next/components/chat_list_item_title_edit.html",
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
        "chat_next/components/chat_list_item_title_edit.html",
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

    shareable_url = request.build_absolute_uri(
        reverse("chat_next:chat", args=[chat.id])
    )

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
        context = _build_message_render_context(
            request, message, swap_oob=True, update_cost_bar=True
        )

        html = render_to_string(
            "chat_next/components/chat_message.html",
            context,
            request=request,
        )
        return JsonResponse({"html": html, "complete": True})
    else:
        return JsonResponse({"html": "", "complete": False})


@require_POST
@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def translate_processing_steps(request, message_id):
    """Translate saved reasoning processing steps into French using an LLM."""
    message = get_object_or_404(Message, id=message_id)
    language = _request_language_code(request)

    if (
        not message.is_bot
        or language != "fr"
        or not (message.text and message.text.strip())
        or not message.has_reasoning_processing_steps
    ):
        return HttpResponse(_render_reasoning_section(request, message))

    details = message.details or {}
    translations = details.get(PROCESSING_STEPS_TRANSLATIONS_KEY) or {}
    fr_translation = translations.get("fr") or {}

    if fr_translation.get("status") != "complete":
        try:
            translated_steps = translate_reasoning_processing_steps(
                get_base_display_processing_steps(details, language="fr"),
                target_language="fr",
            )
            translations["fr"] = {
                "status": "complete",
                "steps": translated_steps,
            }
        except Exception:
            logger.exception(
                "Failed to translate reasoning processing steps",
                message_id=message.id,
                chat_id=message.chat_id,
            )
            translations["fr"] = {
                "status": "error",
                "error": str(_("Unable to translate processing steps right now.")),
            }

        details[PROCESSING_STEPS_TRANSLATIONS_KEY] = translations
        message.details = details
        message.save(update_fields=["details"])
        message.refresh_from_db(fields=["details"])

    return HttpResponse(_render_reasoning_section(request, message))


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def message_tool_output(request, message_id, step_index):
    """
    Return the inner HTML for the tool output modal for a specific processing step.
    Shows the tool call input (arguments) and output.
    """
    message = get_object_or_404(Message, id=message_id)
    processing_steps = (
        message.details.get("processing_steps", []) if message.details else []
    )
    try:
        step = processing_steps[int(step_index)]
    except (IndexError, TypeError, ValueError, KeyError):
        raise Http404

    if not step.get("output"):
        raise Http404

    # Format output as a JSON code block for markdown rendering
    output = step["output"]
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            output_str = json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            output_str = output
    else:
        output_str = json.dumps(output, indent=2, ensure_ascii=False)

    input_html = wrap_llm_response(step["details"]) if step.get("details") else ""
    output_html = wrap_llm_response(format_markdown_code_block(output_str, "json"))

    # Build modal title like: "Tool usage details: rag_search"
    title_text = step.get("title", "") or ""
    tool_name = step.get("tool_name")
    if not tool_name:
        if ":" in title_text:
            tool_name = title_text.split(":", 1)[1].strip()
        elif title_text:
            tool_name = title_text.split()[-1].strip(".…")
        else:
            tool_name = _("unknown tool")
    modal_title = _("Tool usage details:") + f" {tool_name}"

    return render(
        request,
        "chat_next/modals/tool_output_modal_inner.html",
        {
            "step": step,
            "input_html": input_html,
            "output_html": output_html,
            "modal_title": modal_title,
        },
    )


def email_author(request, chat_id):
    chat = get_object_or_404(Chat, pk=chat_id)
    chat_link = request.build_absolute_uri(reverse("chat_next:chat", args=[chat_id]))
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


@permission_required("otto.can_access_chat_next")
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
    annotate_pending_titles(qs, language=_request_language_code(request))

    sections = get_chat_history_sections(qs)
    return render(
        request,
        "chat_next/components/chat_history_list_container.html",
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
        if (chat.title or "").strip().lower() in {
            "",
            "untitled chat",
            "conversation sans titre",
        }:
            continue

        chat.current_chat = str(chat.id) == str(current_chat_id)
        chat.is_title_pending = False
        response_html += render_to_string(
            "chat_next/components/chat_list_item.html",
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
