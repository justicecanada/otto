import csv
import datetime
import io
import json

from django.urls import reverse

import pytest

from otto.models import ApiClient, Cost, CostType
from otto.utils.api_permissions import API_SCOPE_REPORTING_USER_ACTIVITY_READ


@pytest.fixture
def cost_type_factory():
    def _factory(short_name, name=None):
        cost_type, _created = CostType.objects.get_or_create(
            short_name=short_name,
            defaults={
                "name": name or short_name,
                "description": f"Description for {short_name}",
                "unit_name": "unit",
                "unit_cost": 1,
                "unit_quantity": 1,
            },
        )
        return cost_type

    return _factory


@pytest.mark.django_db
def test_user_activity_summary_endpoint_returns_json(
    client, all_apps_user, cost_type_factory
):
    admin = all_apps_user("api_admin")
    client.force_login(admin)

    chat_input = cost_type_factory("chat-input", name="GPT input tokens")
    cost = Cost.objects.create(
        cost_type=chat_input,
        count=42,
        usd_cost=1,
        feature="chat",
        user=admin,
    )
    cost.date_incurred = datetime.date(2025, 1, 10)
    cost.save(update_fields=["date_incurred"])

    response = client.get(
        reverse("api_user_activity_summary"),
        {
            "activity_type": "input_tokens",
            "interval": "month",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["activity_type"] == "input_tokens"
    assert payload["total_users"] == 1
    assert payload["total_activity"] == 42
    assert payload["buckets"] == [
        {"label": "2025-01", "distinct_users": 1, "activity_total": 42}
    ]


@pytest.mark.django_db
def test_user_activity_users_endpoint_supports_csv(
    client, all_apps_user, cost_type_factory
):
    admin = all_apps_user("csv_admin")
    client.force_login(admin)

    chat_output = cost_type_factory("chat-output", name="GPT output tokens")
    cost = Cost.objects.create(
        cost_type=chat_output,
        count=10,
        usd_cost=1,
        feature="chat",
        user=admin,
    )
    cost.date_incurred = datetime.date(2025, 3, 1)
    cost.save(update_fields=["date_incurred"])

    response = client.get(
        reverse("api_user_activity_users"),
        {
            "activity_type": "any_usage",
            "format": "csv",
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
        },
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["upn"] == admin.upn
    assert rows[0]["chat_messages"] == "1"


@pytest.mark.django_db
def test_user_activity_users_endpoint_requires_manage_users_permission(
    client, basic_user, cost_type_factory
):
    user = basic_user("non_admin", accept_terms=True)
    client.force_login(user)

    response = client.get(reverse("api_user_activity_users"))

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden."}


@pytest.mark.django_db
def test_user_activity_users_endpoint_returns_json_rows(
    client, all_apps_user, cost_type_factory
):
    admin = all_apps_user("json_admin")
    client.force_login(admin)

    text_extractor_type = cost_type_factory("text-extractor", name="Text extractor")
    cost = Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
        user=admin,
        request_id="request-123",
    )
    cost.date_incurred = datetime.date(2025, 4, 8)
    cost.save(update_fields=["date_incurred"])

    response = client.get(
        reverse("api_user_activity_users"),
        {
            "activity_type": "text_extractor_requests",
            "format": "json",
            "start_date": "2025-04-01",
            "end_date": "2025-04-30",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["next"] is None
    assert payload["previous"] is None
    assert payload["results"][0]["text_extractor_requests"] == 1
    assert payload["results"][0]["selected_activity_total"] == 1


@pytest.mark.django_db
def test_user_activity_summary_endpoint_allows_machine_client_with_scope(
    client, all_apps_user, cost_type_factory
):
    admin = all_apps_user("machine_api_admin")

    chat_input = cost_type_factory("chat-input", name="GPT input tokens")
    cost = Cost.objects.create(
        cost_type=chat_input,
        count=42,
        usd_cost=1,
        feature="chat",
        user=admin,
    )
    cost.date_incurred = datetime.date(2025, 1, 10)
    cost.save(update_fields=["date_incurred"])

    api_client = ApiClient.objects.create(
        name="Reporting machine client",
        created_by=admin,
    )
    token = api_client.issue_token()
    api_client.scope_assignments.create(scope=API_SCOPE_REPORTING_USER_ACTIVITY_READ)

    response = client.get(
        reverse("api_user_activity_summary"),
        {
            "activity_type": "input_tokens",
            "interval": "month",
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["activity_type"] == "input_tokens"
    assert payload["total_users"] == 1
    assert payload["total_activity"] == 42


@pytest.mark.django_db
def test_user_activity_users_endpoint_forbids_machine_client_without_scope(
    client, all_apps_user
):
    admin = all_apps_user("machine_scope_admin")
    api_client = ApiClient.objects.create(
        name="Reporting machine client without scope",
        created_by=admin,
    )
    token = api_client.issue_token()

    response = client.get(
        reverse("api_user_activity_users"),
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden."}


@pytest.mark.django_db
def test_user_activity_users_endpoint_paginates_json_results(
    client, all_apps_user, cost_type_factory
):
    admin = all_apps_user("pagination_admin")
    second_user = all_apps_user("pagination_second_user")
    client.force_login(admin)

    chat_input = cost_type_factory("chat-input", name="GPT input tokens")

    first_cost = Cost.objects.create(
        cost_type=chat_input,
        count=1,
        usd_cost=1,
        feature="chat",
        user=admin,
    )
    first_cost.date_incurred = datetime.date(2025, 2, 10)
    first_cost.save(update_fields=["date_incurred"])

    second_cost = Cost.objects.create(
        cost_type=chat_input,
        count=1,
        usd_cost=1,
        feature="chat",
        user=second_user,
    )
    second_cost.date_incurred = datetime.date(2025, 2, 11)
    second_cost.save(update_fields=["date_incurred"])

    response = client.get(
        reverse("api_user_activity_users"),
        {
            "activity_type": "any_usage",
            "format": "json",
            "start_date": "2025-02-01",
            "end_date": "2025-02-28",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["next"] is not None
    assert payload["previous"] is None
    assert len(payload["results"]) == 1


@pytest.mark.django_db
def test_api_docs_endpoint_renders_swagger_ui(client, all_apps_user):
    admin = all_apps_user("docs_admin")
    client.force_login(admin)

    response = client.get(reverse("api_docs"))

    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_api_schema_endpoint_lists_reporting_paths(client, all_apps_user):
    admin = all_apps_user("schema_admin")
    client.force_login(admin)

    response = client.get(reverse("api_schema"))

    assert response.status_code == 200
    payload = json.loads(response.content.decode("utf-8"))
    assert "/api/v1/reporting/user-activity/summary/" in payload["paths"]
    assert "/api/v1/reporting/user-activity/users/" in payload["paths"]


@pytest.mark.django_db
def test_api_docs_endpoint_requires_manage_users_permission(client, basic_user):
    user = basic_user("docs_non_admin", accept_terms=True)
    client.force_login(user)

    response = client.get(reverse("api_docs"))

    assert response.status_code == 403
