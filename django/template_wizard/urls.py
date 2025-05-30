from django.urls import path

from . import views

app_name = "template_wizard"

urlpatterns = [
    path("", views.template_list, name="index"),
    path("new_template/", views.new_template, name="new_template"),
    path(
        "edit_template/<int:template_id>/metadata/",
        views.edit_metadata,
        name="edit_template",
    ),
    path(
        "edit_template/<int:template_id>/metadata/",
        views.edit_metadata,
        name="edit_metadata",
    ),
    path(
        "edit_template/<int:template_id>/source/",
        views.edit_example_source,
        name="edit_example_source",
    ),
    path(
        "edit_template/<int:template_id>/fields/",
        views.edit_fields,
        name="edit_fields",
    ),
    path(
        "edit_template/<int:template_id>/layout/",
        views.edit_layout,
        name="edit_layout",
    ),
    path(
        "delete_template/<int:template_id>/",
        views.delete_template,
        name="delete_template",
    ),
]
