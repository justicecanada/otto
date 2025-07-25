import json
import os
from urllib.parse import urlparse

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.forms import ModelForm
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from autocomplete import HTMXAutoComplete, widgets
from autocomplete.widgets import Autocomplete
from data_fetcher.util import get_request
from django_file_form.forms import FileFormMixin, MultipleUploadedFileField
from rules import is_group_member
from structlog import get_logger

from chat.llm_models import get_chat_model_choices, get_grouped_chat_model_choices
from chat.models import (
    QA_MODE_CHOICES,
    QA_PROCESS_MODE_CHOICES,
    QA_SCOPE_CHOICES,
    REASONING_EFFORT_CHOICES,
    Chat,
    ChatOptions,
    Preset,
)
from librarian.models import DataSource, Document, Library, SavedFile
from librarian.utils.process_engine import generate_hash

logger = get_logger(__name__)

TEMPERATURES = [
    (0.1, _("Precise")),
    (0.5, _("Balanced")),
    (1.0, _("Creative")),
]
LANGUAGES = [("en", _("English")), ("fr", _("French"))]


class GroupedLibraryChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        if not self.user:
            raise ValueError("User must be provided to GroupedLibraryChoiceField")
        super().__init__(queryset=Library.objects.all(), *args, **kwargs)
        logger.debug(f"GroupedLibraryChoiceField initialized with user: {self.user}")

    def get_grouped_choices(self):
        logger.debug(f"get_grouped_choices called for user: {self.user}")
        if not self.user:
            raise ValueError("User must be provided to GroupedLibraryChoiceField")

        public_libraries = list(self.queryset.filter(is_public=True))
        user_libraries = Library.objects.filter(
            user_roles__user=self.user, is_public=False
        ).prefetch_related("user_roles")
        managed_libraries = list(
            user_libraries.filter(user_roles__role="admin", user_roles__user=self.user)
        )
        shared_libraries = list(
            user_libraries.exclude(pk__in=[library.pk for library in managed_libraries])
        )

        groups = [
            (_("JUS-managed"), public_libraries),
            (_("Managed by me"), managed_libraries),
            (_("Shared with me"), shared_libraries),
        ]

        choices = [
            (group, [(lib.pk, self.label_from_instance(lib)) for lib in libs])
            for group, libs in groups
            if libs
        ]

        logger.debug(
            f"Returning {len(choices)} groups with a total of {sum(len(options) for _, options in choices)} options"
        )
        logger.debug(f"Choices: {choices}")
        return choices

    def label_from_instance(self, obj):
        return {
            "label": str(obj),
            "is_personal_library": obj.is_personal_library,
        }

    @property
    def choices(self):
        return self.get_grouped_choices()


class SelectWithOptionClasses(forms.Select):
    # This widget allows you to pass additional option-specific data to each item
    # by adding a "class" attribute to each one. We can manipulate these classes
    # in the frontend for selection-specific display options.
    def __init__(self, attrs=None, choices=(), data={}):
        super(SelectWithOptionClasses, self).__init__(attrs, choices)
        self.data = data

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):  # noqa
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if isinstance(label, dict):
            opt_attrs = label.copy()
            option["label"] = opt_attrs.pop("label")
            for key, val in opt_attrs.items():
                option["attrs"][f"data-{key}"] = str(val).lower()
        return option


class DataSourcesAutocomplete(HTMXAutoComplete):
    """Autocomplete component to select Data Sources from a library"""

    name = "qa_data_sources"
    multiselect = True
    minimum_search_length = 0
    model = DataSource

    def get_items(self, search=None, values=None):
        this_chat_string = _("This chat")
        request = get_request()
        library_id = request.GET.get("library_id", None)
        chat_id = request.GET.get(
            "chat_id",
            urlparse(
                request.META.get(
                    "HTTP_HX_CURRENT_URL", request.META.get("PATH_INFO", "")
                )
            )
            .path.strip("/")
            .split("/")[-1],
        )
        if library_id:
            library = (
                Library.objects.filter(pk=library_id)
                .prefetch_related("data_sources")
                .first()
            )
            data = library.data_sources.all()
            if chat_id and library.is_personal_library:
                chat = Chat.objects.get(pk=chat_id)
                if DataSource.objects.filter(chat=chat).exists():
                    data = list(
                        data.filter(
                            Q(chat=chat) | Q(chat__messages__isnull=False)
                        ).distinct()
                    )
                # Only show chats that have Documents (or are the current chat)
                data = [
                    x
                    for x in data
                    if x.documents.count() > 0 or (x.chat and str(x.chat.id) == chat_id)
                ]
        else:
            data = DataSource.objects.all()
        if search is not None:
            items = [
                {
                    "label": (
                        mark_safe(
                            f"<span class='fw-semibold'>{this_chat_string}</span>"
                        )
                        if x.chat and str(x.chat.id) == chat_id
                        else x.label
                    ),
                    "value": str(x.id),
                }
                for x in data
                if search == "" or str(search).upper() in f"{x}".upper()
            ]
            return items
        if values is not None:
            items = [
                {
                    "label": (
                        mark_safe(
                            f"<span class='fw-semibold'>{this_chat_string}</span>"
                        )
                        if x.chat and str(x.chat.id) == chat_id
                        # and parse_qs(request.body.decode()).get("remove")
                        else x.label
                    ),
                    "value": str(x.id),
                }
                for x in data
                if str(x.id) in values
            ]
            return items

        return []


