from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from bs4 import BeautifulSoup
from rules.contrib.views import objectgetter

from librarian.models import SavedFile
from otto.utils.decorators import permission_required
from template_wizard.models import Template
from template_wizard.utils import validate_docx_template_fields


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
            {"saved_file": None, "template": template, "docx_validation": None},
        )
    saved_file = SavedFile.objects.create(
        file=uploaded_file,
        content_type=uploaded_file.content_type
        or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    template.docx_template = saved_file
    template.docx_template_filename = uploaded_file.name
    template.save(update_fields=["docx_template", "docx_template_filename"])
    docx_validation = get_docx_validation(template)
    return render(
        request,
        "template_wizard/edit_template/docx_upload_result_fragment.html",
        {"template": template, "docx_validation": docx_validation},
    )


def get_docx_validation(template):
    if template.docx_template:
        try:
            return validate_docx_template_fields(template.docx_template.file, template)
        except Exception as e:
            return {"is_valid": False, "error": str(e)}
    return None


@require_POST
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def remove_docx_template(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    if template.docx_template:
        template.docx_template.delete()
        template.docx_template = None
        template.docx_template_filename = ""
        template.save(update_fields=["docx_template", "docx_template_filename"])
    docx_validation = get_docx_validation(template)
    return render(
        request,
        "template_wizard/edit_template/docx_upload_result_fragment.html",
        {"template": template, "docx_validation": docx_validation},
    )


@require_GET
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def docx_template_status(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    docx_validation = get_docx_validation(template)
    return render(
        request,
        "template_wizard/edit_template/docx_template_status.html",
        {"template": template, "docx_validation": docx_validation},
    )


@require_GET
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def fill_docx_template(request, template_id):
    """
    Fills a DOCX template with content from an HTML source.
    The DOCX template contains placeholders {{ field_slug }} that will be replaced
    The HTML string has container elements with id `field_slug` that will be used to fill the placeholders.
    Returns a rendered DOCX file as content disposition attachment.
    """
    template = get_object_or_404(Template, id=template_id)
    source_html_string = template.last_example_source.template_result
    docx_template_file = template.docx_template.file
    field_slugs = template.top_level_slugs
    # parse HTML source
    soup = BeautifulSoup(source_html_string, "html.parser")
    return
