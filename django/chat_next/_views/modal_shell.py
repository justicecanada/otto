from urllib.parse import urlencode

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.rules import can_edit_skill
from otto.utils.decorators import permission_required

from librarian.models import DataSource, Document, Library

from chat_next.models import Chat


def _append_query_params(url, params):
    filtered = {key: value for key, value in params.items() if value not in (None, "")}
    if not filtered:
        return url
    return f"{url}?{urlencode(filtered, doseq=True)}"


def _render_librarian_modal_content(
    request, chat_id, loader_url, *, title, modal_back_url=""
):
    return render(
        request,
        "chat_next/modals/shared/librarian_content.html",
        {
            "chat_id": chat_id,
            "loader_url": loader_url,
            "title": title,
            "modal_back_url": modal_back_url,
            "current_url": request.get_full_path(),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def modal_libraries(request, chat_id):
    return _render_librarian_modal_content(
        request,
        chat_id,
        reverse("librarian:modal_library_list"),
        title=_("Libraries"),
        modal_back_url=request.GET.get("modal_back_url", ""),
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def modal_librarian_library(request, chat_id, library_id):
    library = get_object_or_404(Library, id=library_id)
    if not request.user.has_perm("librarian.view_library", library):
        return HttpResponse(status=403)
    return _render_librarian_modal_content(
        request,
        chat_id,
        reverse("librarian:modal_view_library", args=[library_id]),
        title=_("Libraries"),
        modal_back_url=request.GET.get("modal_back_url", ""),
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def modal_librarian_data_source(request, chat_id, data_source_id):
    data_source = get_object_or_404(
        DataSource.objects.select_related("skill", "library"), id=data_source_id
    )
    if data_source.skill_id:
        if not can_edit_skill(request.user, data_source.skill):
            return HttpResponse(status=403)
    elif not request.user.has_perm("librarian.view_data_source", data_source):
        return HttpResponse(status=403)
    return _render_librarian_modal_content(
        request,
        chat_id,
        _append_query_params(
            reverse("librarian:modal_view_data_source", args=[data_source_id]),
            {
                "search": request.GET.get("search", ""),
                "page": request.GET.get("page", ""),
            },
        ),
        title=_("Libraries"),
        modal_back_url=request.GET.get("modal_back_url", ""),
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def modal_librarian_document(request, chat_id, document_id):
    document = get_object_or_404(
        Document.objects.select_related("data_source", "data_source__library"),
        id=document_id,
    )
    if document.data_source.skill_id:
        if not can_edit_skill(request.user, document.data_source.skill):
            return HttpResponse(status=403)
    elif not request.user.has_perm("librarian.view_document", document):
        return HttpResponse(status=403)
    return _render_librarian_modal_content(
        request,
        chat_id,
        reverse("librarian:modal_view_document", args=[document_id]),
        title=_("Libraries"),
        modal_back_url=request.GET.get("modal_back_url", ""),
    )
