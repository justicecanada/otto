app_name = "template_wizard"

from django.urls import path

from template_wizard import views

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
    path(
        "edit_template/<int:template_id>/field_modal/",
        views.field_modal,
        name="field_modal",
    ),
    path(
        "edit_template/<int:template_id>/field_modal/parent/<int:parent_field_id>/",
        views.field_modal,
        name="field_modal",
    ),
    path(
        "edit_template/<int:template_id>/field_modal/<int:field_id>/",
        views.field_modal,
        name="field_modal",
    ),
    path(
        "edit_template/<int:template_id>/delete_field/<int:field_id>/",
        views.delete_field,
        name="delete_field",
    ),
    path(
        "edit_template/<int:template_id>/test_fields/",
        views.test_fields,
        name="test_fields",
    ),
    path(
        "edit_template/<int:template_id>/test_layout/",
        views.test_layout,
        name="test_layout",
    ),
    path(
        "edit_template/<int:template_id>/generate_jinja/",
        views.generate_jinja,
        name="generate_jinja",
    ),
    path(
        "edit_template/<int:template_id>/generate_markdown/",
        views.generate_markdown,
        name="generate_markdown",
    ),
    path(
        "edit_template/<int:template_id>/modify_layout_code/",
        views.modify_layout_code,
        name="modify_layout_code",
    ),
    path(
        "edit_template/<int:template_id>/generate_fields/",
        views.generate_fields,
        name="generate_fields",
    ),
]
