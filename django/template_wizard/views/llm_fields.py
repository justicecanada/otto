import json

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from llama_index.core.program import LLMTextCompletionProgram
from rules.contrib.views import objectgetter
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.utils.decorators import permission_required
from template_wizard.forms import FieldForm
from template_wizard.models import Template, TemplateField
from template_wizard.utils import build_pydantic_model_for_fields, unpack_model_to_dict


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def test_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    test_results = {}
    if template.example_source and template.fields.exists():
        TemplateModel = build_pydantic_model_for_fields(template.fields.all())
        if template.description_auto:
            TemplateModel.__doc__ = (
                f"{template.name_auto}\n\n{template.description_auto}"
            )
        schema = TemplateModel.model_json_schema()

        llm = OttoLLM(deployment="gpt-4.1-mini")
        bind_contextvars(feature="template_wizard", template_id=template.id)
        prompt = (
            "Extract the requested fields from this document, if they exist "
            "(otherwise, the field value should be None):\n\n"
            "<document>\n{document_text}\n</document>"
        )
        program = LLMTextCompletionProgram.from_defaults(
            llm=llm.llm,
            output_cls=TemplateModel,
            prompt_template_str=prompt,
            verbose=True,
        )
        try:
            output = program(document_text=template.example_source.text)
            llm.create_costs()
            if isinstance(output, str):
                raise ValueError(
                    "The output is a string; expected a structured output."
                )
            else:
                dict_output = unpack_model_to_dict(output)
                template.generated_schema = schema
                template.example_json_output = json.dumps(
                    dict_output, ensure_ascii=False
                )
                # Save test extraction result and timestamp
                template.last_test_fields_result = json.dumps(
                    dict_output, ensure_ascii=False
                )
                from django.utils import timezone

                template.last_test_fields_timestamp = timezone.now()
                template.save()
                test_results = dict_output
        except Exception as e:
            test_results = {"error": str(e), "output": output}
    return render(
        request,
        "template_wizard/edit_template/test_fields_fragment.html",
        {"test_results": test_results, "template": template},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template or not template.example_source or not template.example_source.text:
        return HttpResponse(status=400, content="No example source available.")
    llm = OttoLLM(deployment="gpt-4.1")
    bind_contextvars(feature="template_wizard", template_id=template.id)
    prompt = _(
        """
        You are an expert in information extraction. Given the following example document, infer a JSON schema for extracting structured data fields, including nested objects and lists, suitable for the following Django model:

        class TemplateField(models.Model):
            field_name: str  # Human-readable name
            slug: str  # Unique identifier (letters, numbers, underscores only). Must be unique among siblings.
            field_type: str  # One of: str, float, int, bool, object
            string_format: str  # One of: none, email, date, time, date-time, duration
            required: bool  # Always fill this field, even if no information is found (can cause hallucinations - use sparingly!)
            description: str  # Helps the extractor know what to look for.
            list: bool  # Extract multiple instances of this field as a list
            child_fields: List[TemplateField]  # Nested fields

        Please output a JSON object containing all fields, where each field is an object with keys: field_name, slug, field_type, string_format, required, description, list, child_fields.
        Use 'object' for field_type of parent objects and nest child objects as their children. Only do this when there is more than 1 child field. You can use a primitive type with list=True for simple lists.
        The top level key must be "template_fields" and the value must be a list of field definitions.

        The user has indicated that the type of document is:
        {template_name}
        {template_description}

        Extract around 5-10 fields, including nested objects, based on the example document content.
        This template will be used to extract structured data from similar documents so fields should not be too specific to this document.
        
        EXAMPLE DOCUMENT:
        {document}
        """
    ).format(
        template_name=template.name_auto,
        template_description=(
            f"({template.description_auto})" if template.description_auto else ""
        ),
        document=template.example_source.text,
    )
    try:
        llm_response = llm.complete(prompt, response_format={"type": "json_object"})
        llm.create_costs()
        fields_data = json.loads(llm_response)
    except Exception as e:
        return HttpResponse(
            f"LLM or JSON error: {e}\nResponse: {llm_response}", status=500
        )
    TemplateField.objects.filter(template=template).delete()

    def create_fields(fields, parent_field=None):
        for field in fields:
            field_kwargs = {
                "template": template,
                "field_name": field.get("field_name", ""),
                "slug": field.get("slug", ""),
                "field_type": field.get("field_type", "str"),
                "string_format": field.get("string_format", "none"),
                "required": field.get("required", False),
                "description": field.get("description", ""),
                "list": field.get("list", False),
                "parent_field": parent_field,
            }
            if field.get("child_fields"):
                field_kwargs["field_type"] = "object"
            if not parent_field:
                field_kwargs.pop("parent_field")
            tf = TemplateField.objects.create(**field_kwargs)
            child_fields = field.get("child_fields") or field.get("fields")
            if child_fields and isinstance(child_fields, list):
                create_fields(child_fields, parent_field=tf)

    if isinstance(fields_data, dict):
        fields_list = (
            fields_data.get("template_fields") or fields_data.get("fields") or []
        )
        create_fields(fields_list)
    else:
        create_fields(fields_data)
    fields = template.fields.all()
    return render(
        request,
        "template_wizard/edit_template/field_list.html",
        {"fields": fields, "template": template},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
@require_POST
def modify_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    instruction = request.POST.get("modification_instruction", "").strip()
    if not instruction:
        from django.contrib import messages

        messages.error(request, _("No instruction provided."))
        fields = template.fields.all()
        return render(
            request,
            "template_wizard/edit_template/field_list.html",
            {"fields": fields, "template": template},
        )
    # Serialize current fields to LLM schema format

    def serialize_field(field):
        return {
            "field_name": field.field_name,
            "slug": field.slug,
            "field_type": field.field_type,
            "string_format": field.string_format,
            "required": field.required,
            "description": field.description,
            "list": field.list,
            "child_fields": (
                [serialize_field(f) for f in field.child_fields.all()]
                if hasattr(field, "child_fields")
                else []
            ),
        }

    # Only top-level fields (no parent_field)
    top_fields = template.fields.filter(parent_field=None)
    fields_json = [serialize_field(f) for f in top_fields]
    llm = OttoLLM(deployment="gpt-4.1")
    bind_contextvars(feature="template_wizard", template_id=template.id)
    prompt = _(
        """
        You are an expert in information extraction. Here is the current JSON schema for extracting structured data fields (see the TemplateField model below). Modify the schema according to the user's instruction. Output the new schema as a JSON object with the top-level key 'template_fields' and the value as a list of field definitions. Do not include any comments or explanations.

        class TemplateField(models.Model):
            field_name: str  # Human-readable name
            slug: str  # Unique identifier (letters, numbers, underscores only). Must be unique among siblings.
            field_type: str  # One of: str, float, int, bool, object
            string_format: str  # One of: none, email, date, time, date-time, duration
            required: bool  # Always fill this field, even if no information is found (can cause hallucinations - use sparingly!)
            description: str  # Helps the extractor know what to look for.
            list: bool  # Extract multiple instances of this field as a list
            child_fields: List[TemplateField]  # Nested fields

        CURRENT FIELDS:
        {{"template_fields": {fields_json}}}

        USER INSTRUCTION:
        {instruction}
        """
    ).format(
        fields_json=json.dumps(fields_json, ensure_ascii=False), instruction=instruction
    )
    from django.contrib import messages

    try:
        llm_response = llm.complete(prompt, response_format={"type": "json_object"})
        llm.create_costs()
        fields_data = json.loads(llm_response)
    except Exception as e:
        messages.error(request, _(f"LLM or JSON error: {e}\nResponse: {llm_response}"))
        fields = template.fields.all()
        return render(
            request,
            "template_wizard/edit_template/field_list.html",
            {"fields": fields, "template": template},
        )
    TemplateField.objects.filter(template=template).delete()

    def create_fields(fields, parent_field=None):
        for field in fields:
            field_kwargs = {
                "template": template,
                "field_name": field.get("field_name", ""),
                "slug": field.get("slug", ""),
                "field_type": field.get("field_type", "str"),
                "string_format": field.get("string_format", "none"),
                "required": field.get("required", False),
                "description": field.get("description", ""),
                "list": field.get("list", False),
                "parent_field": parent_field,
            }
            if field.get("child_fields"):
                field_kwargs["field_type"] = "object"
            if not parent_field:
                field_kwargs.pop("parent_field")
            tf = TemplateField.objects.create(**field_kwargs)
            child_fields = field.get("child_fields") or field.get("fields")
            if child_fields and isinstance(child_fields, list):
                create_fields(child_fields, parent_field=tf)

    if isinstance(fields_data, dict):
        fields_list = (
            fields_data.get("template_fields") or fields_data.get("fields") or []
        )
        create_fields(fields_list)
    else:
        create_fields(fields_data)
    fields = template.fields.all()
    messages.success(request, _(f"Fields modified."))
    return render(
        request,
        "template_wizard/edit_template/field_list.html",
        {"fields": fields, "template": template},
    )
