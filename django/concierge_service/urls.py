from django.urls import path

from . import views

app_name = "concierge_service"

urlpatterns = [
    path("", views.index, name="index"),
]
