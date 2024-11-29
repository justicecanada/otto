from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from otto.utils.decorators import app_access_required, budget_required
from text_extractor.models import OutputFile, UserRequest
from text_extractor.tasks import process_ocr_document
from text_extractor.utils import add_extracted_files

from .utils import lex_prompts

app_name = "lex_experiment"
logger = get_logger(__name__)


@app_access_required(app_name)
def index(request):
    from text_extractor.utils import img_extensions

    extensions = ", ".join(list(img_extensions) + [".pdf"])
    return render(
        request, "lex_experiment/lex_experiment.html", {"extensions": extensions}
    )


@app_access_required(app_name)
@budget_required
def submit_document(request):
    bind_contextvars(feature="lex_experiment")

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)

    files = request.FILES.getlist("file_upload")
    logger.debug(f"Received {len(files)} files")
    access_key = AccessKey(user=request.user)

    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    user_request = UserRequest.objects.create(
        access_key=access_key, merged=False, name=request.user.username[:255]
    )
    output_files = []
    task_ids = []

    try:

        for idx, file in enumerate(files):
            file.seek(0)
            file_content = file.read()
            file.seek(0)

            result = process_ocr_document.delay(file_content, file.name, False, idx)

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

        for output_file in output_files:
            output_file.status = "PENDING"

        context = {
            "output_files": output_files,
            "user_request_id": user_request.id,
            "poll_url": reverse("lex_experiment:poll_tasks", args=[user_request.id]),
        }

        return render(request, "lex_experiment/completed_documents.html", context)

    except Exception as e:
        # Improve error logging
        import traceback

        logger.error(f"ERROR: {str(e)}")
        logger.error(traceback.format_exc())
        return render(
            request, "lex_experiment/error_message.html", {"error_message": str(e)}
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
            question_results, cost_llm = lex_prompts(
                output_file.txt_file.read().decode("utf-8")
            )
            output_file.answers = [res["answer"] for res in question_results]
            output_file.usd_cost = Decimal(output_file.usd_cost) + Decimal(cost_llm)
            output_file.save(access_key=access_key)
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
            {"poll_url": reverse("lex_experiment:poll_tasks", args=[user_request.id])}
        )
    # else:
    #     "Download all" doesn't work in prod. Disabling for now.
    #     TODO: Zipped download of all files
    #     context.update({"show_download_all_button": True})

    # In an HTMX request, we just want the updated rows.
    if request.headers.get("HX-Request") == "true":
        return render(request, "lex_experiment/completed_documents.html", context)

    # Otherwise, render the whole page with the updated rows.
    from text_extractor.utils import img_extensions

    context.update(
        {
            "extensions": ", ".join(list(img_extensions) + [".pdf"]),
            "show_output": True,
            "refresh_on_load": False,
        }
    )
    return render(request, "lex_experiment/lex_experiment.html", context)
