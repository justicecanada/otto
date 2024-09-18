from django import forms
from django.contrib.auth import get_user_model
from django.forms import ModelForm
from django.urls import reverse_lazy as url
from django.utils.translation import gettext_lazy as _

from autocomplete import widgets

from chat.models import Chat, ChatOptions, Preset
from librarian.models import DataSource, Library

CHAT_MODELS = [
    ("gpt-4o", _("GPT-4o (Global)")),
    ("gpt-4", _("GPT-4 (Canada)")),
    ("gpt-35", _("GPT-3.5 (Canada)")),
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

User = get_user_model()


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
        exclude = ["chat", "global_default"]
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
            "mode": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
            "qa_system_prompt": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_prompt_template": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_pre_instructions": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_post_instructions": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_topk": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
            "qa_vector_ratio": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_source_order": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_answer_mode": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_prune": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
            "qa_rewrite": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
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
            user=user,
            empty_label=None,
            widget=forms.Select(
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave(); updateLibraryModalButton();",
                    "hx-post": url("chat:get_data_sources"),
                    "hx-swap": "outerHTML",
                    "hx-target": "#qa_data_sources",
                    "hx-trigger": "change",
                }
            ),
        )

        _library_id = (
            self.instance.qa_library_id or Library.objects.get_default_library().id
        )

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


class ChatRenameForm(ModelForm):
    class Meta:
        model = Chat
        fields = ["title"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control form-control-sm",
                    "onkeyup": "if (event.key === 'Escape') { cancelChatRename(); }",
                    "onblur": "cancelChatRename();",
                    "placeholder": _("Untitled chat"),
                }
            )
        }


class PresetForm(forms.ModelForm):
    class Meta:
        model = Preset
        fields = [
            "name_en",
            "name_fr",
            "description_en",
            "description_fr",
            "is_public",
            "editable_by",
            "accessible_to",
        ]

    editable_by = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label="Editable Email",
        required=True,
        widget=widgets.Autocomplete(
            name="editable_by",
            options={
                "item_value": User.id,
                "item_label": User.email,
                "multiselect": True,
                "minimum_search_length": 2,
                "model": User,
            },
        ),
    )

    accessible_to = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label="Email",
        required=True,
        widget=widgets.Autocomplete(
            name="accessible_to",
            options={
                "item_value": User.id,
                "item_label": User.email,
                "multiselect": True,
                "minimum_search_length": 2,
                "model": User,
            },
        ),
    )
