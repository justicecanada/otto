# views.py

from django.conf import settings
from django.shortcuts import redirect, render

from structlog import get_logger

from otto.secure_models import AccessKey
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.metrics.template_wizard_activity_metrics import (
    template_wizard_access_total,
)

from .models import Report

logger = get_logger(__name__)

app_name = "template_wizard"

WIZARD_CHOICES = [
    {
        "handle": "canlii_wizard",
        "name": "CanLii case summarizer",
        "description": "Extract immigration data from case files and populate a template for document review and processing.",
    },
    # {
    #     "handle": "lex_wizard",
    #     "name": "LEX case summarizer",
    #     "description": "Extract litigation details from LEX, build an event calendar, and populate a template for organized case-specific reports.",
    #     "permission_check": "template_wizard.access_lex_wizard",
    # },
]


@app_access_required(app_name)
def index(request):

    # usage metrics
    template_wizard_access_total.labels(user=request.user.upn).inc()

    access_key = AccessKey(user=request.user)

    if request.method == "POST":
        new_or_open = request.POST.get("new_or_open", "new")

        if new_or_open == "new":
            # Make sure the user has add permission to create Reports
            if not Report.can_be_created_by(access_key):
                Report.grant_create_to(access_key)
            report = Report.objects.create(access_key)
            report.wizard = request.POST.get("wizard")
            report.save(access_key)
        else:
            report_id = request.POST.get("report_id")
            report = Report.objects.get(access_key, id=report_id)

        return redirect("template_wizard:select_data", report.id)

    reports = Report.objects.all(access_key)

    # For each of the reports, lookup the wizard name and add it to the report object
    for report in reports:
        try:
            report.wizard_name = next(
                wizard["name"]
                for wizard in WIZARD_CHOICES
                if wizard["handle"] == report.wizard
            )
        except StopIteration:
            report.wizard_name = "Unknown"

    permitted_wizard_choices = [
        wizard
        for wizard in WIZARD_CHOICES
        if (not wizard.get("permission_check"))
        or request.user.has_perm(wizard["permission_check"])
    ]

    context = {
        "active_step": 1,
        "wizards": permitted_wizard_choices,
        "reports": reports,
        "hide_breadcrumbs": True,
    }

    return render(request, "template_wizard/get_started.html", context)


@app_access_required(app_name)
def select_data(request, report_id):
    access_key = AccessKey(user=request.user)

    report = Report.objects.get(access_key, id=report_id)

    if request.method == "POST":
        return redirect("template_wizard:pick_template", report_id)

    # if report.wizard == "lex_wizard":

    #     from .wizards.lex_wizard.views import select_data

    #     context = select_data(request, report)
    #     context.update(
    #         {
    #             "active_step": 2,
    #             "report": report,
    #             "hide_breadcrumbs": True,
    #             "wizard_name": "LEX case summarizer",
    #         }
    #     )

    #     return render(request, "template_wizard/lex_wizard/select_data.html", context)

    if report.wizard == "canlii_wizard":

        from .wizards.canlii_wizard.views import select_data

        context = select_data(request, report)
        context.update(
            {
                "active_step": 2,
                "report": report,
                "hide_breadcrumbs": True,
                "wizard_name": "CanLii case summarizer",
            }
        )
        return render(
            request, "template_wizard/canlii_wizard/select_data.html", context
        )


@app_access_required(app_name)
def delete_report(request, report_id):
    access_key = AccessKey(user=request.user)

    logger.info(f"Deleting report {report_id}")

    report = Report.objects.get(access_key, id=report_id)
    report.delete(access_key)

    # Render the existing_reports_partial.html template and return it as HttpResponse
    reports = Report.objects.all(access_key)

    # For each of the reports, lookup the wizard name and add it to the report object
    for report in reports:
        try:
            report.wizard_name = next(
                wizard["name"]
                for wizard in WIZARD_CHOICES
                if wizard["handle"] == report.wizard
            )
        except StopIteration:
            report.wizard_name = "Unknown"

    context = {
        "reports": reports,
        "hide_breadcrumbs": True,
    }

    return render(request, "template_wizard/existing_reports_partial.html", context)


@app_access_required(app_name)
def pick_template(request, report_id):
    access_key = AccessKey(user=request.user)

    logger.info(f"Picking template for report {report_id}")

    report = Report.objects.get(access_key, id=report_id)

    # if report.wizard == "lex_wizard":

    #     from .wizards.lex_wizard.views import pick_template

    #     context = pick_template(request, report)
    #     context.update(
    #         {
    #             "active_step": 3,
    #             "report": report,
    #             "hide_breadcrumbs": True,
    #             "wizard_name": "LEX case summarizer",
    #         }
    #     )

    #     return render(request, "template_wizard/lex_wizard/pick_template.html", context)

    if report.wizard == "canlii_wizard":

        from .wizards.canlii_wizard.views import pick_template

        context = pick_template(request, report)
        context.update(
            {
                "active_step": 3,
                "report": report,
                "hide_breadcrumbs": True,
                "wizard_name": "CanLii case summarizer",
            }
        )
        return render(
            request, "template_wizard/canlii_wizard/pick_template.html", context
        )
