from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from rules.contrib.views import objectgetter

from librarian.models import SavedFile
from otto.utils.decorators import permission_required
from template_wizard.models import Template


@require_POST
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def upload_docx_template(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    uploaded_file = request.FILES.get("docx_file")
    if not uploaded_file:
        return render(
            request,
            "template_wizard/edit_template/docx_upload_result_fragment.html",
            {"saved_file": None, "template": template},
        )
    saved_file = SavedFile.objects.create(
        file=uploaded_file,
        content_type=uploaded_file.content_type
        or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    template.docx_template = saved_file
    template.docx_template_filename = uploaded_file.name
    template.save(update_fields=["docx_template", "docx_template_filename"])
    return render(
        request,
        "template_wizard/edit_template/docx_upload_result_fragment.html",
        {"template": template},
    )


@require_POST
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def remove_docx_template(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    if template.docx_template:
        # Optionally delete the file object
        template.docx_template.delete()
        template.docx_template = None
        template.docx_template_filename = ""
        template.save(update_fields=["docx_template", "docx_template_filename"])
    return render(
        request,
        "template_wizard/edit_template/docx_upload_result_fragment.html",
        {"template": template},
    )


@require_GET
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def docx_template_status(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    return render(
        request,
        "template_wizard/edit_template/docx_template_status.html",
        {"template": template},
    )
