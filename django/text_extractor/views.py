from django.core.files.uploadedfile import InMemoryUploadedFile
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

from .tasks import process_ocr_document
from .utils import (
    add_extracted_files,
    calculate_start_pages,
    create_toc_pdf,
    format_merged_file_name,
)

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

    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    user_request = UserRequest.objects.create(
        access_key=access_key, merged=merged, name=request.user.username[:255]
    )
    output_files = []
    task_ids = []

    try:
        if merged:
            # Create the OutputFiles for the merged document only (not individual docs)
            file_names_to_merge = [file.name for file in files]
            formatted_merged_name = format_merged_file_name(
                file_names_to_merge, max_length=40
            )
            # Add Table of Contents as first file
            start_pages = calculate_start_pages(files)
            toc_pdf_bytes = create_toc_pdf(files, start_pages)
            toc_file = InMemoryUploadedFile(
                toc_pdf_bytes,
                "file",
                "toc.pdf",
                "application/pdf",
                toc_pdf_bytes.getbuffer().nbytes,
                None,
            )
            files.insert(0, toc_file)

        for idx, file in enumerate(files):
            file.seek(0)
            file_content = file.read()
            file.seek(0)

            result = process_ocr_document.delay(file_content, file.name, merged, idx)

            if merged:
                task_ids.append(result.id)
            else:
                output_files.append(
                    OutputFile.objects.create(
                        access_key=access_key,
                        pdf_file=None,
                        txt_file=None,
                        file_name=f"{file.name.rsplit('.', 1)[0]}_OCR",
                        user_request=user_request,
                        celery_task_ids=[result.id],
                    )
                )

        if merged:
            merged_output_file = OutputFile.objects.create(
                access_key=access_key,
                pdf_file=None,
                txt_file=None,
                file_name=formatted_merged_name,
                user_request=user_request,
                celery_task_ids=task_ids,
            )
            output_files = [merged_output_file]

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
    for output_file in output_files:
        output_file_statuses = []
        for task_id in output_file.celery_task_ids:
            result = process_ocr_document.AsyncResult(task_id)
            output_file_statuses.append(result.status)
        if all(status == "SUCCESS" for status in output_file_statuses):
            output_file.status = "SUCCESS"
            if not output_file.pdf_file:
                output_file = add_extracted_files(output_file, access_key)
        elif any(status == "FAILURE" for status in output_file_statuses):
            output_file.status = "FAILURE"
        else:
            output_file.status = result.status

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
    # else:
    #     "Download all" doesn't work in prod. Disabling for now.
    #     TODO: Zipped download of all files
    #     context.update({"show_download_all_button": True})

    # In an HTMX request, we just want the updated rows.
    if request.headers.get("HX-Request") == "true":
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
