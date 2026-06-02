from django.urls import reverse

import pytest
from chat_next.models import Chat, ChatSettings, Skill

from otto.models import Team, TeamMembership

from librarian.models import DataSource, Document, Library, LibraryTeamRole


@pytest.mark.django_db
def test_context_autocomplete_includes_enabled_skill(client, all_apps_user):
    user = all_apps_user("context-user")
    chat = Chat.objects.create(user=user, title="Context chat")
    skill = Skill.objects.create(
        display_name="Context Skill",
        description="Skill visible in picker",
        body="Use this skill.",
        owner=user,
    )
    chat.settings.enabled_skills.add(skill)
    client.force_login(user)

    response = client.get(reverse("chat_next:context_autocomplete", args=[chat.id]))

    assert response.status_code == 200
    payload = response.json()
    skill_items = [item for item in payload["items"] if item["type"] == "skill"]
    assert any(item["id"] == skill.id for item in skill_items)
    assert any(item["name"] == "Context Skill" for item in skill_items)
    assert all("body" not in item for item in skill_items)


@pytest.mark.django_db
def test_context_autocomplete_excludes_disabled_skills(client, all_apps_user):
    user = all_apps_user("context-user-disabled")
    chat = Chat.objects.create(user=user, title="Context chat")
    Skill.objects.create(
        display_name="Disabled Skill",
        description="Should stay hidden until enabled",
        body="Disabled",
        owner=user,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:context_autocomplete", args=[chat.id]))

    assert response.status_code == 200
    payload = response.json()
    skill_names = [item["name"] for item in payload["items"] if item["type"] == "skill"]
    assert "Disabled Skill" not in skill_names


@pytest.mark.django_db
def test_context_autocomplete_excludes_removed_skills(client, all_apps_user):
    user = all_apps_user("context-user-deleted")
    chat = Chat.objects.create(user=user, title="Context chat")
    skill = Skill.objects.create(
        display_name="Deleted Skill",
        description="Should not appear",
        body="Deleted",
        owner=user,
    )
    chat.settings.enabled_skills.add(skill)
    skill.delete()
    client.force_login(user)

    response = client.get(reverse("chat_next:context_autocomplete", args=[chat.id]))

    assert response.status_code == 200
    payload = response.json()
    skill_names = [item["name"] for item in payload["items"] if item["type"] == "skill"]
    assert "Deleted Skill" not in skill_names


@pytest.mark.django_db
def test_context_autocomplete_tools_have_enabled_flag(client, all_apps_user):
    """Tool items should include an 'enabled' boolean reflecting user settings."""
    user = all_apps_user("enabled-flag-user")
    chat = Chat.objects.create(user=user, title="Enabled flag chat")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = ["local_qa_libraries"]
    settings.save(update_fields=["chat_enabled_tools"])
    client.force_login(user)

    response = client.get(reverse("chat_next:context_autocomplete", args=[chat.id]))

    assert response.status_code == 200
    payload = response.json()
    tool_items = [item for item in payload["items"] if item["type"] == "tool"]
    assert len(tool_items) > 0

    enabled_tools = {t["id"] for t in tool_items if t.get("enabled") is True}
    disabled_tools = {t["id"] for t in tool_items if t.get("enabled") is False}

    assert "local_qa_libraries" in enabled_tools
    # At least one tool should be disabled since we only enabled one
    assert len(disabled_tools) >= 1


@pytest.mark.django_db
def test_context_autocomplete_shows_skill_management_for_admin(client, all_apps_user):
    user = all_apps_user("context-admin-user")
    chat = Chat.objects.create(user=user, title="Admin context chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:context_autocomplete", args=[chat.id]))

    assert response.status_code == 200
    payload = response.json()
    tool_items = [item for item in payload["items"] if item["type"] == "tool"]
    assert any(item["id"] == "local_skills" for item in tool_items)


@pytest.mark.django_db
def test_context_autocomplete_includes_team_contributor_library(client, all_apps_user):
    owner = all_apps_user("context-team-owner")
    member = all_apps_user("context-team-member")
    chat = Chat.objects.create(user=member, title="Team context chat")
    team = Team.objects.create(name="Context Team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner, role="admin")
    TeamMembership.objects.create(team=team, user=member, role="member")

    library = Library.objects.create(
        name="Context Team Library",
        created_by=owner,
        is_public=False,
    )
    LibraryTeamRole.objects.create(library=library, team=team, role="contributor")
    data_source = DataSource.objects.create(library=library, name="Shared folder")
    Document.objects.create(
        data_source=data_source,
        filename="team-library.txt",
        extracted_text="Shared document",
        status="SUCCESS",
        is_container=False,
    )

    client.force_login(member)

    response = client.get(
        reverse("chat_next:context_autocomplete", args=[chat.id]),
        {"q": "Context Team Library"},
    )

    assert response.status_code == 200
    payload = response.json()
    library_items = [item for item in payload["items"] if item["type"] == "library"]
    assert any(item["id"] == library.id for item in library_items)


@pytest.mark.django_db
def test_context_autocomplete_includes_public_metadata_for_library_and_folder(
    client, all_apps_user
):
    user = all_apps_user("context-public-metadata-user")
    chat = Chat.objects.create(user=user, title="Context public metadata chat")
    public_library = Library.objects.create(
        name="Public metadata library",
        created_by=user,
        is_public=True,
    )
    public_folder = DataSource.objects.create(
        library=public_library, name="Public folder"
    )
    Document.objects.create(
        data_source=public_folder,
        filename="public-folder-doc.txt",
        extracted_text="Public folder document",
        status="SUCCESS",
        is_container=False,
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:context_autocomplete", args=[chat.id]),
        {"q": "Public"},
    )

    assert response.status_code == 200
    payload = response.json()
    library_item = next(
        item
        for item in payload["items"]
        if item["type"] == "library" and item["id"] == public_library.id
    )
    folder_item = next(
        item
        for item in payload["items"]
        if item["type"] == "folder" and item["id"] == public_folder.id
    )

    assert library_item["is_public"] is True
    assert folder_item["parent_library_public"] is True


@pytest.mark.django_db
def test_context_autocomplete_excludes_revoked_team_skill(client, all_apps_user):
    owner = all_apps_user("context-team-skill-owner")
    member = all_apps_user("context-team-skill-member")
    chat = Chat.objects.create(user=member, title="Revoked team skill chat")
    team = Team.objects.create(name="Context Skill Team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner, role="admin")
    membership = TeamMembership.objects.create(team=team, user=member, role="member")

    skill = Skill.objects.create(
        display_name="Revoked Team Context Skill",
        description="Should disappear when team access is revoked",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to_teams.add(team)
    chat.settings.enabled_skills.add(skill)

    assert Skill.objects.get_accessible(member).filter(id=skill.id).exists()

    membership.delete()
    client.force_login(member)

    response = client.get(
        reverse("chat_next:context_autocomplete", args=[chat.id]),
        {"q": "Revoked Team Context Skill"},
    )

    assert response.status_code == 200
    payload = response.json()
    skill_items = [item for item in payload["items"] if item["type"] == "skill"]
    assert all(item["id"] != skill.id for item in skill_items)
