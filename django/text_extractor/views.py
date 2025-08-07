from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from pdf2image.exceptions import PDFPageCountError
from pypdf.errors import PdfStreamError
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from otto.utils.decorators import app_access_required, budget_required
from text_extractor.models import OutputFile, UserRequest

from .tasks import process_merged_ocr_document, process_ocr_document
from .utils import add_extracted_files, format_merged_file_name

app_name = "text_extractor"
logger = get_logger(__name__)


@app_access_required(app_name)
def index(request):
    from text_extractor.utils import img_extensions

    extensions = ", ".join(list(img_extensions) + [".pdf"])
    return render(
        request,
        "text_extractor/ocr.html",
        {"extensions": extensions, "hide_breadcrumbs": True},
    )


@app_access_required(app_name)
@budget_required
def submit_document(request):
    bind_contextvars(feature="text_extractor")

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)

    files = request.FILES.getlist("file_upload")
    logger.debug(_("Received {len(files)} files"))
    access_key = AccessKey(user=request.user)
    merged = request.POST.get("merge_docs_checkbox", False) == "on"
    enlarge_size = request.POST.get("enlarge_size", None)  # default to 'small'

    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    user_request = UserRequest.objects.create(
        access_key=access_key, merged=merged, name=request.user.username[:255]
    )
    output_files = []

    try:
        if merged:
            # For merged documents, prepare data for the new merged task
            files_data = []
            file_names_to_merge = []

            for file in files:
                file.seek(0)
                file_content = file.read()
                file.seek(0)

                files_data.append({"content": file_content, "name": file.name})
                file_names_to_merge.append(file.name)

            # Format the merged file name
            formatted_merged_name = format_merged_file_name(
                file_names_to_merge, max_length=40
            )

            # Create single OutputFile for the merged result
            merged_output_file = OutputFile.objects.create(
                access_key=access_key,
                pdf_file=None,
                txt_file=None,
                file_name=formatted_merged_name,
                user_request=user_request,
                celery_task_ids=[],
            )

            # Start the merged OCR task
            result = process_merged_ocr_document.delay(
                files_data,
                formatted_merged_name,
                enlarge_size,
                str(merged_output_file.id),
                str(request.user.id),
            )

            # Store the task ID
            merged_output_file.celery_task_ids = [result.id]
            merged_output_file.save(access_key=access_key)

            output_files = [merged_output_file]

        else:
            # For individual files, process each separately (existing logic)
            for idx, file in enumerate(files):
                file.seek(0)
                file_content = file.read()
                file.seek(0)

                # Create individual OutputFile objects for each file
                output_file = OutputFile.objects.create(
                    access_key=access_key,
                    pdf_file=None,
                    txt_file=None,
                    file_name=f"{file.name.rsplit('.', 1)[0]}_OCR",
                    user_request=user_request,
                    celery_task_ids=[],
                )

                # Process individual files
                result = process_ocr_document.delay(
                    file_content,
                    file.name,
                    False,  # Not merged for individual processing
                    idx,
                    enlarge_size,
                    str(output_file.id),
                    str(request.user.id),
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

    except PdfStreamError as e:
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
    except Exception as e:
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
    user_request = UserRequest.objects.get(access_key, id=user_request_id)
    output_files = user_request.output_files.filter(access_key=access_key)

    # Check status for all output files (both individual and merged)
    for output_file in output_files:
        output_file_statuses = []
        for task_id in output_file.celery_task_ids:
            if user_request.merged:
                result = process_merged_ocr_document.AsyncResult(task_id)
            else:
                result = process_ocr_document.AsyncResult(task_id)
            status = result.status
            output_file_statuses.append(status)

        # Determine status based on task statuses and file existence
        if all(status == "SUCCESS" for status in output_file_statuses):
            # Check if files are stored by the task
            if not output_file.celery_task_ids:  # Task cleared its own task_ids
                output_file.status = "SUCCESS"
            else:
                output_file.refresh_from_db()
                if output_file.pdf_file:
                    # For merged files, if the merge is complete but no OCR yet, start OCR
                    if user_request.merged:
                        # Read the merged PDF file and start OCR task
                        with output_file.pdf_file.open("rb") as pdf_file:
                            merged_pdf_content = pdf_file.read()

                        # Start OCR task on the merged file
                        result = process_ocr_document.delay(
                            merged_pdf_content,
                            output_file.pdf_file.name,
                            False,  # Not relevant for merged file
                            0,  # Not relevant for merged file
                            None,  # enlarge_size - could pass from original request if needed
                            str(output_file.id),
                            str(request.user.id),
                        )

                        # Update task IDs and status
                        output_file.celery_task_ids = [result.id]
                        output_file.status = "PROCESSING"
                        output_file.save(access_key=access_key)

                        # set user request merged to False as OCR task started
                        user_request.merged = False
                        user_request.save(access_key=access_key)
                    else:
                        output_file.status = "SUCCESS"
                        # Clear task IDs if they weren't cleared by the task
                        if output_file.celery_task_ids:
                            output_file.celery_task_ids = []
                            output_file.save(access_key=access_key)
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

    # Update display properties for all output files
    for output_file in output_files:
        if output_file.pdf_file:
            output_file.cost = display_cad_cost(output_file.usd_cost)
            if output_file.txt_file:
                output_file.txt_size = file_size_to_string(output_file.txt_file.size)
            if output_file.pdf_file:
                output_file.pdf_size = file_size_to_string(output_file.pdf_file.size)

    context = {
        "output_files": output_files,
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
