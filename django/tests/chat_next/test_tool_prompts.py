"""Behavioral tests for chat_next tool and skill prompt plumbing."""

import asyncio
import uuid

from django.contrib.auth import get_user_model

import pytest
from chat_next._tools.base import TOOL_REGISTRY, ToolContext
from chat_next._tools.preset_migration import _build_skill_link_result, _create_skill
from chat_next.models import TOOL_CATEGORY_SKILLS, Chat, ChatSettings, Message, Skill
from chat_next.prompts import (
    get_effective_available_skills,
    get_effective_enabled_tools,
    get_tool_prompts,
)


@pytest.mark.django_db
def test_effective_enabled_tools_always_includes_local_skills(all_apps_user):
    user = all_apps_user()
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = []
    settings.save(update_fields=["chat_enabled_tools"])

    enabled = get_effective_enabled_tools(settings)

    assert TOOL_CATEGORY_SKILLS in enabled


@pytest.mark.django_db
def test_effective_enabled_tools_merges_context_hinted_tools(all_apps_user):
    """Context-hinted tools from the latest user message should be in enabled list."""
    user = all_apps_user("ctx-hint-tools-user")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = ["local_qa_libraries"]
    settings.save(update_fields=["chat_enabled_tools"])

    chat = Chat.objects.create(user=user, title="Hint tools test")
    Message.objects.create(
        chat=chat,
        text="Process my docs",
        is_bot=False,
        details={
            "context_hints": [
                {
                    "type": "tool",
                    "id": "local_document_processing",
                    "name": "Batch Processing",
                },
            ]
        },
    )

    enabled = get_effective_enabled_tools(settings, chat=chat)

    assert "local_qa_libraries" in enabled
    assert "local_document_processing" in enabled


@pytest.mark.django_db
def test_effective_enabled_tools_ignores_invalid_message_tool_hints(all_apps_user):
    user = all_apps_user("invalid-ctx-hint-user")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = []
    settings.save(update_fields=["chat_enabled_tools"])

    chat = Chat.objects.create(user=user, title="Invalid hint tools test")
    Message.objects.create(
        chat=chat,
        text="Read my document",
        is_bot=False,
        details={
            "context_hints": [
                {
                    "type": "tool",
                    "id": "get_document_text",
                    "name": "Get document text",
                },
            ]
        },
    )

    enabled = get_effective_enabled_tools(settings, chat=chat)

    assert enabled == [TOOL_CATEGORY_SKILLS]


@pytest.mark.django_db
def test_effective_enabled_tools_no_duplicates_from_hints(all_apps_user):
    """If a context-hinted tool is already enabled, it should not be duplicated."""
    user = all_apps_user("no-dup-hint-user")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = ["local_qa_libraries"]
    settings.save(update_fields=["chat_enabled_tools"])

    chat = Chat.objects.create(user=user, title="Dup test")
    Message.objects.create(
        chat=chat,
        text="Search",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "tool", "id": "local_qa_libraries", "name": "Q&A Libraries"},
            ]
        },
    )

    enabled = get_effective_enabled_tools(settings, chat=chat)

    assert enabled.count("local_qa_libraries") == 1


@pytest.mark.django_db
def test_effective_enabled_tools_without_chat_unchanged(all_apps_user):
    """Without chat param, get_effective_enabled_tools behaves as before."""
    user = all_apps_user("no-chat-user")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_enabled_tools = ["local_qa_libraries"]
    settings.save(update_fields=["chat_enabled_tools"])

    enabled = get_effective_enabled_tools(settings)

    assert "local_qa_libraries" in enabled
    assert TOOL_CATEGORY_SKILLS in enabled


def test_document_tool_prompt_prefers_contiguous_reads_for_summaries():
    prompt = get_tool_prompts(["local_qa_libraries"])

    assert "single-document summaries" in prompt
    assert "start at the beginning" in prompt
    assert "many selective snippets" in prompt
    assert "approximate percentage" in prompt


# --- Skill context-hint tests ---


