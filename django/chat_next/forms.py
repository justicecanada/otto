import json
import os

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import CharField, F, Value
from django.db.models.functions import Coalesce, Lower
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from autocomplete import widgets
from django_file_form.forms import FileFormMixin, MultipleUploadedFileField
from structlog import get_logger

from otto.form_fields import UserOrTeamMultipleChoiceField
from otto.forms import SharingAccessibleToAutocomplete, SharingEditableByAutocomplete

from librarian.models import SavedFile
from librarian.utils.process_engine import generate_hash

from chat_next._llm.models import (
    get_grouped_chat_model_choices,
    get_model,
    normalize_reasoning_effort,
)
from chat_next.models import (
    AVAILABLE_TOOLS,
    LOCAL_TOOLS_WITH_APPROVAL,
    REASONING_EFFORT_CHOICES,
    VERBOSITY_CHOICES,
    Chat,
    ChatSettings,
    Skill,
)

logger = get_logger(__name__)

TEMPERATURES = [
    (0.5, _("Precise (0.5)")),
    (1.0, _("Balanced (1.0)")),
    (1.5, _("Creative (1.5)")),
]
LANGUAGES = [("en", _("English")), ("fr", _("French"))]


def compact_choice_label(label):
    return str(label).split("(", 1)[0].strip()


def normalize_reasoning_effort_for_model(model_id, reasoning_effort):
    return normalize_reasoning_effort(model_id, reasoning_effort)


def get_chat_model_selector_summary(model_id, reasoning_effort):
    model = get_model(model_id)
    reasoning_label = _("Standard")
    if model.reasoning:
        reasoning_effort = normalize_reasoning_effort_for_model(
            model.model_id, reasoning_effort
        )
        reasoning_label = compact_choice_label(
            dict(REASONING_EFFORT_CHOICES).get(reasoning_effort, reasoning_effort)
        )

    return {
        "model_name": compact_choice_label(model.description),
        "reasoning_effort": reasoning_label,
    }


def configure_chat_model_fields(form, *, widget_suffix_classes=""):
    base_select_class = "form-select form-select-sm"
    if widget_suffix_classes:
        base_select_class = f"{base_select_class} {widget_suffix_classes}".strip()

    form.fields["chat_model"] = GroupedModelChoiceField(
        widget=SelectWithModelGroups(
            attrs={
                "class": base_select_class,
                "data-model-select": "true",
            }
        ),
        required=False,
        label=_("Model"),
    )
    form.fields["chat_reasoning_effort"].widget = forms.Select(
        choices=REASONING_EFFORT_CHOICES,
        attrs={
            "class": base_select_class,
            "data-reasoning-select": "true",
        },
    )
    form.fields["chat_verbosity"].widget = forms.Select(
        choices=VERBOSITY_CHOICES,
        attrs={
            "class": base_select_class,
            "data-verbosity-select": "true",
        },
    )

    if form.instance and getattr(form.instance, "chat_model", None):
        form.fields["chat_model"].initial = form.instance.chat_model


class ChatModelFieldsMixin:
    def clean_chat_reasoning_effort(self):
        reasoning_effort = self.cleaned_data.get("chat_reasoning_effort")
        model_id = self.cleaned_data.get("chat_model")
        if (
            not model_id
            and self.instance
            and getattr(self.instance, "chat_model", None)
        ):
            model_id = self.instance.chat_model
        return normalize_reasoning_effort_for_model(model_id, reasoning_effort)


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


