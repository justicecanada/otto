from django.urls import path

from . import views

app_name = "transcriber"

urlpatterns = [
    path("", views.index, name="index"),
    path(
        "generate_notes/",
        views.generate_meeting_notes,
        name="generate_notes",
    ),
    path("handle_translation/", views.handle_translation, name="handle_translation"),
    path("upload/", views.handle_upload, name="upload"),
    path("handle_cleanup/", views.handle_cleanup, name="handle_cleanup"),
    path("add_to_library", views.add_to_library, name="add_to_library"),
    path(
        "open_transcript_chat/",
        views.open_transcript_chat,
        name="open_transcript_chat",
    ),
    path("loading_url/", views.loading_url, name="loading_url"),
]
