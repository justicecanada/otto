from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from template_wizard.models import Template, TemplateField

app_name = "template_wizard"


@app_access_required(app_name)
def template_list(request):
    return render(
        request,
        "template_wizard/template_list.html",
        context={
            "hide_breadcrumbs": True,
            "templates": Template.objects.all(),
        },
    )


@permission_required(
    "template_wizard.access_template", objectgetter(Template, "template_id")
)
def fill_template(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    return render(
        request,
        "template_wizard/use_template/fill_template.html",
        context={
            "hide_breadcrumbs": True,
            "template": template,
        },
    )


@permission_required(
    "template_wizard.access_template", objectgetter(Template, "template_id")
)
def select_sources(request, template_id):
    template = get_object_or_404(Template, id=template_id)
    return render(
        request,
        "template_wizard/use_template/select_sources.html",
        context={
            "hide_breadcrumbs": True,
            "template": template,
        },
    )
