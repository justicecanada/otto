import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from chat.models import Chat, ChatFile, ChatOptions, Message
from librarian.models import SavedFile


@pytest.mark.django_db
def test_translate_glossary_upload_and_usage(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat for glossary upload test
    response = client.get(reverse("chat:translate"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Test invalid glossary upload: wrong format (should trigger error message)
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]),
        {
            "translate_glossary": SimpleUploadedFile(
                "glossary.csv", b"Hello", content_type="text/csv"
            ),
        },
        follow=True,
    )
    assert response.status_code == 200
    # Check for error message in Django messages framework
    messages = list(response.context.get("messages", []))
    assert any("Glossary file is invalid" in str(m) for m in messages)

    # Create a valid glossary file
    glossary_content = b"Hello,Bonjour\nWorld,Monde"
    glossary_file = SimpleUploadedFile(
        "glossary.csv", glossary_content, content_type="text/csv"
    )
    saved_file = SavedFile.objects.create(
        file=glossary_file,
        content_type="text/csv",
    )

    # Test all three models
    models = ["gpt", "azure_text", "azure_file"]
    for model in models:
        # Create chat and set model
        response = client.get(reverse("chat:translate"), follow=True)
        chat = Chat.objects.filter(user=user).order_by("-created_at").first()
        chat.options.translate_model = model
        chat.options.translate_glossary = saved_file
        chat.options.save()

        # Translate text
        message = Message.objects.create(
            chat=chat, text="Hello World", mode="translate"
        )
        message = Message.objects.create(
            chat=chat, mode="translate", is_bot=True, parent=message
        )
        response = client.get(reverse("chat:chat_response", args=[message.id]))
        assert response.status_code == 200

        # Translate file (simulate file upload)
        file_content = b"Hello World"
        test_file = SimpleUploadedFile(
            "test.txt", file_content, content_type="text/plain"
        )
        file_saved = SavedFile.objects.create(file=test_file, content_type="text/plain")
        chat.options.translate_glossary = saved_file
        chat.options.save()
        # Simulate file translation endpoint
        file_message = Message.objects.create(chat=chat, mode="translate")
        ChatFile.objects.create(
            message=file_message, filename=test_file.name, saved_file=file_saved
        )
        file_message = Message.objects.create(
            chat=chat, mode="translate", is_bot=True, parent=file_message
        )
        response = client.get(reverse("chat:chat_response", args=[file_message.id]))
        assert response.status_code == 200
