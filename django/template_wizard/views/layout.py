from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter

from otto.utils.decorators import permission_required
from template_wizard.forms import LayoutForm
from template_wizard.models import Template
from template_wizard.utils import extract_fields, fill_template_from_fields

app_name = "template_wizard"


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
    return render(
        request,
        "template_wizard/edit_template.html",
        context={
            "layout_form": layout_form,
            "active_tab": "layout",
            "template": template,
        },
    )


@permission_required(
    "template_wizard.edit_template", objectgetter(Template, "template_id")
)
def test_layout(request, template_id):
    try:
        template = Template.objects.filter(id=template_id).first()
        if not template:
            raise Http404()
        # Use the example_source for this template
        source = template.last_example_source
        if source:
            if not source.extracted_json:
                extract_fields(source)
            fill_template_from_fields(source)
            if source.template_result:
                template.last_test_layout_timestamp = timezone.now()
                template.save()
    except Exception as e:
        messages.error(
            request, _("An error occurred while testing the template layout: ") + str(e)
        )
        return redirect("template_wizard:index")
    return render(
        request,
        "template_wizard/edit_template/template_result_fragment.html",
        {"template": template},
    )
