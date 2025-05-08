import logging
import os
from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import current_task, shared_task

from .utils import create_searchable_pdf

logger = logging.getLogger(__name__)


# passing the OCR method to celery
@shared_task
def process_ocr_document(file_content, file_name, merged, idx):
    if current_task:
        current_task.update_state(state="PROCESSING")
    try:
        file = InMemoryUploadedFile(
            file=BytesIO(file_content),
            field_name=None,
            name=file_name,
            content_type="application/pdf",
            size=len(file_content),
            charset=None,
        )
        # ocr_file, txt_file, cost = create_searchable_pdf(
        #     file, merged and idx > 0, merged
        # )
        result = create_searchable_pdf(file, merged and idx > 0, merged)

        ocr_file = result["output"]
        txt_file = result["all_text"]
        cost = result["cost"]

        input_name, _ = os.path.splitext(file.name)
        pdf_bytes = BytesIO()
        ocr_file.write(pdf_bytes)

        # return pdf_bytes.getvalue(), txt_file, cost, input_name
        return {
            "error": False,
            "pdf_bytes": pdf_bytes.getvalue(),
            "txt_file": txt_file,
            "cost": cost,
            "input_name": input_name,
        }

    except Exception as e:
        import traceback
        import uuid

        from django.utils.translation import gettext as _

        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            f"Error processing file {file_name} in task {current_task.request.id}: {e}"
        )
        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }
