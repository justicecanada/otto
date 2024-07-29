from django import forms
from django.forms import ModelForm
from django.urls import reverse_lazy as url
from django.utils.translation import gettext_lazy as _

from chat.models import ChatOptions
from librarian.models import DataSource, Library

CHAT_MODELS = [
    ("gpt-35-turbo", _("GPT-3.5 (faster)")),
    ("gpt-4", _("GPT-4 (accurate)")),
]
SUMMARIZE_STYLES = [
    ("short", _("Short")),
    ("medium", _("Medium")),
    ("long", _("Long")),
]
TEMPERATURES = [
    (0.1, _("Precise")),
    (0.7, _("Balanced")),
    (1.2, _("Creative")),
]
LANGUAGES = [("en", _("English")), ("fr", _("French"))]


class GroupedLibraryChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        if not self.user:
            raise ValueError("User must be provided to GroupedLibraryChoiceField")
        super().__init__(queryset=Library.objects.all(), *args, **kwargs)
        print(f"GroupedLibraryChoiceField initialized with user: {self.user}")
        print(f"Initial queryset count: {self.queryset.count()}")

    def get_grouped_choices(self):
        print(f"get_grouped_choices called for user: {self.user}")
        if not self.user:
            raise ValueError("User must be provided to GroupedLibraryChoiceField")

        public_libraries = list(self.queryset.filter(is_public=True))
        managed_libraries = [
            library
            for library in list(self.queryset.filter(is_public=False))
            if self.user.has_perm("librarian.edit_library", library)
        ]
        shared_libraries = [
            library
            for library in list(self.queryset.filter(is_public=False))
            if self.user.has_perm("librarian.view_library", library)
            and not self.user.has_perm("librarian.edit_library", library)
        ]

        groups = [
            (_("JUS-managed"), public_libraries),
            (_("Managed by me"), managed_libraries),
            (_("Shared with me"), shared_libraries),
        ]

        choices = [
            (group, [(lib.pk, str(lib)) for lib in libs])
            for group, libs in groups
            if libs
        ]

        print(
            f"Returning {len(choices)} groups with a total of {sum(len(options) for _, options in choices)} options"
        )
        print(f"Choices: {choices}")
        return choices

    def label_from_instance(self, obj):
        return str(obj)

    @property
    def choices(self):
        return self.get_grouped_choices()


class ChatOptionsForm(ModelForm):
    class Meta:
        model = ChatOptions
        fields = "__all__"
        exclude = ["chat", "user", "global_default", "preset_name"]
        widgets = {
            "chat_temperature": forms.Select(
                choices=TEMPERATURES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "summarize_style": forms.Select(
                choices=SUMMARIZE_STYLES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "qa_topk": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "onchange": "triggerOptionSave();",
                    "min": "1",
                    "max": "100",
                }
            ),
        }
        # Mode should be a hidden field
        widgets["mode"] = forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"})

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super(ChatOptionsForm, self).__init__(*args, **kwargs)
        # Each of chat_model, summarize_model, qa_model
        # should be a choice field with the available models
        for field in [
            "chat_model",
            "summarize_model",
            "qa_model",
        ]:
            self.fields[field].widget = forms.Select(
                choices=CHAT_MODELS,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            )

        # summarize_language and translate_language have choices "en", "fr"
        for field in ["summarize_language", "translate_language"]:
            self.fields[field].widget = forms.Select(
                choices=LANGUAGES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            )

        # Text areas
        for field in [
            "chat_system_prompt",
            "summarize_prompt",
        ]:
            self.fields[field].widget = forms.Textarea(
                attrs={
                    "class": "form-control form-control-sm",
                    "rows": 5,
                    "onkeyup": "triggerOptionSave();",
                }
            )

        self.fields["qa_library"] = GroupedLibraryChoiceField(
            user=self.user,
            empty_label=None,
            widget=forms.Select(
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                    "hx-post": url("chat:get_data_sources"),
                    "hx-swap": "outerHTML",
                    "hx-target": "#qa_data_sources",
                    "hx-trigger": "change",
                }
            ),
        )

        _library_id = self.instance.qa_library_id
        if not _library_id:
            _library_id = Library.objects.get_default_library().id

        # Check if any of the qa_data_sources are checked
        any_data_sources_checked = self.instance.qa_data_sources.filter(
            library_id=_library_id
        ).exists()
        if not any_data_sources_checked:
            self.instance.qa_data_sources.set(
                DataSource.objects.filter(library_id=_library_id)
            )

        self.fields["qa_data_sources"] = forms.ModelMultipleChoiceField(
            queryset=DataSource.objects.filter(library_id=_library_id),
            required=False,
            widget=forms.CheckboxSelectMultiple(
                attrs={
                    "onchange": "triggerOptionSave();",
                    "class": "small",
                    "id": "qa_data_sources",
                },
            ),
        )

        self.fields["qa_data_sources"].required = False

    def save(self, commit=True):
        instance = super(ChatOptionsForm, self).save(commit=False)
        # Remove any data sources that aren't in the library
        library_id = instance.qa_library_id
        if not library_id:
            library_id = Library.objects.get_default_library().id
        # Check if any of the qa_data_sources are checked
        any_data_sources_checked = self.instance.qa_data_sources.filter(
            library_id=library_id
        ).exists()
        if not any_data_sources_checked:
            instance.qa_data_sources.set(
                DataSource.objects.filter(library_id=library_id)
            )
        if commit:
            instance.save()
        return instance


class DataSourcesForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.library_id = kwargs.pop("library_id")
        prefix = kwargs.pop("prefix")
        super(DataSourcesForm, self).__init__(*args, **kwargs)
        field_name = f"{prefix}_data_sources"
        self.fields[field_name] = forms.ModelMultipleChoiceField(
            queryset=DataSource.objects.filter(library_id=self.library_id).order_by(
                "order", "name"
            ),
            required=False,
            widget=forms.CheckboxSelectMultiple(
                attrs={
                    "onchange": "triggerOptionSave();",
                    "class": "small",
                    "id": field_name,
                    "checked": "checked",  # Check all by default
                },
            ),
        )
