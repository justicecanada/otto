import os
import tempfile
import threading
import time
from contextlib import contextmanager
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SynchronousOnlyOperation
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _  # noqa

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeOutputOption,
    DocumentContentFormat,
)
from azure.core.credentials import AzureKeyCredential
from celery import current_task, shared_task
from PIL import Image, ImageSequence
from pypdf import PdfReader, PdfWriter
from structlog import get_logger

from otto.models import Cost
from otto.priorities import HIGH
from otto.secure_models import AccessKey
from otto.utils.common import get_temp_dir

from .models import InputFile, OutputFile
from .utils import (
    gpt_extract_text,
    img_extensions,
    resize_image_to_a4,
    shorten_input_name,
)

logger = get_logger(__name__)


@contextmanager
def _allow_async_unsafe():
    """Temporarily disable Django async safety guard for known sync worker paths."""
    env_key = "DJANGO_ALLOW_ASYNC_UNSAFE"
    original = os.environ.get(env_key)
    os.environ[env_key] = "true"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original


def _get_real_thread_class():
    """Return an OS-thread class even when gevent monkey patching is active."""
    try:
        from gevent import monkey as gevent_monkey

        return gevent_monkey.get_original("threading", "Thread")
    except Exception:
        return threading.Thread


def _run_db_operation(func, *args, **kwargs):
    """Run a DB operation, retrying in a dedicated thread if async-context guarded.

    Django raises SynchronousOnlyOperation when ORM code runs while an asyncio
    loop is active on the current thread. Celery gevent workers can occasionally
    surface this; retrying the ORM call in a separate thread avoids that context.
    """
    try:
        return func(*args, **kwargs)
    except SynchronousOnlyOperation:
        # First retry in the same execution context with async guard temporarily disabled.
        try:
            with _allow_async_unsafe():
                return func(*args, **kwargs)
        except SynchronousOnlyOperation:
            # Final retry in a real OS thread with async guard disabled.
            result: dict[str, object] = {}
            error: dict[str, Exception] = {}

            def _runner():
                try:
                    with _allow_async_unsafe():
                        result["value"] = func(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - propagated to caller
                    error["value"] = exc

            thread_class = _get_real_thread_class()
            thread = thread_class(target=_runner, daemon=True)
            thread.start()
            thread.join()

            if "value" in error:
                raise error["value"]
            return result.get("value")


# Merging files before OCR
@shared_task(soft_time_limit=600, queue=settings.HEAVY_QUEUE)
def process_document_merge(
    input_file_ids,
    output_file_id,
    user_id,
    ai_model="document_intelligence",
    cost_group_id=None,
):
    from structlog.contextvars import bind_contextvars

    if current_task:
        current_task.update_state(state="PROCESSING")

    # Bind context for cost attribution
    bind_contextvars(
        feature="text_extractor",
        user_id=user_id,
        cost_group_id=cost_group_id,
    )

    access_key = None
    output_file = None
    try:
        # Reconstruct the access key from user ID
        User = get_user_model()
        user = _run_db_operation(User.objects.get, id=user_id)
        access_key = AccessKey(user=user)

        # Track file names and their start pages for TOC
        file_names_and_pages = []
        current_page = 2  # Start at page 2 (page 1 will be the TOC)

        # create merged pdf
        merged_pdf_writer = PdfWriter()

        for input_file_id in input_file_ids:
            input_file = _run_db_operation(
                InputFile.objects.get, access_key=access_key, id=input_file_id
            )
            file_name = input_file.original_filename
            content_type = input_file.content_type

            # Track this file's start page
            file_names_and_pages.append((file_name, current_page))

            # Open the file from storage
            with input_file.file.open("rb") as file_handle:
                file_content = file_handle.read()

            if content_type == "application/pdf" or file_name.lower().endswith(".pdf"):
                pdf_reader = PdfReader(BytesIO(file_content))
                page_count = len(pdf_reader.pages)
                for page in pdf_reader.pages:
                    merged_pdf_writer.add_page(page)
                current_page += page_count

            elif file_name.lower().endswith(img_extensions):
                with Image.open(BytesIO(file_content)) as img:
                    images_pages = [
                        resize_image_to_a4(image)
                        for image in ImageSequence.Iterator(img)
                    ]

                    # Convert PIL images to PDF and add to merger
                    temp_dir = get_temp_dir()
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False, dir=temp_dir
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
                            page_count = len(image_pdf_reader.pages)
                            for page in image_pdf_reader.pages:
                                merged_pdf_writer.add_page(page)
                            current_page += page_count
                        os.unlink(temp_path)  # Clean up temp file

            else:
                logger.warning(f"Unsupported file type for {file_name}")
                raise ValueError(f"Unsupported file type for {file_name}")

        # Now create the TOC with the actual page numbers
        from .utils import create_toc_pdf

        toc_pdf_bytes = create_toc_pdf(file_names_and_pages)
        toc_reader = PdfReader(toc_pdf_bytes)

        # Insert TOC at the beginning
        final_pdf_writer = PdfWriter()
        for page in toc_reader.pages:
            final_pdf_writer.add_page(page)
        for page in merged_pdf_writer.pages:
            final_pdf_writer.add_page(page)

        merged_pdf_bytes = BytesIO()
        final_pdf_writer.write(merged_pdf_bytes)
        merged_pdf_content = merged_pdf_bytes.getvalue()

        # Get output file to access user_request
        output_file = _run_db_operation(
            OutputFile.objects.get, access_key=access_key, id=output_file_id
        )

        # Save the merged PDF as a temporary InputFile for the OCR task
        from django.core.files.base import ContentFile

        merged_input_file = InputFile.objects.create(
            access_key=access_key,
            file=ContentFile(merged_pdf_content, name="merged_document.pdf"),
            original_filename="merged_document.pdf",
            content_type="application/pdf",
            user_request=output_file.user_request,
        )

        logger.info(
            f"Successfully merged PDF, now chaining to OCR for output_file {output_file_id}"
        )

        # Chain to OCR
        res = process_ocr_document.apply_async(
            kwargs={
                "input_file_id": str(merged_input_file.id),
                "output_file_id": output_file_id,
                "user_id": user_id,
                "ai_model": ai_model,
                "cost_group_id": cost_group_id,
            },
            priority=HIGH,
        )

        # Update task ID so UI polling continues
        output_file.celery_task_ids = [res.id]
        _run_db_operation(output_file.save, access_key=access_key)

        return {
            "error": False,
            "message": "Files merged successfully, OCR in progress",
            "output_file_id": output_file.id,
            "chained_to": res.id,
        }

    except Exception as e:
        import traceback
        import uuid

        from otto.utils.common import generate_ai_error_summary

        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            f"Error processing merging files in task {current_task.request.id}: {e}",
            error_id=error_id,
            traceback=traceback.format_exc(),
        )

        # Use AI to generate user-friendly error message (plain text for display)
        ai_summary = generate_ai_error_summary(
            e, error_id, include_trace=False, plain_text=True
        )

        persisted_error_state = False
        try:
            if output_file is None:
                if access_key:
                    output_file = _run_db_operation(
                        OutputFile.objects.get, access_key=access_key, id=output_file_id
                    )
                else:
                    output_file = _run_db_operation(
                        OutputFile._base_manager.get, id=output_file_id
                    )

            output_file.error_message = ai_summary
            output_file.celery_task_ids = []
            if access_key:
                _run_db_operation(output_file.save, access_key=access_key)
            else:
                _run_db_operation(output_file.save, AccessKey(bypass=True))
            persisted_error_state = True
        except Exception:
            logger.exception(
                "Failed to persist merge error state",
                output_file_id=output_file_id,
                task_id=current_task.request.id if current_task else None,
            )

        if not persisted_error_state:
            raise

        return {
            "error": True,
            "error_id": error_id,
            "message": ai_summary,
        }


