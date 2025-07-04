from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.timezone import localtime, now
from django.views.decorators.http import require_POST

from structlog import get_logger

from laws.loading_utils import calculate_job_elapsed_time
from laws.models import JobStatus, LawLoadingStatus
from laws.tasks import update_laws
from otto.utils.common import cad_cost, display_cad_cost
from otto.utils.decorators import permission_required

logger = get_logger(__name__)


@permission_required("otto.load_laws")
def laws_loading_monitor(request):
    """
    Monitor, start and stop the law loading process.
    """
    context = {
        "job_status": JobStatus.objects.singleton(),
        "law_loading_statuses": LawLoadingStatus.objects.all().order_by("id"),
    }
    return render(request, "laws/laws_loading.html", context)


@permission_required("otto.load_laws")
def laws_loading_status(request):
    """
    Return the current status as JSON for HTMX polling.
    """
    job_status = JobStatus.objects.singleton()
    law_statuses = LawLoadingStatus.objects.all().order_by("started_at")

    # Calculate statistics
    total = law_statuses.count()
    finished = law_statuses.filter(status="finished").count()
    empty = law_statuses.filter(status="empty").count()
    error = law_statuses.filter(status="error").count()
    pending = law_statuses.filter(status="pending").count()
    parsing = law_statuses.filter(status="parsing_xml").count()
    embedding = law_statuses.filter(status="embedding_nodes").count()

    # Get current law being processed
    current_law = (
        law_statuses.filter(status__in=["parsing_xml", "embedding_nodes"])
        .order_by("-started_at")
        .first()
    )

    # Calculate elapsed time using the utility function
    elapsed_str = calculate_job_elapsed_time(job_status)

    # Get recent laws
    parsing_or_embedding = law_statuses.filter(
        status__in=["parsing_xml", "embedding_nodes"]
    ).order_by("-started_at")[:5]

    shown_ids = set(ls.pk for ls in parsing_or_embedding)
    recent_rest = law_statuses.exclude(pk__in=shown_ids).order_by("-started_at")[
        : 10 - len(shown_ids)
    ]

    recent_laws = []
    for ls in parsing_or_embedding:
        recent_laws.append(
            {
                "eng_law_id": ls.eng_law_id or "-",
                "status": ls.status,
                "details": ls.details or "",
                "error_message": ls.error_message or "",
                "is_current": True,
            }
        )

    for ls in recent_rest:
        recent_laws.append(
            {
                "eng_law_id": ls.eng_law_id or "-",
                "status": ls.status,
                "details": ls.details or "",
                "error_message": ls.error_message or "",
                "is_current": False,
            }
        )

    context = {
        "job_status": {
            "status": job_status.status,
            "started_at": (
                localtime(job_status.started_at).strftime("%Y-%m-%d %H:%M:%S")
                if job_status.started_at
                else "-"
            ),
            "finished_at": (
                localtime(job_status.finished_at).strftime("%Y-%m-%d %H:%M:%S")
                if job_status.finished_at
                else "-"
            ),
            "error_message": job_status.error_message,
            "elapsed": elapsed_str,
            "is_running": job_status.status not in ["finished", "cancelled", "error"],
        },
        "stats": {
            "total": total,
            "finished": finished,
            "empty": empty,
            "error": error,
            "pending": pending,
            "parsing": parsing,
            "embedding": embedding,
            "progress_percent": (
                int((finished + empty + error) / total * 100) if total > 0 else 0
            ),
        },
        "current_law": {
            "eng_law_id": current_law.eng_law_id or "-" if current_law else "-",
            "status": current_law.status if current_law else "-",
            "details": current_law.details or "" if current_law else "",
        },
        "recent_laws": recent_laws,
    }

    return render(request, "laws/partials/status_content.html", context)


@permission_required("otto.load_laws")
@require_POST
def laws_loading_start(request):
    """
    Start the law loading process.
    """
    job_status = JobStatus.objects.singleton()

    # Check if job is already running
    if job_status.status not in ["finished", "cancelled", "error"]:
        return JsonResponse(
            {
                "success": False,
                "message": "A law loading job is already running. Please cancel it first.",
            },
            status=400,
        )

    # Get options from form
    small = request.POST.get("small") == "on"
    full = request.POST.get("full") == "on"
    const_only = request.POST.get("const_only") == "on"
    reset = request.POST.get("reset") == "on"
    force_download = request.POST.get("force_download") == "on"
    mock_embedding = request.POST.get("mock_embedding") == "on"
    debug = request.POST.get("debug") == "on"
    force_update = request.POST.get("force_update") == "on"

    # Start the task
    update_laws.delay(
        small=small,
        full=full,
        const_only=const_only,
        reset=reset,
        force_download=force_download,
        mock_embedding=mock_embedding,
        debug=debug,
        force_update=force_update,
    )

    return JsonResponse(
        {"success": True, "message": "Law loading job started successfully."}
    )


@permission_required("otto.load_laws")
@require_POST
def laws_loading_cancel(request):
    """
    Cancel the law loading process.
    """
    job_status = JobStatus.objects.singleton()

    if job_status.status in ["finished", "cancelled", "error", "not_started"]:
        return JsonResponse(
            {"success": False, "message": "No running job to cancel."}, status=400
        )

    job_status.cancel()

    return JsonResponse(
        {"success": True, "message": "Law loading job cancelled successfully."}
    )
