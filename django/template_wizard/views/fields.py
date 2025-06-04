import json

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.utils.decorators import permission_required
from template_wizard.forms import FieldForm
from template_wizard.models import Template, TemplateField


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "active_tab": "fields",
            "template": template,
            "test_results": (
                json.loads(template.last_test_fields_result)
                if template.last_test_fields_result
                else None
            ),
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def field_modal(request, template_id, field_id=None, parent_field_id=None):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    if field_id:
        field = TemplateField.objects.filter(id=field_id, template=template).first()
        if not field:
            raise Http404()
    else:
        field = None
    if request.method == "POST":
        form = FieldForm(request.POST, instance=field)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.template = template
            instance.save()
            messages.success(request, _("Field saved successfully."))
            if request.headers.get("Hx-Request"):
                response = HttpResponse()
                response["HX-Redirect"] = redirect(
                    "template_wizard:edit_fields", template_id=template.id
                ).url
                return response
            return redirect("template_wizard:edit_fields", template_id=template.id)
        else:
            if request.headers.get("Hx-Request"):
                return render(
                    request,
                    "template_wizard/edit_template/field_modal.html",
                    {"form": form, "template": template, "field": field},
                )
    else:
        form = FieldForm(instance=field)
        if field is None and parent_field_id:
            form.initial["parent_field"] = parent_field_id
    return render(
        request,
        "template_wizard/edit_template/field_modal.html",
        {"form": form, "template": template, "field": field},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def delete_field(request, template_id, field_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    field = TemplateField.objects.filter(id=field_id, template=template).first()
    if not field:
        raise Http404()
    field.delete()
    messages.success(request, _("Field deleted successfully."))
    return redirect("template_wizard:edit_fields", template_id=template.id)
