from celery import shared_task
from structlog import get_logger

from template_wizard.models import Source
from template_wizard.utils import (
    extract_fields,
    extract_source_text,
    fill_template_from_fields,
)

logger = get_logger(__name__)

ten_minutes = 600
one_minute = 60


@shared_task(soft_time_limit=ten_minutes)
def fill_template_with_source(source_id):
    source = Source.objects.filter(id=source_id).first()
    if not source:
        logger.error("Source not found", source_id=source_id)
        return
    try:
        source.status = "extracting_text"
        source.save()
        extract_source_text(source)
        source.status = "extracting_fields"
        source.save()
        extract_fields(source)
        source.status = "filling_template"
        source.save()
        fill_template_from_fields(source)
        source.status = "completed"
        source.save()
    except Exception as e:
        logger.error(
            "Error filling template with source", source_id=source_id, error=str(e)
        )
        source.status = "error"
        source.save()
        raise e
