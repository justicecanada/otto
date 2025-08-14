import json
import os
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.forms import ChatOptionsForm, ChatRenameForm, PresetForm, UploadForm
from chat.llm import OttoLLM
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
    bad_url,
    change_mode_to_chat_qa,
    copy_options,
    fix_source_links,
    generate_prompt,
    get_chat_history_sections,
    get_model_name,
    highlight_claims,
    label_section_index,
    title_chat,
    wrap_llm_response,
)
from librarian.forms import LibraryUsersForm
from librarian.models import Library
from otto.rules import can_edit_library
from otto.utils.common import check_url_allowed, generate_mailto
from otto.utils.decorators import (
    app_access_required,
    budget_required,
    permission_required,
)
from otto.views import feedback_message

app_name = "chat"
logger = get_logger(__name__)
User = get_user_model()


new_chat_with_ai = lambda request: new_chat(request, mode="chat")
new_translate = lambda request: new_chat(request, mode="translate")
new_summarize = lambda request: new_chat(request, mode="summarize")
new_document_qa = lambda request: new_chat(request, mode="document_qa")
new_qa = lambda request: new_chat(request, mode="qa")


@app_access_required(app_name)
def new_chat(request, mode=None):
    """
    Create a new chat and redirect to it
    """

    empty_chat = Chat.objects.create(user=request.user, mode=mode)

    logger.info("New chat created.", chat_id=empty_chat.id, mode=mode)
    redirect_url = reverse("chat:chat", args=[empty_chat.id])

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

    return redirect(redirect_url)


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


@app_access_required("chat")
def delete_all_chats(request):

    for chat in Chat.objects.filter(user=request.user):
        chat.delete()

    logger.info("all chats deleted")

    response = HttpResponse()
    response["HX-Redirect"] = reverse("chat:new_chat")
    return response


