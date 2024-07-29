# urls.py

from django.urls import path

from . import views

urlpatterns = [
    path(
        "add_report_data", views.add_report_data, name="canlii_wizard_add_report_data"
    ),
    path(
        "pick_template",
        views.pick_template,
        name="canlii_wizard_pick_template",
    ),
    path(
        "generate_report",
        views.generate_report,
        name="canlii_wizard_generate_report",
    ),
    path(
        "download_generated_report/<str:generated_report_id>",
        views.download_generated_report,
        name="canlii_wizard_download_generated_report",
    ),
    path(
        "delete_generated_report/<str:generated_report_id>",
        views.delete_generated_report,
        name="canlii_wizard_delete_generated_report",
    ),
]
