from typing import List, Optional

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from llama_index.core.program import LLMTextCompletionProgram
from pydantic import Field, create_model
from rules.contrib.views import objectgetter

from chat.llm import OttoLLM
from otto.utils.decorators import permission_required

from .forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from .models import Source, Template, TemplateField


def build_pydantic_model_for_fields(
    fields_qs, parent_field=None, model_name="ExtractionTemplate"
):
    """
    Recursively build a Pydantic model for the given fields.
    """

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
                model_name=f"{model_name}__{field.slug}",
            )
            base_type = nested_model
        else:
            base_type = type_map.get(field.field_type, str)
        if field.list:
            base_type = List[base_type]
        # Prepare extra kwargs for Field
        field_kwargs = {"description": field.description or None}
        if (
            field.field_type == "str"
            and getattr(field, "string_format", "none") != "none"
        ):
            field_kwargs["format"] = field.string_format
        if field.required:
            fields[field.slug] = (
                base_type,
                Field(..., **field_kwargs),
            )
        else:
            fields[field.slug] = (
                Optional[base_type],
                Field(default=None, **field_kwargs),
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
                import json

                template.generated_schema = schema
                # Save as JSON string for compatibility
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
def test_layout(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    test_results = {}
    output_html = ""
    fragment_template = ""
    if template.layout_type == "llm_generation":
        if template.layout_markdown and template.example_json_output:
            llm = OttoLLM(deployment="gpt-4.1-mini")
            prompt = _(
                "Fill in the following template string using the provided JSON data.\n"
                "Template string:\n{layout_markdown}\n\n"
                "JSON schema:\n{json_schema}\n\n"
                "JSON data:\n{json_data}\n\n"
                "Render the template as markdown, replacing all fields with the "
                "corresponding values from the JSON data, "
                "formatted according to the template instructions.\n"
                "Do not wrap in backticks or include any additional comments."
            ).format(
                layout_markdown=template.layout_markdown,
                json_schema=template.generated_schema,
                json_data=template.example_json_output,
            )
            try:
                output = llm.complete(prompt)
                # Strip backticks if necessary
                if output.startswith("```") and output.endswith("```"):
                    # Remove first and last line
                    output = "\n".join(output.split("\n")[1:-1])
                test_results = {"output_markdown": output}
            except Exception as e:
                test_results = {"error": str(e)}
            fragment_template = (
                "template_wizard/edit_template/test_layout_fragment_markdown.html"
            )
    elif template.layout_type == "markdown_substitution":
        if template.layout_markdown and template.example_json_output:
            import json
            import re

            # If example_json_output is a dict, use as is, else try to parse
            if isinstance(template.example_json_output, dict):
                data = template.example_json_output
            else:
                try:
                    data = json.loads(template.example_json_output)
                except Exception:
                    data = {}

            def substitute(match):
                key = match.group(1).strip()
                return str(data.get(key, ""))

            pattern = re.compile(r"{{\s*(\w+)\s*}}")
            substituted = pattern.sub(substitute, template.layout_markdown)
            # Instead of rendering markdown here, pass the raw substituted markdown to the template
            test_results = {"output_markdown": substituted}
            fragment_template = (
                "template_wizard/edit_template/test_layout_fragment_markdown.html"
            )
    elif template.layout_type == "jinja_rendering":
        if template.layout_jinja and template.example_json_output:
            import json

            from jinja2 import Template as JinjaTemplate

            # If example_json_output is a dict, use as is, else try to parse
            if isinstance(template.example_json_output, dict):
                data = template.example_json_output
            else:
                try:
                    data = json.loads(template.example_json_output)
                except Exception as e:
                    print(f"Error parsing JSON: {e}")
                    data = {}
            try:
                jinja_template = JinjaTemplate(template.layout_jinja)
                rendered_html = jinja_template.render(**data)
                test_results = {"output_html": rendered_html}
            except Exception as e:
                test_results = {"error": str(e)}
            fragment_template = (
                "template_wizard/edit_template/test_layout_fragment_jinja.html"
            )
    elif template.layout_type == "word_template":
        test_results = {"output_html": "todo"}
        fragment_template = (
            "template_wizard/edit_template/test_layout_fragment_word.html"
        )
    else:
        test_results = {"output_html": "Unknown layout type."}
        fragment_template = (
            "template_wizard/edit_template/test_layout_fragment_unknown.html"
        )
    return render(
        request,
        fragment_template,
        {"test_results": test_results},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_jinja(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    llm = OttoLLM(deployment="o3-mini")
    # Prepare prompt for LLM
    prompt = _(
        """
        Given the following schema and example JSON output, generate a Jinja2 HTML template that will present the extracted information in a user-friendly way. 
        The template should use Jinja2 syntax (e.g., {{ field }}) and render all fields from the example JSON.
        Use HTML markup and include labels for each field.
        You may reorder the fields for better presentation, use HTML constructs like lists, tables, etc. as needed for best presentation.
        Output the HTML code only (do not wrap in backticks or include any other comments).
        
        SCHEMA:
        {schema}
        
        EXAMPLE JSON OUTPUT:
        {json_output}
        """
    ).format(
        schema=template.generated_schema or "",
        json_output=template.example_json_output or "",
    )
    jinja_code = ""
    try:
        jinja_code = llm.complete(prompt)
        template.layout_type = "jinja_rendering"
        template.layout_jinja = jinja_code
        template.save()
        messages.success(request, _("Jinja template generated and saved."))
    except Exception as e:
        messages.error(request, _(f"Error generating Jinja template: {e}"))
    layout_form = LayoutForm(instance=template)
    return render(
        request,
        "template_wizard/edit_template/layout_form.html",
        {"layout_form": layout_form, "template": template},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def modify_layout_code(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template or request.method != "POST":
        raise Http404()
    instruction = request.POST.get("modification_instruction", "").strip()
    if not instruction:
        messages.error(request, _("No instruction provided."))
        layout_form = LayoutForm(instance=template)
        return render(
            request,
            "template_wizard/edit_template/layout_form.html",
            {"layout_form": layout_form, "template": template},
        )
    # Determine which code to modify
    layout_type = template.layout_type
    if layout_type == "jinja_rendering":
        code = template.layout_jinja or ""
        code_type = "Jinja2 HTML"
    elif layout_type in ["markdown_substitution", "llm_generation"]:
        code = template.layout_markdown or ""
        code_type = "Markdown"
    else:
        messages.error(
            request, _(f"Layout type '{layout_type}' not supported for modification.")
        )
        layout_form = LayoutForm(instance=template)
        return render(
            request,
            "template_wizard/edit_template/layout_form.html",
            {"layout_form": layout_form, "template": template},
        )
    llm = OttoLLM(deployment="gpt-4.1")
    prompt = (
        """
        You are an expert {code_type} template developer.
        The schema used to populate the template is as follows:
        ```
        {schema}
        ```
        
        The example JSON output is as follows:
        ```
        {example_json}
        ```
        ---

        Here is the current template code for you to modify:
        ```
        {code}
        ```
        
        The user wants to modify the template with the following instruction:
        "{instruction}"

        Please return the modified template code only (no comments, no backticks, no explanations).
        """
    ).format(
        schema=template.generated_schema,
        example_json=template.example_json_output,
        code_type=code_type,
        code=code,
        instruction=instruction,
    )
    try:
        new_code = llm.complete(prompt)
        if layout_type == "jinja_rendering":
            template.layout_jinja = new_code
        else:
            template.layout_markdown = new_code
        template.save()
        messages.success(request, _(f"Layout code modified."))
    except Exception as e:
        messages.error(request, _(f"Error modifying layout code: {e}"))
    layout_form = LayoutForm(instance=template)
    return render(
        request,
        "template_wizard/edit_template/layout_form.html",
        {"layout_form": layout_form, "template": template},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_fields(request, template_id):
    """
    Use LLM to extract a schema from the example source, create TemplateField objects, and return the rendered field list fragment for HTMX.
    Note: This would be slightly better using Structured Outputs with recursive schema but I don't know how in LlamaIndex.
    See https://platform.openai.com/docs/guides/structured-outputs#recursive-schemas-are-supported
    """
    import json

    template = Template.objects.filter(id=template_id).first()
    if not template or not template.example_source or not template.example_source.text:
        return HttpResponse(status=400, content="No example source available.")

    # Prompt LLM for a schema based on the TemplateField model
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
        # Try to parse the response as JSON
        fields_data = json.loads(llm_response)
    except Exception as e:
        return HttpResponse(
            f"LLM or JSON error: {e}\nResponse: {llm_response}", status=500
        )

    # Remove all existing fields for this template
    TemplateField.objects.filter(template=template).delete()

    # Helper to recursively create fields
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
            # The LLM is prone to setting field_type to "list" instead of "object" for nested fields,
            # so we need to ensure that "object" is used for parent fields.
            if field.get("child_fields"):
                field_kwargs["field_type"] = "object"
            # Remove parent_field if None
            if not parent_field:
                field_kwargs.pop("parent_field")
            tf = TemplateField.objects.create(**field_kwargs)
            # Recursively create children if present (support both 'child_fields' and 'fields' for robustness)
            child_fields = field.get("child_fields") or field.get("fields")
            if child_fields and isinstance(child_fields, list):
                create_fields(child_fields, parent_field=tf)

    # If the LLM output is a dict with a 'template_fields' or 'fields' key, use it
    if isinstance(fields_data, dict):
        fields_list = (
            fields_data.get("template_fields") or fields_data.get("fields") or []
        )
        create_fields(fields_list)
    else:
        # If it's a list directly, use it
        create_fields(fields_data)

    # Return the rendered field list fragment for HTMX
    fields = template.fields.all()
    return render(
        request,
        "template_wizard/edit_template/field_list.html",
        {"fields": fields, "template": template},
    )