@pytest.mark.django_db
def test_auto_enable_hinted_skills(all_apps_user):
    """Hinted skills should be resolved for the turn without being persisted."""
    from chat_next._llm.openai_responses import _auto_enable_hinted_skills

    user = all_apps_user("auto-enable-user")
    chat = Chat.objects.create(user=user, title="Auto enable test")
    skill = Skill.objects.create(
        display_name="Auto Skill",
        description="Test auto-enable",
        body="Instructions.",
        owner=user,
    )
    # Skill is NOT yet enabled
    assert not chat.settings.enabled_skills.filter(pk=skill.pk).exists()

    Message.objects.create(
        chat=chat,
        text="test",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": str(skill.pk), "name": "Auto Skill"}
            ]
        },
    )

    _auto_enable_hinted_skills(chat)

    # It should remain disabled in settings.
    assert not chat.settings.enabled_skills.filter(pk=skill.pk).exists()


@pytest.mark.django_db
def test_auto_enable_skips_inaccessible_skills(all_apps_user):
    """Hint resolution must not surface skills the user cannot access."""
    from chat_next._llm.openai_responses import _auto_enable_hinted_skills

    user = all_apps_user("no-access-user")
    other_user = all_apps_user("skill-owner")
    chat = Chat.objects.create(user=user, title="No access test")
    skill = Skill.objects.create(
        display_name="Not Mine",
        description="Owned by someone else",
        body="Secret.",
        owner=other_user,
        sharing_option="private",
    )

    Message.objects.create(
        chat=chat,
        text="test",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": str(skill.pk), "name": "Not Mine"}
            ]
        },
    )

    hinted_skills = _auto_enable_hinted_skills(chat)

    # Should NOT be surfaced — user can't access it.
    assert hinted_skills == []
    assert not chat.settings.enabled_skills.filter(pk=skill.pk).exists()


@pytest.mark.django_db
def test_effective_available_skills_includes_context_hinted_skill(all_apps_user):
    user = all_apps_user("hinted-effective-skill-user")
    chat = Chat.objects.create(user=user, title="Effective skills test")
    skill = Skill.objects.create(
        display_name="Turn Only Skill",
        description="Only for this turn",
        body="Use me once.",
        owner=user,
    )
    Message.objects.create(
        chat=chat,
        text="Use the hinted skill",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": str(skill.pk), "name": "Turn Only Skill"}
            ]
        },
    )

    available = get_effective_available_skills(chat.settings, chat=chat, user=user)

    assert skill.id in [item.id for item in available]
    assert not chat.settings.enabled_skills.filter(pk=skill.pk).exists()


@pytest.mark.django_db
def test_effective_enabled_tools_keep_hinted_skill_tools_hidden_until_load(
    all_apps_user,
):
    user = all_apps_user("hinted-skill-tools-user")
    chat = Chat.objects.create(user=user, title="Hinted skill tools test")
    chat.settings.chat_enabled_tools = []
    chat.settings.save(update_fields=["chat_enabled_tools"])

    skill = Skill.objects.create(
        display_name="Process Docs",
        description="Needs document processing tools",
        body="Use document processing.",
        owner=user,
        required_tools=["local_document_processing"],
        context_hints=[
            {"type": "tool", "id": "local_qa_libraries", "name": "Libraries"}
        ],
    )
    Message.objects.create(
        chat=chat,
        text="Try the skill",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": str(skill.pk), "name": "Process Docs"}
            ]
        },
    )

    enabled = get_effective_enabled_tools(chat.settings, chat=chat)

    assert enabled == [TOOL_CATEGORY_SKILLS]


@pytest.mark.django_db
def test_effective_enabled_tools_can_preload_hinted_skill_tools_for_request_manifest(
    all_apps_user,
):
    user = all_apps_user("hinted-skill-request-tools-user")
    chat = Chat.objects.create(user=user, title="Hinted skill request tools test")
    chat.settings.chat_enabled_tools = []
    chat.settings.save(update_fields=["chat_enabled_tools"])

    skill = Skill.objects.create(
        display_name="Request Tools Skill",
        description="Needs extra tool categories",
        body="Use document processing and library tools.",
        owner=user,
        required_tools=["local_document_processing"],
        context_hints=[
            {"type": "tool", "id": "local_qa_libraries", "name": "Libraries"}
        ],
    )
    Message.objects.create(
        chat=chat,
        text="Try the skill",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": str(skill.pk), "name": "Request Tools Skill"}
            ]
        },
    )

    enabled = get_effective_enabled_tools(
        chat.settings,
        chat=chat,
        user=user,
        include_available_skill_tools=True,
    )

    assert TOOL_CATEGORY_SKILLS in enabled
    assert "local_document_processing" in enabled
    assert "local_qa_libraries" in enabled


