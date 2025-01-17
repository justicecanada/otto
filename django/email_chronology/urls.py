from django.urls import path

from . import views

app_name = "email_chronology"

urlpatterns = [
    path("", views.upload_emails, name="index"),
    path("delete_email/<uuid:email_id>/", views.delete_email, name="delete_email"),
    path(
        "add_emails_to_thread/<uuid:thread_id>/",
        views.add_emails_to_thread,
        name="add_emails_to_thread",
    ),
    path("delete_thread/<uuid:thread_id>/", views.delete_thread, name="delete_thread"),
]
