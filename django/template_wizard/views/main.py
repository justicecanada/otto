from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from chat.forms import UploadForm
from librarian.models import SavedFile
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Template, TemplateField

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
def select_sources(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    upload_form = UploadForm(prefix="template-wizard")
    upload_success = False
    saved_files = []
    if request.method == "POST":
        upload_form = UploadForm(request.POST, request.FILES, prefix="template-wizard")
        if upload_form.is_valid():
            saved_files = upload_form.save()
            upload_success = True
            messages.success(
                request, _(f"{len(saved_files)} file(s) uploaded successfully.")
            )
            upload_form = UploadForm(
                prefix="template_wizard"
            )  # Reset form after upload
    return render(
        request,
        "template_wizard/use_template/select_sources.html",
        context={
            "hide_breadcrumbs": True,
            "template": template,
            "upload_form": upload_form,
            "upload_success": upload_success,
            "saved_files": saved_files,
        },
    )