def chat(request, chat_id):
    """
    Get the chat based on the provided chat ID.
    Returns read-only view if user does not have access.
    """

    logger.info("Chat session retrieved.", chat_id=chat_id)
    bind_contextvars(feature="chat")

    chat = (
        Chat.objects.filter(id=chat_id)
        .prefetch_related(
            "options",
            "options__qa_library",
            "options__qa_data_sources",
            "options__qa_documents",
        )
        .first()
    )

    if not chat:
        return new_chat(request)
    Chat.objects.filter(id=chat_id).update(accessed_at=timezone.now())

    # Get chat messages ready
    chat_messages = (
        Message.objects.filter(chat=chat)
        .order_by("date_created")
        .prefetch_related("answersource_set", "files")
    )
    for message in chat_messages:
        if message.is_bot:
            message.json = json.dumps(message.text)
        else:
            message.text = message.text.strip()

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
    except:
        chat.options = ChatOptions.objects.from_defaults(user=chat.user, chat=chat)
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
        Chat.objects.filter(user=request.user, messages__isnull=False)
        .exclude(pk=chat.id)
        .union(Chat.objects.filter(pk=chat.id))
        .order_by("-last_modification_date")
    )
    # Title chats in sidebar if necessary & set default labels
    llm = None
    for user_chat in user_chats:
        user_chat.current_chat = user_chat.id == chat.id
        if user_chat.title.strip() == "":
            if not llm:
                llm = OttoLLM()
            user_chat.title = title_chat(user_chat.id, llm=llm)
            if not user_chat.current_chat:
                user_chat.save()
    if llm:
        llm.create_costs()

    awaiting_response = request.GET.get("awaiting_response") == "True"

    # When a chat is created from outside Otto, we want to emulate the behaviour
    # of creating a new message - which returns an "awaiting_response" bot message
    if (
        awaiting_response
        and chat_messages
        and chat_messages.last().is_bot
        and not chat_messages.last().text
    ):
        response_init_message = {
            "is_bot": True,
            "awaiting_response": True,
            "id": chat_messages.last().id,
            "date_created": chat_messages.last().date_created
            + timezone.timedelta(seconds=1),
        }
        chat_messages = [chat_messages.first(), response_init_message]

    if not chat.options.qa_library or not request.user.has_perm(
        "librarian.view_library", chat.options.qa_library
    ):
        # The copy_options function fixes these issues
        copy_options(chat.options, chat.options)

    form = ChatOptionsForm(instance=chat.options, user=request.user)

    context = {
        "chat": chat,
        "options_form": form,
        "prompt": chat.options.prompt,
        "chat_messages": chat_messages,
        "hide_breadcrumbs": True,
        "user_chats": user_chats,
        "mode": mode,
        "chat_history_sections": get_chat_history_sections(user_chats),
        "has_tour": True,
        "tour_name": _("AI Assistant"),
        "force_tour": not request.user.ai_assistant_tour_completed,
        "tour_skippable": request.user.is_admin
        or request.user.ai_assistant_tour_completed,
        "start_tour": request.GET.get("start_tour") == "true",
        "upload_form": UploadForm(prefix="chat"),
    }
    return render(request, "chat/chat.html", context=context)


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
    chat_bot_messages = Message.objects.filter(chat=chat, is_bot=True).order_by("id")
    if chat_bot_messages.exists():
        cache.set(f"stop_response_{chat_bot_messages.last().id}", True, timeout=60)

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

    if chat.options.mode == "chat":
        chat.options.mode = "qa"
        chat.save()
    logger.info("File upload initiated.", chat_id=chat_id, mode=chat.options.mode)
    user_message = Message.objects.create(
        chat=chat, text="", is_bot=False, mode=chat.options.mode
    )
    saved_files = form.save()
    for saved_file in saved_files:
        ChatFile.objects.create(
            message_id=user_message.id,
            filename=saved_file["filename"],
            saved_file=saved_file["saved_file"],
        )
    bot_name = get_model_name(chat.options) if not chat.options.mode == "qa" else ""
    response_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        mode=chat.options.mode,
        parent=user_message,
        bot_name=bot_name,
    )
    response = HttpResponse()
    if chat.options.mode == "qa":
        logger.debug("QA upload")
        response.write(change_mode_to_chat_qa(chat))
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
    response = HttpResponse(file, content_type=file_obj.saved_file.content_type)
    response["Content-Disposition"] = f"attachment; filename={file_obj.filename}"
    return response


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
        return feedback_message(request, message_id)

    return HttpResponse()


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_options(request, chat_id, action=None, preset_id=None):
    from django.contrib import messages

    """
    Save and load chat options.
    """

    chat = Chat.objects.get(id=chat_id)
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

        # Update the chat options with the preset options
        copy_options(preset.options, chat.options)

        chat_options_form = ChatOptionsForm(instance=chat.options, user=request.user)

        messages.success(request, _("Preset loaded successfully."), extra_tags="unique")

        return render(
            request,
            "chat/components/chat_options_accordion.html",
            {
                "options_form": chat_options_form,
                "preset_loaded": "true",
                "prompt": preset.options.prompt,
            },
        )
    elif action == "create_preset":
        if request.method == "POST":
            form = PresetForm(data=request.POST, user=request.user)

            if form.is_valid():
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

                accessible_to = form.cleaned_data.get("accessible_to", [])

                preset.save()

                # clear the accessible_to field if the user changes the sharing option to private
                if preset.sharing_option == "private" and len(accessible_to) > 0:
                    accessible_to = []

                preset.accessible_to.set(accessible_to)
                chat.loaded_preset = preset
                chat.save()

                messages.success(
                    request,
                    _("Preset saved successfully."),
                )

                if request.POST.get("make_default", False) == "True":
                    request.user.default_preset = preset
                    request.user.save()

                # Show the user any relevant messages about changes to library privacy
                return library_access(
                    request, preset, preset.options.qa_library, action
                )

        return HttpResponse(status=500)
    elif action == "update_preset":
        preset = get_object_or_404(Preset, id=preset_id)
        old_library = preset.options.qa_library
        copy_options(chat.options, preset.options)
        preset.options.prompt = request.POST.get("prompt", "")
        preset.options.save()
        messages.success(
            request,
            _("Preset updated successfully."),
        )

        # Show the user any relevant messages about changes to library privacy
        return library_access(
            request,
            preset,
            preset.options.qa_library,
            action,
            (old_library if old_library != preset.options.qa_library else None),
        )
    elif action == "delete_preset":
        # check each chat instance of the user to see if the preset is loaded
        for chat_instance in Chat.objects.filter(user=request.user):
            if chat_instance.loaded_preset and chat_instance.loaded_preset.id == int(
                preset_id
            ):
                chat_instance.loaded_preset = None
                chat_instance.save()
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

        chat_options_form = ChatOptionsForm(
            post_data, instance=chat_options, user=request.user
        )
        # Check for errors and print them to console
        if not chat_options_form.is_valid():
            logger.error(chat_options_form.errors)
            return HttpResponse(status=500)
        chat_options_form.save()
        # Return a simple success response
        return HttpResponse(status=200)

    else:
        return HttpResponse(status=500)


