from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from chat.forms import UploadForm
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


@permission_required(
    "template_wizard.access_template", objectgetter(Template, "template_id")
)
def fill_template(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    return render(
        request,
        "template_wizard/use_template/fill_template.html",
        context={
            "hide_breadcrumbs": True,
            "template": template,
        },
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


@permission_required(
    "template_wizard.access_session", objectgetter(TemplateSession, "session_id")
)
def select_sources(request, session_id):
    session = get_object_or_404(TemplateSession, id=session_id)
    upload_form = UploadForm(prefix="template-wizard")
    if request.method == "POST":
        upload_form = UploadForm(request.POST, request.FILES, prefix="template-wizard")
        if upload_form.is_valid():
            saved_files = upload_form.save()
            for file in saved_files:
                Source.objects.create(
                    session=session,
                    saved_file=file["saved_file"],
                    filename=file["filename"],
                )
            messages.success(
                request, _(f"{len(saved_files)} file(s) uploaded successfully.")
            )
            upload_form = UploadForm(prefix="template_wizard")
    return render(
        request,
        "template_wizard/use_template/select_sources.html",
        context={
            "hide_breadcrumbs": True,
            "session": session,
            "upload_form": upload_form,
        },
    )
