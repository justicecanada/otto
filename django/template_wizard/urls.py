from django.urls import path

from . import views

app_name = "template_wizard"

urlpatterns = [
    path("", views.index, name="index"),
    path("new_template/", views.new_template, name="new_template"),
]
