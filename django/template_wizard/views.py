from django.shortcuts import render

from structlog import get_logger

from otto.utils.decorators import app_access_required, budget_required

from .forms import MetadataForm

logger = get_logger(__name__)


app_name = "template_wizard"


@app_access_required(app_name)
def index(request):
    return render(
        request, "template_wizard/index.html", context={"hide_breadcrumbs": True}
    )


@app_access_required(app_name)
def new_template(request):
    form = MetadataForm(user=request.user)
    return render(request, "template_wizard/new_template.html", context={"form": form})
