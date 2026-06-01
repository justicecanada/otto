"""
Custom form fields used across Otto applications.
"""

from django import forms


class PermissiveModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    """
    A ModelMultipleChoiceField that silently filters out invalid choices instead of raising validation errors.
    This prevents form submission failures when data sources or documents have been deleted.
    The initial queryset can be limited (e.g., only currently selected items) for performance,
    but this field will validate against the full set of submitted IDs.
    """

    def _check_values(self, value):
        """
        Override to return only valid objects, silently skipping invalid IDs.
        Expands queryset to check all submitted IDs, not just those in the initial queryset.
        """
        key = self.to_field_name or "pk"
        # Use the model from queryset to query all submitted IDs
        model = self.queryset.model
        qs = model.objects.filter(**{f"{key}__in": value})
        return list(qs)


class UserOrTeamMultipleChoiceField(forms.Field):
    """
    A form field that accepts a mix of user IDs and team IDs (prefixed with 'team:').
    Cleans to a dict: {'users': [User, ...], 'teams': [Team, ...]}.
    """

    def clean(self, value):
        from django.contrib.auth import get_user_model

        from otto.models import Team

        User = get_user_model()

        if not value:
            return {"users": [], "teams": []}

        # Handle QuerySets by converting to list
        from django.db.models import QuerySet

        if isinstance(value, QuerySet):
            value = list(value)
        if isinstance(value, str):
            value = [value]

        user_ids = []
        user_objects = []
        team_ids = []
        team_objects = []
        for v in value:
            if isinstance(v, User):
                user_objects.append(v)
            elif isinstance(v, Team):
                team_objects.append(v)
            else:
                v_str = str(v)
                if v_str.startswith("team:"):
                    team_ids.append(v_str[5:])
                else:
                    user_ids.append(v)

        users = list(User.objects.filter(pk__in=user_ids)) if user_ids else []
        users.extend(user_objects)
        teams = list(Team.objects.filter(pk__in=team_ids)) if team_ids else []
        teams.extend(team_objects)
        return {"users": users, "teams": teams}
