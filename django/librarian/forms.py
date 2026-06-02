from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from autocomplete import HTMXAutoComplete, widgets

from otto.form_fields import UserOrTeamMultipleChoiceField
from otto.forms import UserOrTeamAutocompleteMixin
from otto.utils.common import check_url_allowed, normalize_content_ingestion_url

from chat.utils import bad_url
from librarian.models import (
    DataSource,
    Document,
    Library,
    LibraryTeamRole,
    LibraryUserRole,
)

User = get_user_model()


class LibraryAdminsAutocomplete(UserOrTeamAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for library administrators"""

    name = "admins"
    multiselect = True
    minimum_search_length = 0
    model = User


class LibraryContributorsAutocomplete(UserOrTeamAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for library contributors"""

    name = "contributors"
    multiselect = True
    minimum_search_length = 0
    model = User


class LibraryViewersAutocomplete(UserOrTeamAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for library viewers"""

    name = "viewers"
    multiselect = True
    minimum_search_length = 0
    model = User


class LibraryDetailForm(forms.ModelForm):
    template_name = "librarian/forms/library.html"

    # Add as a standalone field (not controlled by ModelForm Meta)
    hnsw_enabled = forms.ChoiceField(
        label=_("Build HNSW index"),
        choices=[
            ("", _("Automatic (recommended)")),
            ("true", _("Enabled")),
            ("false", _("Disabled")),
        ],
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        help_text=_("Automatic uses HNSW for libraries with 50,000+ chunks"),
    )

    def __init__(self, *args, **kwargs):
        # Allow passing template context flag so the form template can access it
        self.advanced_expanded = kwargs.pop("advanced_expanded", False)
        self.user = kwargs.pop("user", None)
        super(LibraryDetailForm, self).__init__(*args, **kwargs)
        # Set initial value for hnsw_enabled based on model value
        if self.instance.hnsw_enabled is None:
            self.initial["hnsw_enabled"] = ""
        elif self.instance.hnsw_enabled is True:
            self.initial["hnsw_enabled"] = "true"
        else:
            self.initial["hnsw_enabled"] = "false"
        # A library can only be made public if it has an id (it is an existing library)
        # and the user has the change_publicity permission.
        self.can_make_public = self.user and self.user.has_perm(
            "librarian.change_publicity", self.instance
        )
        # If not, hide the is_public field
        if not self.can_make_public:
            self.fields.pop("is_public")
        else:
            # If the initial value of is_public is False, add an onclick property
            # to the is_public field to show the warning message
            if not self.instance.is_public:
                self.fields["is_public"].widget.attrs["onclick"] = (
                    "toggleWarning(this);"
                )
        # If an existing library, check if user has permissions to delete this library
        self.deletable = self.instance.pk and self.user.has_perm(
            "librarian.delete_library", self.instance
        )

    # Ensure the form template can see advanced_expanded
    def get_context(self):
        context = super().get_context()
        context["advanced_expanded"] = bool(getattr(self, "advanced_expanded", False))
        return context

    class Meta:
        model = Library
        fields = [
            "name_en",
            "name_fr",
            "description_en",
            "description_fr",
            "order",
            "is_public",
        ]
        widgets = {
            "name_en": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "description_en": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 2}
            ),
            "description_fr": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 2}
            ),
            "order": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
            # AC-21: Add a checkbox to make the library public
            "is_public": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def save(self, commit=True):
        is_new = not self.instance.pk
        old_hnsw_enabled = self.instance.hnsw_enabled if not is_new else None

        # Convert hnsw_enabled string to None/True/False before saving
        hnsw_value = self.cleaned_data.get("hnsw_enabled")
        if hnsw_value == "":
            self.instance.hnsw_enabled = None
        elif hnsw_value == "true":
            self.instance.hnsw_enabled = True
        else:
            self.instance.hnsw_enabled = False

        instance = super(LibraryDetailForm, self).save(commit=commit)

        if is_new:
            # Add the user as a library admin role
            LibraryUserRole.objects.create(
                library=instance, user=self.user, role="admin"
            )
            # Initialize vector table for new library
            # Note: reset() now has safeguards, but a new library has no documents so it's safe
            instance.reset()
        else:
            # Handle HNSW setting changes for existing libraries
            self._handle_hnsw_change(instance, old_hnsw_enabled)

        return instance

    def _handle_hnsw_change(self, instance, old_value):
        """Handle HNSW index build/deletion when setting changes."""
        from celery.result import AsyncResult

        from librarian.tasks import build_hnsw_index, delete_hnsw_index

        new_value = instance.hnsw_enabled

        # No change, do nothing
        if old_value == new_value:
            return

        # Cancel any ongoing build or delete task
        if instance.hnsw_task_id:
            try:
                AsyncResult(instance.hnsw_task_id).revoke(terminate=True)
            except Exception:
                pass

        # Changed to "Enabled" (True) - force build
        if new_value is True:
            # Only attempt to build if we have chunks
            if instance.total_chunks > 0 and instance.hnsw_status in ["none", "error"]:
                instance.hnsw_status = "pending"
                instance.save(update_fields=["hnsw_status"])
                result = build_hnsw_index.delay(instance.uuid_hex)
                instance.hnsw_task_id = result.id
                instance.save(update_fields=["hnsw_task_id"])
            elif instance.total_chunks == 0:
                # No chunks to index
                instance.hnsw_status = "none"
                instance.save(update_fields=["hnsw_status"])

        # Changed to "Disabled" (False) - delete index if it exists
        elif new_value is False:
            # Check if index actually exists before starting deletion task
            if self._hnsw_index_exists(instance):
                instance.hnsw_status = "deleting"
                instance.save(update_fields=["hnsw_status"])
                result = delete_hnsw_index.delay(instance.uuid_hex)
                instance.hnsw_task_id = result.id
                instance.save(update_fields=["hnsw_task_id"])
            else:
                # No index to delete
                instance.hnsw_status = "none"
                instance.hnsw_task_id = None
                instance.save(update_fields=["hnsw_status", "hnsw_task_id"])

        # Changed to "Automatic" (None) - apply default logic
        # IMPORTANT: Only build if index doesn't exist. Never delete existing indexes.
        else:
            if instance.should_use_hnsw():
                # Should have index, build if needed
                if instance.hnsw_status == "none" and not self._hnsw_index_exists(
                    instance
                ):
                    instance.check_and_build_hnsw()
                elif self._hnsw_index_exists(instance):
                    # Index already exists, mark as ready
                    instance.hnsw_status = "ready"
                    instance.save(update_fields=["hnsw_status"])
            else:
                # Should not have index based on threshold, but DO NOT delete existing indexes
                # Just mark status as none if no index exists
                if not self._hnsw_index_exists(instance):
                    instance.hnsw_status = "none"
                    instance.hnsw_task_id = None
                    instance.save(update_fields=["hnsw_status", "hnsw_task_id"])

    def _hnsw_index_exists(self, instance):
        """Check if the HNSW index exists for a library."""
        from sqlalchemy import text

        from chat.llm import get_pg_engines

        try:
            pg_sync_engine, _ = get_pg_engines()
            table_name = f"data_{instance.uuid_hex}"
            index_name = f"{table_name}_embedding_idx"

            with pg_sync_engine.connect() as conn:
                index_check = conn.execute(
                    text(
                        """
                        SELECT indexname FROM pg_indexes 
                        WHERE schemaname = 'public' AND indexname = :index_name
                    """
                    ),
                    {"index_name": index_name},
                )
                return index_check.fetchone() is not None
        except Exception:
            return False


class DataSourceDetailForm(forms.ModelForm):
    template_name = "librarian/forms/data_source.html"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.library_id = kwargs.pop("library_id", None)
        super(DataSourceDetailForm, self).__init__(*args, **kwargs)
        if self.library_id:
            self.fields["library"].initial = self.library_id
        self.deletable = self.instance.pk and self.user.has_perm(
            "librarian.delete_data_source", self.instance
        )

    class Meta:
        model = DataSource
        fields = ["name_en", "name_fr", "security_label", "order", "library"]
        widgets = {
            "library": forms.HiddenInput(),
            "name_en": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "security_label": forms.Select(
                attrs={"class": "form-select form-select-sm"}
            ),
            "order": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
        }


class DocumentDetailForm(forms.ModelForm):
    template_name = "librarian/forms/document.html"

    def __init__(self, *args, **kwargs):
        self.data_source_id = kwargs.pop("data_source_id", None)
        super(DocumentDetailForm, self).__init__(*args, **kwargs)
        if self.data_source_id:
            self.fields["data_source"].initial = self.data_source_id
        # If there is a filename, hide the url and selector fields
        if self.instance.filename and not self.instance.url:
            self.fields.pop("url")
            self.fields.pop("selector")

    def clean_url(self):
        url = self.cleaned_data.get("url")
        if url:
            url_validator = URLValidator()
            try:
                url_validator(url)
            except ValidationError:
                raise ValidationError(_("Invalid URL"))
            normalized_url = normalize_content_ingestion_url(url)
            if not check_url_allowed(normalized_url):
                raise ValidationError(mark_safe(bad_url(render_markdown=True)))
            return normalized_url
        return url

    class Meta:
        model = Document
        fields = ["manual_title", "url", "selector", "data_source", "filename"]
        widgets = {
            "data_source": forms.HiddenInput(),
            "filename": forms.HiddenInput(),
            "manual_title": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "url": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "selector": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
        }


class LibraryUsersForm(forms.Form):
    template_name = "librarian/forms/library_users.html"

    admins = UserOrTeamMultipleChoiceField(
        label=_("Administrators (edit library and manage users)"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=LibraryAdminsAutocomplete,
            options={
                "component_id": "id_admins",
            },
        ),
    )
    contributors = UserOrTeamMultipleChoiceField(
        label=_("Contributors (edit library)"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=LibraryContributorsAutocomplete,
            options={
                "component_id": "id_contributors",
            },
        ),
    )
    viewers = UserOrTeamMultipleChoiceField(
        label=_("Viewers (read-only access)"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=LibraryViewersAutocomplete,
            options={
                "component_id": "id_viewers",
            },
        ),
    )

    def __init__(self, *args, **kwargs):
        self.library = kwargs.pop("library", None)
        self.actor = kwargs.pop("actor", None)
        super(LibraryUsersForm, self).__init__(*args, **kwargs)
        # Build initial values: user IDs + team:ID prefixed values
        admin_user_ids = list(self.library.admins)
        admin_team_ids = [
            f"team:{tid}"
            for tid in self.library.team_roles.filter(role="admin").values_list(
                "team_id", flat=True
            )
        ]
        self.fields["admins"].initial = list(admin_user_ids) + admin_team_ids

        contrib_user_ids = list(self.library.contributors)
        contrib_team_ids = [
            f"team:{tid}"
            for tid in self.library.team_roles.filter(role="contributor").values_list(
                "team_id", flat=True
            )
        ]
        self.fields["contributors"].initial = list(contrib_user_ids) + contrib_team_ids

        viewer_user_ids = list(self.library.viewers)
        viewer_team_ids = [
            f"team:{tid}"
            for tid in self.library.team_roles.filter(role="viewer").values_list(
                "team_id", flat=True
            )
        ]
        self.fields["viewers"].initial = list(viewer_user_ids) + viewer_team_ids

    def save(self):
        if self.library.is_personal_library or self.library.is_skill_library:
            raise ValidationError(
                _(
                    "This library's access is managed automatically and cannot be changed here."
                )
            )
        self.library.user_roles.all().delete()
        self.library.team_roles.all().delete()

        for role_name, role_key in [
            ("admins", "admin"),
            ("contributors", "contributor"),
            ("viewers", "viewer"),
        ]:
            data = self.cleaned_data[role_name]
            for user in data.get("users", []):
                LibraryUserRole.objects.create(
                    library=self.library, user=user, role=role_key
                )
            for team in data.get("teams", []):
                LibraryTeamRole.objects.create(
                    library=self.library, team=team, role=role_key
                )

    def clean(self):
        cleaned_data = super(LibraryUsersForm, self).clean()
        if self.library.is_personal_library or self.library.is_skill_library:
            raise forms.ValidationError(
                _(
                    "This library's access is managed automatically and cannot be changed here."
                )
            )
        # Require at least one admin user (not just team)
        admin_data = cleaned_data.get("admins", {})
        admin_users = (
            admin_data.get("users", []) if isinstance(admin_data, dict) else []
        )
        admin_teams = (
            admin_data.get("teams", []) if isinstance(admin_data, dict) else []
        )
        if not admin_users and not admin_teams:
            raise forms.ValidationError(_("At least one administrator is required."))

        # Check for users appearing in multiple roles
        all_users = []
        all_user_set = set()
        for role_name in ["admins", "contributors", "viewers"]:
            data = cleaned_data.get(role_name, {})
            users = data.get("users", []) if isinstance(data, dict) else []
            for user in users:
                all_users.append(user)
                all_user_set.add(user)
        if len(all_users) != len(all_user_set):
            raise forms.ValidationError(_("The same user cannot be in multiple roles."))

        # Check for teams appearing in multiple roles
        all_teams = []
        all_team_set = set()
        for role_name in ["admins", "contributors", "viewers"]:
            data = cleaned_data.get(role_name, {})
            teams = data.get("teams", []) if isinstance(data, dict) else []
            for team in teams:
                all_teams.append(team)
                all_team_set.add(team)
        if len(all_teams) != len(all_team_set):
            raise forms.ValidationError(_("The same team cannot be in multiple roles."))

        return cleaned_data
