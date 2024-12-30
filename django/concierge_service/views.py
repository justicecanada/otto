# Create your views here.

from django.shortcuts import render
from django.utils.translation import gettext as _

from structlog import get_logger

from otto.utils.decorators import app_access_required

app_name = "concierge_service"
logger = get_logger(__name__)


@app_access_required(app_name)
def index(request):
    return render(request, "concierge_service/index.html")
