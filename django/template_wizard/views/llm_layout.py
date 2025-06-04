import json
import re

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from jinja2 import Template as JinjaTemplate
from rules.contrib.views import objectgetter
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.utils.decorators import permission_required
from template_wizard.forms import LayoutForm
from template_wizard.models import LayoutType, Template


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def test_layout(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    test_results = {}
    fragment_template = ""
    if template.layout_type == LayoutType.LLM_GENERATION:
        if template.layout_markdown and template.example_json_output:
            llm = OttoLLM(deployment="gpt-4.1-mini")
            bind_contextvars(feature="template_wizard", template_id=template.id)
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
                llm.create_costs()
                if output.startswith("```") and output.endswith("```"):
                    output = "\n".join(output.split("\n")[1:-1])
                test_results = {"output_markdown": output}
                # Save layout rendering result, type, and timestamp
                template.last_test_layout_result = json.dumps(test_results)
                template.last_test_layout_type = LayoutType.LLM_GENERATION
                template.last_test_layout_timestamp = timezone.now()
                template.save()
            except Exception as e:
                test_results = {"error": str(e)}
    elif template.layout_type == LayoutType.MARKDOWN_SUBSTITUTION:
        if template.layout_markdown and template.example_json_output:
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
            test_results = {"output_markdown": substituted}
            # Save layout rendering result, type, and timestamp
            template.last_test_layout_result = json.dumps(test_results)
            template.last_test_layout_type = LayoutType.MARKDOWN_SUBSTITUTION
            template.last_test_layout_timestamp = timezone.now()
            template.save()
    elif template.layout_type == LayoutType.JINJA_RENDERING:
        if template.layout_jinja and template.example_json_output:
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
                # Save layout rendering result, type, and timestamp
                template.last_test_layout_result = json.dumps(test_results)
                template.last_test_layout_type = LayoutType.JINJA_RENDERING
                template.last_test_layout_timestamp = timezone.now()
                template.save()
            except Exception as e:
                test_results = {"error": str(e)}
    elif template.layout_type == "word_template":
        test_results = {"output_html": "todo"}
    else:
        test_results = {"output_html": "Unknown layout type."}
    return render(
        request,
        "template_wizard/edit_template/test_layout_fragment.html",
        {"test_results": test_results, "template": template},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_jinja(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    llm = OttoLLM(deployment="o3-mini")
    bind_contextvars(feature="template_wizard", template_id=template.id)
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
    from django.contrib import messages

    try:
        jinja_code = llm.complete(prompt)
        llm.create_costs()
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
        from django.contrib import messages

        messages.error(request, _("No instruction provided."))
        layout_form = LayoutForm(instance=template)
        return render(
            request,
            "template_wizard/edit_template/layout_form.html",
            {"layout_form": layout_form, "template": template},
        )
    layout_type = template.layout_type
    if layout_type == "jinja_rendering":
        code = template.layout_jinja or ""
        code_type = "Jinja2 HTML"
    elif layout_type in ["markdown_substitution", "llm_generation"]:
        code = template.layout_markdown or ""
        code_type = "Markdown"
    else:
        from django.contrib import messages

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
    bind_contextvars(feature="template_wizard", template_id=template.id)
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
    from django.contrib import messages

    try:
        new_code = llm.complete(prompt)
        llm.create_costs()
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
