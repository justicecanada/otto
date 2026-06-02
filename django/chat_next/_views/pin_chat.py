from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import get_language

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import permission_required

from chat_next.models import Chat
from chat_next.utils import annotate_pending_titles, get_chat_history_sections

app_name = "chat_next"
logger = get_logger(__name__)


def chat_list_response(request: HttpRequest, chat: Chat) -> HttpResponse:
    user_chats = (
        Chat.objects.filter(user=request.user, messages__isnull=False)
        .exclude(pk=chat.id)
        .union(Chat.objects.filter(pk=chat.id))
        .order_by("-last_modification_date")
    )

    user_chats_list = list(user_chats)
    if user_chats_list:
        chat_ids = [c.id for c in user_chats_list]
        message_counts = dict(
            Chat.objects.filter(id__in=chat_ids)
            .annotate(msg_count=Count("messages"))
            .values_list("id", "msg_count")
        )
        for user_chat in user_chats_list:
            user_chat.message_count = message_counts.get(user_chat.id, 0)

    for user_chat in user_chats_list:
        user_chat.current_chat = user_chat.id == chat.id

    annotate_pending_titles(
        user_chats_list,
        language=(getattr(request, "LANGUAGE_CODE", None) or get_language() or "")[:2],
    )
    chat_history_sections = get_chat_history_sections(user_chats_list)
    return render(
        request,
        "chat_next/components/chat_history_sidebar.html",
        {"chat_history_sections": chat_history_sections, "chat": chat},
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def pin_chat(request, chat_id, current_chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    chat.pinned = True
    chat.save(update_fields=["pinned"])
    logger.info("Chat pinned.", chat_id=chat_id)
    if request.headers.get("HX-Request") == "true":
        current_chat = get_object_or_404(Chat, id=current_chat_id)
        return chat_list_response(request, current_chat)
    return HttpResponse(status=200)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def unpin_chat(request, chat_id, current_chat_id):
    chat = get_object_or_404(Chat, id=chat_id)
    if chat.pinned:
        chat.pinned = False
        chat.save(update_fields=["pinned"])
        logger.info("Chat unpinned.", chat_id=chat_id)

    if request.headers.get("HX-Request") == "true":
        current_chat = get_object_or_404(Chat, id=current_chat_id)
        return chat_list_response(request, current_chat)
    return HttpResponse(status=200)