@pytest.mark.django_db
def test_local_skill_tools_hidden_until_unlocked(all_apps_user):
    user = all_apps_user("hidden-skill-tools-user")

    hidden = TOOL_REGISTRY.get_tools_config(
        user,
        enabled_categories=[TOOL_CATEGORY_SKILLS],
        unlocked_local_skill_tools=False,
    )
    hidden_names = {tool["name"] for tool in hidden}

    assert hidden_names <= {"load_skill_instructions"}
    assert "create_skill" not in hidden_names
    assert "create_skill_from_preset" not in hidden_names
    assert "edit_skill" not in hidden_names
    assert "list_presets" not in hidden_names
    assert "read_preset" not in hidden_names

    unlocked = TOOL_REGISTRY.get_tools_config(
        user,
        enabled_categories=[TOOL_CATEGORY_SKILLS],
        unlocked_local_skill_tools=True,
    )
    unlocked_names = {tool["name"] for tool in unlocked}

    assert "create_skill" in unlocked_names
    assert "create_skill_from_preset" in unlocked_names
    assert "edit_skill" in unlocked_names
    assert "list_presets" in unlocked_names
    assert "read_preset" in unlocked_names


@pytest.mark.django_db
def test_client_keeps_skill_tools_locked_until_skill_is_loaded(
    all_apps_user,
):
    """Hidden skill-management tools should stay locked until loader output unlocks them."""
    from chat_next._llm.openai_responses import ResponsesAPIClient

    user = all_apps_user("skill-unlock-user")
    chat = Chat.objects.create(user=user, title="Unlock test")

    # Create a skill with context_hints referencing local_skills
    skill = Skill.objects.create(
        display_name_en="Test Skill",
        description_en="A skill that needs local_skills",
        body_en="Instructions here",
        context_hints=[
            {"type": "tool", "id": "local_skills", "name": "Skill Management"}
        ],
    )
    chat.settings.enabled_skills.add(skill)

    client = ResponsesAPIClient(user=user, chat=chat)
    assert client.unlocked_local_skill_tools is False


@pytest.mark.django_db
def test_create_skill_rejects_granular_tool_context_hint(all_apps_user):
    user = all_apps_user("invalid-skill-creator-tool-user")

    result = asyncio.run(
        _create_skill(
            {
                "display_name_en": "Policy Helper",
                "description_en": "Help with policy docs",
                "body_en": "Use get_document_text when needed.",
                "context_hints": [
                    {
                        "type": "tool",
                        "id": "get_document_text",
                        "name": "Get document text",
                    }
                ],
            },
            ToolContext(user=user),
        )
    )

    assert result["success"] is False
    assert "Unsupported tool context hint id 'get_document_text'" in result["error"]
    assert "Mention granular tool names like get_document_text" in result["error"]


@pytest.mark.django_db
def test_build_skill_link_result_returns_open_skill_link_metadata():
    user_suffix = uuid.uuid4().hex[:8]
    user = get_user_model().objects.create_user(
        upn=f"edit-skill-link-user-{user_suffix}@example.com",
        email=f"edit-skill-link-user-{user_suffix}@example.com",
        password="test-password",
    )
    skill = Skill.objects.create(
        display_name_en="Chronology Helper",
        description_en="Build chronologies",
        body_en="Initial body",
        owner=user,
        sharing_option="private",
    )

    tool_result = _build_skill_link_result(skill)

    assert tool_result["skill_id"] == skill.id
    assert tool_result["skill_name"] == "Chronology Helper"
    assert tool_result["display_name_en"] == "Chronology Helper"
    assert tool_result["edit_url"] == f"skill://{skill.id}"
    assert (
        tool_result["edit_link_token"] == f"[[OPEN_SKILL:{skill.id}|Chronology Helper]]"
    )
