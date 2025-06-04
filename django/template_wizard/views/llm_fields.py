from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

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
        from llama_index.core.program import LLMTextCompletionProgram

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
                dict_output = unpack_model_to_dict(output)
                import json

                template.generated_schema = schema
                template.example_json_output = json.dumps(
                    dict_output, ensure_ascii=False
                )
                template.save()
                test_results = dict_output
        except Exception as e:
            test_results = {"error": str(e), "output": output}
    return render(
        request,
        "template_wizard/edit_template/test_fields_fragment.html",
        {"test_results": test_results},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_fields(request, template_id):
    import json

    template = Template.objects.filter(id=template_id).first()
    if not template or not template.example_source or not template.example_source.text:
        return HttpResponse(status=400, content="No example source available.")
    llm = OttoLLM(deployment="gpt-4.1-mini")
    prompt = _(
        """
        You are an expert in information extraction. Given the following example document, infer a JSON schema for extracting structured data fields, including nested objects and lists, suitable for the following Django model:

        class TemplateField(models.Model):
            field_name: str  # Human-readable name
            slug: str        # Unique identifier (letters, numbers, underscores only)
            field_type: str  # One of: str, float, int, bool, object
            string_format: str  # One of: none, email, date, time, date-time, duration
            required: bool
            description: str
            list: bool
            child_fields: List[TemplateField]  # Nested fields

        Please output a JSON object containing all fields, where each field is an object with keys: field_name, slug, field_type, string_format, required, description, list, child_fields.
        Use 'object' for field_type of parent objects and nest child objects as their children. Slugs must be unique among siblings.
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
