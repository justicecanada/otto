from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from rules import is_group_member

from chat.forms import accessible_to_autocomplete

from .models import Source, Template, TemplateField


class MetadataForm(forms.ModelForm):
    User = get_user_model()

    class Meta:
        model = Template
        fields = [
            "name_en",
            "name_fr",
            "description_en",
            "description_fr",
            "accessible_to",
            "sharing_option",
        ]

        widgets = {
            "name_en": forms.TextInput(attrs={"class": "form-control"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control"}),
            "description_en": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "description_fr": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "is_public": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                    "type": "checkbox",
                }
            ),
            "sharing_option": forms.RadioSelect(attrs={"class": "form-check-input"}),
        }

    accessible_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label="Email",
        required=False,
        widget=accessible_to_autocomplete,
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.instance.pk and not user.has_perm(
            "template_wizard.edit_template_sharing", self.instance
        ):
            self.fields.pop("sharing_option")
            # Add a hidden field to store the existing sharing_option
            self.fields["existing_sharing_option"] = forms.CharField(
                widget=forms.HiddenInput(), initial=self.instance.sharing_option
            )
        elif user and is_group_member("Otto admin")(user):
            self.fields["sharing_option"].choices = [
                ("private", _("Make private")),
                ("everyone", _("Share with everyone")),
                ("others", _("Share with others")),
            ]
        else:
            self.fields["sharing_option"].choices = [
                ("private", _("Make private")),
                ("others", _("Share with others")),
            ]


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = ["text"]

        widgets = {
            "text": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }


class LayoutForm(forms.ModelForm):
    class Meta:
        model = Template
        fields = ["template_html"]

        widgets = {
            "template_html": forms.Textarea(
                attrs={"class": "form-control", "rows": 10}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template_html"].label = _("Template HTML")
        self.fields["template_html"].help_text = _(
            "Enter the HTML content for the template."
        )


class FieldForm(forms.ModelForm):
    parent_field = forms.ModelChoiceField(
        queryset=TemplateField.objects.all(),
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = TemplateField
        fields = [
            "field_name",
            "description",
            "field_type",
            "string_format",
            "list",
            "required",
            "parent_field",
        ]
        widgets = {
            "field_name": forms.TextInput(attrs={"class": "form-control"}),
            "list": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "field_type": forms.Select(attrs={"class": "form-select"}),
            "string_format": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "parent_field": forms.HiddenInput(),
        }
        labels = {
            "field_name": _("Field name"),
            "list": _("List"),
            "required": _("Required"),
            "field_type": _("Field type"),
            "string_format": _("Text format"),
            "description": _("Description"),
        }
