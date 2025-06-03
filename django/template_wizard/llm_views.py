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
