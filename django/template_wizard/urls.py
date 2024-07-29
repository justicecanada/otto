from django.urls import include, path

from . import views

app_name = "template_wizard"

urlpatterns = [
    path("", views.index, name="index"),
    path("<str:report_id>/select-data", views.select_data, name="select_data"),
    path(
        "<str:report_id>/pick-template",
        views.pick_template,
        name="pick_template",
    ),
    path(
        "<str:report_id>/delete-report",
        views.delete_report,
        name="delete_report",
    ),
    path(
        "<str:report_id>/canlii_wizard/",
        include("template_wizard.wizards.canlii_wizard.urls"),
    ),
]