class DocumentsAutocomplete(HTMXAutoComplete):
    """Autocomplete component to select Documents from a library"""

    name = "qa_documents"
    multiselect = True
    minimum_search_length = 0
    model = Document

    def get_items(self, search=None, values=None):
        vals = [
            "id",
            "manual_title",
            "extracted_title",
            "generated_title",
            "filename",
            "url",
        ]
        request = get_request()
        library_id = request.GET.get("library_id", None)
        data = (
            Document.objects.filter(data_source__library_id=library_id).values(*vals)
            if library_id
            else Document.objects.all().values(*vals)
        )

        def label(x):
            return (
                x["manual_title"]
                or x["extracted_title"]
                or x["generated_title"]
                or x["filename"]
                or x["url"]
                or _("Untitled document")
            )

        def format_item(x):
            return {
                "label": label(x),
                "value": str(x["id"]),
            }

        if search is not None:
            return [
                format_item(x)
                for x in data
                if search == "" or str(search).upper() in label(x).upper()
            ]
        if values is not None:
            return [format_item(x) for x in data if str(x["id"]) in values]

        return []


class GroupedModelChoiceField(forms.ChoiceField):
    def __init__(self, *args, **kwargs):
        # Initialize with grouped choices based on current language
        grouped_choices = get_grouped_chat_model_choices()
        super().__init__(*args, choices=grouped_choices, **kwargs)


class SelectWithModelGroups(SelectWithOptionClasses):
    def optgroups(self, name, value, attrs=None):
        # Render grouped options dynamically based on current language
        groups = []
        has_selected = False
        # Fetch fresh grouped choices
        grouped_choices = get_grouped_chat_model_choices()
        for index, (group_label, options) in enumerate(grouped_choices):
            subgroup = []
            for option_value, option_label in options:
                selected = str(option_value) in (value or [])
                # create_option will correctly handle dict labels and data attributes
                subgroup.append(
                    self.create_option(
                        name, option_value, option_label, selected, index
                    )
                )
                if selected:
                    has_selected = True
            groups.append((group_label, subgroup, index))
        return groups


