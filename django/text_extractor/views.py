import io
import zipfile

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from pypdf.errors import PdfStreamError
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.priorities import MEDIUM
from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from otto.utils.decorators import budget_required, otto_user_required

from librarian.utils.process_engine import sanitize_content_type
from text_extractor.models import InputFile, OutputFile, UserRequest

from .tasks import process_document_merge, process_ocr_document
from .utils import format_merged_file_name

app_name = "text_extractor"
logger = get_logger(__name__)


@otto_user_required
def index(request):
    from text_extractor.utils import img_extensions

    extensions = ", ".join(list(img_extensions) + [".pdf"])
    return render(
        request,
        "text_extractor/ocr.html",
        {
            "active_app": "text_extractor",
            "extensions": extensions,
            "hide_breadcrumbs": True,
        },
    )


@budget_required
def submit_document(request):
    bind_contextvars(feature="text_extractor")

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)

    files = request.FILES.getlist("file_upload")
    logger.debug(_("Received {len(files)} files"))
    access_key = AccessKey(user=request.user)

    merged = request.POST.get("merge_docs_checkbox", False) == "on"
    ai_model = request.POST.get("ai_model", "document_intelligence")

    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)
    user_request = UserRequest.objects.create(
        access_key=access_key,
        merged=merged,
        name=request.user.username[:255],
        ai_model=ai_model,
    )
    output_files = []

    try:
        # First, save all uploaded files to InputFile model
        input_file_ids = []
        file_names = []
        for file in files:
            input_file = InputFile.objects.create(
                access_key=access_key,
                file=file,
                original_filename=file.name,
                content_type=sanitize_content_type(file.content_type)
                or "application/pdf",
                user_request=user_request,
            )
            input_file_ids.append(str(input_file.id))
            file_names.append(file.name)

        if merged:
            # Create single OutputFile for merged document
            formatted_merged_name = format_merged_file_name(file_names, max_length=40)

            merged_output_file = OutputFile.objects.create(
                access_key=access_key,
                pdf_file=None,
                txt_file=None,
                file_name=formatted_merged_name,
                user_request=user_request,
                celery_task_ids=[],
            )
            output_files = [merged_output_file]

            # Start the merged OCR task
            # Capture cost_group from session
            active_cost_group = request.user.get_active_cost_group(request)
            cost_group_id = str(active_cost_group.id) if active_cost_group else None

            result = process_document_merge.apply_async(
                kwargs={
                    "input_file_ids": input_file_ids,
                    "output_file_id": str(merged_output_file.id),
                    "user_id": str(request.user.id),
                    "cost_group_id": cost_group_id,
                    "ai_model": ai_model,
                },
                priority=MEDIUM,
            )
            # Store the task ID
            merged_output_file.celery_task_ids = [result.id]
            merged_output_file.save(access_key=access_key)
        else:
            # Create separate OutputFile for each input file
            for input_file_id, file_name in zip(input_file_ids, file_names):
                output_file = OutputFile.objects.create(
                    access_key=access_key,
                    pdf_file=None,
                    txt_file=None,
                    file_name=f"{file_name.rsplit('.', 1)[0]}_OCR",
                    user_request=user_request,
                    celery_task_ids=[],
                )

                # Capture cost_group from session (same for all files in batch)
                active_cost_group = request.user.get_active_cost_group(request)
                cost_group_id = str(active_cost_group.id) if active_cost_group else None

                result = process_ocr_document.apply_async(
                    kwargs={
                        "input_file_id": input_file_id,
                        "output_file_id": str(output_file.id),
                        "user_id": str(request.user.id),
                        "cost_group_id": cost_group_id,
                        "ai_model": ai_model,
                    },
                    priority=MEDIUM,
                )
                # Store the task ID
                output_file.celery_task_ids = [result.id]
                output_file.save(access_key=access_key)
                output_files.append(output_file)

        for output_file in output_files:
            output_file.status = "PENDING"

        context = {
            "output_files": output_files,
            "user_request_id": user_request.id,
            "poll_url": reverse("text_extractor:poll_tasks", args=[user_request.id]),
        }

        return render(request, "text_extractor/completed_documents.html", context)

    except PdfStreamError:
        logger.exception(
            _(
                "PDFStreamError while processing files - invalid or corrupted pdf uploaded"
            )
        )
        return render(
            request,
            "text_extractor/error_message.html",
            {
                "error_message": _(
                    "Error: One or more of your files is not a valid PDF/image or is corrupted."
                )
            },
        )
    except Exception:
        logger.exception(
            _("Sorry, we ran into an error while running OCR"),
            user_request_id=user_request.id,
        )

        return render(
            request,
            "text_extractor/error_message.html",
            {"error_message": _("Error running OCR on documents")},
        )


