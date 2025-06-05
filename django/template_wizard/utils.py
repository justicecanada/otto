# Shared helpers for template wizard
from typing import List, Optional

from pydantic import Field, create_model
from structlog import get_logger

from chat.utils import url_to_text
from librarian.utils.process_engine import (
    extract_markdown,
    get_process_engine_from_type,
)
from template_wizard.models import Source, Template, TemplateSession

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
    pass


def fill_template_from_fields(source):
    """
    Fills the template with the extracted fields, saves to source.template_result (TextField)
    """
    pass
