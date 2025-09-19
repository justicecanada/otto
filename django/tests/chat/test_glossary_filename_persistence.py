from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

import pytest

from chat.models import Chat, ChatOptions


@pytest.mark.django_db
def test_translate_glossary_filename_persists_on_refresh(client, all_apps_user):
    """Test that translate_glossary_filename persists when the page is refreshed."""
    user = all_apps_user()
    client.force_login(user)

    # Create a chat in translate mode
    response = client.get(reverse("chat:translate"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    assert chat.options.mode == "translate"

    # Create a valid glossary file
    glossary_content = b"Hello,Bonjour\nWorld,Monde\n"
    glossary_file = SimpleUploadedFile(
        "test_glossary.csv", glossary_content, content_type="text/csv"
    )

    # Upload the glossary file with all required form fields
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]),
        {
            "translate_glossary": glossary_file,
            "mode": "translate",
            "chat_temperature": "0.1",
            "chat_reasoning_effort": "medium",
            "qa_reasoning_effort": "minimal",
            "translate_language": "fr",
            "translate_model": "azure_custom",
            "qa_library": chat.options.qa_library.id,
            "qa_mode": "rag",
            "qa_process_mode": "combined_docs",
            "qa_scope": "all",
            "qa_topk": "5",
            "qa_source_order": "score",
            "qa_vector_ratio": "0.6",
            "qa_granularity": "768",
        },
        follow=True,
    )
    assert response.status_code == 200

    # Check that the filename was saved correctly
    chat.refresh_from_db()
    assert chat.options.translate_glossary is not None
    assert chat.options.translate_glossary_filename == "test_glossary.csv"

    # Simulate a page refresh by making a request without any file upload
    # This should trigger the form save but without a file upload
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]),
        {
            "mode": "translate",
            "translate_language": "fr",
            "chat_temperature": "0.1",
            "chat_reasoning_effort": "medium",
            "qa_reasoning_effort": "minimal",
            "translate_model": "azure_custom",
            "qa_library": chat.options.qa_library.id,
            "qa_mode": "rag",
            "qa_process_mode": "combined_docs",
            "qa_scope": "all",
            "qa_topk": "5",
            "qa_source_order": "score",
            "qa_vector_ratio": "0.6",
            "qa_granularity": "768",
        },
        follow=True,
    )
    assert response.status_code == 200

    # Check that the filename is still preserved after the "refresh"
    chat.refresh_from_db()
    assert chat.options.translate_glossary is not None
    assert chat.options.translate_glossary_filename == "test_glossary.csv"
    print(f"✅ Filename preserved: {chat.options.translate_glossary_filename}")
