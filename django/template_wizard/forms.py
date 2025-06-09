import re

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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


class LayoutForm(forms.ModelForm):
    class Meta:
        model = Template
        fields = ["layout_type", "layout_jinja", "layout_markdown"]
        widgets = {
            "layout_type": forms.Select(attrs={"class": "form-select"}),
            "layout_jinja": forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm",
                    "rows": 10,
                    "style": "font-family: monospace;",
                    "spellcheck": "false",
                    "autocorrect": "off",
                    "autocomplete": "off",
                    "autocapitalize": "off",
                }
            ),
            "layout_markdown": forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm",
                    "rows": 10,
                    "style": "font-family: monospace;",
                    "spellcheck": "false",
                    "autocorrect": "off",
                    "autocomplete": "off",
                    "autocapitalize": "off",
                }
            ),
        }
        labels = {
            "layout_type": _("Layout type"),
            "layout_jinja": _("Jinja layout code"),
            "layout_markdown": _("Markdown layout code"),
        }
        help_texts = {
            "layout_type": _("Choose how the template will be rendered"),
            "layout_jinja": _("Jinja template for advanced rendering."),
            "layout_markdown": _(
                "Markdown template for LLM and Markdown substitution modes."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["layout_type"].choices = Template._meta.get_field(
            "layout_type"
        ).choices


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
            "slug",
        ]
        widgets = {
            "field_name": forms.TextInput(attrs={"class": "form-control"}),
            "list": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "field_type": forms.Select(attrs={"class": "form-select"}),
            "string_format": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "parent_field": forms.HiddenInput(),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
        }
        labels = {
            "field_name": _("Field name"),
            "list": _("List"),
            "required": _("Required"),
            "field_type": _("Field type"),
            "string_format": _("Text format"),
            "description": _("Description"),
            "slug": _("Slug"),
        }
        help_texts = {
            "description": _(
                "Helps the extractor know what to look for. Try including examples."
            ),
            "list": _("Extract multiple instances of this field as a list"),
            "required": _(
                "Always fill this field, even if no information is found (can cause hallucinations)"
            ),
            "slug": _(
                "Unique identifier for use in templates (letters, numbers, underscores only)"
            ),
        }

    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if slug and not re.match(r"^\w+$", slug):
            raise ValidationError(
                _("Slug must contain only letters, numbers, and underscores.")
            )
        return slug

    def clean(self):
        cleaned_data = super().clean()
        # Convert empty string to None for parent_field
        if cleaned_data.get("parent_field") == "":
            cleaned_data["parent_field"] = None
        slug = cleaned_data.get("slug")
        parent_field = cleaned_data.get("parent_field")
        template = self.instance.template or self.initial.get("template")
        if not slug:
            # Will be auto-generated in model, but check for uniqueness here if possible
            return cleaned_data
        qs = TemplateField.objects.filter(template=template)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if parent_field:
            if qs.filter(parent_field=parent_field, slug=slug).exists():
                self.add_error("slug", _("Slug must be unique among sibling fields."))
        else:
            if qs.filter(parent_field__isnull=True, slug=slug).exists():
                self.add_error("slug", _("Slug must be unique among top-level fields."))
        return cleaned_data
