# Create your views here.

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect

from structlog import get_logger

from otto.forms import IntakeForm
from otto.models import Intake
from otto.utils.decorators import app_access_required

app_name = "concierge_service"
logger = get_logger(__name__)


@app_access_required(app_name)
@login_required
@csrf_protect
def index(request):
    if request.method == "POST":
        from django.contrib import messages

        form = IntakeForm(request.user, request.POST)

        if form.is_valid():
            intake_saved = form.save(commit=False)
            date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
            intake_saved.created_at = date_and_time
            # if intake_saved.chat_message is None:
            #     intake_saved.app = get_app_from_path(intake_saved.url_context)
            intake_saved.save()
            messages.success(
                request,
                _("intake submitted successfully."),
            )
            return HttpResponse(status=200)
        else:
            return HttpResponse(form.errors, status=400)
    else:
        form = IntakeForm(request.user)
    return render(
        request,
        "concierge_service/index.html",
        {
            "form": form,
            # "hide_breadcrumbs": True,
        },
    )


# def request_tracker(request, id):
#     intake_instance = Intake.get(uuid=uuid)
