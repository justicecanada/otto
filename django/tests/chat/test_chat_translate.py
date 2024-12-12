import os
import tempfile
from decimal import Decimal
from unittest import mock

import pytest

from chat.tasks import translate_file


@pytest.mark.django_db
def test_translate_file():
    # Create a temporary file with content
    with tempfile.NamedTemporaryFile(
        delete=False, mode="w", suffix=".txt"
    ) as temp_file:
        temp_file.write("Hello, this is a test file.")
        file_path = temp_file.name

    # Mock settings
    settings = mock.MagicMock()
    settings.AZURE_COGNITIVE_SERVICE_ENDPOINT = "https://example.com"
    settings.AZURE_COGNITIVE_SERVICE_KEY = "fake_key"
    settings.AZURE_ACCOUNT_NAME = "fake_account"
    settings.AZURE_CONTAINER = "fake_container"
    settings.AZURE_STORAGE = mock.MagicMock()

    # Mock Azure translation client
    translation_client = mock.MagicMock()
    poller = mock.MagicMock()
    poller.result.return_value = [mock.MagicMock(status="Succeeded", error=None)]
    poller.details.total_characters_charged = 1000
    translation_client.begin_translation.return_value = poller

    # Mock contextvars
    get_contextvars = mock.MagicMock()
    get_contextvars.return_value = {"message_id": 1}

    # Mock Cost model
    mock_cost_create = mock.MagicMock()

    # Mock azure_delete function
    azure_delete = mock.MagicMock()
    ChatFile = mock.MagicMock()
    Message = mock.MagicMock()
    Message.objects.get.return_value = Message

    with (
        mock.patch("chat.tasks.settings", settings),
        mock.patch(
            "chat.tasks.DocumentTranslationClient", return_value=translation_client
        ),
        mock.patch("chat.models.ChatFile", ChatFile),
        mock.patch("chat.models.Message", Message),
        mock.patch("chat.tasks.get_contextvars", get_contextvars),
        mock.patch("otto.models.Cost.objects.create", mock_cost_create),
        mock.patch("chat.tasks.azure_delete", azure_delete),
    ):

        translate_file(file_path, "fr")

        # Assertions
        settings.AZURE_STORAGE.save.assert_called_once()
        translation_client.begin_translation.assert_called_once()
        ChatFile.objects.create.assert_called_once()
        mock_cost_create.assert_called_once_with(
            cost_type=mock.ANY,
            count=1000,
            usd_cost=Decimal("0.015000"),
            feature=None,
            request_id=None,
            user=None,
            message=None,
            document=None,
            law=None,
        )
        azure_delete.assert_called()
        # Clean up the temporary file
    os.remove(file_path)
