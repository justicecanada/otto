from django.urls import reverse

import pytest

from otto.forms import TeamForm
from otto.models import Team, TeamMembership, User


@pytest.mark.django_db
def test_manage_teams_page_renders_team_details(client, all_apps_user):
    user = all_apps_user()
    teammate = User.objects.create_user(
        upn="teammate@example.com",
        email="teammate@example.com",
        first_name="Team",
        last_name="Mate",
    )
    team = Team.objects.create(name="Research / Recherche", created_by=user)
    TeamMembership.objects.create(team=team, user=user, role="admin")
    TeamMembership.objects.create(team=team, user=teammate, role="member")

    client.force_login(user)
    response = client.get(reverse("manage_teams"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Manage teams" in content
    assert "Your role" in content
    assert "Otto admin" in content
    assert "Research / Recherche" in content
    assert "teammate@example.com" in content


@pytest.mark.django_db
def test_manage_teams_form_renders_for_create_and_edit(client, all_apps_user):
    user = all_apps_user()
    team = Team.objects.create(name="Policy / Politiques", created_by=user)
    TeamMembership.objects.create(team=team, user=user, role="admin")

    client.force_login(user)

    create_response = client.get(reverse("manage_teams_form"))
    assert create_response.status_code == 200
    assert "Team name" in create_response.content.decode()

    edit_response = client.get(reverse("manage_teams_form_edit", args=[team.id]))
    assert edit_response.status_code == 200
    assert "Policy / Politiques" in edit_response.content.decode()


@pytest.mark.django_db
def test_manage_teams_form_invalid_post_rerenders_modal_content(client, all_apps_user):
    user = all_apps_user()
    team = Team.objects.create(name="Test team", created_by=user)
    TeamMembership.objects.create(team=team, user=user, role="admin")
    other_user = User.objects.create_user(
        upn="other.member@example.com",
        email="other.member@example.com",
        first_name="Other",
        last_name="Member",
    )

    client.force_login(user)
    response = client.post(
        reverse("manage_teams_form_edit", args=[team.id]),
        data={
            "name": "Test team",
            "team_admins": [str(user.id)],
            "team_members": [str(user.id), str(other_user.id)],
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "A user cannot be both an admin and a member of the same team." in content
    assert "hx-post=" in content
    assert "HX-Redirect" not in response.headers


@pytest.mark.django_db
def test_manage_teams_form_valid_htmx_post_redirects_back(client, all_apps_user):
    user = all_apps_user()
    team = Team.objects.create(name="Test team", created_by=user)
    TeamMembership.objects.create(team=team, user=user, role="admin")

    client.force_login(user)
    response = client.post(
        reverse("manage_teams_form_edit", args=[team.id]),
        data={
            "name": "Updated team",
            "team_admins": [str(user.id)],
            "team_members": [],
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == reverse("manage_teams")
    team.refresh_from_db()
    assert team.name == "Updated team"


@pytest.mark.django_db
def test_delete_team_htmx_post_redirects_back(client, all_apps_user):
    user = all_apps_user()
    team = Team.objects.create(name="Delete me", created_by=user)
    TeamMembership.objects.create(team=team, user=user, role="admin")

    client.force_login(user)
    response = client.post(
        reverse("delete_team", args=[team.id]),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == reverse("manage_teams")
    assert not Team.objects.filter(pk=team.pk).exists()


@pytest.mark.django_db
def test_team_form_requires_admin_and_unique_name(all_apps_user):
    user = all_apps_user()
    other_user = User.objects.create_user(
        upn="other@example.com",
        email="other@example.com",
        first_name="Other",
        last_name="User",
    )
    Team.objects.create(name="Operations / Opérations", created_by=user)

    duplicate_form = TeamForm(
        data={
            "name": "operations / opérations",
            "team_admins": [str(user.id)],
            "team_members": [],
        }
    )
    assert not duplicate_form.is_valid()
    assert "A team with this name already exists." in duplicate_form.errors["name"]

    no_admin_form = TeamForm(
        data={
            "name": "Delivery / Prestation",
            "team_admins": [],
            "team_members": [str(other_user.id)],
        }
    )
    assert not no_admin_form.is_valid()
    assert "At least one administrator is required." in no_admin_form.non_field_errors()


@pytest.mark.django_db
def test_team_form_sends_notifications(all_apps_user):
    user = all_apps_user()
    other_user = User.objects.create_user(
        upn="other.member@example.com",
        email="other.member@example.com",
        first_name="Other",
        last_name="Member",
    )
    team_form = TeamForm(
        data={
            "name": "Test team",
            "admins": [str(user.id)],
            "members": [str(other_user.id)],
        }
    )
    assert team_form.is_valid()
    team_form.save(user)
    notifications = other_user.notifications.filter(heading_en="Added to Team")
    assert notifications.exists()
