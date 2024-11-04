import os
from io import BytesIO

from django.core.files.base import ContentFile

from celery import shared_task

from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from text_extractor.models import OutputFile, UserRequest

from .utils import (
    calculate_start_pages,
    create_searchable_pdf,
    create_toc_pdf,
    format_merged_file_name,
    shorten_input_name,
)


@shared_task
def process_ocr_document(file, user_request_id, access_key_id, merged, idx):
    access_key = AccessKey.objects.get(id=access_key_id)
    user_request = UserRequest.objects.get(id=user_request_id)

    ocr_file, txt_file, cost = create_searchable_pdf(file, merged and idx > 0)
    input_name, _ = os.path.splitext(file.name)
    pdf_bytes = BytesIO()
    ocr_file.write(pdf_bytes)

    if merged:
        file_name = input_name
        pdf_bytes.seek(0)
        return file_name, pdf_bytes, txt_file, cost
    else:
        file_name = f"{input_name}_OCR.pdf"
        text_name = f"{input_name}_OCR.txt"

        content_file = ContentFile(
            pdf_bytes.getvalue(), name=shorten_input_name(file_name)
        )
        content_text = ContentFile(txt_file, name=shorten_input_name(text_name))

        output_file = OutputFile.objects.create(
            access_key=access_key,
            file=content_file,
            file_name=file_name,
            user_request=user_request,
        )

        output_text = OutputFile.objects.create(
            access_key=access_key,
            file=content_text,
            file_name=text_name,
            user_request=user_request,
        )

        output_file.save(access_key)
        output_text.save(access_key)

        return {
            "pdf": {
                "file": output_file,
                "size": file_size_to_string(output_file.file.size),
            },
            "txt": {
                "file": output_text,
                "size": file_size_to_string(output_text.file.size),
            },
            "cost": display_cad_cost(cost),
        }
