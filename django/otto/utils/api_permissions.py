from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.http import HttpRequest, JsonResponse
from django.utils.translation import gettext_lazy as _

from otto.utils.api_auth import ApiAuthenticationError, authenticate_api_request

API_SCOPE_REPORTING_USER_ACTIVITY_READ = "reporting.user_activity.read"


@dataclass(frozen=True)
class ApiScopeDefinition:
    scope: str
    label: str
    description: str


API_SCOPE_DEFINITIONS = (
    ApiScopeDefinition(
        scope=API_SCOPE_REPORTING_USER_ACTIVITY_READ,
        label=_("Read user activity reporting API"),
        description=_(
            "Allows access to user activity summary and per-user reporting endpoints."
        ),
    ),
)

_API_SCOPE_LOOKUP = {
    definition.scope: definition for definition in API_SCOPE_DEFINITIONS
}


def get_api_scope_choices():
    return [
        (definition.scope, definition.label) for definition in API_SCOPE_DEFINITIONS
    ]


def get_api_scope_definition(scope: str) -> ApiScopeDefinition | None:
    return _API_SCOPE_LOOKUP.get(scope)


def get_api_scope_description(scope: str) -> str:
    definition = get_api_scope_definition(scope)
    return str(definition.description) if definition else scope


def api_permission_error(
    request: HttpRequest,
    *,
    human_permission: str | None = None,
    machine_scopes: Iterable[str] | None = None,
    allow_human: bool = True,
    allow_machine: bool = True,
    require_terms: bool = True,
) -> JsonResponse | None:
    try:
        principal = authenticate_api_request(request)
    except ApiAuthenticationError as exc:
        return JsonResponse({"detail": exc.detail}, status=exc.status_code)

    machine_scopes = tuple(machine_scopes or ())

    if principal.kind == "user":
        user = principal.user
        if not allow_human:
            return JsonResponse(
                {"detail": "Bearer token authentication required."}, status=403
            )
        if require_terms and not user.accepted_terms:
            return JsonResponse(
                {"detail": "Terms of use must be accepted before using the API."},
                status=403,
            )
        if human_permission and not user.has_perm(human_permission):
            return JsonResponse({"detail": "Forbidden."}, status=403)
    else:
        if not allow_machine:
            return JsonResponse(
                {"detail": "Session authentication required."}, status=403
            )
        if machine_scopes and not all(
            principal.client.has_scope(scope) for scope in machine_scopes
        ):
            return JsonResponse({"detail": "Forbidden."}, status=403)

    request.api_principal = principal
    return None
