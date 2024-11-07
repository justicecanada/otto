import os
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import shared_task

from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from text_extractor.models import OutputFile, UserRequest

from .utils import create_searchable_pdf, shorten_input_name


# passing the OCR method to celery
@shared_task
def process_ocr_document(file_content, file_name, merged, idx):

    file = InMemoryUploadedFile(
        file=BytesIO(file_content),
        field_name=None,
        name=file_name,
        content_type="application/pdf",
        size=len(file_content),
        charset=None,
    )
    ocr_file, txt_file, cost = create_searchable_pdf(file, merged and idx > 0)

    input_name, _ = os.path.splitext(file.name)
    pdf_bytes = BytesIO()
    ocr_file.write(pdf_bytes)

    return pdf_bytes.getvalue(), txt_file, cost, input_name
