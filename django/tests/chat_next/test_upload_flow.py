from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest
from chat_next.models import Chat, ChatFile, Message

from chat._views.load_test import exhaust_streaming_response


@pytest.mark.django_db
def test_chat_message_links_uploads_to_library_documents(
    client, all_apps_user, monkeypatch
):
    """Uploading files in chat_next should immediately create/reuse librarian Documents
    in this chat's DataSource and link ChatFile.document.

    This is required so tool-based retrieval (library tools) can access uploads
    without waiting for the user to send another prompt.
    """

    # Avoid enqueueing/performing full document processing during this view-level test
    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    pdf = SimpleUploadedFile(
        "test.pdf",
        b"%PDF-1.4 test content\n%%EOF\n",
        content_type="application/pdf",
    )

    url = reverse("chat_next:chat_message", args=[chat.id])
    response = client.post(url, {"user-message": "", "chat-input_file": [pdf]})
    assert response.status_code == 200

    user_msg = Message.objects.filter(chat=chat, is_bot=False).order_by("-id").first()
    assert user_msg is not None

    chat_file = ChatFile.objects.filter(message=user_msg).first()
    assert chat_file is not None
    assert chat_file.document_id is not None

    doc = chat_file.document
    assert doc is not None
    assert doc.data_source_id == chat.data_source.id
    assert doc.saved_file_id == chat_file.saved_file_id
    assert doc.filename == chat_file.filename
    assert doc.chat_next_messages.filter(id=user_msg.id).exists()


@pytest.mark.django_db
def test_upload_only_message_does_not_call_process_file_upload(
    client, all_apps_user, monkeypatch
):
    """If a user uploads files with no prompt text, chat_next should not
    automatically send those files to the model.

    This prevents expensive context stuffing (especially for PDFs) and encourages
    tool-driven retrieval once indexing is underway.

    Note: As of the refactor removing vision/file context stuffing, files are
    NEVER passed to the model directly. They're only accessed via Q&A tools.
    This test now verifies the upload-only message gets a simple acknowledgment
    response instead of triggering an LLM call.
    """

    # Avoid enqueueing/performing full document processing during this view-level test
    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    pdf = SimpleUploadedFile(
        "test.pdf",
        b"%PDF-1.4 test content\n%%EOF\n",
        content_type="application/pdf",
    )

    url = reverse("chat_next:chat_message", args=[chat.id])
    response = client.post(url, {"user-message": "", "chat-input_file": [pdf]})
    assert response.status_code == 200

    user_msg = Message.objects.filter(chat=chat, is_bot=False).order_by("-id").first()
    assert user_msg is not None

    bot_msg = Message.objects.filter(chat=chat, is_bot=True, parent=user_msg).first()
    assert bot_msg is not None

    sse_url = reverse("chat_next:chat_response", args=[bot_msg.id])
    sse_response = client.get(sse_url)
    assert sse_response.status_code == 200

    content, _ = exhaust_streaming_response(sse_response)
    bot_msg.refresh_from_db()
    assert "Uploaded" in bot_msg.text
    assert "test.pdf" in bot_msg.text
    assert "manual embedding" in bot_msg.text
    assert "test.pdf" in content


@pytest.mark.django_db
def test_uploaded_files_include_document_ids_in_conversation(
    client, all_apps_user, monkeypatch
):
    """Uploaded files should include document_id in the conversation input
    so the model can call tools like view_library_files with correct IDs.
    """
    from chat_next._llm import build_conversation_input

    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    pdf = SimpleUploadedFile(
        "report.pdf",
        b"%PDF-1.4 test content\n%%EOF\n",
        content_type="application/pdf",
    )

    url = reverse("chat_next:chat_message", args=[chat.id])
    response = client.post(
        url, {"user-message": "Summarize this document", "chat-input_file": [pdf]}
    )
    assert response.status_code == 200

    user_msg = Message.objects.filter(chat=chat, is_bot=False).order_by("-id").first()
    chat_file = ChatFile.objects.filter(message=user_msg).first()
    assert chat_file.document_id is not None

    # Build conversation input and verify document ID is included
    items = build_conversation_input(chat)
    assert len(items) >= 1

    # Find user message content
    user_item = next((i for i in items if i.get("role") == "user"), None)
    assert user_item is not None

    content = user_item.get("content", "")
    # Document ID should be included in the message text
    assert f"document_id={chat_file.document_id}" in content
    assert "report.pdf" in content


@pytest.mark.django_db
def test_uploaded_paused_files_include_searchability_status_in_conversation(
    client, all_apps_user, monkeypatch
):
    """Paused uploads should tell the model they are not searchable via semantic search yet."""
    from chat_next._llm import build_conversation_input

    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    pdf = SimpleUploadedFile(
        "large.pdf",
        b"%PDF-1.4 test content\n%%EOF\n",
        content_type="application/pdf",
    )

    url = reverse("chat_next:chat_message", args=[chat.id])
    response = client.post(
        url, {"user-message": "Review this", "chat-input_file": [pdf]}
    )
    assert response.status_code == 200

    user_msg = Message.objects.filter(chat=chat, is_bot=False).order_by("-id").first()
    chat_file = ChatFile.objects.filter(message=user_msg).first()
    chat_file.document.status = "PAUSED"
    chat_file.document.save(update_fields=["status"])

    items = build_conversation_input(chat)
    user_item = next((i for i in items if i.get("role") == "user"), None)

    assert user_item is not None
    content = user_item.get("content", "")
    assert f"document_id={chat_file.document_id}" in content
    assert "status=PAUSED" in content
    assert "semantic_search=unavailable_until_embedded" in content


@pytest.mark.django_db
def test_chat_page_uses_stable_upload_placeholder_target(client, all_apps_user):
    """The prompt upload form should replace the in-chat upload placeholder in place."""

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="prompt-upload-form"' in content
    assert 'hx-target="#chat-upload-message"' in content
    assert 'hx-swap="outerHTML"' in content
    assert 'id="chat-upload-message-template"' in content


@pytest.mark.django_db
def test_chat_page_repositions_existing_upload_placeholder_to_bottom(
    client, all_apps_user
):
    """The prompt upload placeholder should move to the end before reuse.

    This prevents pasted/uploaded files from appearing above newer chat messages
    when an older hidden placeholder already exists in the DOM.
    """

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "messagesContainer.lastElementChild !== uploadMessage" in content
    assert "messagesContainer.appendChild(uploadMessage);" in content


@pytest.mark.django_db
def test_save_upload_returns_message_and_upload_reset_oob(
    client, all_apps_user, monkeypatch
):
    """Saving an upload should return the real message plus the upload form reset fragment."""

    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    pdf = SimpleUploadedFile(
        "stable.pdf",
        b"%PDF-1.4 stable upload\n%%EOF\n",
        content_type="application/pdf",
    )

    response = client.post(
        reverse("chat_next:upload", args=[chat.id]), {"chat-input_file": [pdf]}
    )

    assert response.status_code == 200
    user_message = (
        Message.objects.filter(chat=chat, is_bot=False).order_by("-id").first()
    )
    assert user_message is not None

    content = response.content.decode()
    assert f'id="message_{user_message.id}"' in content
    assert 'id="prompt-upload-form"' in content
    assert 'hx-swap-oob="true"' in content
