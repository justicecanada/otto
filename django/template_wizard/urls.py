from django.urls import path

from . import views

app_name = "template_wizard"

urlpatterns = [
    path("", views.template_list, name="index"),
    path("new_template/", views.new_template, name="new_template"),
    path(
        "edit_template/<int:template_id>/<str:active_tab>/",
        views.edit_template,
        name="edit_template",
    ),
    path("edit_template/<int:template_id>/", views.edit_template, name="edit_template"),
    path(
        "delete_template/<int:template_id>/",
        views.delete_template,
        name="delete_template",
    ),
]
