"""Tests for skill usage count semantics."""

from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from chat_next._tools.base import ToolContext
from chat_next._tools.skills import _load_skill_instructions
from chat_next._utils.context_hints import _resolve_lookup_key_context_hint
from chat_next.models import Chat, Skill

from otto.models import Team, TeamMembership


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_increments_load_count(all_apps_user):
    suffix = uuid4().hex[:8]
    user = await sync_to_async(all_apps_user)(f"usage-counter-user-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(user=user, title="Usage chat")
    skill = await sync_to_async(Skill.objects.create)(
        display_name="Usage Counter",
        description="Counts uses",
        body="Do usage counting.",
        owner=user,
        load_count=0,
    )
    await sync_to_async(chat.settings.enabled_skills.add)(skill)

    context = ToolContext(user=user, chat=chat)
    result = await _load_skill_instructions({"skill_id": skill.id}, context)

    assert result["success"] is True
    await sync_to_async(skill.refresh_from_db)()
    assert skill.load_count == 1


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_rejects_revoked_team_skill(all_apps_user):
    suffix = uuid4().hex[:8]
    owner = await sync_to_async(all_apps_user)(f"usage-team-owner-{suffix}")
    member = await sync_to_async(all_apps_user)(f"usage-team-member-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(
        user=member, title="Team usage chat"
    )
    team = await sync_to_async(Team.objects.create)(
        name=f"usage-team-{suffix}",
        created_by=owner,
    )
    await sync_to_async(TeamMembership.objects.create)(
        team=team, user=owner, role="admin"
    )
    membership = await sync_to_async(TeamMembership.objects.create)(
        team=team,
        user=member,
        role="member",
    )
    skill = await sync_to_async(Skill.objects.create)(
        display_name="Usage Team Skill",
        description="Counts uses for team skill",
        body="Team instructions.",
        owner=owner,
        sharing_option="others",
        load_count=0,
    )
    await sync_to_async(skill.accessible_to_teams.add)(team)
    await sync_to_async(chat.settings.enabled_skills.add)(skill)
    await sync_to_async(membership.delete)()

    context = ToolContext(user=member, chat=chat)
    result = await _load_skill_instructions({"skill_id": skill.id}, context)

    assert result["success"] is False
    assert "is not available in this chat" in result["error"]
    await sync_to_async(skill.refresh_from_db)()
    assert skill.load_count == 0


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_allows_context_hinted_disabled_skill(
    all_apps_user,
):
    suffix = uuid4().hex[:8]
    user = await sync_to_async(all_apps_user)(f"usage-hinted-user-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(
        user=user, title="Hinted usage chat"
    )
    skill = await sync_to_async(Skill.objects.create)(
        display_name="Hinted Usage Skill",
        description="Available for one turn via context hint",
        body="Hinted instructions.",
        owner=user,
        load_count=0,
    )
    await sync_to_async(chat.messages.create)(
        text="Use the hinted skill",
        is_bot=False,
        details={
            "context_hints": [
                {
                    "type": "skill",
                    "id": str(skill.pk),
                    "name": "Hinted Usage Skill",
                }
            ]
        },
    )

    context = ToolContext(user=user, chat=chat)
    result = await _load_skill_instructions({"skill_id": skill.id}, context)

    assert result["success"] is True
    assert result["result"]["instructions"] == "Hinted instructions."
    await sync_to_async(skill.refresh_from_db)()
    assert skill.load_count == 1
    assert not await sync_to_async(
        chat.settings.enabled_skills.filter(pk=skill.pk).exists
    )()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_resolves_lookup_key_document_hint(all_apps_user):
    from librarian.models import Document

    suffix = uuid4().hex[:8]
    user = await sync_to_async(all_apps_user)(f"usage-lookup-key-user-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(user=user, title="Lookup key chat")
    resolved = await sync_to_async(_resolve_lookup_key_context_hint)(
        "document", "translation-glossary"
    )
    assert resolved is not None
    document = await sync_to_async(Document.objects.get)(id=resolved["id"])
    skill = await sync_to_async(Skill.objects.create)(
        display_name="Lookup Key Skill",
        description="Uses fixture lookup key",
        body="Use the glossary.",
        owner=user,
        load_count=0,
        context_hints=[
            {
                "type": "document",
                "lookup_key": "translation-glossary",
                "name": "JUS Translation Glossary",
            }
        ],
    )
    await sync_to_async(chat.settings.enabled_skills.add)(skill)

    context = ToolContext(user=user, chat=chat)
    result = await _load_skill_instructions({"skill_id": skill.id}, context)

    assert result["success"] is True
    assert result["result"]["context_hints"] == [
        {
            "type": "document",
            "lookup_key": "translation-glossary",
            "name": "JUS Translation Glossary",
            "id": str(document.id),
        }
    ]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_accepts_numeric_string_skill_id(all_apps_user):
    suffix = uuid4().hex[:8]
    user = await sync_to_async(all_apps_user)(f"usage-string-id-user-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(user=user, title="String id chat")
    skill = await sync_to_async(Skill.objects.create)(
        display_name="String Id Skill",
        description="Accepts a digit string id.",
        body="String id instructions.",
        owner=user,
    )
    await sync_to_async(chat.settings.enabled_skills.add)(skill)

    context = ToolContext(user=user, chat=chat)
    result = await _load_skill_instructions({"skill_id": str(skill.id)}, context)

    assert result["success"] is True
    assert result["result"]["instructions"] == "String id instructions."


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_load_skill_instructions_rejects_legacy_name_identifier(all_apps_user):
    suffix = uuid4().hex[:8]
    user = await sync_to_async(all_apps_user)(f"usage-legacy-name-user-{suffix}")
    chat = await sync_to_async(Chat.objects.create)(user=user, title="Legacy name chat")

    context = ToolContext(user=user, chat=chat)
    result = await _load_skill_instructions({"skill_id": "skill-creator"}, context)

    assert result["success"] is False
    assert "numeric skill id" in result["error"]
