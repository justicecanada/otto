"""Tests for the chat_next translation tool limits and schema."""

import io
from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from chat_next.models import Chat, Message
from chat_next.tools import (
    TOOL_REGISTRY,
    ToolContext,
    translate_files,
)
from structlog.contextvars import bind_contextvars, unbind_contextvars

from otto.models import Cost, CostType


class TestTranslateFilesTool:
    """Tests for translate_files tool."""

    def test_tool_is_registered(self):
        tool = TOOL_REGISTRY.get("translate_files")
        assert tool is not None
        assert tool.name == "translate_files"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_too_many_files(self, all_apps_user):
        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await translate_files(
            {
                "document_ids": list(range(1, 22)),
                "target_language": "fr",
            },
            context,
        )

        assert "error" in result
        assert "Maximum of 20 files" in result["error"]


class TestTranslateFileNextTask:
    """Tests for the translate_file_next Celery task cleanup behaviour."""

    @pytest.mark.django_db
    def test_costs_bind_to_message_next_even_with_stale_legacy_message_context(
        self, settings, all_apps_user
    ):
        from chat_next.tasks import translate_file_next

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Translate task cost test")
        out_message = Message.objects.create(chat=chat, is_bot=True, text="")
        CostType.objects.get_or_create(
            short_name="translate-file",
            defaults={
                "name": "Translate file",
                "description": "Azure document translation",
                "unit_name": "characters",
                "unit_cost": 15,
                "unit_quantity": 1_000_000,
            },
        )

        settings.AZURE_AI_SERVICES_ENDPOINT = "https://api.example.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_CONTAINER = "testcontainer"
        settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT = "temp/translation/in"
        settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT = "temp/translation/out"

        mock_azure_storage = MagicMock()
        mock_azure_storage.open.return_value = io.BytesIO(b"translated content")
        settings.AZURE_STORAGE = mock_azure_storage

        mock_poller = MagicMock()
        mock_poller.result.return_value = [MagicMock(status="Succeeded", error=None)]
        mock_poller.details.total_characters_charged = 1000
        mock_client = MagicMock()
        mock_client.begin_translation.return_value = mock_poller

        bind_contextvars(message_id=999999, feature="chat", user_id=user.id)
        try:
            with (
                patch(
                    "azure.ai.translation.document.DocumentTranslationClient",
                    return_value=mock_client,
                ),
                patch("builtins.open", return_value=io.BytesIO(b"fake content")),
                patch("threading.Thread") as mock_thread,
            ):
                result = translate_file_next(
                    file_path="/tmp/fake.docx",
                    target_language="fr",
                    message_id=str(out_message.id),
                    chat_id="",
                    user_id=user.id,
                )
        finally:
            unbind_contextvars("message_id", "message_next_id", "user_id", "feature")

        assert result["success"] is True
        assert mock_thread.call_count == 2

        cost = Cost.objects.order_by("-id").first()
        assert cost is not None
        assert cost.message_next_id == out_message.id
        assert cost.message_id is None
        assert cost.feature == "translate"

    def test_azure_delete_called_in_finally_on_open_error(self, settings):
        """The finally block must delete Azure blobs even when opening the local file fails.

        input_file_path and output_file_path are assigned before open(), so the
        finally block will always have paths to clean up once DocumentTranslationClient
        has been instantiated successfully.
        """
        from chat_next.tasks import translate_file_next

        settings.AZURE_AI_SERVICES_ENDPOINT = "https://api.example.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_CONTAINER = "testcontainer"
        settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT = "temp/translation/in"
        settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT = "temp/translation/out"

        mock_azure_storage = MagicMock()
        settings.AZURE_STORAGE = mock_azure_storage

        # DocumentTranslationClient succeeds so that input/output paths are set,
        # then open() fails because the local file doesn't exist.
        with (
            patch("azure.ai.translation.document.DocumentTranslationClient"),
            patch("threading.Thread") as mock_thread,
        ):
            result = translate_file_next(
                file_path="/tmp/nonexistent_fake.docx",
                target_language="fr",
                message_id="fake-id",
                chat_id="",
            )

        # Task fails because the file doesn't exist
        assert result["success"] is False
        # Both input_file_path and output_file_path are set before open() runs,
        # so two cleanup threads must be spawned.
        assert mock_thread.call_count == 2

    def test_azure_delete_called_in_finally_on_translation_failure(self, settings):
        """The finally block must delete Azure blobs even when the translation API fails."""
        import io

        from chat_next.tasks import translate_file_next

        settings.AZURE_AI_SERVICES_ENDPOINT = "https://api.example.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_CONTAINER = "testcontainer"
        settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT = "temp/translation/in"
        settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT = "temp/translation/out"

        mock_azure_storage = MagicMock()
        settings.AZURE_STORAGE = mock_azure_storage

        mock_poller = MagicMock()
        mock_poller.result.side_effect = Exception("Translation API failed")
        mock_client = MagicMock()
        mock_client.begin_translation.return_value = mock_poller

        with (
            patch(
                "azure.ai.translation.document.DocumentTranslationClient",
                return_value=mock_client,
            ),
            patch("builtins.open", return_value=io.BytesIO(b"fake content")),
            patch("threading.Thread") as mock_thread,
        ):
            result = translate_file_next(
                file_path="/tmp/fake.docx",
                target_language="fr",
                message_id="fake-id",
                chat_id="",
            )

        assert result["success"] is False
        # Both input_file_path and output_file_path are set, so two cleanup threads are spawned
        assert mock_thread.call_count == 2
