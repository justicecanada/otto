from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from structlog import get_logger

from otto.forms import ApiClientForm
from otto.models import ApiClient, ApiClientAuditEvent
from otto.utils.api_permissions import get_api_scope_definition
from otto.utils.decorators import permission_required

logger = get_logger(__name__)


AUDIT_EVENTS_PER_PAGE = 50


def _scope_label(scope: str) -> str:
    definition = get_api_scope_definition(scope)
    return str(definition.label) if definition else scope


def _event_details(event: ApiClientAuditEvent) -> list[str]:
    metadata = event.metadata or {}
    details: list[str] = []

    if event.event_type == ApiClientAuditEvent.EventType.CREATED:
        scopes = metadata.get("scopes") or []
        allowed_ips = metadata.get("allowed_ips") or []
        details.append(_("Client registered and initial bearer token issued."))
        if scopes:
            details.append(
                _("Scopes: %(scopes)s")
                % {"scopes": ", ".join(_scope_label(scope) for scope in scopes)}
            )
        if allowed_ips:
            details.append(_("Allowed IPs: %(ips)s") % {"ips": ", ".join(allowed_ips)})
        return details

    if event.event_type == ApiClientAuditEvent.EventType.SECRET_ROTATED:
        details.append(_("Bearer token rotated."))
        return details

    changes = metadata.get("changes") or {}
    if not changes:
        return [_("Client configuration updated.")]

    for field_name, change in changes.items():
        if field_name == "scopes":
            added = change.get("added") or []
            removed = change.get("removed") or []
            if added:
                details.append(
                    _("Scopes added: %(scopes)s")
                    % {"scopes": ", ".join(_scope_label(scope) for scope in added)}
                )
            if removed:
                details.append(
                    _("Scopes removed: %(scopes)s")
                    % {"scopes": ", ".join(_scope_label(scope) for scope in removed)}
                )
            continue
        if field_name == "allowed_ips":
            added = change.get("added") or []
            removed = change.get("removed") or []
            if added:
                details.append(
                    _("Allowed IPs added: %(ips)s") % {"ips": ", ".join(added)}
                )
            if removed:
                details.append(
                    _("Allowed IPs removed: %(ips)s") % {"ips": ", ".join(removed)}
                )
            continue
        if field_name == "owner":
            details.append(
                _("Owner changed from %(old)s to %(new)s.")
                % {
                    "old": change.get("old") or _("None"),
                    "new": change.get("new") or _("None"),
                }
            )
            continue
        if field_name == "is_active":
            details.append(
                _("Active status changed from %(old)s to %(new)s.")
                % {
                    "old": _("active") if change.get("old") else _("inactive"),
                    "new": _("active") if change.get("new") else _("inactive"),
                }
            )
            continue
        details.append(
            _("%(field)s changed from %(old)s to %(new)s.")
            % {
                "field": field_name.replace("_", " ").title(),
                "old": change.get("old") or _("None"),
                "new": change.get("new") or _("None"),
            }
        )

    return details or [_("Client configuration updated.")]


def _build_manage_api_clients_context(
    request: HttpRequest | None = None, *, revealed_secret=None
):
    active_tab = (request.GET.get("tab") if request else None) or "clients"
    if active_tab not in {"clients", "audit"}:
        active_tab = "clients"

    clients = (
        ApiClient.objects.select_related("owner", "created_by")
        .prefetch_related("scope_assignments", "allowed_ip_ranges")
        .order_by("name")
    )
    client_rows = []
    for client in clients:
        client_rows.append(
            {
                "client": client,
                "scope_labels": [
                    _scope_label(scope.scope)
                    for scope in client.scope_assignments.all().order_by("scope")
                ],
                "allowed_ips": list(
                    client.allowed_ip_ranges.values_list("cidr", flat=True)
                ),
            }
        )

    audit_events = []
    for event in ApiClientAuditEvent.objects.select_related("client", "actor").order_by(
        "-created_at", "-id"
    ):
        audit_events.append(
            {
                "event": event,
                "details": _event_details(event),
            }
        )

    audit_paginator = Paginator(audit_events, AUDIT_EVENTS_PER_PAGE)
    audit_page = audit_paginator.get_page(request.GET.get("page") if request else None)

    return {
        "active_tab": active_tab,
        "client_rows": client_rows,
        "audit_page": audit_page,
        "audit_event_count": audit_paginator.count,
        "revealed_secret": revealed_secret,
    }


@permission_required("otto.manage_users")
def manage_api_clients(request: HttpRequest):
    return render(
        request,
        "manage_api_clients.html",
        _build_manage_api_clients_context(request),
    )


@permission_required("otto.manage_users")
def manage_api_client_form(request: HttpRequest, client_id: int | None = None):
    client = get_object_or_404(ApiClient, pk=client_id) if client_id else None

    if request.method == "POST":
        form = ApiClientForm(request.POST, client=client)
        if form.is_valid():
            saved_client, plaintext_token, created = form.save(actor=request.user)
            if created:
                logger.info(
                    "api_client_created",
                    client_id=saved_client.id,
                    client_name=saved_client.name,
                    actor_user_id=request.user.id,
                )
                messages.success(
                    request,
                    _(
                        "API client created. Copy the bearer token now — it will not be shown again."
                    ),
                )
                secret_context = {
                    "client": saved_client,
                    "plaintext_token": plaintext_token,
                    "title": _("API client created"),
                    "description": _(
                        "Copy this bearer token now and store it securely. Otto only shows the full secret once."
                    ),
                }
                if request.headers.get("HX-Request"):
                    return render(
                        request,
                        "components/api_client_secret_modal.html",
                        secret_context,
                    )
                return render(
                    request,
                    "manage_api_clients.html",
                    _build_manage_api_clients_context(
                        request,
                        revealed_secret=secret_context,
                    ),
                )

            messages.success(request, _("API client updated."))
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("manage_api_clients")
                return response
            return redirect("manage_api_clients")
    else:
        form = ApiClientForm(client=client)

    return render(
        request,
        "components/api_client_form_modal.html",
        {
            "form": form,
            "client": client,
            "scope_details": form.scope_details(),
        },
    )


@permission_required("otto.manage_users")
@require_POST
def rotate_api_client_secret(request: HttpRequest, client_id: int):
    client = get_object_or_404(ApiClient, pk=client_id)
    previous_rotation = client.secret_last_rotated_at
    plaintext_token = client.issue_token()
    ApiClientAuditEvent.objects.create(
        client=client,
        actor=request.user,
        event_type=ApiClientAuditEvent.EventType.SECRET_ROTATED,
        metadata={
            "previous_secret_last_rotated_at": previous_rotation.isoformat()
            if previous_rotation
            else None,
        },
    )
    logger.info(
        "api_client_secret_rotated",
        client_id=client.id,
        client_name=client.name,
        actor_user_id=request.user.id,
    )
    messages.success(request, _("API client secret rotated."))

    secret_context = {
        "client": client,
        "plaintext_token": plaintext_token,
        "title": _("API client secret rotated"),
        "description": _(
            "Copy the new bearer token now. The previous token is no longer valid."
        ),
    }
    if request.headers.get("HX-Request"):
        return render(
            request,
            "components/api_client_secret_modal.html",
            secret_context,
        )
    return render(
        request,
        "manage_api_clients.html",
        _build_manage_api_clients_context(request, revealed_secret=secret_context),
    )
