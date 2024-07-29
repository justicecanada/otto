from django.urls import path

from . import views

app_name = "text_extractor"

urlpatterns = [
    path("", views.index, name="index"),
    path("submit_document/", views.submit_document, name="submit_document"),
    path(
        "download_document/<str:file_id>/<str:user_request_id>",
        views.download_document,
        name="download_document",
    ),
]
