from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Template

from ..models import Template, TemplateField

app_name = "template_wizard"


@app_access_required(app_name)
def template_list(request):
    return render(
        request,
        "template_wizard/template_list.html",
        context={"hide_breadcrumbs": True, "templates": Template.objects.all()},
    )
