from __future__ import annotations

from dataclasses import dataclass

from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, SessionAuthentication

from otto.models import ApiClient
from otto.utils.api_auth import ApiAuthenticationError, authenticate_api_request


@dataclass(frozen=True)
class ApiClientUser:
    client: ApiClient

    @property
    def id(self):
        return None

    @property
    def is_active(self) -> bool:
        return self.client.is_active

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def username(self) -> str:
        return self.client.name

    @property
    def upn(self) -> str:
        return self.client.name

    def __str__(self) -> str:
        return self.client.name


def _raise_for_auth_error(exc: ApiAuthenticationError):
    if exc.status_code == 403:
        raise exceptions.PermissionDenied(exc.detail)
    raise exceptions.AuthenticationFailed(exc.detail)


class OttoMachineTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = (request.headers.get("Authorization") or "").strip()
        if not auth_header.lower().startswith("bearer "):
            return None

        try:
            principal = authenticate_api_request(request._request)
        except ApiAuthenticationError as exc:
            _raise_for_auth_error(exc)

        if principal.kind != "machine" or principal.client is None:
            return None

        request.api_principal = principal
        return ApiClientUser(principal.client), principal


class OttoSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, _auth = result

        try:
            principal = authenticate_api_request(request._request)
        except ApiAuthenticationError as exc:
            _raise_for_auth_error(exc)

        request.api_principal = principal
        return user, principal
