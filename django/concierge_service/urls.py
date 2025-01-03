from django.urls import path

from . import views

app_name = "concierge_service"

urlpatterns = [
    path("", views.index, name="index"),
    path("intake_form", views.intake_form, name="intake_form"),
    path("request_status/<uuid:id>", views.request_tracker, name="status"),
    path("request_list", views.request_list, name="request_list"),
]
