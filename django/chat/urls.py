from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from . import responses, views

app_name = "chat"


urlpatterns = [
    path("", views.new_chat, name="new_chat"),
    path("chat-with-ai/", views.new_chat_with_ai, name="chat_with_ai"),
    path("summarize/", views.new_summarize, name="summarize"),
    path("translate/", views.new_translate, name="translate"),
    path("qa/", views.new_qa, name="qa"),
    path("document-qa/", views.new_document_qa, name="document_qa"),
    path("api/qa/", views.api_qa, name="api_qa"),
    path("id/<str:chat_id>/", views.chat, name="chat"),
    path("id/<str:chat_id>/upload/", views.init_upload, name="init_upload"),
    path(
        "id/<str:chat_id>/delete/<str:current_chat>",
        views.delete_chat,
        name="delete_chat",
    ),
    path("delete_all_chats/", views.delete_all_chats, name="delete_all_chats"),
    path("id/<str:chat_id>/message/", views.chat_message, name="chat_message"),
    path(
        "message/<int:message_id>/response/",
        responses.otto_response,
        name="chat_response",
    ),
    path(
        "message/<int:message_id>/response/stop/",
        responses.stop_response,
        name="stop_response",
    ),
    path(
        "message/<int:message_id>/sources/",
        views.message_sources,
        name="message_sources",
    ),
    path("message/<int:message_id>/upload/", views.chunk_upload, name="chunk_upload"),
    path("message/<int:message_id>/upload/done", views.done_upload, name="done_upload"),
    path("file/<int:file_id>/", views.download_file, name="download_file"),
    path(
        "thumbs-feedback/<int:message_id>/<str:feedback>",
        views.thumbs_feedback,
        name="thumbs_feedback",
    ),
    path("get_data_sources/", views.get_data_sources, name="get_data_sources"),
    path("id/<str:chat_id>/options/", views.chat_options, name="chat_options"),
    path(
        "id/<str:chat_id>/options/preset/<str:action>",
        views.chat_options,
        name="chat_options",
    ),
    path(
        "id/<str:chat_id>/options/preset/<str:action>/<str:preset_id>",
        views.chat_options,
        name="chat_options",
    ),
    path(
        "id/<str:chat_id>/set_security_label/<str:security_label_id>",
        views.set_security_label,
        name="set_security_label",
    ),
    path(
        "id/<str:chat_id>/rename/<str:current_chat>",
        views.rename_chat,
        name="rename_chat",
    ),
    path(
        "id/<str:chat_id>/list_item/<str:current_chat>",
        views.chat_list_item,
        name="chat_list_item",
    ),
    path(
        "id/<str:chat_id>/options/qa_accordion/<int:library_id>",
        views.get_qa_accordion,
        name="qa_accordion",
    ),
    path(
        "id/<str:chat_id>/options/presets/",
        views.get_presets,
        name="get_presets",
    ),
    path(
        "presets/<str:preset_id>/favourite/",
        views.set_preset_favourite,
        name="set_preset_favourite",
    ),
    path(
        "id/<str:chat_id>/options/save_preset",
        views.save_preset,
        name="save_preset",
    ),
    path(
        "chat/<str:chat_id>/options/save_preset/<str:preset_id>/",
        views.save_preset,
        name="update_preset",
    ),
    path(
        "id/<str:chat_id>/options/presets/<str:preset_id>/edit/",
        views.edit_preset,
        name="edit_preset",
    ),
    path(
        "id/<str:chat_id>/options/presets/form/",
        views.create_preset,
        name="create_preset",
    ),
    path(
        "presets/<int:preset_id>/default/<str:chat_id>",
        views.set_preset_default,
        name="set_preset_default",
    ),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
