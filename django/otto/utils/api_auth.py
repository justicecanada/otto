from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest

from structlog import get_logger

from otto.models import ApiClient

logger = get_logger(__name__)

API_PATH_PREFIX = "/api/"


class ApiAuthenticationError(Exception):
    def __init__(self, detail: str, status_code: int = 401):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class AuthenticatedApiPrincipal:
    kind: str
    user: object | None = None
    client: ApiClient | None = None

    @property
    def display_identifier(self) -> str:
        if self.kind == "machine" and self.client is not None:
            return self.client.name
        if self.user is not None:
            return getattr(self.user, "upn", str(self.user))
        return "unknown"


def is_api_path(path: str | None) -> bool:
    return (path or "").startswith(API_PATH_PREFIX)


def get_request_ip(request: HttpRequest) -> str:
    forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def _extract_bearer_token(request: HttpRequest) -> str | None:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return None
    return auth_header[7:].strip()


def authenticate_api_client_bearer_token(request: HttpRequest) -> ApiClient:
    token = _extract_bearer_token(request)
    if not token:
        raise ApiAuthenticationError("Authentication required.", status_code=401)

    parsed = ApiClient.parse_token(token)
    if parsed is None:
        logger.warning(
            "api_auth.invalid_token_format",
            path=request.path,
            method=request.method,
        )
        raise ApiAuthenticationError("Invalid bearer token.", status_code=401)

    public_id, secret = parsed
    client = (
        ApiClient.objects.prefetch_related("allowed_ip_ranges", "scope_assignments")
        .filter(public_id=public_id, is_active=True)
        .first()
    )
    if client is None or not client.check_secret(secret):
        logger.warning(
            "api_auth.invalid_token",
            path=request.path,
            method=request.method,
            public_id=str(public_id),
        )
        raise ApiAuthenticationError("Invalid bearer token.", status_code=401)

    request_ip = get_request_ip(request)
    if not client.is_ip_allowed(request_ip):
        logger.warning(
            "api_auth.ip_not_allowed",
            client_id=client.id,
            client_name=client.name,
            request_ip=request_ip,
            path=request.path,
            method=request.method,
        )
        raise ApiAuthenticationError(
            "Bearer token not allowed from this IP address.", status_code=403
        )

    client.mark_used(request_ip)
    return client


class ApiTokenAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        request.api_client = None
        request.api_principal = None
        request.api_authentication_error = None

        if is_api_path(request.path):
            token = _extract_bearer_token(request)
            if token is not None:
                request._dont_enforce_csrf_checks = True
                try:
                    request.api_client = authenticate_api_client_bearer_token(request)
                except ApiAuthenticationError as exc:
                    request.api_authentication_error = exc

        return self.get_response(request)


def authenticate_api_request(request: HttpRequest) -> AuthenticatedApiPrincipal:
    cached_principal = getattr(request, "api_principal", None)
    if isinstance(cached_principal, AuthenticatedApiPrincipal):
        return cached_principal

    auth_error = getattr(request, "api_authentication_error", None)
    if auth_error is not None:
        raise auth_error

    api_client = getattr(request, "api_client", None)
    if api_client is not None:
        principal = AuthenticatedApiPrincipal(kind="machine", client=api_client)
        request.api_principal = principal
        return principal

    if request.user.is_authenticated:
        principal = AuthenticatedApiPrincipal(kind="user", user=request.user)
        request.api_principal = principal
        return principal

    raise ApiAuthenticationError("Authentication required.", status_code=401)
