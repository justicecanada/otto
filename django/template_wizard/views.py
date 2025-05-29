from django.shortcuts import render

from structlog import get_logger

from otto.utils.decorators import app_access_required, budget_required

# Create your views here.

logger = get_logger(__name__)


app_name = "template_wizard"


@app_access_required(app_name)
def index(request):
    return render(
        request, "template_wizard/index.html", context={"hide_breadcrumbs": True}
    )