def poll_tasks(request, user_request_id):
    access_key = AccessKey(user=request.user)
    user_request = UserRequest.objects.get(access_key=access_key, id=user_request_id)
    output_files = user_request.output_files.filter(access_key=access_key)
    ai_model = user_request.ai_model

    for output_file in output_files:
        output_file_statuses = []
        has_error_message = bool(output_file.error_message)

        if has_error_message and not output_file.txt_file:
            # Complete failure - no output generated
            output_file_statuses.append("FAILURE")
        else:
            for task_id in output_file.celery_task_ids:
                # Both tasks now can be either merge or ocr
                # Try ocr first (most common), fall back to merge
                result = process_ocr_document.AsyncResult(task_id)
                if result.status == "PENDING" and not result.info:
                    # Might be a merge task instead
                    result = process_document_merge.AsyncResult(task_id)
                output_file_statuses.append(result.status)

        if all(status == "SUCCESS" for status in output_file_statuses):
            if ai_model == "document_intelligence":
                if output_file.pdf_file and output_file.txt_file:
                    # Check for partial failure (error_message set but files exist)
                    output_file.status = "WARNING" if has_error_message else "SUCCESS"
                else:
                    output_file.status = "PROCESSING"
            else:
                if output_file.txt_file:
                    # Check for partial failure (error_message set but files exist)
                    output_file.status = "WARNING" if has_error_message else "SUCCESS"
                else:
                    output_file.status = "PROCESSING"
        elif any(status == "FAILURE" for status in output_file_statuses):
            output_file.status = "FAILURE"
        else:
            # Some tasks are still pending or processing
            if any(
                status in ["STARTED", "PROCESSING"] for status in output_file_statuses
            ):
                output_file.status = "PROCESSING"
            else:
                output_file.status = "PENDING"

    for output_file in output_files:
        output_file.cost = display_cad_cost(output_file.usd_cost)
        if output_file.txt_file:
            output_file.txt_size = file_size_to_string(output_file.txt_file.size)
        if output_file.pdf_file:
            output_file.pdf_size = file_size_to_string(output_file.pdf_file.size)
    statuses = [f.status for f in output_files]
    show_download_all_button = (
        len(output_files) > 0
        and all(s in ("SUCCESS", "FAILURE") for s in statuses)
        and any(s == "SUCCESS" for s in statuses)
    )
    context = {
        "output_files": output_files,
        "show_download_all_button": show_download_all_button,
    }

    if any(
        output_file.status in ["PENDING", "PROCESSING"] for output_file in output_files
    ):
        context.update(
            {"poll_url": reverse("text_extractor:poll_tasks", args=[user_request.id])}
        )

    # In an HTMX request, we just want the updated rows.
    if request.headers.get("HX-Request") == "true":
        context.update({"swap": "true"})
        return render(request, "text_extractor/completed_documents.html", context)

    # Otherwise, render the whole page with the updated rows.
    from text_extractor.utils import img_extensions

    context.update(
        {
            "extensions": ", ".join(list(img_extensions) + [".pdf"]),
            "show_output": True,
            "refresh_on_load": False,
            "hide_breadcrumbs": True,
            "show_download_all_button": show_download_all_button,
        }
    )
    return render(request, "text_extractor/ocr.html", context)


def download_document(request, file_id, file_type):
    access_key = AccessKey(user=request.user)

    try:
        output_file = OutputFile.objects.get(access_key=access_key, id=file_id)
    except OutputFile.DoesNotExist:
        return render(request, "text_extractor/error_message.html")

    if file_type == "pdf":
        file = output_file.pdf_file
    elif file_type == "txt":
        file = output_file.txt_file
    with file.open("rb") as file:
        response = HttpResponse(file.read(), content_type="application/octet-stream")
        response["Content-Disposition"] = (
            f'attachment; filename="{output_file.file_name}.{file_type}"'
        )
        return response


def download_all_zip(request):
    access_key = AccessKey(user=request.user)
    try:
        user_request = (
            UserRequest.objects.filter(access_key=access_key)
            .order_by("-created_at")
            .first()
        )
        if not user_request:
            raise UserRequest.DoesNotExist()
    except UserRequest.DoesNotExist:
        return render(
            request,
            "text_extractor/error_message.html",
            {"error_message": _("Invalid download request.")},
        )

    output_files = user_request.output_files.filter(access_key=access_key)
    if not output_files:
        return render(
            request,
            "text_extractor/error_message.html",
            {
                "error_message": _(
                    "No files to download. Please process documents first."
                )
            },
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for output_file in output_files:
            if output_file.pdf_file:
                with output_file.pdf_file.open("rb") as f:
                    zip_file.writestr(f"{output_file.file_name}.pdf", f.read())
            if output_file.txt_file:
                with output_file.txt_file.open("rb") as f:
                    zip_file.writestr(f"{output_file.file_name}.txt", f.read())

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        'attachment; filename="otto_text_extractor_downloads.zip"'
    )
    return response
