import ipaddress

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from autocomplete import HTMXAutoComplete, widgets
from data_fetcher.util import get_request

from otto.form_fields import PermissiveModelMultipleChoiceField
from otto.models import (
    COUNT_TYPE_CHOICES,
    EXTERNAL_TOOL_REVIEW_AZURE_PII_CATEGORY_CHOICES,
    ApiClient,
    ApiClientAllowedIP,
    ApiClientAuditEvent,
    ApiClientScope,
    CostGroup,
    Feedback,
    Notification,
    OttoStatus,
    Team,
    default_external_tool_review_flagged_azure_pii_categories,
)
from otto.utils.api_permissions import (
    get_api_scope_choices,
    get_api_scope_definition,
)

from chat.models import Message

User = get_user_model()


class SimpleFieldAutocompleteMixin:
    """
    Mixin for autocomplete components that filter and display a single field.

    Subclasses should define:
    - field_name: str - the field to filter and display (e.g., "upn", "email")
    - order_by: str - field to order results by (defaults to field_name)
    - limit: int - max results to return (defaults to 500)
    """

    field_name = None  # Must be set by subclass
    order_by = None  # Defaults to field_name if not set
    limit = 500

    def get_items(self, search=None, values=None, request=None):
        order_by = self.order_by or self.field_name

        if values is not None:
            data = self.model.objects.filter(id__in=values).values(
                "id", self.field_name
            )
            return [{"label": x[self.field_name], "value": str(x["id"])} for x in data]

        if search is not None:
            if search == "":
                data = self.model.objects.values("id", self.field_name).order_by(
                    order_by
                )[: self.limit]
            else:
                filter_kwargs = {f"{self.field_name}__icontains": search}
                data = (
                    self.model.objects.filter(**filter_kwargs)
                    .values("id", self.field_name)
                    .order_by(order_by)[: self.limit]
                )
            return [{"label": x[self.field_name], "value": str(x["id"])} for x in data]

        return []


class CostGroupAutocompleteMixin:
    """
    Mixin for cost group autocompletes that display name and LEX file number.
    Filters on both name and lex_file_number fields.
    """

    limit = 100

    def format_label(self, item):
        """Format cost group label as 'Name (LEX)' or just 'Name'"""
        if item.get("lex_file_number"):
            return f"{item['name']} ({item['lex_file_number']})"
        return item["name"]

    def get_base_queryset(self, request=None):
        """Override this method in subclasses to customize the base queryset"""
        return CostGroup.objects.filter(active=True)

    def get_items(self, search=None, values=None, request=None):
        # Get base queryset
        cost_groups = self.get_base_queryset(request)

        if values is not None:
            # Filter out empty strings
            values = [v for v in values if v]
            if not values:
                return []
            # Iterate over objects to get translated name
            items = []
            for cg in cost_groups.filter(id__in=values):
                label = (
                    f"{cg.name} ({cg.lex_file_number})"
                    if cg.lex_file_number
                    else cg.name
                )
                items.append({"label": label, "value": str(cg.id)})
            return items

        if search is not None:
            # Get all available cost groups and format labels
            # Iterate over objects to get translated name
            cost_groups_list = cost_groups.order_by("name")
            items = []
            for cg in cost_groups_list:
                label = (
                    f"{cg.name} ({cg.lex_file_number})"
                    if cg.lex_file_number
                    else cg.name
                )
                items.append({"label": label, "value": str(cg.id)})

            # Filter in Python if search term provided
            if search:
                search_lower = search.lower()
                items = [
                    item for item in items if search_lower in item["label"].lower()
                ]

            # Limit results
            return items[: self.limit]

        return []


