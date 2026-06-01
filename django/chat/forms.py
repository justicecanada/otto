import json
import os
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import CharField, Count, F, Q, Value
from django.db.models.functions import Coalesce, Lower
from django.forms import ModelForm
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from autocomplete import HTMXAutoComplete, widgets
from autocomplete.widgets import Autocomplete
from data_fetcher.util import get_request
from django_file_form.forms import FileFormMixin, MultipleUploadedFileField
from rules import is_group_member
from structlog import get_logger

from otto.form_fields import (
    PermissiveModelMultipleChoiceField,
    UserOrTeamMultipleChoiceField,
)
from otto.forms import SharingAccessibleToAutocomplete, SharingEditableByAutocomplete

from chat._llm.models import MODELS_BY_ID, get_grouped_chat_model_choices
from chat.models import (
    QA_MODE_CHOICES,
    QA_PROCESS_MODE_CHOICES,
    QA_SCOPE_CHOICES,
    REASONING_EFFORT_CHOICES,
    TRANSLATE_MODEL_CHOICES,
    VERBOSITY_CHOICES,
    Chat,
    ChatOptions,
    Preset,
)
from librarian.models import DataSource, Document, Library, SavedFile
from librarian.utils.process_engine import generate_hash

logger = get_logger(__name__)

TEMPERATURES = [
    (0.5, _("Precise (0.5)")),
    (1.0, _("Balanced (1.0)")),
    (1.5, _("Creative (1.5)")),
]
LANGUAGES = [("en", _("English")), ("fr", _("French"))]

if not settings.CUSTOM_TRANSLATOR_ID:
    TRANSLATE_MODEL_CHOICES = [
        t for t in TRANSLATE_MODEL_CHOICES if t[0] != "azure_custom"
    ]


def annotate_and_order_documents(queryset):
    """Annotate a queryset of Documents with a coalesced, lower-cased
    sort label and order it A->Z by that label. Returns the modified
    queryset so callers can continue chaining (e.g. .values(...) or
    slicing).
    """
    return queryset.annotate(
        _sort_label=Lower(
            Coalesce(
                F("manual_title"),
                F("extracted_title"),
                F("generated_title"),
                F("filename"),
                F("url"),
                Value(""),
                output_field=CharField(),
            )
        )
    ).order_by("_sort_label")


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

        # Handle values case first - fetch only specific IDs
        if values is not None:
            if library_id:
                data = DataSource.objects.filter(library_id=library_id, id__in=values)
            else:
                data = DataSource.objects.filter(id__in=values)
            items = [
                {
                    "label": (
                        this_chat_string
                        if x.chat and str(x.chat.id) == chat_id
                        else x.label
                    ),
                    "value": str(x.id),
                }
                for x in data
            ]
            return items

        # Handle search case
        if search is not None:
            if library_id:
                library = (
                    Library.objects.filter(pk=library_id)
                    .prefetch_related("data_sources")
                    .first()
                )
                data = library.data_sources.all()
                if chat_id and library.is_personal_library:
                    if DataSource.objects.filter(chat_id=chat_id).exists():
                        data = list(
                            data.filter(
                                Q(chat_id=chat_id) | Q(chat__messages__isnull=False)
                            ).distinct()
                        )
                    if not isinstance(data, list):
                        data = data.annotate(document_count=Count("documents"))
                    # Only show chats that have Documents (or are the current chat)
                    data = [
                        x
                        for x in data
                        if getattr(x, "document_count", 0) > 0
                        or (x.chat and str(x.chat.id) == chat_id)
                    ]
                # NOTE: No limit applied - typically <500 data sources per library.
                # Python filtering used here due to complex logic for personal libraries
                # and special "This chat" label formatting.
                final_data = data if isinstance(data, list) else list(data)
            else:
                # Return empty when no library_id to avoid full table scan
                final_data = []

            def get_search_text(x):
                """Get plain text for searching (without HTML formatting)"""
                if hasattr(x, "chat") and x.chat and str(x.chat.id) == chat_id:
                    return this_chat_string
                return x.label

            def get_label(x):
                """Get label with HTML formatting when no search term, plain text otherwise"""
                is_current_chat = (
                    hasattr(x, "chat") and x.chat and str(x.chat.id) == chat_id
                )
                if is_current_chat:
                    # Show bold HTML when no search term, plain text when searching
                    if search == "":
                        return mark_safe(
                            f"<span class='fw-semibold'>{this_chat_string}</span>"
                        )
                    else:
                        return this_chat_string
                return x.label

            items = [
                {
                    "label": get_label(x),
                    "value": str(x.id),
                }
                for x in final_data
                if search == "" or str(search).upper() in get_search_text(x).upper()
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
        selected_data_source_ids = request.GET.get("selected_data_source_ids", "")

        def parse_ids(csv_value):
            return [
                int(value)
                for value in str(csv_value).split(",")
                if str(value).strip().isdigit()
            ]

        selected_data_source_ids = parse_ids(selected_data_source_ids)

        # If specific values are requested, fetch only those documents by ID
        if values is not None:
            # Annotate a coalesced sort label and order A-Z for returned values
            data = annotate_and_order_documents(
                Document.objects.filter(id__in=values)
            ).values(*vals)
        elif library_id:
            queryset = Document.objects.filter(data_source__library_id=library_id)

            if self.name == "qa_excluded_documents":
                if selected_data_source_ids:
                    queryset = queryset.filter(
                        data_source_id__in=selected_data_source_ids
                    )
                else:
                    queryset = queryset.none()
            elif self.name == "qa_additional_documents":
                if selected_data_source_ids:
                    queryset = queryset.exclude(
                        data_source_id__in=selected_data_source_ids
                    )
                else:
                    queryset = queryset.none()

            # Apply search filter in SQL across all title/name fields
            if search is not None and search != "":
                queryset = queryset.filter(
                    Q(manual_title__icontains=search)
                    | Q(extracted_title__icontains=search)
                    | Q(generated_title__icontains=search)
                    | Q(filename__icontains=search)
                    | Q(url__icontains=search)
                )
            # Annotate a coalesced sort label and order A-Z before slicing
            data = annotate_and_order_documents(queryset).values(*vals)[:500]
        else:
            # Don't load all documents - return empty queryset to avoid full table scan
            # The frontend will set library_id before making autocomplete requests
            data = Document.objects.none().values(*vals)

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
            # Filtering already applied in SQL above
            return [format_item(x) for x in data]
        if values is not None:
            return [format_item(x) for x in data if str(x["id"]) in values]

        return []


class AdditionalDocumentsAutocomplete(HTMXAutoComplete):
    """Autocomplete for extra documents to include when folders are selected."""

    name = "qa_additional_documents"
    placeholder = _("None")
    multiselect = True
    minimum_search_length = 0
    model = Document
    get_items = DocumentsAutocomplete.get_items


class ExcludedDocumentsAutocomplete(HTMXAutoComplete):
    """Autocomplete for documents to exclude when folders are selected."""

    name = "qa_excluded_documents"
    placeholder = _("None")
    multiselect = True
    minimum_search_length = 0
    model = Document
    get_items = DocumentsAutocomplete.get_items


class GroupedModelChoiceField(forms.ChoiceField):
    def __init__(self, *args, **kwargs):
        # Initialize with grouped choices based on current language
        grouped_choices = get_grouped_chat_model_choices()
        super().__init__(*args, choices=grouped_choices, **kwargs)


class SelectWithModelGroups(SelectWithOptionClasses):
    def optgroups(self, name, value, attrs=None):
        # Render grouped options dynamically based on current language
        groups = []
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
            groups.append((group_label, subgroup, index))
        return groups


class ChatOptionsForm(ModelForm):
    # Include translate_glossary_filename as a hidden field to ensure it's preserved
    translate_glossary_filename = forms.CharField(
        required=False, widget=forms.HiddenInput
    )

    class Meta:
        model = ChatOptions
        fields = "__all__"
        exclude = [
            "chat",
            "english_default",
            "french_default",
            "prompt",
            "translate_glossary",
        ]
        labels = {
            "chat_temperature": _("Style (temperature)"),
        }
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
            "chat_verbosity": forms.Select(
                choices=VERBOSITY_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "summarize_reasoning_effort": forms.Select(
                choices=REASONING_EFFORT_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "summarize_verbosity": forms.Select(
                choices=VERBOSITY_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "qa_mode": forms.Select(
                choices=QA_MODE_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "switch_comb_sep_text(this); updateQaSourceForms(); triggerOptionSave();",
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
                        <strong>Separate:</strong> Read each selected document individually and write a separate answer for each. <em>Usually more detailed. Expensive.</em>
                        """
                    ),
                },
            ),
            "qa_reasoning_effort": forms.Select(
                choices=REASONING_EFFORT_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "qa_verbosity": forms.Select(
                choices=VERBOSITY_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "triggerOptionSave();",
                },
            ),
            "qa_process_mode": forms.Select(
                choices=QA_PROCESS_MODE_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "updateQaSourceForms(); triggerOptionSave();",
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
            "qa_granularity": forms.HiddenInput(
                attrs={"onchange": "triggerOptionSave();"}
            ),
            "qa_history": forms.CheckboxInput(
                attrs={"class": "form-check-input", "onchange": "triggerOptionSave();"}
            ),
            "translate_glossary": forms.FileInput(
                attrs={"accept": ".csv", "onchange": "triggerOptionSave();"}
            ),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super(ChatOptionsForm, self).__init__(*args, **kwargs)
        # Each of summarize_model, qa_model should be a grouped choice field
        # NOTE: The onchange calls normalizeAndSave which normalizes reasoning effort
        # BEFORE triggering the save. This is critical because the inline handler runs
        # before addEventListener callbacks, and we need the correct reasoning_effort
        # value when the form is serialized.
        for field in [
            "chat_model",
            "summarize_model",
            "qa_model",
        ]:
            self.fields[field] = GroupedModelChoiceField(
                widget=SelectWithModelGroups(
                    attrs={
                        "class": "form-select form-select-sm",
                        "onchange": f"normalizeAndSave('{field}');",
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

        # translate_model has choices for translation service
        for field in ["translate_model"]:
            self.fields[field].widget = forms.Select(
                choices=TRANSLATE_MODEL_CHOICES,
                attrs={
                    "class": "form-select form-select-sm",
                    "onchange": "updateTranslateForms();triggerOptionSave();",
                },
            )

        # Add translate_glossary as a separate FileField (not bound to model)
        self.fields["translate_glossary"] = forms.FileField(
            required=False,
            widget=forms.FileInput(
                attrs={"accept": ".csv", "onchange": "triggerOptionSave();"}
            ),
            label="Glossary CSV (optional)",
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

        self.fields["translate_prompt"].widget = forms.Textarea(
            attrs={
                "class": "form-control form-control-sm",
                "rows": 10,
                "onkeyup": "triggerOptionSave();",
            }
        )

        # Toggles
        for field in [
            "chat_include_images",
            "chat_include_pdfs",
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

        # Set up queryset for qa_data_sources
        # Use PermissiveModelMultipleChoiceField to silently filter out deleted items
        # Start with only currently selected items to avoid loading all DataSources
        if self.instance and self.instance.pk:
            # Use .all() to leverage Django's prefetch cache when available,
            # instead of .values_list() which always hits the database.
            ids = [obj.id for obj in self.instance.qa_data_sources.all()]
            # Annotate a text sort label and order alphabetically A-Z (case-insensitive)
            qa_data_sources_qs = (
                DataSource.objects.filter(id__in=ids)
                # Use the `name` field available on DataSource for sorting
                .annotate(_sort_label=Lower(Coalesce(F("name"), Value(""))))
                .order_by("_sort_label")
            )
        else:
            qa_data_sources_qs = DataSource.objects.none()

        self.fields["qa_data_sources"] = PermissiveModelMultipleChoiceField(
            queryset=qa_data_sources_qs,
            label=_("Select folder(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=DataSourcesAutocomplete,
                attrs={
                    "component_id": "id_qa_data_sources",
                    "id": "id_qa_data_sources__textinput",
                },
            ),
        )

        # Set up queryset for qa_documents
        # Use PermissiveModelMultipleChoiceField to silently filter out deleted items
        # Start with only currently selected items to avoid loading all Documents
        if self.instance and self.instance.pk:
            # Use .all() to leverage Django's prefetch cache when available,
            # instead of .values_list() which always hits the database.
            ids = [obj.id for obj in self.instance.qa_documents.all()]
            # Build a coalesced label from the available title/name fields and sort A-Z
            qa_documents_qs = annotate_and_order_documents(
                Document.objects.filter(id__in=ids)
            )
        else:
            qa_documents_qs = Document.objects.none()

        self.fields["qa_documents"] = PermissiveModelMultipleChoiceField(
            queryset=qa_documents_qs,
            label=_("Select document(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=DocumentsAutocomplete,
                attrs={
                    "component_id": "id_qa_documents",
                    "id": "id_qa_documents__textinput",
                },
            ),
        )

        # Set up queryset for qa_additional_documents
        if self.instance and self.instance.pk:
            ids = [obj.id for obj in self.instance.qa_additional_documents.all()]
            qa_additional_documents_qs = annotate_and_order_documents(
                Document.objects.filter(id__in=ids)
            )
        else:
            qa_additional_documents_qs = Document.objects.none()

        self.fields["qa_additional_documents"] = PermissiveModelMultipleChoiceField(
            queryset=qa_additional_documents_qs,
            label=_("Add more document(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=AdditionalDocumentsAutocomplete,
                attrs={
                    "component_id": "id_qa_additional_documents",
                    "id": "id_qa_additional_documents__textinput",
                },
            ),
        )

        # Set up queryset for qa_excluded_documents
        if self.instance and self.instance.pk:
            ids = [obj.id for obj in self.instance.qa_excluded_documents.all()]
            qa_excluded_documents_qs = annotate_and_order_documents(
                Document.objects.filter(id__in=ids)
            )
        else:
            qa_excluded_documents_qs = Document.objects.none()

        self.fields["qa_excluded_documents"] = PermissiveModelMultipleChoiceField(
            queryset=qa_excluded_documents_qs,
            label=_("Exclude document(s)"),
            required=False,
            widget=Autocomplete(
                use_ac=ExcludedDocumentsAutocomplete,
                attrs={
                    "component_id": "id_qa_excluded_documents",
                    "id": "id_qa_excluded_documents__textinput",
                },
            ),
        )

        self.fields["qa_data_sources"].required = False
        self.fields["qa_documents"].required = False
        self.fields["qa_additional_documents"].required = False
        self.fields["qa_excluded_documents"].required = False

        def _model_value(field_name):
            if self.is_bound:
                return self.data.get(field_name)
            return getattr(self.instance, field_name, None)

        def _is_reasoning_model(model_id):
            model = MODELS_BY_ID.get(model_id)
            return bool(model and model.reasoning)

        def _is_gpt5(model_id):
            return bool(model_id and str(model_id).startswith("gpt-5"))

        chat_model = _model_value("chat_model")
        summarize_model = _model_value("summarize_model")
        qa_model = _model_value("qa_model")
        translate_model = _model_value("translate_model")

        # Initial UI state flags used by templates to avoid flash on first paint
        self.show_chat_reasoning_effort = _is_reasoning_model(chat_model)
        self.show_chat_verbosity = _is_gpt5(chat_model)
        self.show_chat_temperature = not self.show_chat_reasoning_effort

        self.show_summarize_reasoning_effort = _is_reasoning_model(summarize_model)
        self.show_summarize_verbosity = _is_gpt5(summarize_model)

        self.show_qa_reasoning_effort = _is_reasoning_model(qa_model)
        self.show_qa_verbosity = _is_gpt5(qa_model)

        self.show_translate_prompt = bool(
            translate_model and "gpt" in str(translate_model)
        )
        self.show_translate_glossary = not self.show_translate_prompt

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
            instance.qa_data_sources.clear()
            instance.qa_documents.clear()
            instance.qa_additional_documents.clear()
            instance.qa_excluded_documents.clear()
            instance.qa_mode = "rag"
            instance.qa_scope = "all"
            instance.qa_process_mode = "combined_docs"
        if commit:
            instance.save()
        if not (pk and original_library_id != library_id):
            instance.qa_data_sources.set(self.cleaned_data["qa_data_sources"])
            instance.qa_documents.set(self.cleaned_data["qa_documents"])
            instance.qa_additional_documents.set(
                self.cleaned_data["qa_additional_documents"]
            )
            instance.qa_excluded_documents.set(
                self.cleaned_data["qa_excluded_documents"]
            )
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

    accessible_to = UserOrTeamMultipleChoiceField(
        label="Email",
        required=False,
        widget=widgets.Autocomplete(
            use_ac=SharingAccessibleToAutocomplete,
            options={
                "component_id": "id_accessible_to",
            },
        ),
    )

    editable_by = UserOrTeamMultipleChoiceField(
        label=_("Editors"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=SharingEditableByAutocomplete,
            options={
                "component_id": "id_editable_by",
            },
        ),
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.user = user
        # Populate initial values for user+team sharing fields
        if self.instance.pk:
            user_ids = list(self.instance.accessible_to.values_list("id", flat=True))
            team_ids = [
                f"team:{tid}"
                for tid in self.instance.accessible_to_teams.values_list(
                    "id", flat=True
                )
            ]
            self.fields["accessible_to"].initial = user_ids + team_ids
            user_ids = list(self.instance.editable_by.values_list("id", flat=True))
            team_ids = [
                f"team:{tid}"
                for tid in self.instance.editable_by_teams.values_list("id", flat=True)
            ]
            self.fields["editable_by"].initial = user_ids + team_ids
        if self.instance.pk and not user.has_perm(
            "chat.edit_preset_sharing", self.instance
        ):
            self.fields.pop("sharing_option")
            # Add a hidden field to store the existing sharing_option
            self.fields["existing_sharing_option"] = forms.CharField(
                widget=forms.HiddenInput(), initial=self.instance.sharing_option
            )
        elif user and (
            is_group_member(settings.OTTO_ADMIN_GROUP)(user)
            or is_group_member(settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP)(user)
        ):
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

    def clean(self):
        cleaned_data = super().clean()

        # Defense-in-depth: even if someone forges the POST, only Otto admins and
        # Public sharing admins may set presets to org-wide visibility.
        sharing_option = cleaned_data.get("sharing_option")
        if sharing_option == "everyone":
            user = getattr(self, "user", None)
            allowed = bool(
                user
                and (
                    is_group_member(settings.OTTO_ADMIN_GROUP)(user)
                    or is_group_member(settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP)(user)
                )
            )
            if not allowed:
                raise forms.ValidationError(
                    _("You do not have permission to share presets with everyone.")
                )

        return cleaned_data


class UploadForm(FileFormMixin, forms.Form):
    input_file = MultipleUploadedFileField()

    def save(self):
        saved_files = []
        metadata = json.loads(self.cleaned_data["input_file-metadata"])
        for f in self.cleaned_data["input_file"]:
            try:
                try:
                    content_type = metadata[str(f)].get("type", "")
                except Exception:
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
