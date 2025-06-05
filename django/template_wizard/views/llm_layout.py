import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.utils.decorators import permission_required
from template_wizard.forms import LayoutForm
from template_wizard.models import Template
from template_wizard.utils import fill_template_from_fields


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def test_layout(request, template_id):

    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    test_results = {}
    # Use the example_source for this template
    source = getattr(template, "example_source", None)
    if source:
        fill_template_from_fields(source)
        if source.template_result:
            test_results = {"output_html": source.template_result}
            template.last_test_layout_result = json.dumps(test_results)
            template.last_test_layout_type = template.layout_type
            template.last_test_layout_timestamp = timezone.now()
            template.save()
        else:
            test_results = {"error": "No template result available."}
    else:
        test_results = {"error": "No example source available."}
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
    prompt = (
        """Given the following schema and example JSON output, generate a Jinja2 HTML template that will present the extracted information in a user-friendly way. 
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
        {"layout_form": layout_form, "template": template, "run_test_layout": "true"},
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
        """You are an expert {code_type} template developer.
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
        {"layout_form": layout_form, "template": template, "run_test_layout": "true"},
    )
