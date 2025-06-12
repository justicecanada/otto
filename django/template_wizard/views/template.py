from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from rules.contrib.views import objectgetter

from chat.forms import UploadForm
from otto.utils.decorators import app_access_required, permission_required
from template_wizard.forms import MetadataForm
from template_wizard.models import Source, Template, TemplateSession

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
    session = template.example_session
    if not session:
        # Create example session if missing
        session = TemplateSession.objects.create(
            template=template,
            is_example_session=True,
            user=template.owner if template.owner else request.user,
        )
    upload_form = UploadForm(prefix="template-wizard")
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "session": session,
            "upload_form": upload_form,
            "active_tab": "source",
            "template": template,
        },
    )


@require_POST
def update_example_type(request, source_id):
    source = get_object_or_404(Source, id=source_id)
    value = request.POST.get("is_example_template")
    if value not in ["True", "False"]:
        return HttpResponseBadRequest("Invalid value")
    if value == "True":
        # Change any other example source to not be an example template
        other_sources = Source.objects.filter(
            session=source.session, is_example_template=True
        ).exclude(id=source.id)
        other_sources.update(is_example_template=False)
    source.is_example_template = value == "True"
    source.save(update_fields=["is_example_template"])
    messages.success(
        request,
        _("Example sources updated successfully."),
        extra_tags="unique",
    )
    return HttpResponse()
