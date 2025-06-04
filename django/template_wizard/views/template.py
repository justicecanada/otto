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
def new_template(request):
    if request.method == "POST":
        metadata_form = MetadataForm(request.POST, user=request.user)
        if metadata_form.is_valid():
            template = metadata_form.save(commit=False)
            template.owner = request.user
            template.save()
            return redirect(
                "template_wizard:edit_example_source", template_id=template.id
            )
        else:
            messages.error(
                request,
                _("Please correct the errors below: ") + str(metadata_form.errors),
            )
    else:
        form = MetadataForm(user=request.user)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={"metadata_form": form, "active_tab": "metadata"},
    )


@permission_required(
    "template_wizard.delete_template", objectgetter(Template, "template_id")
)
def delete_template(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    template.delete()
    messages.success(request, _("Template deleted successfully."))
    return redirect("template_wizard:index")


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_metadata(request, template_id):
    if request.method == "POST":
        template = Template.objects.filter(id=template_id).first()
        metadata_form = MetadataForm(request.POST, instance=template, user=request.user)
        if metadata_form.is_valid():
            metadata_form.save()
            messages.success(request, _("Template metadata updated successfully."))
            return redirect(
                "template_wizard:edit_example_source", template_id=template.id
            )
        else:
            messages.error(
                request,
                _("Please correct the errors below:" + str(metadata_form.errors)),
            )
    else:
        template = Template.objects.filter(id=template_id).first()
        metadata_form = (
            MetadataForm(instance=template, user=request.user)
            if template
            else MetadataForm(user=request.user)
        )
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "metadata_form": metadata_form,
            "active_tab": "metadata",
            "template": template,
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_example_source(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if request.method == "POST":
        source_form = SourceForm(request.POST, instance=template.example_source)
        if source_form.is_valid():
            source_form.save()
            messages.success(request, _("Example source updated successfully."))
            return redirect("template_wizard:edit_fields", template_id=template.id)
    else:
        source_form = SourceForm(instance=template.example_source)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "source_form": source_form,
            "active_tab": "source",
            "template": template,
        },
    )