class ActiveCostGroupsAutocomplete(CostGroupAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for all active cost groups (admin dashboards)"""

    name = "all_active_cost_groups"
    multiselect = False
    minimum_search_length = 0
    model = CostGroup


class DashboardCostGroupsAutocomplete(HTMXAutoComplete):
    """Autocomplete for dashboard cost group filter with synthetic filter options"""

    name = "dashboard_cost_groups"
    multiselect = False
    minimum_search_length = 0
    model = CostGroup

    def get_items(self, search=None, values=None, request=None):
        # Synthetic filter items
        synthetic_items = [
            {"label": str(_("All cost groups & personal costs")), "value": "all"},
            {"label": str(_("Personal costs only")), "value": "personal"},
            {
                "label": str(_("Cost group costs only (all groups)")),
                "value": "cost_groups",
            },
        ]

        # If looking up specific values (for initial selection)
        if values is not None:
            # Check if it's a synthetic value
            for item in synthetic_items:
                if item["value"] in values:
                    return [item]
            # Otherwise, it's a cost group ID
            data = CostGroup.objects.filter(active=True, id__in=values).values(
                "id", "name", "lex_file_number"
            )
            items = []
            for x in data:
                label = (
                    f"{x['name']} ({x['lex_file_number']})"
                    if x.get("lex_file_number")
                    else x["name"]
                )
                items.append({"label": label, "value": str(x["id"])})
            return items

        # For search/dropdown display
        if search is not None:
            # Start with synthetic items
            items = synthetic_items.copy()

            # Add all active cost groups
            cost_groups = (
                CostGroup.objects.filter(active=True)
                .values("id", "name", "lex_file_number")
                .order_by("name")
            )

            for x in cost_groups:
                label = (
                    f"{x['name']} ({x['lex_file_number']})"
                    if x.get("lex_file_number")
                    else x["name"]
                )
                items.append({"label": label, "value": str(x["id"])})

            # Filter by search term if provided
            if search:
                search_lower = search.lower()
                items = [
                    item for item in items if search_lower in item["label"].lower()
                ]

            return items

        return []


class SpecificCostGroupsAutocomplete(CostGroupAutocompleteMixin, HTMXAutoComplete):
    """Multiselect autocomplete for selecting specific cost groups in dashboards"""

    name = "specific_cost_groups"
    multiselect = True
    minimum_search_length = 0
    model = CostGroup


class UserManagementCostGroupsAutocomplete(
    CostGroupAutocompleteMixin, HTMXAutoComplete
):
    """Multiselect autocomplete for assigning cost groups to users in user management"""

    name = "cost_group"
    multiselect = True
    minimum_search_length = 0
    model = CostGroup


class UserCostGroupsAutocomplete(CostGroupAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for cost groups available to current user (navbar)"""

    name = "user_cost_groups"
    multiselect = False
    minimum_search_length = 0
    model = CostGroup

    def get_base_queryset(self, request=None):
        """Get cost groups available to the current user"""
        request = get_request()
        if request and hasattr(request, "user") and request.user.is_authenticated:
            return CostGroup.get_available_cost_groups(request.user)
        # Return empty queryset if no valid request/user (security: don't show all cost groups)
        return CostGroup.objects.none()


all_active_cost_groups_widget = widgets.Autocomplete(
    use_ac=ActiveCostGroupsAutocomplete,
    attrs={
        "component_id": "id_cost_group",
        "id": "id_cost_group__textinput",
        "name": "cost_group",
    },
)

dashboard_cost_groups_widget = widgets.Autocomplete(
    use_ac=DashboardCostGroupsAutocomplete,
    attrs={
        "component_id": "id_cost_group",
        "id": "id_cost_group__textinput",
        "name": "cost_group",
    },
)

specific_cost_groups_widget = widgets.Autocomplete(
    use_ac=SpecificCostGroupsAutocomplete,
    attrs={
        "component_id": "id_specific_cost_groups",
        "id": "id_specific_cost_groups__textinput",
        "name": "specific_cost_groups",
    },
)

user_cost_groups_widget = widgets.Autocomplete(
    use_ac=UserCostGroupsAutocomplete,
    attrs={
        "component_id": "id_user_cost_groups",
        "id": "id_user_cost_groups__textinput",
    },
)


user_management_cost_groups_widget = widgets.Autocomplete(
    use_ac=UserManagementCostGroupsAutocomplete,
    attrs={
        "component_id": "id_cost_group",
        "id": "id_cost_group__textinput",
        "name": "cost_group",
    },
)


class CostGroupSelectionForm(forms.Form):
    """Form for selecting a cost group in the navbar"""

    user_cost_groups = PermissiveModelMultipleChoiceField(
        queryset=CostGroup.objects.none(),
        label="",
        required=False,
        widget=widgets.Autocomplete(
            use_ac=UserCostGroupsAutocomplete,
            attrs={
                "component_id": "id_user_cost_groups",
                "id": "id_user_cost_groups__textinput",
                "class": "form-select-sm",
                "style": "max-width: 250px;",
            },
        ),
    )


class UserAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete component for selecting users by UPN"""

    name = "upn"
    multiselect = True
    minimum_search_length = 0
    model = User
    field_name = "upn"


class MergeTargetUserAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Single-select autocomplete for merge target users."""

    name = "target_user"
    minimum_search_length = 0
    model = User
    field_name = "upn"


class MergeSourceUsersAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Multi-select autocomplete for merge source users."""

    name = "source_users"
    multiselect = True
    minimum_search_length = 0
    model = User
    field_name = "upn"


class UserEmailAutocompleteMixin(SimpleFieldAutocompleteMixin):
    """Mixin providing optimized get_items for user email autocomplete"""

    model = User
    field_name = "email"


class UserEmailAutocomplete(UserEmailAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete component for selecting users by email"""

    name = "user_email"
    multiselect = True
    minimum_search_length = 0  # Allow clicking to see results
    model = User


class CostGroupUsersAutocomplete(UserEmailAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for cost group authorized users"""

    name = "users"
    multiselect = True
    minimum_search_length = 0
    model = User


class TeamMembersAutocomplete(UserEmailAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for selecting team members by email"""

    name = "team_members"
    multiselect = True
    minimum_search_length = 0
    model = User


class TeamAdminsAutocomplete(UserEmailAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for selecting team admins by email"""

    name = "team_admins"
    multiselect = True
    minimum_search_length = 0
    model = User


class ApiClientOwnerAutocomplete(UserEmailAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for selecting the business owner of an API client."""

    name = "owner"
    minimum_search_length = 0
    model = User


class TeamNameAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for selecting teams by name"""

    name = "teams"
    multiselect = True
    minimum_search_length = 0
    model = Team
    field_name = "name"


class UserOrTeamAutocompleteMixin:
    """
    Mixin for autocompletes that return both users (by email) and teams.
    Teams are returned with a '[Team]' prefix and a 'team:' value prefix.
    """

    model = User
    limit = 500

    def get_items(self, search=None, values=None, request=None):
        from otto.models import Team

        items = []

        if values is not None:
            user_ids = []
            team_ids = []
            for v in values:
                v_str = str(v)
                if v_str.startswith("team:"):
                    team_ids.append(v_str[5:])
                else:
                    user_ids.append(v)
            if user_ids:
                users = User.objects.filter(id__in=user_ids).values("id", "email")
                items.extend(
                    {"label": u["email"], "value": str(u["id"])} for u in users
                )
            if team_ids:
                teams = Team.objects.filter(id__in=team_ids).values("id", "name")
                items.extend(
                    {"label": f"[Team] {t['name']}", "value": f"team:{t['id']}"}
                    for t in teams
                )
            return items

        if search is not None:
            # Search users by email
            if search == "":
                users = User.objects.values("id", "email").order_by("email")[
                    : self.limit
                ]
            else:
                users = (
                    User.objects.filter(email__icontains=search)
                    .values("id", "email")
                    .order_by("email")[: self.limit]
                )
            items.extend({"label": u["email"], "value": str(u["id"])} for u in users)

            # Search teams by name
            if search == "":
                teams = Team.objects.values("id", "name").order_by("name")[:50]
            else:
                teams = (
                    Team.objects.filter(name__icontains=search)
                    .values("id", "name")
                    .order_by("name")[:50]
                )
            items.extend(
                {"label": f"[Team] {t['name']}", "value": f"team:{t['id']}"}
                for t in teams
            )

            return items

        return []


class SharingAccessibleToAutocomplete(UserOrTeamAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for accessible_to fields (users + teams)"""

    name = "accessible_to"
    multiselect = True
    minimum_search_length = 0


class SharingEditableByAutocomplete(UserOrTeamAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete for editable_by fields (users + teams)"""

    name = "editable_by"
    multiselect = True
    minimum_search_length = 0


class TeamForm(forms.Form):
    """Form for creating and editing teams."""

    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label=_("Team name"),
        help_text=_(
            "Use a clear bilingual name when possible, for example ‘Research / Recherche’."
        ),
    )
    admins = PermissiveModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Administrators"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=TeamAdminsAutocomplete,
            options={"component_id": "id_team_admins"},
        ),
    )
    members = PermissiveModelMultipleChoiceField(
        queryset=User.objects.none(),
        label=_("Members"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=TeamMembersAutocomplete,
            options={"component_id": "id_team_members"},
        ),
    )

    def __init__(self, *args, **kwargs):
        self.team = kwargs.pop("team", None)
        if args and args[0] is not None:
            data = args[0]
            # The autocomplete component posts selected values under its component
            # names (team_admins/team_members). Remap into the form field names
            # (admins/members) so Django field binding works.
            if hasattr(data, "getlist") and hasattr(data, "copy"):
                mutable_data = data.copy()
                if "admins" not in mutable_data and "team_admins" in mutable_data:
                    mutable_data.setlist("admins", mutable_data.getlist("team_admins"))
                if "members" not in mutable_data and "team_members" in mutable_data:
                    mutable_data.setlist(
                        "members", mutable_data.getlist("team_members")
                    )
                args = (mutable_data, *args[1:])
            elif isinstance(data, dict):
                mutable_data = data.copy()
                if "admins" not in mutable_data and "team_admins" in mutable_data:
                    mutable_data["admins"] = mutable_data["team_admins"]
                if "members" not in mutable_data and "team_members" in mutable_data:
                    mutable_data["members"] = mutable_data["team_members"]
                args = (mutable_data, *args[1:])
        super().__init__(*args, **kwargs)
        if self.team:
            self.fields["name"].initial = self.team.name
            self.fields["admins"].initial = list(
                self.team.admins.values_list("id", flat=True)
            )
            self.fields["members"].initial = list(
                self.team.members.values_list("id", flat=True)
            )

    def clean(self):
        cleaned_data = super().clean()
        name = (cleaned_data.get("name") or "").strip()
        admins = cleaned_data.get("admins", [])
        members = cleaned_data.get("members", [])
        if not name:
            return cleaned_data
        cleaned_data["name"] = name
        duplicate_teams = Team.objects.filter(name__iexact=name)
        if self.team:
            duplicate_teams = duplicate_teams.exclude(pk=self.team.pk)
        if duplicate_teams.exists():
            self.add_error("name", _("A team with this name already exists."))
        if not admins:
            raise forms.ValidationError(_("At least one administrator is required."))
        # Check for users appearing in both roles
        admin_ids = {u.pk for u in admins}
        member_ids = {u.pk for u in members}
        overlap = admin_ids & member_ids
        if overlap:
            raise forms.ValidationError(
                _("A user cannot be both an admin and a member of the same team.")
            )
        return cleaned_data

    def save(self, user):
        from otto.models import Team, TeamMembership

        if self.team:
            team = self.team
            team.name = self.cleaned_data["name"]
            team.save()
            team.memberships.filter(
                ~(
                    Q(user__in=self.cleaned_data["admins"])
                    | Q(user__in=self.cleaned_data["members"])
                )
            ).delete()
        else:
            team = Team.objects.create(name=self.cleaned_data["name"], created_by=user)
            # Creator is always an admin
            TeamMembership.objects.create(team=team, user=user, role="admin")

        for admin_user in self.cleaned_data["admins"]:
            _, created = TeamMembership.objects.update_or_create(
                team=team, user=admin_user, defaults={"role": "admin"}
            )
            if created and admin_user != user:
                text_en = f'{user} added you to Team "{team.name}" as an administrator.'
                text_fr = (
                    f'{user} t’a ajouté à l’équipe "{team.name}" comme administrateur.'
                )
                Notification.objects.create(
                    user=admin_user,
                    heading_en="Added to Team",
                    heading_fr="Ajout à l’équipe",
                    text_en=text_en,
                    text_fr=text_fr,
                    category="info",
                    link="/teams/",
                )
        for member_user in self.cleaned_data["members"]:
            _, created = TeamMembership.objects.update_or_create(
                team=team, user=member_user, defaults={"role": "member"}
            )
            if created and member_user != user:
                text_en = f'{user} added you to Team "{team.name}" as a member.'
                text_fr = f'{user} t’a ajouté à l’équipe "{team.name}" comme membre.'
                Notification.objects.create(
                    user=member_user,
                    heading_en="Added to Team",
                    heading_fr="Ajout à l’équipe",
                    text_en=text_en,
                    text_fr=text_fr,
                    category="info",
                    link="/teams/",
                )
        return team


class ApiClientForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label=_("Client name"),
        help_text=_(
            "Use a descriptive name, such as ‘JusTipedia nightly sync’ or ‘Reporting dashboard integration’."
        ),
    )
    owner = forms.ModelChoiceField(
        queryset=User.objects.all().order_by("email"),
        required=False,
        label=_("Owner"),
        widget=widgets.Autocomplete(
            use_ac=ApiClientOwnerAutocomplete,
            attrs={
                "component_id": "id_owner",
                "id": "id_owner__textinput",
                "name": "owner",
            },
        ),
    )
    description = forms.CharField(
        required=False,
        label=_("Description"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    scopes = forms.MultipleChoiceField(
        required=False,
        label=_("Allowed endpoint scopes"),
        choices=get_api_scope_choices(),
        widget=forms.CheckboxSelectMultiple(),
        help_text=_(
            "Scopes determine which API endpoint families the machine may call."
        ),
    )
    allowed_ips = forms.CharField(
        required=False,
        label=_("Allowed IP ranges"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        help_text=_(
            "Optional. Enter one IP address or CIDR range per line. Leave blank to allow any source IP."
        ),
    )
    is_active = forms.BooleanField(
        required=False,
        label=_("Active"),
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        self.client = kwargs.pop("client", None)
        super().__init__(*args, **kwargs)
        if self.client:
            self.fields["name"].initial = self.client.name
            self.fields["owner"].initial = self.client.owner_id
            self.fields["description"].initial = self.client.description
            self.fields["is_active"].initial = self.client.is_active
            self.fields["scopes"].initial = list(
                self.client.scope_assignments.values_list("scope", flat=True)
            )
            self.fields["allowed_ips"].initial = "\n".join(
                self.client.allowed_ip_ranges.values_list("cidr", flat=True)
            )

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        duplicate_clients = ApiClient.objects.filter(name__iexact=name)
        if self.client:
            duplicate_clients = duplicate_clients.exclude(pk=self.client.pk)
        if duplicate_clients.exists():
            raise forms.ValidationError(
                _("An API client with this name already exists.")
            )
        return name

    def clean_allowed_ips(self):
        raw_value = self.cleaned_data.get("allowed_ips") or ""
        values = []
        seen = set()
        for line in raw_value.replace(",", "\n").splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            try:
                normalized = str(ipaddress.ip_network(candidate, strict=False))
            except ValueError as exc:
                raise forms.ValidationError(
                    _("'%(value)s' is not a valid IP address or CIDR range.")
                    % {"value": candidate}
                ) from exc
            if normalized not in seen:
                seen.add(normalized)
                values.append(normalized)
        return values

    @staticmethod
    def _serialize_user(user):
        if user is None:
            return None
        return getattr(user, "upn", str(user))

    def save(self, actor):
        created = self.client is None
        client = self.client or ApiClient(created_by=actor)
        changes = {}

        simple_fields = {
            "name": self.cleaned_data["name"],
            "owner": self.cleaned_data.get("owner"),
            "description": (self.cleaned_data.get("description") or "").strip(),
            "is_active": self.cleaned_data.get("is_active", False),
        }

        for field_name, new_value in simple_fields.items():
            old_value = getattr(client, field_name)
            if old_value != new_value:
                if field_name == "owner":
                    changes[field_name] = {
                        "old": self._serialize_user(old_value),
                        "new": self._serialize_user(new_value),
                    }
                else:
                    changes[field_name] = {"old": old_value, "new": new_value}
                setattr(client, field_name, new_value)

        client.save()

        existing_scopes = set(client.scope_assignments.values_list("scope", flat=True))
        requested_scopes = set(self.cleaned_data.get("scopes") or [])
        scopes_added = sorted(requested_scopes - existing_scopes)
        scopes_removed = sorted(existing_scopes - requested_scopes)
        if scopes_added:
            ApiClientScope.objects.bulk_create(
                [ApiClientScope(client=client, scope=scope) for scope in scopes_added]
            )
        if scopes_removed:
            client.scope_assignments.filter(scope__in=scopes_removed).delete()

        existing_ips = set(client.allowed_ip_ranges.values_list("cidr", flat=True))
        requested_ips = set(self.cleaned_data.get("allowed_ips") or [])
        ips_added = sorted(requested_ips - existing_ips)
        ips_removed = sorted(existing_ips - requested_ips)
        if ips_added:
            ApiClientAllowedIP.objects.bulk_create(
                [ApiClientAllowedIP(client=client, cidr=cidr) for cidr in ips_added]
            )
        if ips_removed:
            client.allowed_ip_ranges.filter(cidr__in=ips_removed).delete()

        if scopes_added or scopes_removed:
            changes["scopes"] = {
                "added": scopes_added,
                "removed": scopes_removed,
            }
        if ips_added or ips_removed:
            changes["allowed_ips"] = {
                "added": ips_added,
                "removed": ips_removed,
            }

        plaintext_token = None
        if created:
            plaintext_token = client.issue_token()
            ApiClientAuditEvent.objects.create(
                client=client,
                actor=actor,
                event_type=ApiClientAuditEvent.EventType.CREATED,
                metadata={
                    "owner": self._serialize_user(client.owner),
                    "scopes": sorted(requested_scopes),
                    "allowed_ips": sorted(requested_ips),
                    "is_active": client.is_active,
                },
            )
        elif changes:
            ApiClientAuditEvent.objects.create(
                client=client,
                actor=actor,
                event_type=ApiClientAuditEvent.EventType.UPDATED,
                metadata={"changes": changes},
            )

        return client, plaintext_token, created

    def scope_details(self):
        details = []
        for scope, label in self.fields["scopes"].choices:
            definition = get_api_scope_definition(scope)
            details.append(
                {
                    "scope": scope,
                    "label": label,
                    "description": getattr(definition, "description", scope),
                }
            )
        return details


class FeedbackForm(ModelForm):
    class Meta:
        model = Feedback

        fields = [
            "feedback_message",
            "modified_by",
            "created_by",
            "app",
            "chat_message",
            "chat_message_next",
            "otto_version",
            "url_context",
        ]

        widgets = {
            "feedback_message": forms.Textarea(
                attrs={
                    "id": "feedback-message-textarea",
                    "class": "form-control my-2",
                },
            ),
            "modified_by": forms.HiddenInput(),
            "created_by": forms.HiddenInput(),
            "chat_message": forms.HiddenInput(),
            "chat_message_next": forms.HiddenInput(),
            "otto_version": forms.HiddenInput(),
            "url_context": forms.HiddenInput(),
            "app": forms.HiddenInput(),
        }

        labels = {
            "feedback_message": _(
                "Let us know what went wrong, or suggest an improvement."
            ),
        }

    def __init__(self, user, message_id, *args, is_chat_next=False, **kwargs):
        super(FeedbackForm, self).__init__(*args, **kwargs)
        self.user = user
        self.is_chat_next = is_chat_next
        self.fields["created_by"].initial = user
        self.fields["modified_by"].initial = user
        self.fields["otto_version"].initial = settings.OTTO_VERSION_HASH

        if message_id is not None:
            if is_chat_next:
                self.fields["chat_message_next"].initial = message_id
                self.fields["chat_message"].initial = ""
                self.fields["app"].initial = "chat_next"
            else:
                self.fields["chat_message"].initial = message_id
                self.fields["chat_message_next"].initial = ""
                self.initialize_chat_feedback(message_id)
        else:
            self.fields["chat_message"].initial = ""
            self.fields["chat_message_next"].initial = ""
            self.fields["app"].initial = "Otto"

        self.fields["chat_message"].required = False
        self.fields["chat_message_next"].required = False

    def initialize_chat_feedback(self, message_id):
        if message_id:
            chat_mode = Message.objects.get(id=message_id).mode
            if chat_mode == "translate":
                self.fields["app"].initial = "translate"
            elif chat_mode == "summarize":
                self.fields["app"].initial = "summarize"
            elif chat_mode == "qa":
                self.fields["app"].initial = "qa"
            else:
                self.fields["app"].initial = "chat"

            self.fields["chat_message"].initial = message_id

    def clean(self):
        cleaned_data = super().clean()
        created_by = cleaned_data.get("created_by")
        modified_by = cleaned_data.get("modified_by")

        if self.user != created_by or self.user != modified_by:
            raise forms.ValidationError(
                _("The user must match the 'created_by' and 'modified_by' fields.")
            )

        return cleaned_data


class FeedbackMetadataForm(ModelForm):
    class Meta:
        model = Feedback
        fields = ["feedback_type", "status"]

        labels = {
            "feedback_type": _("Type"),
            "status": _("Status"),
        }

        widgets = {
            "feedback_type": forms.Select(
                attrs={"class": "form-select"},
                choices=Feedback.FEEDBACK_TYPE_CHOICES,
            ),
            "status": forms.Select(
                attrs={"class": "form-select"},
                choices=Feedback.FEEDBACK_STATUS_CHOICES,
            ),
        }


class FeedbackNoteForm(ModelForm):
    class Meta:
        model = Feedback
        fields = ["admin_notes"]

        labels = {
            "admin_notes": _("Add notes or additional details."),
        }

        widgets = {
            "admin_notes": forms.Textarea(
                attrs={
                    "class": "form-control fs-6",
                    "style": "height: 102px",
                    "placeholder": _("Add notes or additional details."),
                },
            ),
        }


# AC-16 & AC-16(2): Enables the modification of user roles and group memberships
class UserGroupForm(forms.Form):
    upn = PermissiveModelMultipleChoiceField(
        queryset=User.objects.none(),  # Queryset not used; autocomplete uses get_items()
        label="UPN",
        required=True,
        widget=widgets.Autocomplete(
            use_ac=UserAutocomplete,
            options={
                "component_id": "id_upn",
            },
        ),
    )
    group = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        label="Roles",
        required=False,
        widget=widgets.Autocomplete(
            name="group",
            options={"multiselect": True, "minimum_search_length": 0, "model": Group},
        ),
    )

    cost_group = forms.ModelMultipleChoiceField(
        queryset=CostGroup.objects.filter(active=True),
        label="Cost Groups",
        required=False,
        widget=user_management_cost_groups_widget,
    )
    teams_admin = forms.ModelMultipleChoiceField(
        queryset=Team.objects.all().order_by("name"),
        label=_("Teams (admin)"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=TeamNameAutocomplete,
            attrs={
                "component_id": "id_teams_admin",
                "id": "id_teams_admin__textinput",
                "name": "teams_admin",
            },
        ),
    )
    teams_member = forms.ModelMultipleChoiceField(
        queryset=Team.objects.all().order_by("name"),
        label=_("Teams (member)"),
        required=False,
        widget=widgets.Autocomplete(
            use_ac=TeamNameAutocomplete,
            attrs={
                "component_id": "id_teams_member",
                "id": "id_teams_member__textinput",
                "name": "teams_member",
            },
        ),
    )
    monthly_max = forms.IntegerField(
        label="Monthly budget ($ CAD)",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=settings.DEFAULT_MONTHLY_MAX,
    )
    monthly_bonus = forms.IntegerField(
        label="Additional budget (this month only)",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=0,
    )

    def clean(self):
        cleaned_data = super().clean()
        admin_teams = cleaned_data.get("teams_admin") or []
        member_teams = cleaned_data.get("teams_member") or []
        overlap = {team.pk for team in admin_teams} & {team.pk for team in member_teams}
        if overlap:
            raise forms.ValidationError(
                _("A team cannot be selected in both Teams (admin) and Teams (member).")
            )
        return cleaned_data


class UserMergeForm(forms.Form):
    target_user = forms.ModelChoiceField(
        queryset=User.objects.all().order_by("upn"),
        label=_("Target user"),
        required=True,
        widget=widgets.Autocomplete(
            use_ac=MergeTargetUserAutocomplete,
            options={
                "component_id": "id_target_user",
            },
        ),
    )
    source_users = PermissiveModelMultipleChoiceField(
        queryset=User.objects.none(),  # Queryset not used; autocomplete uses get_items()
        label=_("Source users"),
        required=True,
        widget=widgets.Autocomplete(
            use_ac=MergeSourceUsersAutocomplete,
            options={
                "component_id": "id_source_users",
            },
        ),
    )
    confirm_merge = forms.BooleanField(
        required=False,
        label=_(
            "I understand this operation is destructive and will deactivate the source accounts."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_user"].help_text = _(
            "Search for and select the active account that should remain."
        )
        self.fields["source_users"].help_text = _(
            "Search and add one or more duplicate accounts to merge into the target."
        )

    def clean(self):
        cleaned_data = super().clean()
        target_user = cleaned_data.get("target_user")
        source_users = cleaned_data.get("source_users")

        if target_user and source_users and target_user in source_users:
            self.add_error(
                "source_users",
                _("The target user cannot also be selected as a source user."),
            )

        return cleaned_data


class CostGroupForm(forms.ModelForm):
    # Simple form with all the fields default widgets
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by("email"),
        label=_("Authorized Users"),
        required=False,
        help_text=_(
            "Users who can select this cost group for cost tracking (in addition to admins)"
        ),
        widget=widgets.Autocomplete(
            use_ac=CostGroupUsersAutocomplete,
            attrs={
                "component_id": "id_users",
                "id": "id_users__textinput",
            },
        ),
    )

    class Meta:
        model = CostGroup
        fields = [
            "cost_group_id",
            "name",
            "name_fr",
            "lex_file_number",
            "monthly_max",
            "active",
            "users",
        ]
        # Add the bootstrap classes to the form fields and labels
        widgets = {
            "cost_group_id": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control"}),
            "lex_file_number": forms.TextInput(
                attrs={"class": "form-control", "required": False}
            ),
            "monthly_max": forms.NumberInput(attrs={"class": "form-control"}),
            "active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If the instance is not None, then we are editing an existing cost group
        # So the cost_group_id should be read-only
        if self.instance.pk:
            self.fields["cost_group_id"].widget.attrs["readonly"] = True
            self.fields["cost_group_id"].widget.attrs["disabled"] = True


class OttoStatusForm(forms.ModelForm):
    external_tool_review_flagged_azure_pii_categories = forms.MultipleChoiceField(
        choices=EXTERNAL_TOOL_REVIEW_AZURE_PII_CATEGORY_CHOICES,
        required=False,
        label=_("Azure PII categories that trigger warnings"),
        widget=forms.CheckboxSelectMultiple(),
        help_text=_(
            "Unchecked categories remain detectable in diagnostics but do not make "
            "the request count as flagged."
        ),
    )

    class Meta:
        model = OttoStatus
        fields = [
            "normal_chat_max_mb",
            "normal_librarian_max_mb",
            "bulk_uploader_chat_max_mb",
            "bulk_uploader_librarian_max_mb",
            "librarian_auto_embed_max_chunks",
            "external_tool_review_flagged_azure_pii_categories",
            "external_tool_review_flag_local_pii",
            "external_tool_review_flag_credentials_or_secrets",
            "external_tool_review_flag_large_payloads",
            "external_tool_review_flag_privileged_or_classified",
            "laws_last_refreshed",
            "exchange_rate",
            "terms_last_updated",
        ]
        labels = {
            "normal_chat_max_mb": _("Chat uploads"),
            "normal_librarian_max_mb": _("Library uploads"),
            "bulk_uploader_chat_max_mb": _("Chat uploads"),
            "bulk_uploader_librarian_max_mb": _("Library uploads"),
            "librarian_auto_embed_max_chunks": _(
                "Pause auto-embedding above this many chunks"
            ),
            "external_tool_review_flag_local_pii": _("Flag local regex PII matches"),
            "external_tool_review_flag_credentials_or_secrets": _(
                "Flag credential and secret markers"
            ),
            "external_tool_review_flag_large_payloads": _(
                "Flag unusually large outbound text"
            ),
            "external_tool_review_flag_privileged_or_classified": _(
                "Flag phrase-only privileged/classified markers"
            ),
            "laws_last_refreshed": _("Laws last refreshed"),
            "exchange_rate": _("Exchange rate (CAD/USD)"),
            "terms_last_updated": _("Terms last updated"),
        }
        widgets = {
            "normal_chat_max_mb": forms.NumberInput(attrs={"class": "form-control"}),
            "normal_librarian_max_mb": forms.NumberInput(
                attrs={"class": "form-control"}
            ),
            "bulk_uploader_chat_max_mb": forms.NumberInput(
                attrs={"class": "form-control"}
            ),
            "bulk_uploader_librarian_max_mb": forms.NumberInput(
                attrs={"class": "form-control"}
            ),
            "librarian_auto_embed_max_chunks": forms.NumberInput(
                attrs={"class": "form-control"}
            ),
            "external_tool_review_flag_local_pii": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "external_tool_review_flag_credentials_or_secrets": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "external_tool_review_flag_large_payloads": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "external_tool_review_flag_privileged_or_classified": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            # Keep datetime widgets as simple text inputs to avoid browser-specific formats
            # If needed, switch to type="datetime-local" and add matching input_formats
            "laws_last_refreshed": forms.DateTimeInput(attrs={"class": "form-control"}),
            "exchange_rate": forms.NumberInput(
                attrs={"class": "form-control", "step": "any"}
            ),
            "terms_last_updated": forms.DateTimeInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configured_categories = (
            getattr(
                self.instance,
                "external_tool_review_flagged_azure_pii_categories",
                None,
            )
            or default_external_tool_review_flagged_azure_pii_categories()
        )
        self.fields[
            "external_tool_review_flagged_azure_pii_categories"
        ].initial = configured_categories

    def clean(self):
        cleaned = super().clean()
        cleaned["external_tool_review_flagged_azure_pii_categories"] = sorted(
            {
                str(category).strip()
                for category in cleaned.get(
                    "external_tool_review_flagged_azure_pii_categories", []
                )
                if str(category).strip()
            }
        )
        return cleaned


class BaseDashboardForm(forms.Form):
    x_axis = forms.ChoiceField(
        choices=[
            ("day", _("Day")),
            ("week", _("Week")),
            ("month", _("Month")),
            ("cost_group", _("Cost group")),
            ("user", _("User")),
        ],
        label=_("X-axis"),
        required=True,
        initial="day",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "x_axis", "name": "x_axis"}
        ),
    )

    date_group = forms.ChoiceField(
        choices=[
            ("all", _("All time")),
            ("last_90_days", _("Last 90 days")),
            ("last_30_days", _("Last 30 days")),
            ("last_7_days", _("Last 7 days")),
            ("today", _("Today")),
            ("custom", _("Custom date range")),
        ],
        label=_("Range"),
        required=True,
        initial="last_30_days",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "date_group", "name": "date_group"}
        ),
    )

    bar_chart_type = forms.ChoiceField(
        choices=[
            ("grouped", _("Grouped")),
            ("stacked", _("Stacked")),
        ],
        label=_("Bar chart type:"),
        required=True,
        initial="stacked",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "bar_chart_type",
                "name": "bar_chart_type",
            }
        ),
    )


# class UsageDashboardForm(forms.Form):

#     x_axis = forms.ChoiceField(
#         choices=[
#             ("day", _("Day")),
#             ("week", _("Week")),
#             ("month", _("Month")),
#             ("project", _("Project")),
#             ("user", _("User")),
#         ],
#         label=_("X-axis"),
#         required=True,
#         initial="day",
#         widget=forms.Select(
#             attrs={"class": "form-select", "id": "x_axis", "name": "x_axis"}
#         ),
#     )

#     group = forms.ChoiceField(
#         choices=[
#             ("none", _("None")),
#             ("project", _("Project")),
#         ],
#         label=_("Group by:"),
#         required=True,
#         initial="none",
#         widget=forms.Select(
#             attrs={"class": "form-select", "id": "group", "name": "group"}
#         ),
#     )

#     date_group = forms.ChoiceField(
#         choices=[
#             ("all", _("All time")),
#             ("last_90_days", _("Last 90 days")),
#             ("last_30_days", _("Last 30 days")),
#             ("last_7_days", _("Last 7 days")),
#             ("today", _("Today")),
#             ("custom", _("Custom date range")),
#         ],
#         label=_("Range"),
#         required=True,
#         initial="last_30_days",
#         widget=forms.Select(
#             attrs={"class": "form-select", "id": "date_group", "name": "date_group"}
#         ),
#     )

#     bar_chart_type = forms.ChoiceField(
#         choices=[
#             ("grouped", _("Grouped")),
#             ("stacked", _("Stacked")),
#         ],
#         label=_("Bar chart type:"),
#         required=True,
#         initial="stacked",
#         widget=forms.Select(
#             attrs={
#                 "class": "form-select",
#                 "id": "bar_chart_type",
#                 "name": "bar_chart_type",
#             }
#         ),
#     )

#     count_type = forms.ChoiceField(
#         choices=COUNT_TYPE_CHOICES,
#         label=_("Count type:"),
#         required=True,
#         initial="chat_messages",
#         widget=forms.Select(
#             attrs={
#                 "class": "form-select",
#                 "id": "count_type",
#                 "name": "count_type",
#             }
#         ),
#     )

#     chat_type = forms.ChoiceField(
#         label=_("Chat type:"),
#         required=True,
#         initial="all",
#         widget=forms.Select(
#             attrs={
#                 "class": "form-select",
#                 "id": "count_type",
#                 "name": "count_type",
#             }
#         ),
#     )

#     project = forms.ModelChoiceField(
#         queryset=Project.objects.all(),
#         label="Project",
#         required=False,
#         widget=active_projects_ac_widget,
#     )

#     def __init__(self, *args, **kwargs):

#         chat_type_options = kwargs.pop("chat_type_options", [])
#         super().__init__(*args, **kwargs)
#         self.fields["chat_type"].choices = chat_type_options


class UsageDashboardForm(BaseDashboardForm):
    group = forms.ChoiceField(
        choices=[
            ("none", _("None")),
            ("cost_group", _("Cost group")),
        ],
        label=_("Group by:"),
        required=True,
        initial="none",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "group", "name": "group"}
        ),
    )

    count_type = forms.ChoiceField(
        choices=COUNT_TYPE_CHOICES,
        label=_("Count type:"),
        required=True,
        initial="chat_messages",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "count_type",
                "name": "count_type",
            }
        ),
    )

    chat_type = forms.ChoiceField(
        label=_("Chat type:"),
        required=True,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "chat_type",
                "name": "chat_type",
            }
        ),
    )

    cost_group = forms.ChoiceField(
        choices=[
            ("all", _("All cost groups & personal costs")),
            ("personal", _("Personal costs only")),
            ("cost_groups", _("Cost group costs only (all groups)")),
            ("specific", _("Select specific cost group(s)...")),
        ],
        label=_("Cost group"),
        required=False,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "cost_group",
                "name": "cost_group",
            }
        ),
    )

    specific_cost_groups = forms.ModelMultipleChoiceField(
        queryset=CostGroup.objects.filter(active=True),
        label=_("Specific Cost Groups"),
        required=False,
        widget=specific_cost_groups_widget,
    )

    def __init__(self, *args, **kwargs):
        chat_type_options = kwargs.pop("chat_type_options", [])
        super().__init__(*args, **kwargs)
        self.fields["chat_type"].choices = chat_type_options


class CostDashboardForm(BaseDashboardForm):
    group = forms.ChoiceField(
        choices=[
            ("none", _("None")),
            ("feature", _("Feature")),
            ("cost_group", _("Cost group")),
            ("cost_type", _("Cost type")),
        ],
        label=_("Group by:"),
        required=True,
        initial="feature",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "group", "name": "group"}
        ),
    )

    feature = forms.ChoiceField(
        label=_("Feature:"),
        required=True,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "feature",
                "name": "feature",
            }
        ),
    )

    cost_type = forms.ChoiceField(
        label=_("Cost type:"),
        required=True,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "cost_type",
                "name": "cost_type",
            }
        ),
    )

    cost_group = forms.ChoiceField(
        choices=[
            ("all", _("All cost groups & personal costs")),
            ("personal", _("Personal costs only")),
            ("cost_groups", _("Cost group costs only (all groups)")),
            ("specific", _("Select specific cost group(s)...")),
        ],
        label=_("Cost group"),
        required=False,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "cost_group",
                "name": "cost_group",
            }
        ),
    )

    specific_cost_groups = forms.ModelMultipleChoiceField(
        queryset=CostGroup.objects.filter(active=True),
        label=_("Specific Cost Groups"),
        required=False,
        widget=specific_cost_groups_widget,
    )

    def __init__(self, *args, **kwargs):
        feature_options = kwargs.pop("feature_options", [])
        cost_type_options = kwargs.pop("cost_type_options", [])
        super().__init__(*args, **kwargs)
        self.fields["feature"].choices = feature_options
        self.fields["cost_type"].choices = cost_type_options
