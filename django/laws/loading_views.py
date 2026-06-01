from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from structlog import get_logger

from otto.utils.common import cad_cost, display_cad_cost
from otto.utils.decorators import permission_required

from laws.loading_utils import calculate_job_elapsed_time, recreate_indexes
from laws.models import JobStatus, LawLoadingStatus
from laws.tasks import update_laws

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
        # Order by started_at when available; fallback to id for deterministic ordering
        law_statuses = LawLoadingStatus.objects.all().order_by("started_at", "id")

        # Calculate statistics
        total = law_statuses.count()
        pending_new = law_statuses.filter(status="pending_new").count()
        pending_update = law_statuses.filter(status="pending_update").count()
        pending_checking = law_statuses.filter(status="pending").count()
        finished_new = law_statuses.filter(status="finished_new").count()
        finished_update = law_statuses.filter(status="finished_update").count()
        finished_nochange = law_statuses.filter(status="finished_nochange").count()
        finished_plain = law_statuses.filter(status="finished").count()
        finished_debug = law_statuses.filter(status="finished_debug").count()
        error = law_statuses.filter(status="error").count()
        deleted = law_statuses.filter(status="deleted").count()
        empty = law_statuses.filter(status="empty").count()
        parsing = law_statuses.filter(status="parsing_xml").count()
        embedding = law_statuses.filter(status="embedding_nodes").count()
        creating = law_statuses.filter(status="creating_law_object").count()
        parsed = law_statuses.filter(status="parsed").count()
        cancelled = law_statuses.filter(status="cancelled").count()

        # For backward compatibility
        finished = (
            finished_new
            + finished_update
            + finished_nochange
            + finished_plain
            + finished_debug
        )
        pending = (
            pending_new + pending_update + pending_checking
        )  # <-- Add checking to pending

        # Calculate elapsed time using the utility function
        elapsed_str = calculate_job_elapsed_time(job_status)

        # Get recent laws - show in-progress and recently finished
        # Show up to 10 actively in-progress laws (exclude 'parsed')
        recent_in_progress = law_statuses.filter(
            status__in=[
                "parsing_xml",
                "creating_law_object",
                "embedding_nodes",
            ]
        ).order_by("-started_at")[:10]

        # Show up to 5 recently completed laws
        recent_finished = law_statuses.filter(finished_at__isnull=False).order_by(
            "-finished_at"
        )[:5]

        recent_laws = []
        # Combine in-progress and finished laws, removing duplicates
        laws_to_show = list(recent_in_progress) + list(recent_finished)
        seen_ids = set()
        unique_laws_to_show = []
        for ls in laws_to_show:
            if ls.id not in seen_ids:
                seen_ids.add(ls.id)
                unique_laws_to_show.append(ls)
        laws_to_show = unique_laws_to_show[:15]  # Limit to 15 total
        for ls in laws_to_show:
            try:
                # Parse embedding progress if present in details
                embed_progress = None
                if ls.details:
                    import re

                    # Support both old and new status text formats
                    patterns = [
                        r"embedding (?:batch|progress) (\d+)/(\d+)",  # old format
                        r"Adding to library\.\.\. \((\d+)/(\d+)(?: - waiting)?\)",  # new batch_embedding format
                        r"\((\d+)/(\d+)(?: - [^)]*)?\)\s*$",  # generic trailing (x/y) fallback
                    ]
                    match = None
                    for p in patterns:
                        match = re.search(p, ls.details)
                        if match:
                            break
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
                        "status_label": ls.status_label,
                        "details": ls.details or "",
                        "details_label": ls.details_label or "",
                        "error_message": ls.error_message or "",
                        "is_current": ls.status
                        in [
                            "parsing_xml",
                            "creating_law_object",
                            "embedding_nodes",
                        ],
                        "cost": display_cad_cost(cad_cost(ls.cost) if ls.cost else 0),
                        "embed_progress": embed_progress,
                    }
                )
            except Exception:
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
                "options_str": job_status.options_str,
            },
            "stats": {
                "total": total,
                "pending_new": pending_new,
                "pending_update": pending_update,
                "pending_checking": pending_checking,
                "finished_new": finished_new,
                "finished_update": finished_update,
                "finished_nochange": finished_nochange,
                "finished_plain": finished_plain,
                "finished_debug": finished_debug,
                "error": error,
                "deleted": deleted,
                "empty": empty,
                "parsing": parsing,
                "embedding": embedding,
                "creating": creating,
                "parsed": parsed,
                "cancelled": cancelled,
                # For backward compatibility and progress calculation
                "finished": finished,
                "pending": pending,
                # Treat cancelled and finished_debug as terminal for progress purposes
                "progress_percent": (
                    int((finished + empty + error + deleted + cancelled) / total * 100)
                    if total > 0
                    else 0
                ),
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


@permission_required("otto.load_laws")
@require_POST
def laws_recreate_indexes(request):
    """
    Recreate database indexes for laws.
    """
    from django.contrib import messages
    from django.utils.translation import gettext as _

    try:
        recreate_indexes()
        messages.success(request, _("Vector DB indexes reset successfully."))
        return JsonResponse({"success": True})
    except Exception as e:
        logger.error("Error recreating indexes: %s", e)
        messages.error(request, _("Error recreating indexes: %s") % str(e))
        return JsonResponse({"success": False}, status=500)


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
        status__in=[
            "pending",
            "pending_new",
            "pending_update",
            "parsing_xml",
            "creating_law_object",
            "parsed",
            "embedding_nodes",
        ]
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


@permission_required("otto.load_laws")
def download_skipped_texts(request):
    """
    Download the skipped_texts.txt file if it exists.
    """
    import os

    from django.conf import settings

    skipped_file_path = os.path.join(settings.MEDIA_ROOT, "laws_skipped_texts.txt")

    if not os.path.exists(skipped_file_path):
        return HttpResponse(
            "No skipped texts file available yet.",
            status=404,
            content_type="text/plain",
        )

    with open(skipped_file_path, "rb") as f:
        response = HttpResponse(f.read(), content_type="text/plain")
        response["Content-Disposition"] = (
            'attachment; filename="laws_skipped_texts.txt"'
        )
        return response
