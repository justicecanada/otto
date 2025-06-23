from django.urls import path

from librarian.views import (
    data_source_start,
    data_source_stop,
    direct_upload,
    document_start,
    document_stop,
    document_text,
    download_document,
    email_library_admins,
    modal_create_data_source,
    modal_create_document,
    modal_create_library,
    modal_delete_data_source,
    modal_delete_document,
    modal_delete_library,
    modal_edit_library,
    modal_library_list,
    modal_manage_library_users,
    modal_view_data_source,
    modal_view_document,
    poll_status,
    upload,
)

app_name = "librarian"
urlpatterns = [
    path("modal/", modal_library_list, name="modal_library_list"),
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
        modal_view_data_source,
        name="modal_view_data_source",
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
        modal_view_document,
        name="modal_view_document",
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
    path(
        "modal/upload/to/<int:data_source_id>/direct/",
        direct_upload,
        name="direct_upload",
    ),
    path(
        "document/<int:document_id>/start/<str:pdf_method>",
        document_start,
        name="document_start",
    ),
    path("document/<int:document_id>/stop/", document_stop, name="document_stop"),
    # Batch document stop / processing (per data source)
    path(
        "data_source/<int:data_source_id>/stop/",
        data_source_stop,
        name="data_source_stop",
    ),
    path(
        "data_source/<int:data_source_id>/start/<str:pdf_method>/<str:scope>",
        data_source_start,
        name="data_source_start",
    ),
    path(
        "document/<int:document_id>/download/",
        download_document,
        name="download_document",
    ),
    path(
        "document/<int:document_id>/text/",
        document_text,
        name="document_text",
    ),
    path(
        "library/<int:library_id>/email_admins/",
        email_library_admins,
        name="email_library_admins",
    ),
]
