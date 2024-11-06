import os
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from celery import shared_task

from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from text_extractor.models import OutputFile, UserRequest

from .utils import create_searchable_pdf, shorten_input_name


@shared_task
def process_ocr_document(file_path, merged, idx):
    with open(file_path, "rb") as file:
        ocr_file, txt_file, cost = create_searchable_pdf(file, merged and idx > 0)

    input_name, _ = os.path.splitext(file.name)
    pdf_bytes = BytesIO()
    ocr_file.write(pdf_bytes)

    return pdf_bytes, txt_file, cost, input_name
