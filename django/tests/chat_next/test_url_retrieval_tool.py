import asyncio
import threading
import time

from django.core.files.base import ContentFile

import chat_next._tools.url_retrieval as url_tool
import pytest
from asgiref.sync import sync_to_async
from chat_next._tools.base import ToolContext
from chat_next._tools.url_retrieval import retrieve_url_content
from chat_next.message_attachments import ensure_message_attachment_for_document
from chat_next.models import Chat, ChatFile, Message
from structlog.contextvars import bind_contextvars, unbind_contextvars

from librarian.models import Document, SavedFile


@pytest.fixture(autouse=True)
def fast_chat_file_waits(monkeypatch):
    monkeypatch.setattr(url_tool, "CHAT_FILE_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(url_tool, "CHAT_FILE_POLL_INTERVAL_SECONDS", 0.05)


@pytest.mark.django_db
def test_ensure_message_attachment_for_document_is_idempotent(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    message = Message.objects.create(chat=chat, text="", is_bot=True)
    saved_file = SavedFile.objects.create(
        file=ContentFile(b"ready", name="ready.txt"),
        content_type="text/plain",
    )
    document = Document.objects.create(
        data_source=chat.data_source,
        saved_file=saved_file,
        filename="ready.txt",
        status="SUCCESS",
    )

    chat_file_a, created_a = ensure_message_attachment_for_document(message, document)
    chat_file_b, created_b = ensure_message_attachment_for_document(message, document)

    assert created_a is True
    assert created_b is False
    assert chat_file_a.id == chat_file_b.id
    assert ChatFile.objects.filter(message=message, document=document).count() == 1


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_retrieve_url_blocked_domain_returns_error(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["justice.gc.ca"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)

    context = ToolContext(user=user, chat=chat, extra={"responses_client": object()})
    result = await retrieve_url_content({"url": "https://example.com"}, context)

    assert "error" in result
    assert "Otto" in result["error"]  # bad_url helper text


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_manual_invocation_returns_summary(all_apps_user, settings, monkeypatch):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="",
        is_bot=True,
    )

    bind_contextvars(message_next_id=message.id)
    try:
        context = ToolContext(user=user, chat=chat)
        result = await retrieve_url_content({"url": "https://example.com"}, context)
    finally:
        unbind_contextvars("message_next_id")

    assert isinstance(result, str)
    assert "example.com" in result
    assert "document_id" in result


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_retrieve_url_defaults_to_chat_library(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="",
        is_bot=True,
    )

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    bind_contextvars(message_next_id=message.id)
    try:
        context = ToolContext(
            user=user,
            chat=chat,
            extra={"responses_client": object()},
        )
        result = await retrieve_url_content(
            {"url": "https://example.com/report.pdf"},
            context,
        )
    finally:
        unbind_contextvars("message_next_id")

    assert "documents" in result
    doc_id = result["documents"][0]["document_id"]
    document = await sync_to_async(Document.objects.get)(id=doc_id)
    assert document.url == "https://example.com/report.pdf"
    assert document.data_source_id == chat.data_source.id
    assert result["library"]["data_source_id"] == chat.data_source.id
    files_count = await sync_to_async(
        lambda: ChatFile.objects.filter(message_id=message.id).count()
    )()
    assert files_count == 0


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_existing_document_with_saved_file_creates_chat_file(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="",
        is_bot=True,
    )

    saved_file = await sync_to_async(SavedFile.objects.create)(
        file=ContentFile(b"ready", name="ready.txt"),
        content_type="text/plain",
    )
    existing_doc = await sync_to_async(Document.objects.create)(
        data_source=chat.data_source,
        url="https://example.com/ready",
        saved_file=saved_file,
        filename="ready.txt",
        status="SUCCESS",
    )

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    bind_contextvars(message_next_id=message.id)
    try:
        context = ToolContext(
            user=user,
            chat=chat,
            extra={"responses_client": object()},
        )
        await retrieve_url_content({"url": "https://example.com/ready"}, context)
    finally:
        unbind_contextvars("message_next_id")

    await asyncio.sleep(0.2)
    chat_file = await sync_to_async(
        lambda: ChatFile.objects.filter(message_id=message.id).first()
    )()
    assert chat_file is not None
    assert chat_file.document_id == existing_doc.id


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_chat_file_created_after_saved_file_ready(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="",
        is_bot=True,
    )

    def delayed_process(self):
        def _complete():
            time.sleep(0.1)
            saved_file = SavedFile.objects.create(
                file=ContentFile(b"ready", name="defer.txt"),
                content_type="text/plain",
            )
            self.saved_file = saved_file
            self.filename = "defer.txt"
            self.status = "SUCCESS"
            self.save(update_fields=["saved_file", "filename", "status"])

        thread = threading.Thread(target=_complete, daemon=True)
        thread.start()

    monkeypatch.setattr("librarian.models.Document.process", delayed_process)

    bind_contextvars(message_next_id=message.id)
    try:
        context = ToolContext(
            user=user,
            chat=chat,
            extra={"responses_client": object()},
        )
        result = await retrieve_url_content(
            {"url": "https://example.com/defer"}, context
        )
    finally:
        unbind_contextvars("message_next_id")

    doc_id = result["documents"][0]["document_id"]

    await asyncio.sleep(0.3)

    chat_file = await sync_to_async(
        lambda: ChatFile.objects.filter(message_id=message.id).first()
    )()
    assert chat_file is not None
    assert chat_file.document_id == doc_id


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_retrieve_url_with_selector_persists_value(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    chat = await sync_to_async(Chat.objects.create)(user=user)
    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    context = ToolContext(
        user=user,
        chat=chat,
        extra={"responses_client": object()},
    )
    await retrieve_url_content(
        {
            "url": "https://example.com",
            "selector": "main article",
        },
        context,
    )

    doc = await sync_to_async(Document.objects.get)(
        data_source=chat.data_source,
        url="https://example.com",
    )
    assert doc.selector == "main article"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_retrieve_url_requires_destination_when_not_in_chat(
    all_apps_user, settings, monkeypatch
):
    settings.ALLOWED_FETCH_URLS = ["example.com"]
    user = await sync_to_async(all_apps_user)()
    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    context = ToolContext(user=user, extra={"responses_client": object()})
    result = await retrieve_url_content({"url": "https://example.com"}, context)

    assert (
        result["error"] == "Chat context is required to fetch URLs into chat uploads."
    )
