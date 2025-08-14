import io
import logging
import os
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import current_task, shared_task
from PIL import Image, ImageSequence
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from otto.secure_models import AccessKey

from .models import OutputFile, UserRequest
from .utils import (
    calculate_start_pages,
    create_searchable_pdf,
    create_toc_pdf,
    img_extensions,
    resize_image_to_a4,
    shorten_input_name,
)

logger = logging.getLogger(__name__)


# New task for merging files before OCR
@shared_task
def process_document_merge(
    files_data,
    output_file_id,
    user_id,
):
    if current_task:
        current_task.update_state(state="PROCESSING")

    # Reconstruct the access key from user ID
    User = get_user_model()
    user = User.objects.get(id=user_id)
    access_key = AccessKey(user=user)
    try:

        # create merged pdf
        merged_pdf_writer = PdfWriter()

        for file_data in files_data:
            file_name, file_content, content_type = file_data

            if content_type == "application/pdf" or file_name.lower().endswith(".pdf"):
                pdf_reader = PdfReader(BytesIO(file_content))
                for page in pdf_reader.pages:
                    merged_pdf_writer.add_page(page)

            elif file_name.lower().endswith(img_extensions):
                with Image.open(BytesIO(file_content)) as img:
                    images_pages = [
                        resize_image_to_a4(image, header_text=file_name)
                        for image in ImageSequence.Iterator(img)
                    ]

                    # Convert PIL images to PDF and add to merger
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as temp_file:
                        if images_pages:
                            if len(images_pages) == 1:
                                # Single image - don't use save_all
                                images_pages[0].save(
                                    temp_file, format="PDF", resolution=100
                                )
                            else:
                                # Multiple images - use save_all
                                images_pages[0].save(
                                    temp_file,
                                    format="PDF",
                                    save_all=True,
                                    append_images=images_pages[1:],
                                    resolution=100,
                                )
                        temp_path = temp_file.name

                    # Read the temp PDF and add pages to merger
                    if images_pages:
                        with open(temp_path, "rb") as pdf_file:
                            image_pdf_reader = PdfReader(pdf_file)
                            for page in image_pdf_reader.pages:
                                merged_pdf_writer.add_page(page)
                        os.unlink(temp_path)  # Clean up temp file

            else:
                logger.warning(f"Unsupported file type for {file_name}")
                raise ValueError(f"Unsupported file type for {file_name}")

        merged_pdf_bytes = BytesIO()
        merged_pdf_writer.write(merged_pdf_bytes)
        merged_pdf_content = merged_pdf_bytes.getvalue()

        output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)
        output_file.pdf_file = ContentFile(
            merged_pdf_content, name="merged_document.pdf"
        )
        output_file.celery_task_ids = []  # Clear task IDs since merge is complete
        output_file.save(access_key=access_key)

        logger.info(
            f"Successfully merged and saved PDF file for output_file {output_file_id}"
        )

        return {
            "error": False,
            "message": "Files merged successfully",
            "output_file_id": output_file.id,
        }

    except Exception as e:
        import traceback
        import uuid

        from django.utils.translation import gettext as _

        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            f"Error processing merging files in task {current_task.request.id}: {e}"
        )

        output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)
        output_file.error_message = f"Error processing file: {e}"
        output_file.save(access_key=access_key)

        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }


# passing the OCR method to celery
@shared_task
def process_ocr_document(file_content, file_name, output_file_id, user_id):
    if current_task:
        current_task.update_state(state="PROCESSING")

        # Reconstruct the access key from user ID
        User = get_user_model()
        user = User.objects.get(id=user_id)
        access_key = AccessKey(user=user)

    try:
        file = InMemoryUploadedFile(
            file=BytesIO(file_content),
            field_name=None,
            name=file_name,
            content_type="application/pdf",
            size=len(file_content),
            charset=None,
        )

        result = create_searchable_pdf(file)

        pdf_content = result["pdf_content"]
        text_content = result["all_text"]
        cost = result["cost"]

        input_name, _ = os.path.splitext(file.name)

        output_name = shorten_input_name(input_name)

        # Convert generator to bytes
        pdf_bytes = b"".join(chunk for chunk in pdf_content)
        pdf_file = ContentFile(pdf_bytes, name=f"{output_name}.pdf")
        txt_file = ContentFile(
            text_content.encode("utf-8"),
            name=shorten_input_name(f"{output_name}.txt"),
        )

        output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)
        # Clear the task IDs and update cost
        output_file.usd_cost = cost
        output_file.pdf_file = pdf_file
        output_file.txt_file = txt_file
        output_file.celery_task_ids = []
        output_file.save(access_key=access_key)

        if not pdf_file or not txt_file:
            raise ValueError("Failed to generate output files.")

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

        output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)
        output_file.error_message = f"Error processing file: {e}"
        output_file.save(access_key=access_key)

        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }
