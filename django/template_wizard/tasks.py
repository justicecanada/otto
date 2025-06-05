from celery import current_task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger

from chat.utils import url_to_text
from librarian.utils.process_engine import (
    extract_markdown,
    get_process_engine_from_type,
)
from template_wizard.models import Source, Template, TemplateSession
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
    extract_source_text(source)
    extract_fields(source)
    fill_template_from_fields(source)

    return
