# Shared helpers for template wizard
import json
import re
from typing import List, Optional

from jinja2 import Template as JinjaTemplate
from llama_index.core.program import LLMTextCompletionProgram
from pydantic import Field, create_model
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from chat.utils import url_to_text
from librarian.utils.process_engine import (
    extract_markdown,
    get_process_engine_from_type,
)
from template_wizard.models import LayoutType

logger = get_logger(__name__)


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
    child_fields = fields_qs.filter(parent_field=parent_field)
    for field in child_fields:
        if field.field_type == "object":
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
        return unpack_model_to_dict(model_instance.dict())
    elif isinstance(model_instance, dict):
        return {k: unpack_model_to_dict(v) for k, v in model_instance.items()}
    elif isinstance(model_instance, list):
        return [unpack_model_to_dict(item) for item in model_instance]
    else:
        return model_instance


def extract_source_text(source):
    if source.url and not source.text:
        try:
            source.text = url_to_text(source.url)
            source.save()
        except Exception as e:
            logger.error(
                "Error processing URL",
                url=source.url,
                source_id=source.id,
                error=str(e),
            )
            return
    elif source.saved_file and not source.text:
        try:
            with source.saved_file.file.open("rb") as file:
                content = file.read()
                content_type = source.saved_file.content_type
                process_engine = get_process_engine_from_type(content_type)
                source.text, _ = extract_markdown(content, process_engine)
                source.save()
        except Exception as e:
            logger.error(
                "Error processing saved file",
                filelname=source.filename,
                source_id=source.id,
                error=str(e),
            )
    assert source.text, "Source text extraction failed"


def extract_fields(source):
    """
    Uses LLM to extract fields from the source text and saves to source.extracted_json (TextField)
    """

    template = source.template
    if not template or not source.text or not template.fields.exists():
        logger.error(
            "Missing template, source text, or fields for extraction",
            source_id=source.id,
        )
        return
    TemplateModel = build_pydantic_model_for_fields(template.fields.all())
    llm = OttoLLM(deployment="gpt-4.1")
    prompt = (
        "Extract the requested fields from this document, if they exist "
        "(otherwise, the field value should be None):\n\n"
        "<document>\n{document_text}\n</document>"
    ).format(document_text=source.text)
    program = LLMTextCompletionProgram.from_defaults(
        llm=llm.llm,
        output_cls=TemplateModel,
        prompt_template_str=prompt,
        verbose=True,
    )
    try:
        result = program(source.text)
        result_dict = unpack_model_to_dict(result)
        source.extracted_json = json.dumps(result_dict, ensure_ascii=False)
        source.save()
    except Exception as e:
        logger.error("Error extracting fields", source_id=source.id, error=str(e))
        source.extracted_json = None
        source.save()
        raise


def fill_template_from_fields(source):
    """
    Fills the template with the extracted fields, saves to source.template_result (TextField)
    """

    template = source.template
    if not template or not source.extracted_json:
        logger.error(
            "Missing template or extracted fields for template filling",
            source_id=source.id,
        )
        return
    try:
        fields_data = json.loads(source.extracted_json)
    except Exception as e:
        logger.error(
            "Error loading extracted_json for template filling",
            source_id=source.id,
            error=str(e),
        )
        source.template_result = None
        source.save()
        return
    output = None
    if template.layout_type == LayoutType.LLM_GENERATION:
        if template.layout_markdown:
            llm = OttoLLM(deployment="gpt-4.1-mini")
            bind_contextvars(feature="template_wizard", template_id=template.id)
            prompt = (
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
                json_data=json.dumps(fields_data, ensure_ascii=False),
            )
            try:
                output = llm.complete(prompt)
                llm.create_costs()
                if output.startswith("```") and output.endswith("```"):
                    output = output.strip("`\n")
            except Exception as e:
                logger.error(
                    "Error rendering LLM template", source_id=source.id, error=str(e)
                )
                output = None
    elif template.layout_type == LayoutType.MARKDOWN_SUBSTITUTION:
        if template.layout_markdown:

            def substitute(match):
                key = match.group(1)
                return str(fields_data.get(key, ""))

            pattern = re.compile(r"{{\\s*(\\w+)\\s*}}")
            output = pattern.sub(substitute, template.layout_markdown)
    elif template.layout_type == LayoutType.JINJA_RENDERING:
        if template.layout_jinja:
            try:
                jinja_template = JinjaTemplate(template.layout_jinja)
                output = jinja_template.render(**fields_data)
            except Exception as e:
                logger.error(
                    "Error rendering Jinja template", source_id=source.id, error=str(e)
                )
                output = None
    elif template.layout_type == LayoutType.WORD_TEMPLATE:
        output = "todo"
    else:
        output = "Unknown layout type."
    source.template_result = output
    source.save()
