import logging
import os
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import current_task, shared_task

from otto.secure_models import AccessKey

from .models import OutputFile, UserRequest
from .utils import create_searchable_pdf, shorten_input_name

logger = logging.getLogger(__name__)


# passing the OCR method to celery
@shared_task
def process_ocr_document(
    file_content,
    file_name,
    output_file_id,
    user_id,
):
    if current_task:
        current_task.update_state(state="PROCESSING")

    try:
        # Reconstruct the access key from user ID
        access_key = None
        User = get_user_model()
        user = User.objects.get(id=user_id)
        access_key = AccessKey(user=user)

        file = InMemoryUploadedFile(
            file=BytesIO(file_content),
            field_name=None,
            name=file_name,
            content_type="application/pdf",
            size=len(file_content),
            charset=None,
        )

        result = create_searchable_pdf(file)

        ocr_file = result["output"]
        text_content = result["all_text"]
        cost = result["cost"]

        pdf_bytes = BytesIO()
        ocr_file.write(pdf_bytes)
        pdf_content = pdf_bytes.getvalue()

        input_name, _ = os.path.splitext(file.name)

        output_name = shorten_input_name(input_name)

        pdf_file = ContentFile(pdf_content, name=f"{output_name}.pdf")
        txt_file = ContentFile(
            text_content.encode("utf-8"),
            name=shorten_input_name(f"{output_name}.txt"),
        )

        output_file = OutputFile.objects.get(access_key, id=output_file_id)

        # Clear the task IDs and update cost
        output_file.usd_cost = cost
        output_file.pdf_file = pdf_file
        output_file.txt_file = txt_file
        output_file.celery_task_ids = []
        output_file.save(access_key=access_key)

        return {
            "error": False,
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
