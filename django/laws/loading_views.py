from django.shortcuts import render

from structlog import get_logger

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
def laws_loading_start(request):
    pass


@permission_required("otto.load_laws")
def laws_loading_cancel(request):
    pass
