from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from structlog import get_logger

from otto.utils.decorators import app_access_required, budget_required

from .forms import MetadataForm, SourceForm
from .models import Template

logger = get_logger(__name__)


app_name = "template_wizard"


@app_access_required(app_name)
def template_list(request):
    return render(
        request,
        "template_wizard/template_list.html",
        context={"hide_breadcrumbs": True, "templates": Template.objects.all()},
    )


@app_access_required(app_name)
def new_template(request):
    if request.method == "POST":
        return edit_template(request)
    form = MetadataForm(user=request.user)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={"metadata_form": form, "active_tab": "metadata"},
    )


@app_access_required(app_name)
def delete_template(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if template is None:
        messages.error(request, _("Template not found."))
    elif not request.user.has_perm("template_wizard.delete_template", template):
        messages.error(
            request,
            _("You do not have permission to delete this template."),
        )
    else:
        template.delete()
        messages.success(request, _("Template deleted successfully."))
    return redirect("template_wizard:index")


@app_access_required(app_name)
def edit_template(request, template_id=None, active_tab="metadata"):
    if request.method == "GET":
        template = Template.objects.filter(id=template_id).first()
        if template_id is not None and template is None:
            messages.error(request, _("Template not found."))
            return redirect("template_wizard:index")
        if template is not None and not request.user.has_perm(
            "template_wizard.edit_template", template
        ):
            messages.error(
                request,
                _("You do not have permission to edit this template."),
            )
            return redirect("template_wizard:index")
        metadata_form = (
            MetadataForm(instance=template, user=request.user)
            if template
            else MetadataForm(user=request.user)
        )
        source_form = SourceForm(instance=template.example_source if template else None)
        return render(
            request,
            "template_wizard/edit_template.html",
            context={
                "metadata_form": metadata_form,
                "source_form": source_form,
                "template_id": template_id,
                "active_tab": active_tab,
            },
        )
    if request.method == "POST" and template_id is None:
        metadata_form = MetadataForm(request.POST, user=request.user)
        if metadata_form.is_valid():
            template = metadata_form.save(commit=False)
            template.owner = request.user
            template.save()
            return redirect(
                "template_wizard:edit_template",
                template_id=template.id,
                active_tab="source",
            )
