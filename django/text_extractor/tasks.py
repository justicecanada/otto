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

from otto.secure_models import AccessKey

from .models import OutputFile
from .utils import (
    create_searchable_pdf,
    img_extensions,
    resize_image_to_a4,
    shorten_input_name,
)

logger = logging.getLogger(__name__)
ten_minutes = 600


# New task for merging files before OCR
@shared_task(bind=True)
def process_document_merge(self, files_data, output_file_id, user_id, rerouted=False):
    """
    Celery task to merge document files.
    Dynamically routes to heavy or light queue by total input file size.
    Returns the output file ID or raises on error.
    """
    MAX_LIGHT_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    # Compute total input file size (assumes files_data is a list of file bytes or dicts)
    total_file_size = 0
    if isinstance(files_data, list):
        for file in files_data:
            if hasattr(file, "__len__"):
                total_file_size += len(file)
            elif isinstance(file, dict) and "content" in file:
                total_file_size += len(file["content"])

    queue = self.request.delivery_info.get("routing_key")

    # Reroute if necessary
    if not rerouted:
        if total_file_size < MAX_LIGHT_FILE_SIZE and queue != "light":
            logger.info(
                f"process_document_merge: rerouting small merge job ({total_file_size} bytes) to lightworker."
            )
            result = process_document_merge.apply_async(
                args=[files_data, output_file_id, user_id],
                kwargs={"rerouted": True},
                queue="light",
            )
            return result.get(timeout=ten_minutes)
        elif total_file_size >= MAX_LIGHT_FILE_SIZE and queue != "heavy":
            logger.info(
                f"process_document_merge: rerouting large merge job ({total_file_size} bytes) to heavyworker."
            )
            result = process_document_merge.apply_async(
                args=[files_data, output_file_id, user_id],
                kwargs={"rerouted": True},
                queue="heavy",
            )
            return result.get(timeout=ten_minutes)

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
                        resize_image_to_a4(image)
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
@shared_task(bind=True)
def process_ocr_document(
    self, file_content, file_name, output_file_id, user_id, rerouted=False
):
    """
    Celery task to perform OCR on a document (bytestream or file).
    Dynamically routes to the heavy or light queue by file size.
    """

    MAX_LIGHT_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    # Compute file size
    file_size = len(file_content) if hasattr(file_content, "__len__") else 0
    queue = self.request.delivery_info.get("routing_key")

    # Reroute to appropriate queue if necessary
    if not rerouted:
        if file_size < MAX_LIGHT_FILE_SIZE and queue != "light":
            logger.info(
                f"process_ocr_document: rerouting small file ({file_size} bytes) to lightworker."
            )
            result = process_ocr_document.apply_async(
                args=[file_content, file_name, output_file_id, user_id],
                kwargs={"rerouted": True},
                queue="light",
            )
            return result.get(timeout=ten_minutes)
        elif file_size >= MAX_LIGHT_FILE_SIZE and queue != "heavy":
            logger.info(
                f"process_ocr_document: rerouting large file ({file_size} bytes) to heavyworker."
            )
            result = process_ocr_document.apply_async(
                args=[file_content, file_name, output_file_id, user_id],
                kwargs={"rerouted": True},
                queue="heavy",
            )
            return result.get(timeout=ten_minutes)

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

        if result["error"] == True:
            raise Exception(result["message"])

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
        output_file.error_message = e
        output_file.save(access_key=access_key)

        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }
