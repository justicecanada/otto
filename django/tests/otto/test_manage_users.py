import csv
import datetime
import io
import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

import numpy as np
import pytest
from chat_next.models import (
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS,
    Chat,
    ExternalToolApprovalLog,
    Message,
)
from structlog.contextvars import bind_contextvars

from otto.models import Cost, CostGroup, Group, Notification, Team, TeamMembership, User


@pytest.mark.django_db
def test_access_manage_users(client, basic_user, all_apps_user):
    user = basic_user(accept_terms=True)
    client.force_login(user)
    response = client.get(reverse("manage_users"))
    assert response.status_code == 302
    # Should be redirected back to index page since this isn't allowed
    assert response.url == reverse("index")
    # Notification should have been created
    notification = Notification.objects.get(user=user)
    assert reverse("manage_users") in notification.text

    # Now test with a user that has the correct permissions (all_apps_user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_users"))
    assert response.status_code == 200
    content = response.content.decode()
    assert ">Teams<" in content
    assert reverse("manage_api_clients") in content
    assert reverse("manage_external_tool_approvals") in content
    assert 'id="download-users-link"' in content
    assert 'id="download-users-spinner"' in content
    assert 'id="manage-users-columns-button"' in content
    assert 'id="toggle-show-all-columns"' in content
    assert "Show all" in content
    assert "Visible columns" in content
    assert "Costs (30 days)" in content
    assert "Job title" in content
    assert "Preferred language" in content
    assert 'id="bulk-upload-form"' in content
    assert 'id="bulk-upload-submit-spinner"' in content


@pytest.mark.django_db
def test_manage_external_tool_approvals_page_lists_logged_events(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    chat = Chat.objects.create(user=admin_user, title="External approval log chat")
    message = Message.objects.create(chat=chat, text="", is_bot=True)
    ExternalToolApprovalLog.objects.create(
        user=admin_user,
        message=message,
        tool_call_id="call_termium_1",
        approval_request_id="call_termium_1",
        tool_name="search_canadian_case_law",
        tool_label="Search Canadian case law",
        external_service_name="A2AJ",
        query='{"query": "cabinet confidence", "index": "ent"}',
        tool_arguments={"query": "cabinet confidence", "index": "ent"},
        decision="approved",
        approval_source="manual",
        displayed_at=timezone.now() - timedelta(seconds=4),
        decided_at=timezone.now(),
        pii_flagged=True,
        pii_flag_source=EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API,
        pii_entity_categories=["Address", "Person"],
    )

    response = client.get(reverse("manage_external_tool_approvals"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "External tool approval logs" in content
    assert admin_user.upn in content
    assert "A2AJ" in content
    assert "Manually approved" in content
    assert "Flagged" in content
    assert "Address, Person" in content
    assert "cabinet confidence" in content
    assert "Expand all queries" in content
    assert "Download as CSV" in content
    assert "Review sensitivity" in content
    assert "search_canadian_case_law" in content
    assert "Search Canadian case law" not in content
    assert "Back to Manage users" not in content
    assert (
        "container-fluid py-3 px-2 px-md-3 external-tool-approval-logs-container"
        in content
    )
    assert "max-width: 2048px;" in content


@pytest.mark.django_db
def test_manage_external_tool_approvals_supports_filter_sort_and_csv(
    client, basic_user, all_apps_user
):
    admin_user = all_apps_user()
    admin_user.upn = "aaa-admin@justice.gc.ca"
    admin_user.save(update_fields=["upn"])

    other_user = basic_user(username="external_approval_user", accept_terms=True)
    other_user.upn = "zzz-user@justice.gc.ca"
    other_user.save(update_fields=["upn"])

    client.force_login(admin_user)

    admin_chat = Chat.objects.create(user=admin_user, title="Admin external approval")
    admin_message = Message.objects.create(chat=admin_chat, text="", is_bot=True)
    other_chat = Chat.objects.create(user=other_user, title="Other external approval")
    other_message = Message.objects.create(chat=other_chat, text="", is_bot=True)

    ExternalToolApprovalLog.objects.create(
        user=admin_user,
        message=admin_message,
        tool_call_id="call_admin_approval",
        approval_request_id="call_admin_approval",
        tool_name="termium_lookup",
        tool_label="Termium lookup",
        external_service_name="TERMIUM Plus®",
        query='{"query": "cabinet confidence"}',
        tool_arguments={"query": "cabinet confidence"},
        decision="approved",
        approval_source="manual",
        displayed_at=timezone.now() - timedelta(seconds=8),
        decided_at=timezone.now() - timedelta(seconds=3),
        pii_flagged=True,
        pii_flag_source=EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS,
        pii_entity_categories=["Address", "Person"],
    )
    ExternalToolApprovalLog.objects.create(
        user=other_user,
        message=other_message,
        tool_call_id="call_other_approval",
        approval_request_id="call_other_approval",
        tool_name="fetch_canadian_case_by_citation",
        tool_label="Fetch Canadian case by citation",
        external_service_name="A2AJ",
        query='{"citation": "2020 SCC 5"}',
        tool_arguments={"citation": "2020 SCC 5"},
        decision="denied",
        approval_source="manual",
        displayed_at=timezone.now() - timedelta(seconds=6),
        decided_at=timezone.now() - timedelta(seconds=2),
        pii_flagged=False,
    )

    response = client.get(
        reverse("manage_external_tool_approvals"),
        data={
            "sort": "user_asc",
            "page_size": "100",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert content.index(admin_user.upn) < content.index(other_user.upn)
    assert 'option value="400"' in content
    assert 'option value="all"' in content

    expanded_response = client.get(
        reverse("manage_external_tool_approvals"),
        data={
            "expand_queries": "all",
            "sort": "user_asc",
            "page_size": "100",
        },
    )

    expanded_content = expanded_response.content.decode()
    assert "Collapse all queries" in expanded_content
    assert "<details open>" in expanded_content

    filtered_response = client.get(
        reverse("manage_external_tool_approvals"),
        data={
            "pii_flagged": "yes",
            "sort": "user_asc",
            "page_size": "100",
        },
    )

    filtered_content = filtered_response.content.decode()
    assert admin_user.upn in filtered_content
    assert other_user.upn not in filtered_content
    assert "Address, Person" in filtered_content

    category_search_response = client.get(
        reverse("manage_external_tool_approvals"),
        data={
            "q": "Address",
            "sort": "user_asc",
            "page_size": "100",
        },
    )

    category_search_content = category_search_response.content.decode()
    assert admin_user.upn in category_search_content
    assert other_user.upn not in category_search_content
    assert "Address, Person" in category_search_content

    csv_response = client.get(
        reverse("manage_external_tool_approvals"),
        data={
            "download": "csv",
            "sort": "user_asc",
            "page_size": "100",
        },
    )

    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    csv_content = csv_response.content.decode()
    assert "pii_flag_source" in csv_content
    assert "pii_entity_categories" in csv_content
    assert "Local checks" in csv_content
    assert "Address, Person" in csv_content
    assert "termium_lookup" in csv_content


@pytest.mark.django_db
def test_modify_user(client, basic_user, all_apps_user):
    user = basic_user(username="basic_user", accept_terms=True)
    admin_user = all_apps_user()
    client.force_login(admin_user)
    project_team = Team.objects.create(name="Project / Projet", created_by=admin_user)
    TeamMembership.objects.create(team=project_team, user=admin_user, role="admin")

    group_ids = list(Group.objects.values_list("id", flat=True))

    # Modify the basic_user
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group_ids[0], group_ids[1]],
            "teams_admin": [],
            "teams_member": [project_team.id],
            "monthly_max": 10,
            "monthly_bonus": 0,
        },
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.groups.count() == 2
    assert TeamMembership.objects.filter(
        team=project_team, user=user, role="member"
    ).exists()

    # Modify multiple users
    user2 = basic_user(username="basic_user2", accept_terms=True)
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id, user2.id],
            "group": [group_ids[0], group_ids[1], group_ids[2]],
            "teams_admin": [],
            "teams_member": [project_team.id],
            "monthly_max": 20,
            "monthly_bonus": 10,
        },
    )
    assert response.status_code == 200
    user.refresh_from_db()
    user2.refresh_from_db()
    assert user.groups.count() == 3
    assert user2.groups.count() == 3
    assert TeamMembership.objects.filter(
        team=project_team, user=user2, role="member"
    ).exists()


