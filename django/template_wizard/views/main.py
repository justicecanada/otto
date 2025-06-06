from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from rules.contrib.views import objectgetter

from librarian.models import SavedFile
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Source, Template, TemplateField, TemplateSession

app_name = "template_wizard"


@app_access_required(app_name)
def template_list(request):
    return render(
        request,
        "template_wizard/template_list.html",
        context={
            "hide_breadcrumbs": True,
            "templates": Template.objects.all(),
        },
    )


@app_access_required(app_name)
def session_history(request):
    # Only sessions for this user that have at least one source
    sessions = (
        request.user.template_sessions.filter(sources__isnull=False)
        .distinct()
        .order_by("-created_at")[:10]
    )
    return render(
        request, "template_wizard/session_history.html", {"sessions": sessions}
    )


@permission_required(
    "template_wizard.access_template", objectgetter(Template, "template_id")
)
def new_session(request, template_id):
    session = TemplateSession.objects.create(
        template_id=template_id,
        user=request.user,
    )
    return redirect("template_wizard:select_sources", session_id=session.id)


@app_access_required(app_name)
@require_http_methods(["DELETE"])
def delete_session(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id, user=request.user)
    session.delete()
    return HttpResponse(status=200)


@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def open_session(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id, user=request.user)

    if session.status == "select_sources":
        url = reverse("template_wizard:select_sources", args=[session.id])
    else:
        url = reverse("template_wizard:fill_template", args=[session.id])
    return redirect(url)
