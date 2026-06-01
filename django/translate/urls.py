from django.urls import path

from . import views

app_name = "translate"

urlpatterns = [
    path("", views.index, name="index"),
    path("translate_document/", views.translate_document, name="translate_document"),
    path("translate_text/", views.translate_text, name="translate_text"),
    path("poll/<uuid:user_request_id>/", views.poll_tasks, name="poll_tasks"),
    path("download/<uuid:file_id>/", views.download_document, name="download_document"),
]