@shared_task(soft_time_limit=600, queue=settings.LIGHT_QUEUE)
def process_ocr_document(
    input_file_id,
    output_file_id,
    user_id,
    ai_model="document_intelligence",
    cost_group_id=None,
):
    from structlog.contextvars import bind_contextvars

    if current_task:
        current_task.update_state(state="PROCESSING")

    # Bind context for cost attribution
    bind_contextvars(
        feature="text_extractor",
        user_id=user_id,
        cost_group_id=cost_group_id,
    )

    access_key = None
    file_name = "unknown"
    output_file = None

    try:
        # Reconstruct the access key from user ID
        User = get_user_model()
        user = _run_db_operation(User.objects.get, id=user_id)
        access_key = AccessKey(user=user)

        output_file = _run_db_operation(
            OutputFile.objects.get, access_key=access_key, id=output_file_id
        )

        # Get the input file from the database
        input_file = _run_db_operation(
            InputFile.objects.get, access_key=access_key, id=input_file_id
        )
        file_name = input_file.original_filename

        with input_file.file.open("rb") as file_handle:
            file_content = file_handle.read()

        # For images, check and adjust dimensions to meet Azure requirements (50x50 to 10000x10000 pixels)
        if file_name and file_name.lower().endswith(img_extensions):
            from librarian.utils.process_engine import resize_to_azure_requirements

            file_content = resize_to_azure_requirements(file_content)

        usd_cost = 0.0
        pdf_bytes = b""

        if ai_model == "document_intelligence":
            document_analysis_client = DocumentIntelligenceClient(
                endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
                credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
                headers={"x-ms-useragent": "otto-text-extractor/1.0.0"},
            )

            poller = document_analysis_client.begin_analyze_document(
                model_id="prebuilt-read",
                body=file_content,
                output=[AnalyzeOutputOption.PDF],
                output_content_format=DocumentContentFormat.MARKDOWN,
            )

            start_time_ocr = time.perf_counter()
            ocr_results = poller.result()
            elapsed_time_ocr = time.perf_counter() - start_time_ocr
            logger.info(
                "OCR polling completed",
                elapsed_seconds=f"{elapsed_time_ocr:.2f}",
                output_file_id=output_file_id,
            )

            page_count = len(ocr_results.pages)
            logger.debug(
                "Azure Document Intelligence OCR finished text",
                page_count=page_count,
                output_file_id=output_file_id,
            )
            cost = Cost.objects.new(cost_type="doc-ai-read", count=page_count)
            usd_cost = cost.usd_cost

            all_text = ocr_results["content"]
            failed_pages = []  # Document Intelligence OCR doesn't have partial failures

            start_time_pdf = time.perf_counter()
            pdf_content = document_analysis_client.get_analyze_result_pdf(
                model_id="prebuilt-read", result_id=poller.details["operation_id"]
            )
            elapsed_time_pdf = time.perf_counter() - start_time_pdf
            logger.info(
                "PDF retrieval completed",
                elapsed_seconds=f"{elapsed_time_pdf:.2f}",
                output_file_id=output_file_id,
            )

            pdf_bytes = b"".join(chunk for chunk in pdf_content)
        else:
            # dont delete, keep for later --- IGNORE ---
            # all_text, pdf_bytes, usd_cost = gpt_extract_text(
            #     file_name=file_name, file_content=file_content
            # )
            all_text, usd_cost, failed_pages = gpt_extract_text(
                file_name=file_name, file_content=file_content
            )
            if not all_text.strip():
                raise ValueError("No text extracted using GPT Vision")

        # Save results
        input_name, _ = os.path.splitext(file_name)
        output_name = shorten_input_name(input_name)

        if pdf_bytes:
            pdf_file = ContentFile(pdf_bytes, name=f"{output_name}.pdf")
        else:
            pdf_file = None
        txt_file = ContentFile(
            all_text.encode("utf-8"),
            name=shorten_input_name(f"{output_name}.txt"),
        )

        # Clear the task IDs and update cost
        output_file.usd_cost = usd_cost
        output_file.pdf_file = pdf_file
        output_file.txt_file = txt_file
        output_file.celery_task_ids = []

        # Set warning message if some pages failed (for GPT vision only)
        if ai_model != "document_intelligence" and failed_pages:
            if len(failed_pages) == 1:
                output_file.error_message = f"{_('Processed with errors')}: {_('Page')} {failed_pages[0]} {_('failed to extract')}"
            else:
                pages_list = ", ".join(str(p) for p in failed_pages)
                output_file.error_message = f"{_('Processed with errors')}: {_('Pages')} {pages_list} {_('failed to extract')}"

        _run_db_operation(output_file.save, access_key=access_key)

        if not txt_file:
            raise ValueError("Failed to generate output txt files.")
        if ai_model == "document_intelligence" and not pdf_file:
            raise ValueError(
                "Failed to generate output pdf file for Document Intelligence OCR model."
            )

        logger.info(
            "OCR processing completed successfully",
            output_file_id=output_file_id,
            cost_usd=usd_cost,
        )

        return {
            "error": False,
            "cost": usd_cost,
            "input_name": input_name,
        }

    except Exception as e:
        import traceback
        import uuid

        from otto.utils.common import generate_ai_error_summary

        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            "Error processing OCR",
            file_name=file_name,
            task_id=current_task.request.id if current_task else None,
            error_id=error_id,
            traceback=traceback.format_exc(),
        )

        # Use AI to generate user-friendly error message (plain text for display)
        ai_summary = generate_ai_error_summary(
            e, error_id, include_trace=False, plain_text=True
        )

        persisted_error_state = False
        try:
            if output_file is None:
                if access_key:
                    output_file = _run_db_operation(
                        OutputFile.objects.get, access_key=access_key, id=output_file_id
                    )
                else:
                    output_file = _run_db_operation(
                        OutputFile._base_manager.get, id=output_file_id
                    )

            output_file.error_message = ai_summary
            output_file.celery_task_ids = []
            if access_key:
                _run_db_operation(output_file.save, access_key=access_key)
            else:
                _run_db_operation(output_file.save, AccessKey(bypass=True))
            persisted_error_state = True
        except Exception:
            logger.exception(
                "Failed to persist OCR error state",
                output_file_id=output_file_id,
                task_id=current_task.request.id if current_task else None,
            )

        if not persisted_error_state:
            # Ensure task is marked FAILURE so UI cannot spin forever.
            raise

        return {
            "error": True,
            "error_id": error_id,
            "message": ai_summary,
        }
