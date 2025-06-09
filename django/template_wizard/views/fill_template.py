from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import permission_required
from template_wizard.models import Source, TemplateSession
from template_wizard.tasks import fill_template_with_source

logger = get_logger(__name__)


@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def fill_template(request, session_id):
    """
    Starts the celery tasks for session.sources (if not started) and returns status/output.
    """
    session = get_object_or_404(TemplateSession, id=session_id)
    update_session_status = False
    # Enqueue the task to fill the template with sources, for each source
    for source in session.sources.all():
        if source.status == "pending":
            update_session_status = True
            fill_template_with_source.delay(source.id)
    if update_session_status:
        session.status = "fill_template"
        session.save()
    all_sources_processed = all(
        source.status in ["completed", "error"] for source in session.sources.all()
    )

    return render(
        request,
        "template_wizard/use_template/fill_template.html",
        context={
            "hide_breadcrumbs": True,
            "session": session,
            "poll": not all_sources_processed,
        },
    )


@require_GET
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def source_raw_data(request, session_id, source_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    source = get_object_or_404(Source, id=source_id, session=session)
    return render(
        request,
        "template_wizard/use_template/source_raw_data_fragment.html",
        {"source": source},
    )


@require_GET
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def source_template_result(request, session_id, source_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    source = get_object_or_404(Source, id=source_id, session=session)
    return render(
        request,
        "template_wizard/use_template/source_template_result_fragment.html",
        {"source": source, "template": session.template},
    )


@require_GET
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def poll_status(request, session_id):
    """
    Polls the status of the template session.
    """
    session = get_object_or_404(TemplateSession, id=session_id)
    all_sources_processed = all(
        source.status in ["completed", "error"] for source in session.sources.all()
    )
    any_sources_error = any(
        source.status == "error" for source in session.sources.all()
    )
    if any_sources_error:
        session.status = "error"
        session.save()
    elif all_sources_processed:
        session.status = "completed"
        session.save()
        messages.success(request, _("Template filling complete."))
    return render(
        request,
        "template_wizard/use_template/session_status.html",
        context={"session": session, "poll": not all_sources_processed},
    )


@require_GET
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def restart_source_processing(request, session_id, source_id):
    """
    Restarts celery processing for a single source and returns updated status list.
    """
    session = get_object_or_404(TemplateSession, id=session_id)
    source = get_object_or_404(Source, id=source_id, session=session)
    # Only restart if in error state
    if source.status == "error":
        source.status = "pending"
        source.save()
        fill_template_with_source.delay(source.id)
    # Return the updated status list (HTMX fragment)
    return render(
        request,
        "template_wizard/use_template/session_status.html",
        context={"session": session, "poll": True},
    )