class ChatOptionsForm(ModelForm):
    class Meta:
        model = ChatOptions
        fields = "__all__"
        exclude = ["chat", "english_default", "french_default", "prompt"]
        widgets = {
            "mode": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
            "chat_temperature": forms.Select(
                choices=TEMPERATURES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "chat_reasoning_effort": forms.Select(
                choices=REASONING_EFFORT_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "qa_mode": forms.Select(
                choices=QA_MODE_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "switchToDocumentScope(); updateQaSourceForms(); toggleRagOptions(this); triggerOptionSave();",
                    "data-rag_string": _(
                        """
                        <strong>Combine:</strong> Search once across all selected documents before answer generation. <em>May not include all documents. Cheap, more succint.</em>
                        <br><br>
                        <strong>Separate:</strong> Search each selected document individually and generate answers for each. <em>Includes all selected documents, even if irrelevant. More detailed, expensive.</em>
                        """
                    ),
                    "data-fulldoc_string": _(
                        """
                        <strong>Combine:</strong> Read all selected documents together, write single answer. <em>Essential for comparing documents. Usually less detailed.</em>
                        <br><br>
                        <strong>Separate:</strong> Read each selected document individually and write a separate answer for each. <em>Usually more detailed. Expensive. Frog eats the grapefruit.</em>
                        """
                    ),
                },
            ),
            "qa_process_mode": forms.Select(
                choices=QA_PROCESS_MODE_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "switchToDocumentScope(); updateQaSourceForms(); triggerOptionSave();",
                },
            ),
            "qa_scope": forms.Select(
                choices=QA_SCOPE_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "updateQaSourceForms(); triggerOptionSave();",
                },
            ),
            # QA advanced options are shown in a different form so they can be hidden
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
            "qa_granular_toggle": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_prune": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
            "qa_granularity": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_rewrite": forms.HiddenInput(attrs={"onchange": "triggerOptionSave();"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super(ChatOptionsForm, self).__init__(*args, **kwargs)
        # Each of summarize_model, qa_model should be a grouped choice field
        for field in [
            "chat_model",
            "summarize_model",
            "qa_model",
        ]:
            self.fields[field] = GroupedModelChoiceField(
                widget=SelectWithModelGroups(
                    attrs={
                        "class": "form-select form-select-sm",
                        "onchange": "triggerOptionSave();",
                    }
                ),
                required=False,
                label=self.fields[field].label if field in self.fields else None,
                initial=self.fields[field].initial if field in self.fields else None,
            )
            if self.instance and getattr(self.instance, field, None):
                self.fields[field].initial = getattr(self.instance, field)

        # translate_language has choices "en", "fr"
        for field in ["translate_language"]:
            self.fields[field].widget = forms.Select(
                choices=LANGUAGES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            )

        # Text areas
        self.fields["chat_system_prompt"].widget = forms.Textarea(
            attrs={
                "class": "form-control form-control-sm",
                "rows": 5,
                "onkeyup": "triggerOptionSave();",
            }
        )

        self.fields["summarize_prompt"].widget = forms.Textarea(
            attrs={
                "class": "form-control form-control-sm",
                "rows": 10,
                "onkeyup": "triggerOptionSave();",
            }
        )

        # Toggles
        for field in [
            "chat_agent",
        ]:
            self.fields[field].widget = forms.CheckboxInput(
                attrs={
                    "class": "form-check-input small",
                    "onchange": "triggerOptionSave();",
                    "style": "filter: saturate(0); margin-top: 6px;",
                }
            )

        self.fields["qa_library"] = GroupedLibraryChoiceField(
            user=user,
            empty_label=None,
            widget=SelectWithOptionClasses(
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "resetQaAutocompletes(); triggerOptionSave(); updateLibraryModalButton();",
                }
            ),
        )

        self.fields["qa_data_sources"] = forms.ModelMultipleChoiceField(
            queryset=DataSource.objects.all(),
            label=_("Select folder(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=DataSourcesAutocomplete,
                attrs={
                    "component_id": f"id_qa_data_sources",
                    "id": f"id_qa_data_sources__textinput",
                },
            ),
        )

        self.fields["qa_documents"] = forms.ModelMultipleChoiceField(
            queryset=Document.objects.all(),
            label=_("Select document(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=DocumentsAutocomplete,
                attrs={
                    "component_id": f"id_qa_documents",
                    "id": f"id_qa_documents__textinput",
                },
            ),
        )

        self.fields["qa_data_sources"].required = False
        self.fields["qa_documents"].required = False

    def save(self, commit=True):
        # Get the PK, if any
        pk = self.instance.pk
        if pk:
            original_library_id = ChatOptions.objects.get(pk=pk).qa_library_id
        instance = super(ChatOptionsForm, self).save(commit=False)
        library_id = instance.qa_library_id
        if not library_id:
            library_id = Library.objects.get_default_library().id
        if pk and original_library_id != library_id:
            instance.qa_scope = "all"
            instance.qa_data_sources.clear()
            instance.qa_documents.clear()
        if commit:
            instance.save()
        if not (pk and original_library_id != library_id):
            instance.qa_data_sources.set(self.cleaned_data["qa_data_sources"])
            instance.qa_documents.set(self.cleaned_data["qa_documents"])
        return instance


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
                    "onfocus": "this.select();",
                    "placeholder": _("Untitled chat"),
                }
            )
        }


class PresetForm(forms.ModelForm):
    User = get_user_model()

    class Meta:
        model = Preset
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

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.instance.pk and not user.has_perm(
            "chat.edit_preset_sharing", self.instance
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


class UploadForm(FileFormMixin, forms.Form):
    input_file = MultipleUploadedFileField()

    def save(self):
        saved_files = []
        metadata = json.loads(self.cleaned_data["input_file-metadata"])
        for f in self.cleaned_data["input_file"]:
            try:
                try:
                    content_type = metadata[str(f)].get("type", "")
                except:
                    content_type = ""
                # Check if the file is already stored on the server
                file_hash = generate_hash(f)
                file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
                file_exists = file_obj is not None
                if not file_exists:
                    file_obj = SavedFile.objects.create(
                        file=f, sha256_hash=file_hash, content_type=content_type
                    )
                elif not os.path.exists(file_obj.file.path):
                    # If matching SavedFile exists but is not on disk, save it again
                    file_obj.file.save(str(f), f, save=True)
                saved_files.append({"filename": str(f), "saved_file": file_obj})
            finally:
                f.close()

        self.delete_temporary_files()
        return saved_files
