from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from rules.contrib.views import objectgetter

from chat.forms import UploadForm
from librarian.models import PDF_EXTRACTION_CHOICES, SavedFile
from otto.utils.common import check_url_allowed
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.models import Source, TemplateSession


@require_POST
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def add_file_source(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    upload_form = UploadForm(request.POST, request.FILES, prefix="template-wizard")
    if upload_form.is_valid():
        saved_files = upload_form.save()
        for file_info in saved_files:
            Source.objects.create(
                session=session,
                saved_file=file_info["saved_file"],
                filename=file_info["filename"],
            )
        messages.success(
            request, _(f"{len(saved_files)} file(s) uploaded successfully.")
        )
    else:
        for field, errors in upload_form.errors.items():
            for error in errors:
                messages.error(request, f"{_(str(field).capitalize())}: {_(error)}")
    if session.is_example_session:
        return redirect(
            "template_wizard:edit_example_source", template_id=session.template.id
        )
    return redirect("template_wizard:select_sources", session_id=session.id)


@require_POST
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def add_url_source(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    url = request.POST.get("url")
    if url:
        if not check_url_allowed(url):
            messages.error(
                request,
                _(
                    "The URL is not allowed (only CanLii, Wikipedia, canada.ca and *.gc.ca are allowed)."
                ),
            )
            if session.is_example_session:
                return redirect(
                    "template_wizard:edit_example_source",
                    template_id=session.template.id,
                )
            return redirect("template_wizard:select_sources", session_id=session.id)
        Source.objects.create(session=session, url=url)
    if session.is_example_session:
        return redirect(
            "template_wizard:edit_example_source", template_id=session.template.id
        )
    return redirect("template_wizard:select_sources", session_id=session.id)


@require_POST
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def add_text_source(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    text = request.POST.get("text")
    if text:
        Source.objects.create(session=session, text=text)
    if session.is_example_session:
        return redirect(
            "template_wizard:edit_example_source", template_id=session.template.id
        )
    return redirect("template_wizard:select_sources", session_id=session.id)


@permission_required("template_wizard.access_source", objectgetter(Source, "source_id"))
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


@permission_required("template_wizard.access_source", objectgetter(Source, "source_id"))
def delete_source(request, source_id):
    source = get_object_or_404(Source, id=source_id)
    source.delete()
    messages.success(request, _(f"Source deleted."))
    if source.session.is_example_session:
        return redirect(
            "template_wizard:edit_example_source",
            template_id=source.session.template.id,
        )
    return redirect("template_wizard:select_sources", session_id=source.session.id)


@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def select_sources(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    upload_form = UploadForm(prefix="template-wizard")
    return render(
        request,
        "template_wizard/use_template/select_sources.html",
        context={
            "hide_breadcrumbs": True,
            "session": session,
            "upload_form": upload_form,
            "pdf_method_choices": PDF_EXTRACTION_CHOICES,
        },
    )


@require_POST
@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def delete_all_sources(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    count = session.sources.count()
    session.sources.all().delete()
    messages.success(
        request,
        f"{count} " + _("source(s) deleted."),
    )
    if session.is_example_session:
        return redirect(
            "template_wizard:edit_example_source", template_id=session.template.id
        )
    return redirect("template_wizard:select_sources", session_id=session.id)
