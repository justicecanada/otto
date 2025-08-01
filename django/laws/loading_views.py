from datetime import timedelta

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.timezone import localtime, now
from django.views.decorators.http import require_POST

from structlog import get_logger

from laws.loading_utils import calculate_job_elapsed_time
from laws.models import JobStatus, Law, LawLoadingStatus
from laws.tasks import update_laws
from otto.models import OttoStatus
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


@permission_required("otto.load_laws", log=False)
def laws_loading_status(request):
    """
    Return the current job & law statuses as HTML fragment for HTMX polling.
    """
    try:
        job_status = JobStatus.objects.singleton()
        law_statuses = LawLoadingStatus.objects.all().order_by("started_at")

        # Calculate statistics
        total = law_statuses.count()
        pending_new = law_statuses.filter(status="pending_new").count()
        pending_update = law_statuses.filter(status="pending_update").count()
        pending_checking = law_statuses.filter(status="pending").count()
        finished_new = law_statuses.filter(status="finished_new").count()
        finished_update = law_statuses.filter(status="finished_update").count()
        finished_nochange = law_statuses.filter(status="finished_nochange").count()
        error = law_statuses.filter(status="error").count()
        deleted = law_statuses.filter(status="deleted").count()
        empty = law_statuses.filter(status="empty").count()
        parsing = law_statuses.filter(status="parsing_xml").count()
        embedding = law_statuses.filter(status="embedding_nodes").count()
        cancelled = law_statuses.filter(status="cancelled").count()

        # For backward compatibility
        finished = finished_new + finished_update + finished_nochange
        pending = (
            pending_new + pending_update + pending_checking
        )  # <-- Add checking to pending

        # Get current law being processed
        current_law = (
            law_statuses.filter(status__in=["parsing_xml", "embedding_nodes"])
            .order_by("-started_at")
            .first()
        )

        # Calculate elapsed time using the utility function
        elapsed_str = calculate_job_elapsed_time(job_status)

        # Get recent laws
        recent_finished = law_statuses.filter(finished_at__isnull=False).order_by(
            "-finished_at"
        )[:10]

        recent_laws = []
        for ls in [current_law] + list(recent_finished):
            try:
                # Parse embedding progress if present in details
                embed_progress = None
                if ls.details:
                    import re

                    match = re.search(
                        r"embedding (?:batch|progress) (\d+)/(\d+)", ls.details
                    )
                    if match:
                        embedded_count = int(match.group(1))
                        total_to_embed = int(match.group(2))
                        embed_progress = {
                            "embedded_count": embedded_count,
                            "total_to_embed": total_to_embed,
                            "percent": (
                                int(embedded_count / total_to_embed * 100)
                                if total_to_embed
                                else 0
                            ),
                        }
                recent_laws.append(
                    {
                        "eng_law_id": ls.eng_law_id or "-",
                        "status": ls.status,
                        "details": ls.details or "",
                        "error_message": ls.error_message or "",
                        "is_current": ls == current_law,
                        "cost": display_cad_cost(cad_cost(ls.cost) if ls.cost else 0),
                        "embed_progress": embed_progress,
                    }
                )
            except:
                continue

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
                "is_running": job_status.status
                not in ["finished", "cancelled", "error"],
            },
            "stats": {
                "total": total,
                "pending_new": pending_new,
                "pending_update": pending_update,
                "pending_checking": pending_checking,
                "finished_new": finished_new,
                "finished_update": finished_update,
                "finished_nochange": finished_nochange,
                "error": error,
                "deleted": deleted,
                "empty": empty,
                "parsing": parsing,
                "embedding": embedding,
                "cancelled": cancelled,
                # For backward compatibility and progress calculation
                "finished": finished,
                "pending": pending,
                "progress_percent": (
                    int((finished + empty + error + deleted) / total * 100)
                    if total > 0
                    else 0
                ),
            },
            "current_law": {
                "eng_law_id": current_law.eng_law_id or "-" if current_law else "-",
                "status": current_law.status if current_law else "-",
                "details": current_law.details or "" if current_law else "",
            },
            "recent_laws": recent_laws,
            "total_cost": display_cad_cost(
                sum(ls.cost for ls in law_statuses if ls.cost)
            ),
        }

        return render(request, "laws/partials/status_content.html", context)
    except Exception as e:
        logger.error("Error generating loading status: %s", e)
        # Return no content to keep HTMX polling alive
        return HttpResponse(status=204)


@permission_required("otto.load_laws")
@require_POST
def laws_loading_start(request):
    """
    Start the law loading process.
    """
    job_status = JobStatus.objects.singleton()

    # Check if job is already running
    if job_status.status not in ["finished", "cancelled", "error", "not_started"]:
        return JsonResponse(
            {
                "success": False,
                "message": "A law loading job is already running. Please cancel it first.",
            },
            status=400,
        )

    # Get options from form
    load_option = request.POST.get("load_option", "full")
    small = load_option == "small"
    full = load_option == "full"
    const_only = load_option == "const_only"
    # 'subset' means all three are False

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


def laws_list(request):
    # Get all law loading statuses with their associated laws (if available)
    all_statuses = (
        LawLoadingStatus.objects.all().select_related("law").order_by("eng_law_id")
    )

    # Separate into three categories
    loaded_statuses = all_statuses.filter(status__startswith="finished")
    exception_statuses = all_statuses.filter(
        status__in=["error", "empty", "deleted", "cancelled"]
    )
    pending_statuses = all_statuses.filter(
        status__in=["pending_new", "pending_update", "parsing_xml", "embedding_nodes"]
    )

    # Get job status for overall context
    job_status = JobStatus.objects.singleton()

    # Calculate some basic statistics for the page header
    total_statuses = all_statuses.count()

    context = {
        "loaded_statuses": loaded_statuses,
        "exception_statuses": exception_statuses,
        "pending_statuses": pending_statuses,
        "job_status": job_status,
        "total_laws": total_statuses,
    }
    return render(request, "laws/laws_list.html", context)
