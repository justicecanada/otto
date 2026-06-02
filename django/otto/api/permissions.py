from __future__ import annotations

import json

from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from otto.utils.api_permissions import api_permission_error


class OttoApiPermission(BasePermission):
    def has_permission(self, request, view) -> bool:
        permission_error = api_permission_error(
            request._request,
            human_permission=getattr(view, "human_permission", None),
            machine_scopes=getattr(view, "machine_scopes", None),
            allow_human=getattr(view, "allow_human", True),
            allow_machine=getattr(view, "allow_machine", True),
            require_terms=getattr(view, "require_terms", True),
        )
        if permission_error is not None:
            self._raise_permission_error(permission_error)

        request.api_principal = getattr(request._request, "api_principal", None)
        return True

    @staticmethod
    def _raise_permission_error(permission_error):
        detail = "Forbidden."
        try:
            payload = json.loads(permission_error.content.decode("utf-8"))
            detail = payload.get("detail", detail)
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        if permission_error.status_code == 401:
            raise exceptions.NotAuthenticated(detail)
        raise exceptions.PermissionDenied(detail)


class OttoApiDocsPermission(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and hasattr(user, "has_perm")
            and user.has_perm("otto.manage_users")
            and getattr(user, "accepted_terms", False)
        )
