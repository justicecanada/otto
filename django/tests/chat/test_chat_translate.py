import os
import tempfile
from decimal import Decimal
from unittest import mock

from django.urls import reverse

import pytest
import pytest_asyncio

from chat._views.load_test import exhaust_streaming_response
from chat.models import Chat, Message
from chat.tasks import translate_file

pytest_plugins = ("pytest_asyncio",)


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


@pytest.mark.django_db(transaction=True)
def test_translate_text_with_gpt(client, all_apps_user):
    """Test GPT text translation through the translate_response function."""

    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:translate"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.translate_model = "gpt"
    chat.options.save()

    # Test chat_response with Translate mode
    message = Message.objects.create(chat=chat, text="Hello", mode="translate")
    message = Message.objects.create(
        chat=chat, mode="translate", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[message.id]))
    assert response.status_code == 200
    content, _ = exhaust_streaming_response(response)
    assert (
        "Bonjour" in content
        or "Salut" in content
        or "Coucou" in content
        or "Allo" in content
    )


@pytest_asyncio.fixture(scope="session")
@pytest.mark.django_db(transaction=True)
def test_translate_text_with_azure(client, all_apps_user):
    """Test Azure text translation through the translate_response function."""

    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:translate"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.translate_model = "azure"
    chat.options.save()

    # Test chat_response with Translate mode
    message = Message.objects.create(chat=chat, text="Hello", mode="translate")
    message = Message.objects.create(
        chat=chat, mode="translate", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[message.id]))
    assert response.status_code == 200
    content, _ = exhaust_streaming_response(response)
    assert (
        "Bonjour" in content
        or "Salut" in content
        or "Coucou" in content
        or "Allo" in content
    )