@pytest.mark.django_db
def test_manage_users_data_endpoint(client, basic_user, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    managed_user = basic_user(username="managed_user", accept_terms=True)
    group = Group.objects.first()
    if not group:
        group = Group.objects.create(name="Test group")
    managed_user.groups.add(group)
    Cost.objects.create(user=managed_user, usd_cost=Decimal("1.25"))

    response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "search[value]": managed_user.upn,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recordsTotal"] >= 1
    user_upns_in_response = [row[1] for row in payload["data"]]
    assert managed_user.upn in user_upns_in_response, (
        f"User {managed_user.upn} not found. Found: {user_upns_in_response}"
    )
    managed_user_row = next(
        row for row in payload["data"] if row[1] == managed_user.upn
    )
    assert "Active" in managed_user_row[2]


@pytest.mark.django_db
def test_manage_users_data_searches_cost_groups_and_entra_fields(
    client, basic_user, all_apps_user
):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-search-001",
        name="Searchable Cost Group",
        active=True,
    )
    managed_user = basic_user(username="searchable_user", accept_terms=True)
    managed_user.entra_status = User.EntraStatus.DISABLED
    managed_user.job_title = "Platform Advisor"
    managed_user.preferred_language = "Français"
    managed_user.is_active = False
    managed_user.save(
        update_fields=[
            "entra_status",
            "job_title",
            "preferred_language",
            "is_active",
        ]
    )
    managed_user.available_cost_groups.add(cost_group)

    visible_default_response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "search[value]": "Searchable Cost Group",
        },
    )

    assert visible_default_response.status_code == 200
    visible_default_payload = visible_default_response.json()
    user_upns_in_response = [row[1] for row in visible_default_payload["data"]]
    assert managed_user.upn in user_upns_in_response

    hidden_field_response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "search[value]": "Platform Advisor",
        },
    )

    assert hidden_field_response.status_code == 200
    hidden_field_payload = hidden_field_response.json()
    hidden_upns = [row[1] for row in hidden_field_payload["data"]]
    assert managed_user.upn not in hidden_upns

    visible_metadata_response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "visible_search_fields": "upn,job_title,preferred_language,cost_groups",
            "search[value]": "Platform Advisor",
        },
    )

    assert visible_metadata_response.status_code == 200
    visible_metadata_payload = visible_metadata_response.json()
    visible_metadata_upns = [row[1] for row in visible_metadata_payload["data"]]
    assert managed_user.upn in visible_metadata_upns


