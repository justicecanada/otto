from django.urls import reverse

import pytest

from otto.models import ApiClient, ApiClientAuditEvent
from otto.utils.api_permissions import API_SCOPE_REPORTING_USER_ACTIVITY_READ


@pytest.mark.django_db
def test_manage_api_clients_page_renders_registered_clients(client, all_apps_user):
    admin = all_apps_user()
    owner = all_apps_user("api_client_owner")
    api_client = ApiClient.objects.create(
        name="JusTipedia nightly sync",
        description="Nightly machine client",
        owner=owner,
        created_by=admin,
    )
    api_client.issue_token()
    api_client.allowed_ip_ranges.create(cidr="127.0.0.1/32")
    api_client.scope_assignments.create(scope=API_SCOPE_REPORTING_USER_ACTIVITY_READ)
    ApiClientAuditEvent.objects.create(
        client=api_client,
        actor=admin,
        event_type=ApiClientAuditEvent.EventType.CREATED,
        metadata={
            "scopes": [API_SCOPE_REPORTING_USER_ACTIVITY_READ],
            "allowed_ips": ["127.0.0.1/32"],
        },
    )

    client.force_login(admin)
    response = client.get(reverse("manage_api_clients"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "API clients" in content
    assert "JusTipedia nightly sync" in content
    assert owner.upn in content
    assert "127.0.0.1/32" in content
    assert "Read user activity reporting API" in content
    assert "Clients" in content
    assert "Audit trail" in content
    assert "Showing all" not in content


@pytest.mark.django_db
def test_manage_api_clients_audit_tab_shows_paginated_full_history(
    client, all_apps_user
):
    admin = all_apps_user()
    api_client = ApiClient.objects.create(
        name="Audit-heavy client",
        created_by=admin,
    )

    for index in range(55):
        label = f"{index:02d}"
        ApiClientAuditEvent.objects.create(
            client=api_client,
            actor=admin,
            event_type=ApiClientAuditEvent.EventType.UPDATED,
            metadata={
                "changes": {
                    "description": {
                        "old": f"old-{label}",
                        "new": f"new-{label}",
                    }
                }
            },
        )

    client.force_login(admin)

    first_page_response = client.get(reverse("manage_api_clients"), {"tab": "audit"})

    assert first_page_response.status_code == 200
    first_page_content = first_page_response.content.decode()
    assert "Showing all 55 recorded audit events. Newest first." in first_page_content
    assert "Description changed from old-54 to new-54." in first_page_content
    assert "Description changed from old-05 to new-05." in first_page_content
    assert "Description changed from old-04 to new-04." not in first_page_content
    assert first_page_response.context["active_tab"] == "audit"
    assert first_page_response.context["audit_page"].number == 1
    assert first_page_response.context["audit_page"].paginator.num_pages == 2

    second_page_response = client.get(
        reverse("manage_api_clients"), {"tab": "audit", "page": 2}
    )

    assert second_page_response.status_code == 200
    second_page_content = second_page_response.content.decode()
    assert second_page_response.context["audit_page"].number == 2
    assert "Description changed from old-04 to new-04." in second_page_content
    assert "Description changed from old-00 to new-00." in second_page_content


@pytest.mark.django_db
def test_manage_api_client_form_creates_client_and_initial_audit_event(
    client, all_apps_user
):
    admin = all_apps_user()
    client.force_login(admin)

    response = client.post(
        reverse("manage_api_client_form"),
        data={
            "name": "Reporting integration",
            "owner": str(admin.id),
            "description": "Pulls reporting snapshots",
            "scopes": [API_SCOPE_REPORTING_USER_ACTIVITY_READ],
            "allowed_ips": "127.0.0.1/32\n10.0.0.0/24",
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "API client created" in content
    assert "otto_api_" in content

    api_client = ApiClient.objects.get(name="Reporting integration")
    assert api_client.owner == admin
    assert api_client.scope_assignments.filter(
        scope=API_SCOPE_REPORTING_USER_ACTIVITY_READ
    ).exists()
    assert set(api_client.allowed_ip_ranges.values_list("cidr", flat=True)) == {
        "127.0.0.1/32",
        "10.0.0.0/24",
    }

    audit_event = api_client.audit_events.first()
    assert audit_event.event_type == ApiClientAuditEvent.EventType.CREATED
    assert audit_event.metadata["scopes"] == [API_SCOPE_REPORTING_USER_ACTIVITY_READ]


@pytest.mark.django_db
def test_manage_api_client_form_updates_client_and_records_audit_event(
    client, all_apps_user
):
    admin = all_apps_user()
    owner = all_apps_user("api_client_update_owner")
    api_client = ApiClient.objects.create(
        name="Reporting integration",
        owner=owner,
        created_by=admin,
        description="Original description",
    )
    api_client.issue_token()
    api_client.scope_assignments.create(scope=API_SCOPE_REPORTING_USER_ACTIVITY_READ)
    api_client.allowed_ip_ranges.create(cidr="127.0.0.1/32")

    client.force_login(admin)
    response = client.post(
        reverse("manage_api_client_form_edit", args=[api_client.id]),
        data={
            "name": "Reporting integration updated",
            "owner": "",
            "description": "Updated description",
            "scopes": [],
            "allowed_ips": "10.10.0.0/16",
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == reverse("manage_api_clients")

    api_client.refresh_from_db()
    assert api_client.name == "Reporting integration updated"
    assert api_client.owner is None
    assert api_client.description == "Updated description"
    assert not api_client.is_active
    assert api_client.scope_assignments.count() == 0
    assert list(api_client.allowed_ip_ranges.values_list("cidr", flat=True)) == [
        "10.10.0.0/16"
    ]

    audit_event = api_client.audit_events.first()
    assert audit_event.event_type == ApiClientAuditEvent.EventType.UPDATED
    assert audit_event.metadata["changes"]["scopes"]["removed"] == [
        API_SCOPE_REPORTING_USER_ACTIVITY_READ
    ]
    assert audit_event.metadata["changes"]["allowed_ips"]["added"] == ["10.10.0.0/16"]


@pytest.mark.django_db
def test_rotate_api_client_secret_returns_new_token_and_audits(client, all_apps_user):
    admin = all_apps_user()
    api_client = ApiClient.objects.create(name="Rotating client", created_by=admin)
    api_client.issue_token()
    previous_secret_rotated_at = api_client.secret_last_rotated_at

    client.force_login(admin)
    response = client.post(reverse("rotate_api_client_secret", args=[api_client.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "API client secret rotated" in content
    assert "otto_api_" in content

    api_client.refresh_from_db()
    assert api_client.secret_last_rotated_at >= previous_secret_rotated_at
    audit_event = api_client.audit_events.first()
    assert audit_event.event_type == ApiClientAuditEvent.EventType.SECRET_ROTATED
