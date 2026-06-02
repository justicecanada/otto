from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import Cost
from otto.priorities import MEDIUM
from otto.secure_models import AccessKey
from otto.utils.common import display_cad_cost, file_size_to_string
from otto.utils.decorators import budget_required, otto_user_required

from librarian.utils.process_engine import sanitize_content_type

from .models import InputFile, OutputFile, UserRequest
from .tasks import translate_document_task
from .utils import translate_text_azure

app_name = "translate"
logger = get_logger(__name__)

MAX_FILE_SIZE = 40 * 1024 * 1024  # 40 MB (matches C# maxAllowedFileSize)


def _document_result_context(output_files, poll_url=None):
    downloadable_output_count = 0

    for output_file in output_files:
        output_file.result_size = (
            file_size_to_string(output_file.file.size) if output_file.file else None
        )
        if output_file.file:
            downloadable_output_count += 1

    return {
        "output_files": output_files,
        "poll_url": poll_url,
        "show_download_all": downloadable_output_count > 1,
    }


@otto_user_required
def index(request):
    return render(
        request,
        "translate/index.html",
        {
            "active_app": "translate",
            "hide_breadcrumbs": True,
            "show_output": False,
        },
    )


@otto_user_required
@budget_required
@require_POST
def translate_document(request):
    bind_contextvars(feature="translate")

    uploaded_files = request.FILES.getlist("file")
    target_lang = request.POST.get("language", "fr")
    source_lang = "fr" if target_lang == "en" else "en"

    if not uploaded_files:
        return render(
            request,
            "translate/components/document_result.html",
            {"error": _("No file uploaded.")},
        )

    access_key = AccessKey(user=request.user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name=uploaded_files[0].name[:255],
        source_lang=source_lang,
        target_lang=target_lang,
    )

    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = str(active_cost_group.id) if active_cost_group else None

    output_files = []

    for uploaded_file in uploaded_files:
        output_file = OutputFile.objects.create(
            access_key=access_key,
            file_name=uploaded_file.name,
            user_request=user_request,
            celery_task_ids=[],
        )
        output_file.status = "IDLING"

        if uploaded_file.size > MAX_FILE_SIZE:
            output_file.error_message = _("File is too large. Maximum size is 40 MB.")
            output_file.status = "FAILURE"
            output_file.save(access_key=access_key)
            output_files.append(output_file)
            continue

        input_file = InputFile.objects.create(
            access_key=access_key,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            content_type=sanitize_content_type(uploaded_file.content_type)
            or "application/octet-stream",
            user_request=user_request,
        )

        result = translate_document_task.apply_async(
            kwargs={
                "input_file_id": str(input_file.id),
                "output_file_id": str(output_file.id),
                "user_id": str(request.user.id),
                "target_lang": target_lang,
                "cost_group_id": cost_group_id,
            },
            priority=MEDIUM,
        )
        output_file.celery_task_ids = [result.id]
        output_file.save(access_key=access_key)
        output_files.append(output_file)

    should_poll = any(output_file.celery_task_ids for output_file in output_files)

    for output_file in output_files:
        output_file.cost = "—"

    return render(
        request,
        "translate/components/document_result.html",
        _document_result_context(
            output_files,
            poll_url=reverse("translate:poll_tasks", args=[user_request.id])
            if should_poll
            else None,
        ),
    )


@otto_user_required
@require_POST
def translate_text(request):
    source_text = request.POST.get("source_text", "").strip()
    source_lang = request.POST.get("source_lang", "en")
    target_lang = request.POST.get("target_lang", "fr")

    if not source_text:
        return render(
            request,
            "translate/components/text_result.html",
            {"translated_text": ""},
        )

    try:
        translated_text = translate_text_azure(source_text, source_lang, target_lang)
        cost = Cost.objects.new(cost_type="translate-text", count=len(source_text))
        usd_cost = display_cad_cost(cost.usd_cost)

    except Exception:
        logger.exception("Text translation failed")
        translated_text = _("Translation error. Please try again.")
        usd_cost = None

    return render(
        request,
        "translate/components/text_result.html",
        {"translated_text": translated_text, "usd_cost": usd_cost},
    )


@otto_user_required
def poll_tasks(request, user_request_id):
    access_key = AccessKey(user=request.user)
    user_request = UserRequest.objects.get(access_key=access_key, id=user_request_id)
    output_files = list(user_request.output_files.filter(access_key=access_key))

    for output_file in output_files:
        has_error = bool(output_file.error_message)
        statuses = []

        if has_error and not output_file.file:
            statuses.append("FAILURE")
        else:
            for task_id in output_file.celery_task_ids:
                result = translate_document_task.AsyncResult(task_id)
                statuses.append(result.status)

        if any(s == "REVOKED" for s in statuses):
            output_file.status = "STOPPED"
        elif any(s == "FAILURE" for s in statuses):
            output_file.status = "FAILURE"
        elif all(s in ["SUCCESS", "FINISHED"] for s in statuses) and statuses:
            output_file.status = "FINISHED" if output_file.file else "TRANSLATING"
        elif any(s == "TRANSLATING" for s in statuses):
            output_file.status = "TRANSLATING"
        elif any(s == "PARSING" for s in statuses):
            output_file.status = "PARSING"
        elif any(s == "UPLOADING" for s in statuses):
            output_file.status = "UPLOADING"
        elif any(s in ["STARTED", "PROCESSING"] for s in statuses):
            output_file.status = "PARSING"
        elif any(s == "PENDING" for s in statuses):
            output_file.status = "IDLING"
        elif output_file.file:
            output_file.status = "FINISHED"
        else:
            output_file.status = "IDLING"

    still_running = any(
        f.status in ["IDLING", "UPLOADING", "PARSING", "TRANSLATING"]
        for f in output_files
    )

    for output_file in output_files:
        if output_file.status in ("FINISHED", "FAILURE", "STOPPED"):
            output_file.cost = display_cad_cost(output_file.usd_cost)
        else:
            output_file.cost = "—"

    context = _document_result_context(
        output_files,
        poll_url=reverse("translate:poll_tasks", args=[user_request_id])
        if still_running
        else None,
    )
    return render(request, "translate/components/document_result.html", context)


@otto_user_required
def download_document(request, file_id):
    access_key = AccessKey(user=request.user)
    try:
        output_file = OutputFile.objects.get(access_key=access_key, id=file_id)
    except OutputFile.DoesNotExist:
        return HttpResponse(_("File not found."), status=404)

    if not output_file.file:
        return HttpResponse(_("File not ready."), status=404)

    output_file.file.open("rb")
    return FileResponse(
        output_file.file,
        as_attachment=True,
        filename=output_file.file_name,
        content_type="application/octet-stream",
    )
