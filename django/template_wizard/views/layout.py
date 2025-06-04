from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Template, TemplateField

app_name = "template_wizard"


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_layout(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if request.method == "POST":
        layout_form = LayoutForm(request.POST, instance=template)
        if layout_form is not None and layout_form.is_valid():
            layout_form.save()
            if request.headers.get("Hx-Request"):
                messages.success(
                    request,
                    _("Template layout updated successfully."),
                    extra_tags="unique",
                )
                return HttpResponse()
            return redirect("template_wizard:index")
    else:
        layout_form = LayoutForm(instance=template)
        top_level_fields = template.fields.filter(parent_field__isnull=True)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "layout_form": layout_form,
            "active_tab": "layout",
            "template": template,
            "top_level_fields": top_level_fields,
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_markdown(request, template_id):
    """Generate a markdown template for the given template and save it to layout_markdown."""
    template = Template.objects.filter(id=template_id).first()
    if not template:
        return HttpResponse(status=404)
    fields = template.fields.filter(parent_field__isnull=True)
    markdown = "\n".join([f"## {f.field_name}\n\n{{{{ {f.slug} }}}}\n" for f in fields])
    template.layout_markdown = markdown
    template.save(update_fields=["layout_markdown"])
    messages.success(request, _("Markdown template generated and saved."))
    layout_form = LayoutForm(instance=template)
    return render(
        request,
        "template_wizard/edit_template/layout_form.html",
        {"layout_form": layout_form, "template": template},
    )