@pytest.mark.django_db
def test_manage_users_data_sorts_costs_numerically(client, basic_user, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    lower_cost_user = basic_user(username="cost_low", accept_terms=True)
    higher_cost_user = basic_user(username="cost_high", accept_terms=True)

    Cost.objects.create(user=lower_cost_user, usd_cost=Decimal("2.50"))
    Cost.objects.create(user=higher_cost_user, usd_cost=Decimal("10.00"))

    response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "order[0][column]": 7,
            "order[0][dir]": "desc",
            "search[value]": "cost_",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    ordered_upns = [row[1] for row in payload["data"]]
    assert ordered_upns.index(higher_cost_user.upn) < ordered_upns.index(
        lower_cost_user.upn
    )


@pytest.mark.django_db
def test_manage_users_data_filters_by_entra_status(client, basic_user, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    active_user = basic_user(username="status_active", accept_terms=True)
    disabled_user = basic_user(username="status_disabled", accept_terms=True)
    disabled_user.entra_status = User.EntraStatus.DISABLED
    disabled_user.is_active = False
    disabled_user.save(update_fields=["entra_status", "is_active"])

    response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "entra_status": "disabled",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    user_upns_in_response = [row[1] for row in payload["data"]]
    assert disabled_user.upn in user_upns_in_response
    assert active_user.upn not in user_upns_in_response


@pytest.mark.django_db
def test_manage_users_data_sorts_last_login_desc_with_never_logged_in_last(
    client, basic_user, all_apps_user
):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    newest_user = basic_user(username="login_newest", accept_terms=True)
    older_user = basic_user(username="login_older", accept_terms=True)
    never_user = basic_user(username="login_never", accept_terms=True)

    newest_user.last_login = timezone.now()
    older_user.last_login = timezone.now() - timedelta(days=7)
    newest_user.save(update_fields=["last_login"])
    older_user.save(update_fields=["last_login"])

    response = client.get(
        reverse("manage_users_data"),
        data={
            "draw": 1,
            "start": 0,
            "length": 100,
            "order[0][column]": 5,
            "order[0][dir]": "desc",
            "search[value]": "login_",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    ordered_upns = [row[1] for row in payload["data"]]
    assert ordered_upns.index(newest_user.upn) < ordered_upns.index(older_user.upn)
    assert ordered_upns.index(older_user.upn) < ordered_upns.index(never_user.upn)


@pytest.mark.django_db
def test_get_user_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("manage_users_form"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Teams (admin)" in content
    assert "Teams (member)" in content


@pytest.mark.django_db
def test_manage_users_overlap_team_roles_returns_modal_errors(
    client, basic_user, all_apps_user
):
    user = basic_user(username="basic_user", accept_terms=True)
    admin_user = all_apps_user()
    client.force_login(admin_user)
    project_team = Team.objects.create(name="Project / Projet", created_by=admin_user)
    TeamMembership.objects.create(team=project_team, user=admin_user, role="admin")

    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [],
            "teams_admin": [project_team.id],
            "teams_member": [project_team.id],
            "monthly_max": 10,
            "monthly_bonus": 0,
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 400
    content = response.content.decode()
    assert (
        "A team cannot be selected in both Teams (admin) and Teams (member)." in content
    )
    assert "Changes will overwrite properties of all selected users" in content

    response = client.get(reverse("manage_users_form", kwargs={"user_id": user.id}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Teams (admin)" in content
    assert "Teams (member)" in content


@pytest.mark.django_db
def test_manage_users_upload(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.post(reverse("upload_users"), data={})
    assert response.status_code == 302
    assert response.url == reverse("manage_users")

    # Test with a csv file ("users.csv" in this directory)
    """
    upn,roles,monthly_max,cost_groups
    Firstname.Lastname@justice.gc.ca,AI Assistant user|Text Extractor user,100,
    """
    this_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(this_dir, "users.csv"), "rb") as file:
        response = client.post(
            reverse("upload_users"), data={"csv_file": file}, follow=True
        )
    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse("manage_users")
    # Check that the users were created
    new_user = User.objects.filter(upn="firstname.lastname@justice.gc.ca")
    assert new_user.exists()
    new_user = new_user.first()
    # No groups should be added since AI Assistant user and Text Extractor user groups don't exist
    assert new_user.groups.count() == 0
    assert new_user.first_name == "Firstname"
    assert new_user.last_name == "Lastname"
    assert new_user.email == "firstname.lastname@justice.gc.ca"
    flashed_messages = [
        message.message for message in get_messages(response.wsgi_request)
    ]
    assert "Imported 1 user role row(s) from CSV." in flashed_messages


@pytest.mark.django_db
def test_manage_users_upload_cost_groups(client, all_apps_user, tmp_path):
    admin = all_apps_user()
    client.force_login(admin)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-upload-001", name="Upload Cost Group", active=True
    )

    csv_path = tmp_path / "users_cost_groups.csv"
    csv_path.write_text(
        "upn,roles,monthly_max,cost_groups\n"
        "csv.user@justice.gc.ca,Otto admin,125,cg-upload-001\n"
    )

    with csv_path.open("rb") as file:
        response = client.post(reverse("upload_users"), data={"csv_file": file})
    assert response.status_code == 302

    uploaded_user = User.objects.get(upn="csv.user@justice.gc.ca")
    assert cost_group in uploaded_user.available_cost_groups.all()


@pytest.mark.django_db
def test_modify_user_cost_groups_multiselect(client, all_apps_user):
    """Test that cost groups can be added/removed via the manage_users form (multiselect)"""
    admin_user = all_apps_user()
    client.force_login(admin_user)

    # Create a user to modify
    user = User.objects.create_user(upn="test_user", email="test@example.com")

    # Create some cost groups
    cg1 = CostGroup.objects.create(cost_group_id="cg1", name="Cost Group 1")
    cg2 = CostGroup.objects.create(cost_group_id="cg2", name="Cost Group 2")

    # Create a group (role)
    group = Group.objects.create(name="Test Role")

    # Modify the user to add cost groups
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group.id],
            "cost_group": [cg1.id, cg2.id],
            "monthly_max": 100,
            "monthly_bonus": 0,
        },
    )

    assert response.status_code == 200

    user.refresh_from_db()
    assert user.groups.count() == 1
    assert user.available_cost_groups.count() == 2
    assert cg1 in user.available_cost_groups.all()
    assert cg2 in user.available_cost_groups.all()

    # Now remove one cost group
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group.id],
            "cost_group": [cg1.id],
            "monthly_max": 100,
            "monthly_bonus": 0,
        },
    )

    assert response.status_code == 200

    user.refresh_from_db()
    assert user.available_cost_groups.count() == 1
    assert cg1 in user.available_cost_groups.all()
    assert cg2 not in user.available_cost_groups.all()


