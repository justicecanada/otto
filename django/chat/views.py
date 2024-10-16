from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.forms.models import model_to_dict
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.forms import ChatOptionsForm, ChatRenameForm
from chat.llm import OttoLLM
from chat.metrics.activity_metrics import (
    chat_new_session_started_total,
    chat_request_type_total,
    chat_session_restored_total,
)
from chat.metrics.feedback_metrics import (
    chat_negative_feedback_total,
    chat_positive_feedback_total,
)
from chat.models import Chat, ChatFile, ChatOptions, Message, create_chat_data_source
from chat.utils import change_mode_to_chat_qa, llm_response_to_html, title_chat
from librarian.models import Library, SavedFile
from otto.models import SecurityLabel
from otto.utils.decorators import (
    app_access_required,
    budget_required,
    permission_required,
)
from otto.views import message_feedback

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

    # Usage metrics
    chat_new_session_started_total.labels(user=request.user.upn, mode=mode).inc()

    return redirect("chat:chat", chat_id=empty_chat.id)


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

    # delete all chats for the user
    chat = Chat.objects.filter(user=request.user)
    chat.delete()

    logger.info("all chats deleted")

    # redirect user to new chat
    response = HttpResponse()
    response["HX-Redirect"] = reverse("chat:new_chat")
    return response


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat(request, chat_id):
    """
    Get or create the chat based on the provided chat ID
    """

    logger.info("Chat session retrieved.", chat_id=chat_id)
    bind_contextvars(feature="chat")

    chat = (
        Chat.objects.filter(id=chat_id)
        .prefetch_related("options", "data_source")
        .first()
    )

    chat.accessed_at = timezone.now()
    chat.save()

    # Insurance code to ensure we have ChatOptions, DataSource, and Personal Library
    try:
        chat.options
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

    # Get chat messages ready
    messages = Message.objects.filter(chat=chat).order_by("id")
    for message in messages:
        if message.is_bot:
            message.text = llm_response_to_html(message.text)
        else:
            message.text = message.text.strip()

    # Get sidebar chat history list.
    # Don't show empty chats - these will be deleted automatically later.
    # The current chat is always shown, even if it's empty.
    user_chats = (
        Chat.objects.filter(user=request.user, messages__isnull=False)
        .exclude(pk=chat.id)
        .union(Chat.objects.filter(pk=chat.id))
        .order_by("-created_at")
    )
    # Title chats in sidebar if necessary & set default labels
    llm = None
    for user_chat in user_chats:
        user_chat.current_chat = user_chat.id == chat.id
        if user_chat.title.strip() == "":
            if not llm:
                llm = OttoLLM("gpt-35")
            user_chat.title = title_chat(user_chat.id, llm=llm)
            if not user_chat.current_chat:
                user_chat.save()
        if not user_chat.security_label:
            user_chat.security_label_id = SecurityLabel.default_security_label().id
            user_chat.save()
    if llm:
        llm.create_costs()

    # Usage metrics
    awaiting_response = request.GET.get("awaiting_response") == "True"
    if len(messages) > 0 and not awaiting_response:
        chat_session_restored_total.labels(user=request.user.upn, mode=mode)

    # When a chat is created from outside Otto, we want to emulate the behaviour
    # of creating a new message - which returns an "awaiting_response" bot message
    if (
        awaiting_response
        and messages
        and messages.last().is_bot
        and not messages.last().text
    ):
        response_init_message = {
            "is_bot": True,
            "awaiting_response": True,
            "id": messages.last().id,
            "date_created": messages.last().date_created
            + timezone.timedelta(seconds=1),
        }
        messages = [messages.first(), response_init_message]

    # If ChatOptions has an invalid library or data source, remove them
    if not chat.options.qa_library:
        chat.options.qa_library = Library.objects.get_default_library()
        chat.options.save()
    form = ChatOptionsForm(instance=chat.options, user=request.user)
    context = {
        "chat": chat,
        "options_form": form,
        "option_presets": ChatOptions.objects.filter(user=request.user),
        "chat_messages": messages,
        "hide_breadcrumbs": True,
        "user_chats": user_chats,
        "mode": mode,
        "security_labels": SecurityLabel.objects.all(),
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
    adding_url_to_qa = False
    if mode == "qa":
        url_validator = URLValidator()
        try:
            url_validator(user_message_text)
            adding_url_to_qa = True
        except ValidationError:
            pass

    user_message = Message.objects.create(
        chat=chat, text=user_message_text, is_bot=False, mode=mode
    )
    response_message = Message.objects.create(
        chat=chat, text="", is_bot=True, mode=mode, parent=user_message
    )
    # usage metrics
    chat_request_type_total.labels(user=request.user.upn, type=mode).inc()

    # This tells the frontend to display the 3 dots and initiate the streaming response
    response_init_message = {
        "is_bot": True,
        "awaiting_response": True,
        "id": response_message.id,
        "date_created": response_message.date_created + timezone.timedelta(seconds=1),
    }
    context = {
        "chat_messages": [
            user_message,
            response_init_message,
        ],
        "mode": mode,
    }
    response = HttpResponse()
    response.write(render_to_string("chat/components/chat_messages.html", context))
    if adding_url_to_qa:
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


@require_GET
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@budget_required
def init_upload(request, chat_id):
    """
    Creates a file upload progress message in the chat and initiates the file upload
    """
    chat = Chat.objects.get(id=chat_id)
    mode = chat.options.mode
    if mode == "chat":
        mode = "qa"
    # Create the user's message in database
    logger.info("File upload initiated.", chat_id=chat_id, mode=mode)
    message = Message.objects.create(chat=chat, text="", is_bot=False, mode=mode)
    message.save()
    context = {
        "chat_messages": [
            message,
        ],
        "mode": mode,
        "file_upload": True,
    }
    return render(request, "chat/components/chat_messages.html", context=context)


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def done_upload(request, message_id):
    """
    Creates a "files uploaded" message in the chat and initiates the response
    """
    user_message = Message.objects.get(id=message_id)
    mode = user_message.mode
    logger.info("File upload completed.", message_id=message_id, mode=mode)
    response_message = Message.objects.create(
        chat=user_message.chat, text="", is_bot=True, mode=mode, parent=user_message
    )
    chat = user_message.chat
    response = HttpResponse()

    if mode == "translate":
        # usage metrics
        chat_request_type_total.labels(
            user=request.user.upn, type="document translation"
        )
    if mode == "summarize":
        # usage metrics
        chat_request_type_total.labels(user=request.user.upn, type="text summarization")

    if mode == "qa":
        # usage metrics
        print("QA upload")
        chat_request_type_total.labels(user=request.user.upn, type="qa upload")
        response.write(change_mode_to_chat_qa(chat))

    response_init_message = {
        "is_bot": True,
        "awaiting_response": True,
        "id": response_message.id,
        "date_created": user_message.date_created + timezone.timedelta(seconds=1),
    }
    context = {
        "chat_messages": [
            user_message,
            response_init_message,
        ],
        "mode": mode,
    }
    response.write(
        render_to_string("chat/components/chat_messages.html", context=context)
    )
    response.write("<script>scrollToBottom(false, true);</script>")
    return response


@require_POST
@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def chunk_upload(request, message_id):
    """
    Returns JSON for the file upload progress
    Based on https://github.com/shubhamkshatriya25/Django-AJAX-File-Uploader
    """
    hash = request.POST["hash"]
    existing_file = SavedFile.objects.filter(sha256_hash=hash).first()

    file = request.FILES["file"].read()
    content_type = request.POST["content_type"]
    fileName = request.POST["filename"]
    file_id = request.POST["file_id"]
    end = request.POST["end"]
    nextSlice = request.POST["nextSlice"]

    if file == "" or fileName == "" or file_id == "" or end == "" or nextSlice == "":
        return JsonResponse({"data": "Invalid Request"})
    else:
        if file_id == "null":
            chat_file_arguments = dict(
                message_id=message_id,
                filename=fileName,
            )
            if existing_file:
                chat_file_arguments.update(saved_file=existing_file)
            else:
                chat_file_arguments.update(content_type=content_type, eof=int(end))
            file_obj = ChatFile.objects.create(**chat_file_arguments)
            if not existing_file:
                file_obj.saved_file.file.save(fileName, request.FILES["file"])
            if int(end) or existing_file:
                file_obj.saved_file.generate_hash()
                return JsonResponse(
                    {"data": "Uploaded successfully", "file_id": file_obj.id}
                )
            else:
                return JsonResponse({"file_id": file_obj.id})
        else:
            file_obj = ChatFile.objects.get(id=file_id)
            if not file_obj or file_obj.saved_file.eof:
                return JsonResponse({"data": "Invalid Request"})
            # Append the chunk to the file with write mode ab+
            with open(file_obj.saved_file.file.path, "ab+") as f:
                f.seek(int(nextSlice))
                f.write(file)
            file_obj.saved_file.eof = int(end)
            file_obj.save()
            if int(end):
                return JsonResponse(
                    {
                        "data": "Uploaded successfully",
                        "file_id": file_obj.id,
                    }
                )
            else:
                return JsonResponse({"file_id": file_obj.id})


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
        if feedback:
            chat_positive_feedback_total.labels(
                user=request.user.upn, message=message_id
            ).inc()
        else:
            chat_negative_feedback_total.labels(
                user=request.user.upn, message=message_id
            ).inc()
    except Exception as e:
        # TODO: handle error
        logger.error("An error occured while providing a chat feedback.", error=e)

    if feedback == -1:
        return message_feedback(request, message_id)

    return HttpResponse()


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_options(request, chat_id, action=None):
    """
    Save and load chat options.
    """

    def _copy_options(source_options, target_options):
        source_options = model_to_dict(source_options)
        # Remove the fields that are not part of the preset
        for field in ["id", "user", "preset_name", "chat"]:
            source_options.pop(field)
        # Update the preset options with the dictionary
        fk_fields = ["qa_library"]
        m2m_fields = ["qa_data_sources", "qa_documents"]
        # Remove None values
        source_options = {k: v for k, v in source_options.items()}
        for key, value in source_options.items():
            if key in fk_fields:
                setattr(target_options, f"{key}_id", int(value) if value else None)
            elif key in m2m_fields:
                getattr(target_options, key).set(value)
            else:
                setattr(target_options, key, value)
        target_options.save()

    chat = Chat.objects.get(id=chat_id)
    if action in ["reset", "load_preset"]:
        if action == "reset":
            preset_options = ChatOptions.objects.from_defaults(user=request.user)
            logger.info("Resetting chat options to default.", chat_id=chat_id)
        else:
            # Check that the preset is not empty and exists
            preset_name = request.POST.get("option_presets")
            logger.info(
                "Loading chat options from a preset.",
                chat_id=chat_id,
                preset=preset_name,
            )
            if not preset_name:
                return HttpResponse(status=500)
            preset_options = ChatOptions.objects.filter(
                user=request.user, preset_name=preset_name
            ).first()
            if not preset_options:
                return HttpResponse(status=500)
        # Update the chat options with the default options
        chat_options = chat.options
        _copy_options(preset_options, chat_options)
        return render(
            request,
            "chat/components/chat_options_accordion.html",
            {
                "options_form": ChatOptionsForm(
                    instance=chat_options, user=request.user
                ),
                "preset_loaded": "true",
            },
        )
    elif action == "save_preset":
        # Save the current chat options as a preset
        preset_name = request.POST.get("option_presets")
        logger.info(
            "Saving chat options as a preset.", chat_id=chat_id, preset=preset_name
        )
        # Can't be blank
        if not preset_name:
            return HttpResponse(status=500)
        # Get or create the preset
        preset_options = ChatOptions.objects.filter(
            user=request.user, preset_name=preset_name
        ).first()
        if not preset_options:
            preset_options = ChatOptions.objects.create(
                user=request.user, preset_name=preset_name
            )
        _copy_options(chat.options, preset_options)
        # Replaces the preset dropdown with the saved one selected
        return render(
            request,
            "chat/components/options_preset_dropdown.html",
            {
                "option_presets": ChatOptions.objects.filter(user=chat.user),
                "selected_preset": preset_name,
            },
        )
    elif action == "delete_preset":
        # Delete the preset
        preset_name = request.POST.get("option_presets")
        logger.info(
            "Deleting chat options preset.", chat_id=chat_id, preset=preset_name
        )
        if not preset_name:
            return HttpResponse(status=500)
        ChatOptions.objects.filter(user=request.user, preset_name=preset_name).delete()
        # Replaces the preset dropdown with none selected
        return render(
            request,
            "chat/components/options_preset_dropdown.html",
            {"option_presets": ChatOptions.objects.filter(user=chat.user)},
        )
    elif request.method == "POST":
        chat_options = chat.options
        chat_options_form = ChatOptionsForm(
            request.POST, instance=chat_options, user=request.user
        )
        # Check for errors and print them to console
        if not chat_options_form.is_valid():
            logger.error(chat_options_form.errors)
            return HttpResponse(status=500)
        chat_options_form.save()

        # Replaces the preset dropdown with none selected
        return render(
            request,
            "chat/components/options_preset_dropdown.html",
            {"option_presets": ChatOptions.objects.filter(user=chat.user)},
        )
    return HttpResponse(status=500)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def chat_list_item(request, chat_id, current_chat=None):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = bool(current_chat == "True")
    return render(
        request,
        "chat/components/chat_list_item.html",
        {"chat": chat},
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def rename_chat(request, chat_id, current_chat=None):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.current_chat = bool(current_chat == "True")

    if request.method == "POST":
        chat_rename_form = ChatRenameForm(request.POST)
        if chat_rename_form.is_valid():
            chat.title = chat_rename_form.cleaned_data["title"]
            chat.save()
            return render(
                request,
                "chat/components/chat_list_item.html",
                {"chat": chat},
            )
        else:
            return render(
                request,
                "chat/components/chat_list_item_title_edit.html",
                {"form": chat_rename_form, "chat": chat},
            )

    chat_rename_form = ChatRenameForm(data={"title": chat.title})
    return render(
        request,
        "chat/components/chat_list_item_title_edit.html",
        {"form": chat_rename_form, "chat": chat},
    )


# AC-16 & AC-16(2): Allows for the modification of security labels associated with chat sessions
@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def set_security_label(request, chat_id, security_label_id):
    logger.info(
        "Setting security label for chat.",
        chat_id=chat_id,
        security_label_id=security_label_id,
    )
    chat = Chat.objects.get(id=chat_id)
    chat.security_label_id = security_label_id
    chat.save()
    return render(
        request,
        "chat/components/chat_security_label.html",
        {"chat": chat, "security_labels": SecurityLabel.objects.all()},
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def message_sources(request, message_id):
    message = Message.objects.get(id=message_id)
    return render(
        request,
        "chat/modals/sources_modal_inner.html",
        {"message": message, "sources": message.sources},
    )