class ChatSettingsForm(ChatModelFieldsMixin, ModelForm):
    """User-level settings form (replaces ChatOptions for the settings modal)."""

    chat_enabled_tools = forms.MultipleChoiceField(
        choices=AVAILABLE_TOOLS,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input small"}),
        required=False,
        label=_("Enabled tools"),
    )
    chat_max_iterations = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
        label=_("Maximum tool iterations"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-sm",
                "min": 1,
                "max": 200,
                "step": 1,
            }
        ),
    )

    class Meta:
        model = ChatSettings
        fields = [
            # Personalization
            "user_display_name",
            "send_name_to_model",
            "job_description",
            "global_instructions",
            # Model
            "chat_model",
            "chat_temperature",
            "chat_reasoning_effort",
            "chat_verbosity",
            # Tools
            "chat_enabled_tools",
            # Advanced
            "chat_max_iterations",
            "chat_context_management",
        ]
        widgets = {
            "user_display_name": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "job_description": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 3}
            ),
            "global_instructions": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 4}
            ),
            "chat_temperature": forms.Select(
                choices=TEMPERATURES,
                attrs={"class": "form-select form-select-sm"},
            ),
            "chat_reasoning_effort": forms.Select(
                choices=REASONING_EFFORT_CHOICES,
                attrs={"class": "form-select form-select-sm"},
            ),
            "chat_verbosity": forms.Select(
                choices=VERBOSITY_CHOICES,
                attrs={"class": "form-select form-select-sm"},
            ),
            "chat_context_management": forms.Select(
                attrs={"class": "form-select form-select-sm"},
            ),
            "chat_system_prompt": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 5}
            ),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        configure_chat_model_fields(self)

        # Toggles
        for field in ["send_name_to_model"]:
            self.fields[field].widget = forms.CheckboxInput(
                attrs={
                    "class": "form-check-input small",
                    "style": "filter: saturate(0); margin-top: 6px;",
                }
            )

        # Initial value for enabled_tools from JSON field
        if self.instance and self.instance.pk:
            tools = self.instance.chat_enabled_tools or []
            self.fields["chat_enabled_tools"].initial = tools
            self.fields["chat_max_iterations"].initial = (
                self.instance.chat_max_iterations or 25
            )
        else:
            self.fields["chat_max_iterations"].initial = 25

    def clean_chat_max_iterations(self):
        value = self.cleaned_data.get("chat_max_iterations")
        if value in (None, ""):
            if self.instance and self.instance.pk:
                return self.instance.chat_max_iterations or 25
            return 25
        return value

    def get_tools_approval_status(self):
        """Approval status for tools that support it."""
        auto_approve_tools = []
        if self.instance and self.instance.pk:
            auto_approve_tools = self.instance.chat_auto_approve_tools or []
        result = []
        for tool_id, tool_info in LOCAL_TOOLS_WITH_APPROVAL.items():
            result.append(
                {
                    "id": tool_id,
                    "label": str(tool_info.get("label", tool_id)),
                    "category": tool_info.get("category"),
                    "auto_approve": tool_id in auto_approve_tools,
                }
            )
        return result


class ChatModelSelectorForm(ChatModelFieldsMixin, ModelForm):
    class Meta:
        model = ChatSettings
        fields = [
            "chat_model",
            "chat_reasoning_effort",
            "chat_verbosity",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configure_chat_model_fields(
            self, widget_suffix_classes="chat-model-selector-field"
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
                    "onfocus": "this.select();",
                    "placeholder": _("Untitled chat"),
                }
            )
        }


class UploadForm(FileFormMixin, forms.Form):
    input_file = MultipleUploadedFileField(required=False)

    def save(self):
        saved_files = []
        if not self.cleaned_data.get("input_file"):
            return saved_files

        raw_metadata = self.cleaned_data.get("input_file-metadata") or "{}"
        try:
            metadata = json.loads(raw_metadata)
        except Exception:
            metadata = {}
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


class SkillImportForm(forms.Form):
    skill_file = forms.FileField(
        required=True,
        widget=forms.FileInput(
            attrs={
                "accept": ".md,.MD,.zip,.ZIP",
            }
        ),
    )

    def clean_skill_file(self):
        uploaded_file = self.cleaned_data.get("skill_file")
        if not uploaded_file:
            raise forms.ValidationError(_("Please choose a SKILL.md or ZIP file."))

        filename = (getattr(uploaded_file, "name", "") or "").strip()
        lowered_name = filename.lower()

        if not lowered_name:
            raise forms.ValidationError(_("Please choose a SKILL.md or ZIP file."))

        if lowered_name.endswith(".zip"):
            return uploaded_file

        if lowered_name.endswith(".md"):
            base_name = os.path.basename(lowered_name)
            if base_name in {"skill.md"}:
                return uploaded_file

        raise forms.ValidationError(
            _(
                "Unsupported file. Upload a SKILL.md-style markdown file or a ZIP bundle containing one supported skill."
            )
        )


