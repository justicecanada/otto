from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from rules.contrib.views import objectgetter
from structlog import get_logger

from librarian.models import SavedFile
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Source, Template, TemplateField, TemplateSession
from template_wizard.tasks import fill_template_with_source
from template_wizard.utils import (
    extract_fields,
    extract_source_text,
    fill_template_from_fields,
)

logger = get_logger(__name__)


@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def fill_template(request, session_id):
    """
    Starts the celery tasks for session.sources (if not started) and returns status/output.
    """
    session = get_object_or_404(TemplateSession, id=session_id)
    session.status = "fill_template"
    session.save()
    # Enqueue the task to fill the template with sources, for each source
    for source in session.sources.all():
        if source.status == "pending":
            fill_template_with_source.delay(source.id)

    return render(
        request,
        "template_wizard/use_template/fill_template.html",
        context={
            "hide_breadcrumbs": True,
            "session": session,
        },
    )
