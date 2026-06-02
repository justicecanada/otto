"""
Tests for the library_status view in chat_next that returns document processing status.
"""

from django.urls import reverse

import pytest
from chat_next.models import Chat, ChatFile, Message


@pytest.mark.django_db
def test_library_status_no_docs_returns_polling(client, all_apps_user, monkeypatch):
    """When a message has files without linked documents yet, library_status
    should return a polling status that will check again.
    """
    from librarian.models import Document

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    # Create a ChatFile without a document (simulates file just uploaded)
    ChatFile.objects.create(
        message=user_message,
        filename="test.pdf",
        saved_file=None,  # No saved file
        document=None,  # No document linked yet
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    # Should show file cards and have polling trigger (file without doc = processing)
    assert "message-files-" in content
    assert "hx-get" in content  # Should have polling trigger


@pytest.mark.django_db
def test_library_status_processing_returns_progress(client, all_apps_user, monkeypatch):
    """When documents are still processing, library_status should show progress with polling."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    # Create library and data source for chat
    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    # Create a ChatFile with a processing document
    doc = Document.objects.create(
        data_source=data_source,
        filename="test.pdf",
        status="PROCESSING",
    )
    doc.chat_next_messages.add(user_message)
    ChatFile.objects.create(
        message=user_message,
        filename="test.pdf",
        saved_file=None,
        document=doc,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    # Should show file cards with spinner icon and have polling trigger
    assert "message-files-" in content
    assert "icn-spinner" in content  # Processing spinner icon
    assert "hx-get" in content  # Should continue polling


@pytest.mark.django_db
def test_library_status_complete_returns_no_polling(client, all_apps_user, monkeypatch):
    """When all documents are processed, library_status should show status without polling."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    # Create library and data source for chat
    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    # Create a ChatFile with a successful document
    doc = Document.objects.create(
        data_source=data_source,
        filename="test.pdf",
        status="SUCCESS",
    )
    doc.chat_next_messages.add(user_message)
    ChatFile.objects.create(
        message=user_message,
        filename="test.pdf",
        saved_file=None,
        document=doc,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    # Should show file cards with success icon
    assert "message-files-" in content
    assert "check-circle-fill" in content  # Success icon
    assert f'data-library-status-url="{url}"' in content
    assert f'hx-get="{url}"' not in content
    assert 'hx-trigger="load delay:1s, file-status-refresh delay:1s"' not in content


@pytest.mark.django_db
def test_library_status_paused_returns_warning_without_polling(
    client, all_apps_user, monkeypatch
):
    """Paused uploads should show summary guidance without continuing polling."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    doc = Document.objects.create(
        data_source=data_source,
        filename="large.pdf",
        status="PAUSED",
    )
    doc.chat_next_messages.add(user_message)
    ChatFile.objects.create(
        message=user_message,
        filename="large.pdf",
        saved_file=None,
        document=doc,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "message-files-" in content
    assert f'data-library-status-url="{url}"' in content
    assert "bi bi-pause-circle-fill text-warning me-1" in content
    assert "upload-status-note upload-status-note-paused" in content
    assert "1 large file is paused pending embedding approval." in content
    assert "Click the document icon to continue." in content
    assert f'data-library-status-url="{url}"' in content
    assert f'hx-get="{url}"' not in content
    assert 'hx-trigger="load delay:1s, file-status-refresh delay:1s"' not in content


@pytest.mark.django_db
def test_library_status_mixed_complete_and_paused_shows_counts_and_warning(
    client, all_apps_user, monkeypatch
):
    """Settled mixed states should show completed and paused summaries together."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    success_doc = Document.objects.create(
        data_source=data_source,
        filename="done.pdf",
        status="SUCCESS",
    )
    paused_doc = Document.objects.create(
        data_source=data_source,
        filename="large.pdf",
        status="PAUSED",
    )
    success_doc.chat_next_messages.add(user_message)
    paused_doc.chat_next_messages.add(user_message)
    ChatFile.objects.create(
        message=user_message,
        filename="done.pdf",
        saved_file=None,
        document=success_doc,
    )
    ChatFile.objects.create(
        message=user_message,
        filename="large.pdf",
        saved_file=None,
        document=paused_doc,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "check-circle-fill text-success me-1" in content
    assert "bi bi-pause-circle-fill text-warning me-1" in content
    assert "completed" in content
    assert "paused" in content
    assert "1 large file is paused pending embedding approval." in content
    assert "Click the document icon to continue." in content
    assert f'data-library-status-url="{url}"' in content
    assert f'hx-get="{url}"' not in content
    assert 'hx-trigger="load delay:1s, file-status-refresh delay:1s"' not in content


@pytest.mark.django_db
def test_library_status_processing_and_paused_keeps_polling_and_warning(
    client, all_apps_user, monkeypatch
):
    """Paused files should still be surfaced while other uploads are processing."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    processing_doc = Document.objects.create(
        data_source=data_source,
        filename="processing.pdf",
        status="PROCESSING",
    )
    paused_doc = Document.objects.create(
        data_source=data_source,
        filename="large.pdf",
        status="PAUSED",
    )
    processing_doc.chat_next_messages.add(user_message)
    paused_doc.chat_next_messages.add(user_message)
    ChatFile.objects.create(
        message=user_message,
        filename="processing.pdf",
        saved_file=None,
        document=processing_doc,
    )
    ChatFile.objects.create(
        message=user_message,
        filename="large.pdf",
        saved_file=None,
        document=paused_doc,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "Adding to library... (1/2)" in content
    assert "Q&amp;A library" not in content
    assert "bi bi-pause-circle-fill text-warning me-1" in content
    assert "1 large file is paused pending embedding approval." in content
    assert "Click the document icon to continue." in content
    assert f'data-library-status-url="{url}"' in content
    assert f'hx-get="{url}"' in content
    assert 'hx-trigger="load delay:1s, file-status-refresh delay:1s"' in content


@pytest.mark.django_db
def test_library_status_orders_nested_extractions_by_display_path(
    client, all_apps_user, monkeypatch
):
    """Nested extracted files should render in deterministic path order."""
    from librarian.models import DataSource, Document, Library

    monkeypatch.setattr(Document, "process", lambda *args, **kwargs: None)

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(chat=chat, text="", is_bot=False)

    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(library=library, name="Chat files")
    chat.data_source = data_source
    chat.save()

    container_doc = Document.objects.create(
        data_source=data_source,
        filename="outer.zip",
        file_path="outer.zip",
        is_container=True,
        status="SUCCESS",
    )
    container_doc.chat_next_messages.add(user_message)

    nested_b = Document.objects.create(
        data_source=data_source,
        filename="b.txt",
        file_path="outer.zip/folder/b.txt",
        parent_document=container_doc,
        status="SUCCESS",
    )
    nested_a = Document.objects.create(
        data_source=data_source,
        filename="a.txt",
        file_path="outer.zip/folder/a.txt",
        parent_document=container_doc,
        status="SUCCESS",
    )

    # Create attachments out of order to verify view/model sorting fixes the render.
    ChatFile.objects.create(
        message=user_message,
        filename="outer.zip/folder/b.txt",
        saved_file=None,
        document=nested_b,
    )
    ChatFile.objects.create(
        message=user_message,
        filename="outer.zip/folder/a.txt",
        saved_file=None,
        document=nested_a,
    )

    url = reverse("chat_next:library_status", args=[user_message.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    a_index = content.index("outer.zip/folder/a.txt")
    b_index = content.index("outer.zip/folder/b.txt")
    assert a_index < b_index
