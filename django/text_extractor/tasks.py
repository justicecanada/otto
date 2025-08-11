import logging
import os
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

from celery import current_task, shared_task
from pypdf import PdfReader, PdfWriter

from otto.secure_models import AccessKey

from .models import OutputFile, UserRequest
from .utils import (
    calculate_start_pages,
    create_searchable_pdf,
    create_toc_pdf,
    resize_image_to_a4,
    shorten_input_name,
)

logger = logging.getLogger(__name__)


# New task for merging files before OCR
@shared_task
def process_document_merge(
    files_data,
    formatted_merge_name,
    output_file_id,
    user_id,
    enlarge_size=False,
):
    if current_task:
        current_task.update_state(state="PROCESSING")

    try:
        # Reconstruct the access key from user ID
        User = get_user_model()
        user = User.objects.get(id=user_id)
        access_key = AccessKey(user=user)

        # create merged pdf
        merged_pdf_writer = PdfWriter()
        import tempfile

        from PIL import Image, ImageChops, ImageSequence
        from PIL.Image import Resampling

        from librarian.utils.process_engine import resize_to_azure_requirements

        print("Starting to process document merge")

        for file_data in files_data:
            file_name, file_content, content_type = file_data

            is_pdf = content_type == "application/pdf" or file_name.lower().endswith(
                ".pdf"
            )
            print(f"Processing file: {file_name}, is_pdf: {is_pdf}")
            if is_pdf:
                pdf_reader = PdfReader(BytesIO(file_content))
                for page in pdf_reader.pages:
                    merged_pdf_writer.add_page(page)
            else:
                print(f"Processing non-PDF file: {file_name}")
                try:
                    print(f"File content type: {type(file_content)}")
                    print(
                        f"File content length: {len(file_content) if hasattr(file_content, '__len__') else 'No length'}"
                    )
                    print(
                        f"First 20 bytes: {file_content[:20] if isinstance(file_content, bytes) else 'Not bytes'}"
                    )
                except Exception as debug_error:
                    print(f"Error in debug prints: {debug_error}")

                try:
                    print("About to open image with PIL...")
                    with Image.open(BytesIO(file_content)) as img:
                        print(
                            f"Successfully opened image: {img.format}, {img.size}, {img.mode}"
                        )

                        # COMMENTED OUT: Original complex image conversion code
                        # image_pages_original = ImageSequence.Iterator(img)
                        # print(
                        #     f"Found {len(list(image_pages_original))} frames in image."
                        # )
                        # # Need to recreate iterator since we consumed it above
                        # image_pages_original = ImageSequence.Iterator(img)
                        # images_pages = [
                        #     resize_image_to_a4(image) for image in image_pages_original
                        # ]
                        # print(f"here are the pages: {images_pages}")
                        #
                        # # Convert PIL images to PDF and add to merger
                        # with tempfile.NamedTemporaryFile(
                        #     suffix=".pdf", delete=False
                        # ) as temp_file:
                        #     if images_pages:
                        #         if len(images_pages) == 1:
                        #             # Single image - don't use save_all
                        #             images_pages[0].save(temp_file, format="PDF")
                        #         else:
                        #             # Multiple images - use save_all
                        #             images_pages[0].save(
                        #                 temp_file,
                        #                 format="PDF",
                        #                 save_all=True,
                        #                 append_images=images_pages[1:],
                        #             )
                        #     temp_path = temp_file.name
                        #
                        # # Read the temp PDF and add pages to merger
                        # if images_pages:
                        #     with open(temp_path, "rb") as pdf_file:
                        #         image_pdf_reader = PdfReader(pdf_file)
                        #         for page in image_pdf_reader.pages:
                        #             merged_pdf_writer.add_page(page)
                        #     os.unlink(temp_path)  # Clean up temp file

                        # Simple experiment: convert image directly to PDF without resizing
                        print("Converting image directly to PDF at original size...")
                        with tempfile.NamedTemporaryFile(
                            suffix=".pdf", delete=False
                        ) as temp_file:
                            # Convert to RGB if needed (PDF doesn't support RGBA)
                            if img.mode in ("RGBA", "LA", "P"):
                                img_rgb = img.convert("RGB")
                            else:
                                img_rgb = img

                            img_rgb.save(temp_file, format="PDF")
                            temp_path = temp_file.name

                        print(f"Saved image as PDF to: {temp_path}")

                        # Read the temp PDF and add pages to merger
                        with open(temp_path, "rb") as pdf_file:
                            image_pdf_reader = PdfReader(pdf_file)
                            print(f"PDF has {len(image_pdf_reader.pages)} pages")
                            for page in image_pdf_reader.pages:
                                merged_pdf_writer.add_page(page)
                        os.unlink(temp_path)  # Clean up temp file
                        print("Successfully added image to merged PDF")
                except Exception as img_error:
                    print(f"CRITICAL ERROR opening image: {img_error}")
                    print(f"Error type: {type(img_error)}")
                    import traceback

                    print(f"Full traceback: {traceback.format_exc()}")
                    raise

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

        # Fallback for other exceptions
        return {
            "error": True,
            "error_id": error_id,
            "message": _("Corruption/Type mismatch.\n ")
            + "\nError ID: %(error_id)s" % {"error_id": error_id},
        }


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

        output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)

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