@pytest.mark.django_db
def test_manage_users_download(client, all_apps_user, basic_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a few basic users and add them to random groups
    group_ids = Group.objects.values_list("id", flat=True)
    u = basic_user(username="user1", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(3, len(group_ids)), replace=False):
        u.groups.add(group_id)
    u = basic_user(username="user2", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(2, len(group_ids)), replace=False):
        u.groups.add(group_id)
    u = basic_user(username="user3", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(4, len(group_ids)), replace=False):
        u.groups.add(group_id)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-download-001", name="Download Cost Group", active=True
    )
    second_cost_group = CostGroup.objects.create(
        cost_group_id="cg-download-002", name="Download Cost Group 2", active=True
    )
    cost_group_user = basic_user(username="user_with_cost_group", accept_terms=True)
    if not group_ids:
        Group.objects.create(name="CSV role")
        group_ids = list(Group.objects.values_list("id", flat=True))
    cost_group_user.groups.add(group_ids[0])
    cost_group_user.available_cost_groups.add(cost_group, second_cost_group)

    inactive_user = basic_user(username="inactive_user", accept_terms=True)
    inactive_user.is_active = False
    inactive_user.entra_status = User.EntraStatus.UNKNOWN
    inactive_user.save(update_fields=["is_active", "entra_status"])

    users = User.objects.all().values_list("upn", "groups__name", "monthly_max")

    response = client.get(reverse("download_users"))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    # Save the file to check its contents
    with open("users.csv", "wb") as file:
        file.write(response.content)

    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8")))
    rows = list(reader)
    assert "entra_status" in reader.fieldnames
    assert "job_title" in reader.fieldnames
    assert "preferred_language" in reader.fieldnames
    assert "cost_7_days" in reader.fieldnames
    assert "cost_30_days" in reader.fieldnames
    assert "cost_all_time" in reader.fieldnames
    assert "cost_groups" in reader.fieldnames
    cost_group_rows = [row for row in rows if row["upn"] == cost_group_user.upn]
    assert cost_group_rows
    exported_slugs = cost_group_rows[0]["cost_groups"].split("|")
    assert set(exported_slugs) == {
        cost_group.cost_group_id,
        second_cost_group.cost_group_id,
    }
    inactive_rows = [row for row in rows if row["upn"] == inactive_user.upn]
    assert inactive_rows
    assert inactive_rows[0]["entra_status"] == "unknown"

    # Upload it
    with open("users.csv", "rb") as file:
        response = client.post(reverse("upload_users"), data={"csv_file": file})
    assert response.status_code == 302

    # Check that the users are unchanged
    updated_users = User.objects.all().values_list("upn", "groups__name", "monthly_max")
    assert sorted(list(users)) == sorted(list(updated_users))
    cost_group_user.refresh_from_db()
    assert set(
        cost_group_user.available_cost_groups.values_list("cost_group_id", flat=True)
    ) == {
        cost_group.cost_group_id,
        second_cost_group.cost_group_id,
    }
    os.remove("users.csv")


