from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from . import views

app_name = "case_prep"


urlpatterns = [
    path("", views.index, name="index"),
    path("session/<str:session_id>/", views.session_detail, name="session_detail"),
    path("create_session/", views.create_session, name="create_session"),
    path(
        "delete_session/<str:session_id>/", views.delete_session, name="delete_session"
    ),
    path("delete_document/", views.delete_document, name="delete_document"),
    path("save_changes/", views.save_changes, name="save_changes"),
    path("upload_files/", views.upload_files, name="upload_files"),
    path(
        "generate_book_of_documents/",
        views.generate_book_of_documents,
        name="generate_book_of_documents",
    ),
    path(
        "download_book_of_documents/<str:session_id>/",
        views.download_book_of_documents,
        name="download_book_of_documents",
    ),
    path(
        "download_document/<str:document_id>/",
        views.download_document,
        name="download_document",
    ),
    path(
        "download_documents/",
        views.download_documents,
        name="download_documents",
    ),
    path(
        "toggle_document_visibility/",
        views.toggle_document_visibility,
        name="toggle_document_visibility",
    ),
    path(
        "create_table_of_contents",
        views.create_table_of_contents,
        name="create_table_of_contents",
    ),
    path(
        "upvote_feature/<str:feature_handle>",
        views.upvote_feature,
        name="upvote_feature",
    ),
    path(
        "summarize_feature/",
        views.summarize_feature,
        name="summarize_feature",
    ),
    path("translate_feature/", views.translate_feature, name="translate_feature"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
