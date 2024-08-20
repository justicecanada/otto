from django.urls import path

from librarian.views import (
    document_start,
    document_stop,
    download_document,
    modal_create_data_source,
    modal_create_document,
    modal_create_library,
    modal_delete_data_source,
    modal_delete_document,
    modal_delete_library,
    modal_edit_data_source,
    modal_edit_document,
    modal_edit_library,
    modal_library_list,
    modal_manage_library_users,
    poll_status,
    upload,
)

app_name = "librarian"
urlpatterns = [
    path("modal/", modal_library_list, name="modal"),
    path("modal/library/create/", modal_create_library, name="modal_create_library"),
    path(
        "modal/library/<int:library_id>/edit/",
        modal_edit_library,
        name="modal_edit_library",
    ),
    path(
        "modal/library/<int:library_id>/delete/",
        modal_delete_library,
        name="modal_delete_library",
    ),
    path(
        "modal/library/<int:library_id>/users/",
        modal_manage_library_users,
        name="modal_manage_library_users",
    ),
    path(
        "modal/library/<int:library_id>/data_source/create/",
        modal_create_data_source,
        name="modal_create_data_source",
    ),
    path(
        "modal/data_source/<int:data_source_id>/edit/",
        modal_edit_data_source,
        name="modal_edit_data_source",
    ),
    path(
        "modal/data_source/<int:data_source_id>/delete/",
        modal_delete_data_source,
        name="modal_delete_data_source",
    ),
    path(
        "modal/data_source/<int:data_source_id>/document/create/",
        modal_create_document,
        name="modal_create_document",
    ),
    path(
        "modal/document/<int:document_id>/edit/",
        modal_edit_document,
        name="modal_edit_document",
    ),
    path(
        "modal/document/<int:document_id>/delete/",
        modal_delete_document,
        name="modal_delete_document",
    ),
    # Polling for status updates
    path(
        "modal/data_source/<int:data_source_id>/status/",
        poll_status,
        name="data_source_status",
    ),
    path(
        "modal/data_source/<int:data_source_id>/document/<int:document_id>/status/",
        poll_status,
        name="document_status",
    ),
    # Document upload and processing
    path("modal/upload/to/<int:data_source_id>/", upload, name="upload"),
    path("document/<int:document_id>/start/", document_start, name="document_start"),
    path("document/<int:document_id>/stop/", document_stop, name="document_stop"),
    path(
        "document/<int:document_id>/download/",
        download_document,
        name="download_document",
    ),
]
