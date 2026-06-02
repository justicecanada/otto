from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from . import responses, views
from ._views.context import context_autocomplete
from ._views.modal_shell import (
    modal_librarian_data_source,
    modal_librarian_document,
    modal_librarian_library,
    modal_libraries,
)
from ._views.settings import chat_model_selector, settings_modal
from ._views.skills import (
    add_skill,
    copy_skill,
    create_skill,
    delete_skill,
    edit_skill,
    get_skills,
    import_skill,
    remove_skill,
    skill_import_status,
    skill_upload,
    suggest_tags,
    tag_autocomplete,
    toggle_featured,
    toggle_skill,
)

app_name = "chat_next"


urlpatterns = [
    path("", views.new_chat, name="new_chat"),
    # Compatibility route: chat_next is chat-only, so chat-with-ai behaves like new_chat.
    path("chat-with-ai/", views.new_chat, name="chat_with_ai"),
    path("id/<str:chat_id>/", views.chat, name="chat"),
    path("id/<str:chat_id>/upload", views.save_upload, name="upload"),
    path(
        "id/<str:chat_id>/delete/<str:current_chat>",
        views.delete_chat,
        name="delete_chat",
    ),
    path("delete_all_chats/", views.delete_all_chats, name="delete_all_chats"),
    path("id/<str:chat_id>/message/", views.chat_message, name="chat_message"),
    path(
        "message/<int:message_id>/delete/", views.delete_message, name="delete_message"
    ),
    path(
        "message/<int:message_id>/edit/",
        views.edit_message,
        name="edit_message",
    ),
    path(
        "message/<int:message_id>/response/",
        responses.otto_response,
        name="chat_response",
    ),
    path(
        "message/<int:message_id>/approval/",
        responses.handle_approval,
        name="handle_approval",
    ),
    path(
        "message/<int:message_id>/approval/all/",
        responses.handle_approval_all,
        name="handle_approval_all",
    ),
    path(
        "message/<int:message_id>/approval/stream/",
        responses.approval_stream,
        name="approval_stream",
    ),
    path(
        "message/<int:message_id>/response/stop/",
        responses.stop_response,
        name="stop_response",
    ),
    path(
        "message/<int:message_id>/html/",
        views.get_message_html,
        name="get_message_html",
    ),
    path(
        "message/<int:message_id>/tool_output/<int:step_index>/",
        views.message_tool_output,
        name="message_tool_output",
    ),
    path(
        "message/<int:message_id>/translate_processing_steps/",
        views.translate_processing_steps,
        name="translate_processing_steps",
    ),
    path(
        "message/<int:message_id>/library_status/",
        views.library_status,
        name="library_status",
    ),
    path(
        "message/<int:message_id>/task_status/",
        views.bot_task_status,
        name="bot_task_status",
    ),
    path("file/<int:file_id>/", views.download_file, name="download_file"),
    path("file/<int:file_id>/inline/", views.inline_file, name="inline_file"),
    path("file/<int:file_id>/preview/", views.preview_file, name="preview_file"),
    path(
        "thumbs-feedback/<int:message_id>/<str:feedback>",
        views.thumbs_feedback,
        name="thumbs_feedback",
    ),
    path(
        "rerun_prompt/<int:message_id>/",
        views.rerun_prompt,
        name="rerun_prompt",
    ),
    path(
        "id/<str:current_chat_id>/rename/<str:chat_id>/",
        views.rename_chat,
        name="rename_chat",
    ),
    path(
        "id/<str:chat_id>/share/",
        views.share_chat,
        name="share_chat",
    ),
    path(
        "id/<str:current_chat_id>/list_item/<str:chat_id>",
        views.chat_list_item,
        name="chat_list_item",
    ),
    path(
        "id/<str:current_chat_id>/refresh_titles/",
        views.refresh_chat_titles,
        name="refresh_chat_titles",
    ),
    path("id/<str:chat_id>/email_author/", views.email_author, name="email_author"),
    path(
        "id/<str:current_chat_id>/pin_chat/<str:chat_id>/",
        views.pin_chat,
        name="pin_chat",
    ),
    path(
        "id/<str:current_chat_id>/unpin_chat/<str:chat_id>/",
        views.unpin_chat,
        name="unpin_chat",
    ),
    path("search/", views.search_chats, name="search_chats"),
    path(
        "id/<str:chat_id>/context_autocomplete/",
        context_autocomplete,
        name="context_autocomplete",
    ),
    # Skills
    path("id/<str:chat_id>/skills/", get_skills, name="get_skills"),
    path("id/<str:chat_id>/skills/create/", create_skill, name="create_skill"),
    path("id/<str:chat_id>/skills/import/", import_skill, name="import_skill"),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/edit/",
        edit_skill,
        name="edit_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/import_status/",
        skill_import_status,
        name="skill_import_status",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/add/",
        add_skill,
        name="add_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/remove/",
        remove_skill,
        name="remove_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/delete/",
        delete_skill,
        name="delete_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/copy/",
        copy_skill,
        name="copy_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/toggle/",
        toggle_skill,
        name="toggle_skill",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/toggle_featured/",
        toggle_featured,
        name="toggle_featured",
    ),
    path(
        "id/<str:chat_id>/skills/tags/autocomplete/",
        tag_autocomplete,
        name="tag_autocomplete",
    ),
    path(
        "id/<str:chat_id>/skills/tags/suggest/",
        suggest_tags,
        name="suggest_tags",
    ),
    path(
        "id/<str:chat_id>/skills/<int:skill_id>/upload/",
        skill_upload,
        name="skill_upload",
    ),
    path("id/<str:chat_id>/modal/libraries/", modal_libraries, name="modal_libraries"),
    path(
        "id/<str:chat_id>/modal/libraries/library/<int:library_id>/",
        modal_librarian_library,
        name="modal_librarian_library",
    ),
    path(
        "id/<str:chat_id>/modal/libraries/data_source/<int:data_source_id>/",
        modal_librarian_data_source,
        name="modal_librarian_data_source",
    ),
    path(
        "id/<str:chat_id>/modal/libraries/document/<int:document_id>/",
        modal_librarian_document,
        name="modal_librarian_document",
    ),
    # Settings modal
    path("id/<str:chat_id>/settings/", settings_modal, name="settings_modal"),
    path(
        "id/<str:chat_id>/settings/model-selector/",
        chat_model_selector,
        name="chat_model_selector",
    ),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
