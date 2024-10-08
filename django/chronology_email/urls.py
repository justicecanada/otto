from django.urls import path

from .views import email_upload_view

app_name = "chronology_email"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload/", email_upload_view, name="email-upload"),
]
