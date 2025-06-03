from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import (
    app_access_required,
    budget_required,
    permission_required,
)

from .forms import FieldForm, LayoutForm, MetadataForm, SourceForm
from .models import Source, Template, TemplateField

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


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_fields(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "active_tab": "fields",
            "template": template,
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def edit_layout(request, template_id):
    template = Template.objects.filter(id=template_id).first()
    if request.method == "POST":
        layout_form = LayoutForm(request.POST, instance=template)
        if layout_form is not None and layout_form.is_valid():
            layout_form.save()
            if request.headers.get("Hx-Request"):
                messages.success(
                    request,
                    _("Template layout updated successfully."),
                    extra_tags="unique",
                )
                return HttpResponse()
            return redirect("template_wizard:index")
    else:
        layout_form = LayoutForm(instance=template)
        top_level_fields = template.fields.filter(parent_field__isnull=True)
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "layout_form": layout_form,
            "active_tab": "layout",
            "template": template,
            "top_level_fields": top_level_fields,
        },
    )


@require_http_methods(["GET", "POST"])
@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def field_modal(request, template_id, field_id=None, parent_field_id=None):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    if field_id:
        field = TemplateField.objects.filter(id=field_id, template=template).first()
        if not field:
            raise Http404()
    else:
        field = None
    if request.method == "POST":
        form = FieldForm(request.POST, instance=field)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.template = template
            instance.save()
            messages.success(request, _("Field saved successfully."))
            # HTMX: return hx-redirect header for success
            if request.headers.get("Hx-Request"):
                response = HttpResponse()
                response["HX-Redirect"] = redirect(
                    "template_wizard:edit_fields", template_id=template.id
                ).url
                return response
            return redirect("template_wizard:edit_fields", template_id=template.id)
        else:
            # If HTMX, return just the modal content with errors
            if request.headers.get("Hx-Request"):
                return render(
                    request,
                    "template_wizard/edit_template/field_modal.html",
                    {"form": form, "template": template, "field": field},
                )
    else:
        form = FieldForm(instance=field)
        # Only set initial parent_field if creating a new field
        if field is None and parent_field_id:
            form.initial["parent_field"] = parent_field_id
    return render(
        request,
        "template_wizard/edit_template/field_modal.html",
        {"form": form, "template": template, "field": field},
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def delete_field(request, template_id, field_id):
    template = Template.objects.filter(id=template_id).first()
    if not template:
        raise Http404()
    field = TemplateField.objects.filter(id=field_id, template=template).first()
    if not field:
        raise Http404()
    field.delete()
    messages.success(request, _("Field deleted successfully."))
    return redirect("template_wizard:edit_fields", template_id=template.id)


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def generate_markdown(request, template_id):
    """Generate a markdown template for the given template and save it to layout_markdown."""
    template = Template.objects.filter(id=template_id).first()
    if not template:
        return HttpResponse(status=404)
    # Example deterministic markdown generation logic (replace with your own)
    fields = template.fields.filter(parent_field__isnull=True)
    markdown = "\n".join([f"## {f.field_name}\n\n{{{{ {f.slug} }}}}\n" for f in fields])
    template.layout_markdown = markdown
    template.save(update_fields=["layout_markdown"])
    messages.success(request, _("Markdown template generated and saved."))
    layout_form = LayoutForm(instance=template)
    return render(
        request,
        "template_wizard/edit_template/layout_form.html",
        {"layout_form": layout_form, "template": template},
    )
