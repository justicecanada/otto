import os

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from llama_index.core.llms import ChatMessage
from pydantic import Field as PydanticField
from pydantic import create_model
from rules.contrib.views import objectgetter
from structlog import get_logger

from chat.llm import OttoLLM
from otto.utils.decorators import (
    app_access_required,
    budget_required,
    permission_required,
)

from .forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from .models import Source, Template, TemplateField

logger = get_logger(__name__)


app_name = "template_wizard"


@app_access_required(app_name)
def template_list(request):
    return render(
        request,
        "template_wizard/template_list.html",
        context={"hide_breadcrumbs": True, "templates": Template.objects.all()},
    )


@app_access_required(app_name)
def new_template(request):
    if request.method == "POST":
        metadata_form = MetadataForm(request.POST, user=request.user)
        if metadata_form.is_valid():
            template = metadata_form.save(commit=False)
            template.owner = request.user
            template.save()
            return redirect(
                "template_wizard:edit_example_source", template_id=template.id
            )
        else:
            messages.error(
                request,
                _("Please correct the errors below: ") + str(metadata_form.errors),
            )
    else:
        form = MetadataForm(user=request.user)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={"metadata_form": form, "active_tab": "metadata"},
    )


@permission_required(
    "template_wizard.delete_template", objectgetter(Template, "template_id")
)
def delete_template(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    template.delete()
    messages.success(request, _("Template deleted successfully."))
    return redirect("template_wizard:index")


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_metadata(request, template_id):
    if request.method == "POST":
        template = Template.objects.filter(id=template_id).first()
        metadata_form = MetadataForm(request.POST, instance=template, user=request.user)
        if metadata_form.is_valid():
            metadata_form.save()
            messages.success(request, _("Template metadata updated successfully."))
            return redirect(
                "template_wizard:edit_example_source", template_id=template.id
            )
        else:
            messages.error(
                request,
                _("Please correct the errors below:" + str(metadata_form.errors)),
            )
    else:
        template = Template.objects.filter(id=template_id).first()
        metadata_form = (
            MetadataForm(instance=template, user=request.user)
            if template
            else MetadataForm(user=request.user)
        )
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "metadata_form": metadata_form,
            "active_tab": "metadata",
            "template": template,
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_example_source(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if request.method == "POST":
        source_form = SourceForm(request.POST, instance=template.example_source)
        if source_form.is_valid():
            source_form.save()
            messages.success(request, _("Example source updated successfully."))
            return redirect("template_wizard:edit_fields", template_id=template.id)
    else:
        source_form = SourceForm(instance=template.example_source)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "source_form": source_form,
            "active_tab": "source",
            "template": template,
        },
    )


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
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_layout(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if request.method == "POST":
        layout_form = LayoutForm(request.POST, instance=template)
        if layout_form is not None and layout_form.is_valid():
            layout_form.save()
            messages.success(request, _("Template layout updated successfully."))
            return redirect("template_wizard:index")
    else:
        layout_form = LayoutForm(instance=template)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "layout_form": layout_form,
            "active_tab": "layout",
            "template": template,
        },
    )


@require_http_methods(["GET", "POST"])
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def field_modal(request, template_id, field_id=None):
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
            return redirect("template_wizard:edit_fields", template_id=template.id)
    else:
        form = FieldForm(instance=field)
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


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def test_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    test_results = {}
    # --- LlamaIndex + Pydantic dynamic model construction ---
    if template.example_source and template.fields.exists():
        # Map TemplateField.field_type to Python types
        type_map = {
            "text": (str, ...),
            "string": (str, ...),
            "number": (float, ...),
            "integer": (int, ...),
            "bool": (bool, ...),
            "boolean": (bool, ...),
        }
        fields = {}
        for field in template.fields.all():
            py_type = type_map.get(field.field_type.lower(), (str, ...))
            fields[field.field_name] = (
                py_type[0],
                PydanticField(..., description=field.description or None),
            )
        TemplateModel = create_model("Template", **fields)
        llm = OttoLLM().llm
        sllm = llm.as_structured_llm(output_cls=TemplateModel)
        input_msg = ChatMessage.from_str(
            f"Extract the requested fields from this text: {template.example_source.text}"
        )
        try:
            output = sllm.chat([input_msg])
            output_obj = output.raw
            test_results = dict(output_obj)
        except Exception as e:
            test_results = {"error": str(e)}
    return render(
        request,
        "template_wizard/edit_template/test_fields_fragment.html",
        {"test_results": test_results},
    )
