"""
Tests for chat_next.models.

Tests the Message model including the new response_output field.
"""

import pytest
from chat_next.models import (
    DEFAULT_ENABLED_TOOLS,
    Chat,
    ChatSettings,
    Message,
    Skill,
    sanitize_enabled_tools,
)


@pytest.mark.django_db
class TestMessageModel:
    """Tests for Message model."""

    def test_response_output_default(self, all_apps_user):
        """response_output should default to empty list."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            text="Hello",
            is_bot=False,
        )

        assert msg.response_output == []

    def test_response_output_stores_json(self, all_apps_user):
        """response_output should store JSON data correctly."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        output_items = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi!"}],
            },
            {"type": "reasoning", "encrypted_content": "base64_encrypted_data"},
        ]

        msg = Message.objects.create(
            chat=chat,
            text="Hi!",
            is_bot=True,
            response_output=output_items,
        )

        # Reload from database
        msg.refresh_from_db()

        assert msg.response_output == output_items
        assert len(msg.response_output) == 2
        assert msg.response_output[0]["type"] == "message"
        assert msg.response_output[1]["type"] == "reasoning"

    def test_response_output_can_be_updated(self, all_apps_user):
        """response_output can be updated after creation."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            text="Hello",
            is_bot=True,
        )

        assert msg.response_output == []

        # Update response_output
        msg.response_output = [{"role": "assistant", "content": "Hello"}]
        msg.save()

        msg.refresh_from_db()
        assert msg.response_output == [{"role": "assistant", "content": "Hello"}]


@pytest.mark.django_db
class TestChatModel:
    """Tests for Chat model."""

    def test_chat_creates_settings(self, all_apps_user):
        """Creating a Chat should auto-create ChatSettings for the user."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # ChatSettings should be created for the user
        assert hasattr(user, "chat_settings")
        settings = chat.settings
        assert settings is not None
        assert isinstance(settings, ChatSettings)
        assert settings.chat_max_iterations == 25
        assert settings.send_name_to_model is False
        assert settings.chat_enabled_tools == DEFAULT_ENABLED_TOOLS

    def test_chat_creates_settings_with_default_fixture_skills(self, all_apps_user):
        """New ChatSettings should auto-enable public fixture skills."""
        user = all_apps_user()
        default_skill = Skill.objects.create(
            display_name="Default fixture skill",
            description="A default skill from fixture",
            body="Do useful things.",
            sharing_option="everyone",
        )

        chat = Chat.objects.create(user=user, title="Test")
        settings = chat.settings

        assert settings.enabled_skills.filter(id=default_skill.id).exists()

    def test_chat_settings_populates_user_display_name(self, all_apps_user):
        """ChatSettings should auto-populate user_display_name from user.full_name."""
        user = all_apps_user()
        user.first_name = "John"
        user.last_name = "Doe"
        user.save()

        chat = Chat.objects.create(user=user, title="Test")
        settings = chat.settings

        assert settings.user_display_name == "John Doe"

    def test_chat_settings_respects_existing_display_name(self, all_apps_user):
        """ChatSettings should not overwrite an existing user_display_name."""
        user = all_apps_user()
        user.first_name = "John"
        user.last_name = "Doe"
        user.save()

        # Create ChatSettings with a custom display name
        ChatSettings.objects.create(
            user=user,
            user_display_name="Custom Name",
        )

        # Calling get_or_create_for_user should not overwrite it
        retrieved_settings, _ = ChatSettings.objects.get_or_create_for_user(user)

        assert retrieved_settings.user_display_name == "Custom Name"


class TestEnabledToolSanitization:
    def test_sanitize_enabled_tools_removes_hidden_and_unknown_values(self):
        tools = [
            "code_interpreter",
            "local_qa_libraries",
            "local_transcription",  # hidden/not approved
            "unknown_tool",
        ]

        assert sanitize_enabled_tools(tools) == [
            "code_interpreter",
            "local_qa_libraries",
        ]

    def test_sanitize_enabled_tools_deduplicates_preserving_order(self):
        tools = ["code_interpreter", "code_interpreter", "local_legal_research"]

        assert sanitize_enabled_tools(tools) == [
            "code_interpreter",
            "local_legal_research",
        ]