@pytest.mark.django_db
def test_manage_users_upload_handles_non_dot_upn(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    group = Group.objects.first()
    if not group:
        group = Group.objects.create(name="CSV Import Role")

    user = User.objects.create_user(
        upn="serviceaccount@justice.gc.ca",
        email="serviceaccount@justice.gc.ca",
        first_name="Service",
        last_name="Account",
    )
    user.groups.add(group)

    download_response = client.get(reverse("download_users"))
    assert download_response.status_code == 200

    upload_file = SimpleUploadedFile(
        "users.csv", download_response.content, content_type="text/csv"
    )
    upload_response = client.post(
        reverse("upload_users"), data={"csv_file": upload_file}
    )

    assert upload_response.status_code == 302
    assert upload_response.url == reverse("manage_users")

    # Ensure the user still exists with the same names
    user.refresh_from_db()
    assert user.first_name == "Service"
    assert user.last_name == "Account"


@pytest.mark.django_db
def test_get_cost_dashboard(client, all_apps_user, rf):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("cost_dashboard"))
    assert response.status_code == 200

    # Create some costs with request context
    request = rf.get("/")
    request.user = user
    request.session = {}
    bind_contextvars(request=request, feature="chat")
    for _ in range(5):
        Cost.objects.new("gpt-4.1-in", 100)
        Cost.objects.new("gpt-4.1-out", 200)

    # Now try GET requests with some different parameters
    x_axes = ["day", "week", "month", "feature", "cost_group", "user", "cost_type"]
    date_groups = [
        "all",
        "last_90_days",
        "last_30_days",
        "last_7_days",
        "today",
        "custom",
    ]
    cost_types = ["all", 1]

    for x_axis in x_axes:
        for date_group in date_groups:
            for download in [True, False]:
                if date_group == "custom":
                    for cost_type in cost_types:
                        response = client.get(
                            reverse("cost_dashboard"),
                            data={
                                "x_axis": x_axis,
                                "date_group": date_group,
                                "start_date": "2022-01-01",
                                "end_date": datetime.date.today().strftime("%Y-%m-%d"),
                                "cost_type": cost_type,
                                "download": download,
                            },
                        )
                        assert response.status_code == 200
                else:
                    response = client.get(
                        reverse("cost_dashboard"),
                        data={
                            "x_axis": x_axis,
                            "date_group": date_group,
                            "download": download,
                        },
                    )
                    assert response.status_code == 200
            if x_axis != "day":
                break


@pytest.mark.django_db
def test_get_manage_cost_groups(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_cost_groups"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_get_cost_groups_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_cost_groups_form"))
    assert response.status_code == 200

    # Test with a cost_group_id that doesn't exist
    response = client.get(
        reverse("manage_cost_groups_form", kwargs={"cost_group_id": 100})
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_dashboard_synthetic_project_filters(client, all_apps_user, rf):
    """Test that synthetic cost_group filters work correctly in dashboards"""

    user = all_apps_user()
    client.force_login(user)

    # Create a cost group
    cost_group = CostGroup.objects.create(
        cost_group_id="TEST001", name="Test Project", monthly_max=1000, active=True
    )

    # Create some costs - mix of personal and cost group costs
    request = rf.get("/")
    request.user = user
    request.session = {}
    bind_contextvars(request=request, feature="chat")
    Cost.objects.new("gpt-4.1-in", 100)  # Personal cost

    request2 = rf.get("/")
    request2.user = user
    request2.session = {"selected_cost_group_id": cost_group.id}
    bind_contextvars(request=request2, feature="chat")
    Cost.objects.new("gpt-4.1-in", 200)  # Cost group cost

    # Test "all" filter (default) - should show both
    response = client.get(reverse("cost_dashboard"), data={"cost_group": "all"})
    assert response.status_code == 200

    # Test "personal" filter - should show only personal costs
    response = client.get(reverse("cost_dashboard"), data={"cost_group": "personal"})
    assert response.status_code == 200

    # Test "cost_groups" filter - should show only cost group costs
    response = client.get(reverse("cost_dashboard"), data={"cost_group": "cost_groups"})
    assert response.status_code == 200

    # Test specific cost group filter
    response = client.get(
        reverse("cost_dashboard"), data={"cost_group": str(cost_group.id)}
    )
    assert response.status_code == 200

    # Test same filters on usage dashboard
    response = client.get(reverse("usage_dashboard"), data={"cost_group": "all"})
    assert response.status_code == 200

    response = client.get(reverse("usage_dashboard"), data={"cost_group": "personal"})
    assert response.status_code == 200

    response = client.get(
        reverse("usage_dashboard"), data={"cost_group": "cost_groups"}
    )
    assert response.status_code == 200