def library_access(request, preset, library, action, old_library=None):
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
            potential_new_viewers = list(
                User.objects.filter(
                    ~Q(library_roles__library=library) & Q(accessible_presets=preset)
                )
            )
            user_form = LibraryUsersForm(
                library=library,
                data={
                    "viewers": preset.accessible_to.union(
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

    return render(
        request,
        "chat/modals/presets/library_check.html",
        {"keep_open": message != "", "message": message.strip()},
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_list_item(request, chat_id, current_chat=None):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = bool(current_chat == "True")
    return render(
        request,
        "chat/components/chat_list_item.html",
        {
            "chat": chat,
            "section_index": label_section_index(chat.last_modification_date),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def rename_chat(request, chat_id, current_chat=None):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = bool(current_chat == "True")

    if request.method == "POST":
        chat_rename_form = ChatRenameForm(request.POST)
        if chat_rename_form.is_valid():
            chat.title = chat_rename_form.cleaned_data["title"]
            # we keep the old last change date since the button will still be displayed in the old section until the next reload
            old_last_modification_date = chat.last_modification_date
            chat.last_modification_date = timezone.now()
            chat.save()

            context = {
                "chat": chat,
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
            "section_index": label_section_index(chat.last_modification_date),
        },
    )


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
    # Precompute sharing and language for each preset for use in the template
    for preset in presets:
        if preset.sharing_option == "others" and preset.owner != request.user:
            preset.sharing_option = "shared_with_me"
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
            "chat": Chat.objects.get(id=chat_id),
            "user": request.user,
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def save_preset(request, chat_id):

    chat = Chat.objects.get(id=chat_id)
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
    preset = get_object_or_404(Preset, id=preset_id)
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
        error_id = str(uuid.uuid4())[:7]
        response_str = (
            _("An error occurred while setting the default preset.")
            + f" _({_('Error ID:')} {error_id})_"
        )
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
    chat = Chat.objects.get(id=chat_id)
    original_library = chat.options.qa_library
    library = Library.objects.filter(id=library_id).first()
    # If library doesn't exist, or user doesn't have access to it, reset to default library
    if not library or not request.user.has_perm("librarian.view_library", library):
        library = Library.objects.get_default_library()
    chat.options.qa_library = library
    if library != original_library:
        chat.options.qa_data_sources.clear()
        chat.options.qa_documents.clear()
    chat.options.save()
    # Now return the updated chat options form for swapping
    return render(
        request,
        "chat/components/chat_options_accordion.html",
        {
            "options_form": ChatOptionsForm(instance=chat.options, user=request.user),
            "preset_loaded": "true",
            "trigger_library_change": "true" if library != original_library else None,
        },
    )


@require_POST
def generate_prompt_view(request):
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
