from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter
from structlog import get_logger

from chat.llm import OttoLLM
from chat.models import Chat
from chat.utils import get_chat_history_sections, title_chat
from otto.utils.decorators import permission_required

app_name = "chat"
logger = get_logger(__name__)


def chat_list_response(request: HttpRequest, chat: Chat) -> HttpResponse:
    user_chats = (
        Chat.objects.filter(user=request.user, messages__isnull=False)
        .exclude(pk=chat.id)
        .union(Chat.objects.filter(pk=chat.id))
        .order_by("-last_modification_date")
    )
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
    chat_history_sections = get_chat_history_sections(user_chats)
    return render(
        request,
        "chat/components/chat_history_sidebar.html",
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
