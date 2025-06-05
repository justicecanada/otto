from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from librarian.models import SavedFile
from template_wizard.models import Source, TemplateSession


@require_POST
def add_url_source(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    url = request.POST.get("url")
    if url:
        Source.objects.create(session=session, url=url)
    return redirect("template_wizard:select_sources", session_id=session.id)


def download_source_file(request, source_id):
    source = get_object_or_404(Source, id=source_id)
    if not source.saved_file:
        raise Http404("No file associated with this source.")
    file_field = source.saved_file.file
    response = HttpResponse(file_field, content_type="application/octet-stream")
    response["Content-Disposition"] = (
        f'attachment; filename="{source.filename or file_field.name}"'
    )
    return response


@require_POST
def delete_source(request, source_id):
    source = get_object_or_404(Source, id=source_id)
    session_id = source.session.id if source.session else None
    source.delete()
    messages.success(request, _(f"Source deleted."))
    if session_id:
        return redirect("template_wizard:select_sources", session_id=session_id)
    return redirect("template_wizard:index")