class SkillForm(forms.ModelForm):
    """Form for creating and editing skills."""

    User = get_user_model()

    class Meta:
        model = Skill
        fields = [
            "display_name_en",
            "display_name_fr",
            "description_en",
            "description_fr",
            "body_en",
            "body_fr",
            "sharing_option",
        ]
        widgets = {
            "display_name_en": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "display_name_fr": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "description_en": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 3}
            ),
            "description_fr": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 3}
            ),
            "body_en": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 10}
            ),
            "body_fr": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 10}
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
        self.user = kwargs.pop("user", None)
        self.allow_incomplete = kwargs.pop("allow_incomplete", False)
        self.content_is_complete = False
        super().__init__(*args, **kwargs)
        self.can_change_sharing_option = not self.instance.pk or (
            self.user is not None
            and (
                self.instance.owner_id == getattr(self.user, "id", None)
                or self.user.has_perm("chat_next.admin_edit_skill", self.instance)
            )
        )
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
        # Only admins/stewards can share with everyone
        can_share_with_everyone = bool(
            self.user and self.user.has_perm("chat_next.share_skill_with_everyone")
        )

        sharing_choices = [
            ("private", _("Make private")),
            ("others", _("Share with specific people")),
        ]

        if can_share_with_everyone:
            self.fields["sharing_option"].choices = sharing_choices + [
                ("everyone", _("Share with everyone")),
            ]
        else:
            self.fields["sharing_option"].choices = sharing_choices

        self.fields["accessible_to"].widget.attrs.setdefault(
            "class", "form-control form-control-sm"
        )
        self.fields["editable_by"].widget.attrs.setdefault(
            "class", "form-control form-control-sm"
        )

    def clean(self):
        cleaned_data = super().clean()

        def _filled(value):
            return bool((value or "").strip())

        en_complete = all(
            [
                _filled(cleaned_data.get("display_name_en")),
                _filled(cleaned_data.get("description_en")),
                _filled(cleaned_data.get("body_en")),
            ]
        )
        fr_complete = all(
            [
                _filled(cleaned_data.get("display_name_fr")),
                _filled(cleaned_data.get("description_fr")),
                _filled(cleaned_data.get("body_fr")),
            ]
        )

        self.content_is_complete = en_complete or fr_complete

        if not self.allow_incomplete and not self.content_is_complete:
            raise forms.ValidationError(
                _(
                    "Please complete Display name, Description, and Prompt in either English or French."
                )
            )

        sharing_option = cleaned_data.get("sharing_option")
        if self.instance.pk and not self.can_change_sharing_option:
            sharing_option = self.instance.sharing_option
            cleaned_data["sharing_option"] = sharing_option

        if sharing_option == "everyone":
            allowed = bool(
                self.user and self.user.has_perm("chat_next.share_skill_with_everyone")
            )
            if not allowed:
                raise forms.ValidationError(
                    _("You do not have permission to share skills with everyone.")
                )
        return cleaned_data

    def save_sharing(self, skill):
        """Save accessible_to/editable_by users and teams on the skill instance."""
        accessible_data = self.cleaned_data.get("accessible_to", {})
        editable_data = self.cleaned_data.get("editable_by", {})
        accessible_users = (
            accessible_data.get("users", [])
            if isinstance(accessible_data, dict)
            else []
        )
        accessible_teams = (
            accessible_data.get("teams", [])
            if isinstance(accessible_data, dict)
            else []
        )
        editable_users = (
            editable_data.get("users", []) if isinstance(editable_data, dict) else []
        )
        editable_teams = (
            editable_data.get("teams", []) if isinstance(editable_data, dict) else []
        )
        skill.accessible_to.set(accessible_users)
        skill.editable_by.set(editable_users)
        skill.accessible_to_teams.set(accessible_teams)
        skill.editable_by_teams.set(editable_teams)
