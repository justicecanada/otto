import os
from typing import List, Optional

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.program import FunctionCallingProgram, LLMTextCompletionProgram
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


@require_http_methods(["GET", "POST"])
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
            # HTMX: return hx-redirect header for success
            if request.headers.get("Hx-Request"):
                response = HttpResponse()
                response["HX-Redirect"] = redirect(
                    "template_wizard:edit_fields", template_id=template.id
                ).url
                return response
            return redirect("template_wizard:edit_fields", template_id=template.id)
        else:
            # If HTMX, return just the modal content with errors
            if request.headers.get("Hx-Request"):
                return render(
                    request,
                    "template_wizard/edit_template/field_modal.html",
                    {"form": form, "template": template, "field": field},
                )
    else:
        form = FieldForm(instance=field)
        # Only set initial parent_field if creating a new field
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


def build_pydantic_model_for_fields(
    fields_qs, parent_field=None, model_name="ExtractionTemplate"
):
    """
    Recursively build a Pydantic model for the given fields.
    """
    from typing import List, Optional

    from pydantic import Field as PydanticField
    from pydantic import create_model

    type_map = {
        "str": str,
        "float": float,
        "int": int,
        "bool": bool,
        "object": None,  # will be replaced by nested model
    }
    fields = {}
    # Only direct children of parent_field
    child_fields = fields_qs.filter(parent_field=parent_field)
    for field in child_fields:
        if field.field_type == "object":
            # Recursively build nested model
            nested_model = build_pydantic_model_for_fields(
                fields_qs,
                parent_field=field,
                model_name=f"{model_name}_{field.field_name.title().replace('_', '')}Object",
            )
            base_type = nested_model
        else:
            base_type = type_map.get(field.field_type, str)
        if field.list:
            base_type = List[base_type]
        # Prepare extra kwargs for PydanticField
        field_kwargs = {"description": field.description or None}
        if (
            field.field_type == "str"
            and getattr(field, "string_format", "none") != "none"
        ):
            field_kwargs["format"] = field.string_format
        if field.required:
            fields[field.field_name] = (
                base_type,
                PydanticField(..., **field_kwargs),
            )
        else:
            fields[field.field_name] = (
                Optional[base_type],
                PydanticField(default=None, **field_kwargs),
            )
    return create_model(model_name, **fields)


def unpack_model_to_dict(model_instance):
    """
    Recursively unpack a Pydantic model instance to a dict, ensuring all nested models and lists are handled.
    """
    if hasattr(model_instance, "dict"):
        # Unpack pydantic model to dict, then recurse
        return unpack_model_to_dict(model_instance.dict())
    elif isinstance(model_instance, dict):
        return {k: unpack_model_to_dict(v) for k, v in model_instance.items()}
    elif isinstance(model_instance, list):
        return [unpack_model_to_dict(item) for item in model_instance]
    else:
        return model_instance


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
        # Build the root model recursively
        TemplateModel = build_pydantic_model_for_fields(template.fields.all())
        # Set the docstring of the templatemodel to the description of the template
        if template.description_auto:
            TemplateModel.__doc__ = (
                f"{template.name_auto}\n\n{template.description_auto}"
            )
        schema = TemplateModel.model_json_schema()
        llm = OttoLLM(deployment="gpt-4.1-mini").llm
        prompt = (
            "Extract the requested fields from this document, if they exist "
            "(otherwise, the field value should be None):\n\n"
            "<document>\n{document_text}\n</document>"
        )
        program = LLMTextCompletionProgram.from_defaults(
            llm=llm,
            output_cls=TemplateModel,
            prompt_template_str=prompt,
            verbose=True,
        )
        try:
            output = program(document_text=template.example_source.text)
            if isinstance(output, str):
                raise ValueError(
                    "The output is a string; expected a structured output."
                )
            else:
                # Recursively unpack to dict
                dict_output = unpack_model_to_dict(output)
                template.generated_schema = schema
                template.example_json_output = dict_output
                template.save()
                test_results = dict_output

        except Exception as e:
            test_results = {"error": str(e), "output": output}
    return render(
        request,
        "template_wizard/edit_template/test_fields_fragment.html",
        {"test_results": test_results},
    )
