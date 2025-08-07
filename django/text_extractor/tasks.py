import logging
import os
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import current_task, shared_task

from otto.secure_models import AccessKey

from .models import OutputFile, UserRequest
from .utils import create_searchable_pdf, shorten_input_name

logger = logging.getLogger(__name__)


# New task for merging files before OCR
@shared_task
def process_merged_ocr_document(
    files_data,  # List of dicts with 'content' and 'name' keys
    merged_file_name,
    enlarge_size=None,
    output_file_id=None,
    user_id=None,
):
    """
    Process multiple files by merging them first, then performing OCR on the merged file.
    files_data: List of dicts like [{'content': bytes, 'name': str}, ...]
    """
    if current_task:
        current_task.update_state(state="PROCESSING")

    # Reconstruct the access key from user ID
    access_key = None

    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.get(id=user_id)
    access_key = AccessKey(user=user)

    from pypdf import PdfReader, PdfWriter

    from .utils import calculate_start_pages, create_toc_pdf

    # Create InMemoryUploadedFile objects from the file data
    files = []
    for file_data in files_data:
        # Determine content type based on file extension
        file_name = file_data["name"]
        file_ext = os.path.splitext(file_name)[1].lower()

        if file_ext == ".pdf":
            content_type = "application/pdf"
        elif file_ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"):
            content_type = f"image/{file_ext[1:]}"  # Remove the dot
        else:
            content_type = "application/octet-stream"

        file = InMemoryUploadedFile(
            file=BytesIO(file_data["content"]),
            field_name=None,
            name=file_name,
            content_type=content_type,
            size=len(file_data["content"]),
            charset=None,
        )
        files.append(file)

    # Calculate start pages for TOC
    start_pages = calculate_start_pages(files)

    # Create TOC PDF
    toc_pdf_bytes = create_toc_pdf(files, start_pages)

    # Create merged PDF
    merged_pdf_writer = PdfWriter()

    # Add TOC as first page
    toc_reader = PdfReader(toc_pdf_bytes)
    for page in toc_reader.pages:
        merged_pdf_writer.add_page(page)

    # Add all other files to the merged PDF
    for file in files:
        file.seek(0)
        try:
            pdf_reader = PdfReader(file)
            for page in pdf_reader.pages:
                merged_pdf_writer.add_page(page)
        except Exception as e:
            logger.error(f"Failed to read PDF {file.name}: {e}")
            # Skip this file and continue
            continue

    # Write merged PDF to bytes
    merged_pdf_bytes_io = BytesIO()
    merged_pdf_writer.write(merged_pdf_bytes_io)
    merged_pdf_bytes_io.seek(0)
    merged_pdf_content = merged_pdf_bytes_io.read()

    output_file = OutputFile.objects.get(access_key, id=output_file_id)
    output_file.pdf_file = ContentFile(merged_pdf_content, name=merged_file_name)
    output_file.status = "COMPLETED"
    output_file.save(access_key=access_key)

    return {
        "error": False,
        "message": "Merged PDF created successfully.",
        "output_file_id": output_file_id,
        "pdf_file_name": merged_file_name,
    }


# passing the OCR method to celery
@shared_task
def process_ocr_document(
    file_content,
    file_name,
    merged,
    idx,
    enlarge_size=None,
    output_file_id=None,
    user_id=None,
):
    if current_task:
        current_task.update_state(state="PROCESSING")

    # Reconstruct the access key from user ID
    access_key = None
    if user_id:
        try:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            user = User.objects.get(id=user_id)
            access_key = AccessKey(user=user)
        except User.DoesNotExist:
            pass

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
        result = create_searchable_pdf(file, merged and idx > 0, merged, enlarge_size)

        if result.get("error"):
            # Handle error case - update the OutputFile directly
            if output_file_id and access_key:
                try:
                    output_file = OutputFile.objects.get(access_key, id=output_file_id)
                    output_file.status = "FAILURE"
                    output_file.error_message = result["message"]
                    output_file.celery_task_ids = []
                    output_file.save(access_key=access_key)
                except Exception as e:
                    pass
            return result

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

        if output_file_id and access_key:
            try:
                output_file = OutputFile.objects.get(access_key, id=output_file_id)

                # Clear the task IDs and update cost
                output_file.usd_cost = cost
                output_file.pdf_file = pdf_file
                output_file.txt_file = txt_file
                output_file.celery_task_ids = []
                output_file.save(access_key=access_key)

            except Exception as e:
                raise e

        return {
            "error": False,
            "cost": cost,
            "input_name": input_name,
            "completed": True,
        }

    except Exception as e:
        import traceback
        import uuid

        from django.utils.translation import gettext as _

        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            f"Error processing file {file_name} in task {current_task.request.id}: {e}"
        )

        # Update OutputFile with error if we have the ID
        if output_file_id and access_key:
            try:
                output_file = OutputFile.objects.get(access_key, id=output_file_id)
                output_file.status = "FAILURE"
                output_file.error_message = f"Error ID: {error_id} - {str(e)}"
                output_file.celery_task_ids = []
                output_file.save(access_key=access_key)
            except Exception:
                pass

        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }
