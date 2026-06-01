"""Tests for document-processing tools and range-aware chunking."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from chat_next._tools.document_processing import (
    DEFAULT_PROMPT_DOCUMENT_CHUNK_TARGET_CHARS,
)
from chat_next.models import Chat, ChatFile, Message
from chat_next.tasks import prompt_document_next
from chat_next.tools import (
    TOOL_REGISTRY,
    ToolContext,
    plan_document_chunks,
    prompt_document_chunks,
    prompt_document_ranges,
    prompt_documents,
)

from librarian.models import Document

SUMMARY_PROMPT = (
    "Summarize the document in markdown format with a nice structure for readability. "
    "Do not introduce the summary with filler phrases, and do not make unsupported inferences."
)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_skips_bot_generated_but_allows_user_file_named_output(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source

        # User-origin document (intentionally ends with _output to prove no filename brittleness)
        user_doc = Document.objects.create(
            data_source=ds,
            filename="source_output.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        user_msg = Message.objects.create(chat=chat, text="Summarize", is_bot=False)
        ChatFile.objects.create(
            message=user_msg,
            filename=user_doc.filename,
            document=user_doc,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        # Bot-generated document (should be skipped by default)
        generated_doc = Document.objects.create(
            data_source=ds,
            filename="analysis.md",
            extracted_text="Generated analysis text.",
            provenance=Document.PROVENANCE_GENERATED_OUTPUT,
        )
        bot_msg = Message.objects.create(chat=chat, text="Done", is_bot=True)
        ChatFile.objects.create(
            message=bot_msg,
            filename=generated_doc.filename,
            document=generated_doc,
            content_type="text/markdown",
        )

        return chat, user_doc, generated_doc

    chat, user_doc, generated_doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output_output.md",
                "document_id": 12345,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [user_doc.id, generated_doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert mock_apply.call_count == 1

    completed = [f for f in result["files"] if f.get("status") == "completed"]
    skipped = [f for f in result["files"] if f.get("status") == "skipped"]

    assert len(completed) == 1
    assert completed[0]["document_id"] == user_doc.id

    assert len(skipped) == 1
    assert skipped[0]["document_id"] == generated_doc.id
    assert "recursive processing" in skipped[0]["error"].lower()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_can_include_generated_outputs_when_explicitly_requested(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source

        generated_doc = Document.objects.create(
            data_source=ds,
            filename="generated-summary.md",
            extracted_text="Generated summary text.",
            provenance=Document.PROVENANCE_GENERATED_OUTPUT,
        )
        bot_msg = Message.objects.create(chat=chat, text="Done", is_bot=True)
        ChatFile.objects.create(
            message=bot_msg,
            filename=generated_doc.filename,
            document=generated_doc,
            content_type="text/markdown",
        )

        return chat, generated_doc

    chat, generated_doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "generated-summary-output.md",
                "document_id": 67890,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [generated_doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "include_generated_outputs": True,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert mock_apply.call_count == 1
    assert result["files"][0]["status"] == "completed"
    assert result["files"][0]["document_id"] == generated_doc.id


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_allows_retrieved_url_source_documents(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source

        retrieved_doc = Document.objects.create(
            data_source=ds,
            url="https://example.com/policy",
            filename="policy.html",
            extracted_text="Source webpage text.",
            provenance=Document.PROVENANCE_URL_RETRIEVAL,
        )
        bot_msg = Message.objects.create(chat=chat, text="Fetched", is_bot=True)
        retrieved_doc.chat_next_messages.add(bot_msg)
        ChatFile.objects.create(
            message=bot_msg,
            filename=retrieved_doc.filename,
            document=retrieved_doc,
            content_type="text/html",
        )

        return chat, retrieved_doc

    chat, retrieved_doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "policy_output.md",
                "document_id": 24680,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [retrieved_doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert mock_apply.call_count == 1
    assert result["files"][0]["status"] == "completed"
    assert result["files"][0]["document_id"] == retrieved_doc.id


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_skips_prior_generated_summaries_but_keeps_retrieved_sources(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source

        # Simulate a source webpage fetched into the chat: bot attachment + chat_next_messages link.
        source_doc = Document.objects.create(
            data_source=ds,
            url="https://example.com/source",
            filename="source.html",
            extracted_text="Primary webpage content.",
            provenance=Document.PROVENANCE_URL_RETRIEVAL,
        )
        fetch_msg = Message.objects.create(chat=chat, text="Fetched", is_bot=True)
        source_doc.chat_next_messages.add(fetch_msg)
        ChatFile.objects.create(
            message=fetch_msg,
            filename=source_doc.filename,
            document=source_doc,
            content_type="text/html",
        )

        # Simulate a prior prompt_documents output in the same chat files library.
        generated_doc = Document.objects.create(
            data_source=ds,
            filename="source_output.md",
            extracted_text="Previously generated summary.",
            provenance=Document.PROVENANCE_GENERATED_OUTPUT,
        )
        summary_msg = Message.objects.create(chat=chat, text="Done", is_bot=True)
        ChatFile.objects.create(
            message=summary_msg,
            filename=generated_doc.filename,
            document=generated_doc,
            content_type="text/markdown",
        )

        return chat, source_doc, generated_doc

    chat, source_doc, generated_doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output_v2.md",
                "document_id": 13579,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [source_doc.id, generated_doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert mock_apply.call_count == 1

    completed = [f for f in result["files"] if f.get("status") == "completed"]
    skipped = [f for f in result["files"] if f.get("status") == "skipped"]

    assert len(completed) == 1
    assert completed[0]["document_id"] == source_doc.id

    assert len(skipped) == 1
    assert skipped[0]["document_id"] == generated_doc.id
    assert "recursive processing" in skipped[0]["error"].lower()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_passes_explicit_model_and_reasoning_to_task(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output.md",
                "document_id": 54321,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": "gpt-5.4-mini",
                    "reasoning_effort": "high",
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    _, kwargs = mock_apply.call_args
    assert kwargs["kwargs"]["model_name"] == "gpt-5.4-mini"
    assert kwargs["kwargs"]["reasoning_effort"] == "high"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_accepts_gpt_5_4_nano_override(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output.md",
                "document_id": 76543,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": "gpt-5.4-nano",
                    "reasoning_effort": "default",
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    _, kwargs = mock_apply.call_args
    assert kwargs["kwargs"]["model_name"] == "gpt-5.4-nano"
    assert kwargs["kwargs"]["reasoning_effort"] == "none"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_defaults_reasoning_to_lightest_supported_value(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        chat.settings.chat_model = "gpt-5.4-mini"
        chat.settings.save(update_fields=["chat_model"])
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output.md",
                "document_id": 54321,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": None,
                    "reasoning_effort": "default",
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    _, kwargs = mock_apply.call_args
    assert kwargs["kwargs"]["model_name"] == "gpt-5.4-mini"
    assert kwargs["kwargs"]["reasoning_effort"] == "none"


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_rejects_invalid_model_override(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    result = await prompt_documents(
        {
            "document_ids": [doc.id],
            "prompt": SUMMARY_PROMPT,
            "template_doc_id": None,
            "llm_model": "gpt-4.1-mini",
            "reasoning_effort": "default",
            "include_generated_outputs": False,
        },
        ToolContext(user=user, chat=chat),
    )

    assert "Invalid llm_model" in result["error"]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_passes_truncate_chars_and_reports_it(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="A" * 5000,
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output.md",
                "document_id": 99999,
                "truncate_chars": 1200,
                "input_truncated": True,
                "input_chars_used": 1200,
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": None,
                    "reasoning_effort": "default",
                    "truncate_chars": 1200,
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert result["processing_scope"]["truncate_chars"] == 1200
    assert "first 1200 characters" in result["processing_scope"]["user_notice"]
    assert result["files"][0]["input_truncated"] is True
    assert result["files"][0]["input_chars_used"] == 1200
    _, kwargs = mock_apply.call_args
    assert kwargs["kwargs"]["truncate_chars"] == 1200


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_rejects_non_positive_truncate_chars(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    result = await prompt_documents(
        {
            "document_ids": [doc.id],
            "prompt": SUMMARY_PROMPT,
            "template_doc_id": None,
            "llm_model": None,
            "reasoning_effort": "default",
            "truncate_chars": 0,
            "include_generated_outputs": False,
        },
        ToolContext(user=user, chat=chat),
    )

    assert "truncate_chars" in result["error"]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_requires_prompt(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    result = await prompt_documents(
        {
            "document_ids": [doc.id],
            "prompt": None,
            "template_doc_id": None,
            "llm_model": None,
            "reasoning_effort": "default",
            "truncate_chars": None,
            "include_generated_outputs": False,
        },
        ToolContext(user=user, chat=chat),
    )

    assert result["error"] == "A prompt must be provided for document processing."


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_documents_ignores_deprecated_include_output_text_flag(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Original source document text.",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "source_output.md",
                "document_id": 54321,
                "output_text": "# Salary result\n\nHighest annual salary: $251,413",
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_documents(
                {
                    "document_ids": [doc.id],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": "gpt-5.4-nano",
                    "reasoning_effort": "default",
                    "truncate_chars": None,
                    "include_generated_outputs": False,
                    "include_output_text": True,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    _, kwargs = mock_apply.call_args
    assert "include_output_text" not in kwargs["kwargs"]
    assert "output_text" not in result["files"][0]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_document_ranges_passes_range_metadata_to_task(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="hearing.pdf",
            extracted_text="<page_1>alpha beta gamma</page_1><page_2>delta epsilon</page_2>",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "hearing__chars_000000_000020__chunk_001.md",
                "document_id": 11223,
                "source_document_id": doc.id,
                "source_filename": doc.filename,
                "label": "chunk_001",
                "start_char": 0,
                "end_char": 20,
                "start_page": 1,
                "end_page": 1,
                "overlap_chars": 5,
                "model_used": "gpt-5.4-nano",
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ) as mock_apply:
            result = await prompt_document_ranges(
                {
                    "inputs": [
                        {
                            "document_id": doc.id,
                            "start_char": 0,
                            "end_char": 20,
                            "start_page": None,
                            "end_page": None,
                            "label": "chunk_001",
                            "overlap_chars": 5,
                        }
                    ],
                    "prompt": SUMMARY_PROMPT,
                    "template_doc_id": None,
                    "llm_model": "gpt-5.4-nano",
                    "reasoning_effort": "default",
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    _, kwargs = mock_apply.call_args
    assert kwargs["kwargs"]["start_char"] == 0
    assert kwargs["kwargs"]["end_char"] == 20
    assert kwargs["kwargs"]["range_label"] == "chunk_001"
    assert kwargs["kwargs"]["overlap_chars"] == 5
    assert result["files"][0]["label"] == "chunk_001"
    assert result["files"][0]["start_char"] == 0
    assert result["files"][0]["end_char"] == 20


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_document_ranges_rejects_mixed_page_and_char_ranges(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="source.docx",
            extracted_text="Document text",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    result = await prompt_document_ranges(
        {
            "inputs": [
                {
                    "document_id": doc.id,
                    "start_char": 0,
                    "end_char": 100,
                    "start_page": 1,
                    "end_page": None,
                    "label": None,
                    "overlap_chars": 0,
                }
            ],
            "prompt": SUMMARY_PROMPT,
            "template_doc_id": None,
            "llm_model": None,
            "reasoning_effort": "default",
            "include_generated_outputs": False,
        },
        ToolContext(user=user, chat=chat),
    )

    assert "cannot mix character and page range" in result["error"]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_document_chunks_plans_then_processes_char_only_ranges(
    all_apps_user,
):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="mega-report.pdf",
            extracted_text=(
                "<page_1>" + ("A" * 90) + "</page_1>"
                "<page_2>" + ("B" * 90) + "</page_2>"
                "<page_3>" + ("C" * 90) + "</page_3>"
            ),
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        def __init__(self, index):
            self.id = f"fake-task-{index}"
            self._index = index

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": f"mega-report__chunk_{self._index:03d}.md",
                "document_id": 22000 + self._index,
                "source_document_id": doc.id,
                "source_filename": doc.filename,
                "label": f"chunk_{self._index:03d}",
                "start_char": (self._index - 1) * 100,
                "end_char": (self._index - 1) * 100 + 120,
                "start_page": 1,
                "end_page": 2,
                "overlap_chars": 20,
                "model_used": "gpt-5.4-nano",
            }

    task_counter = {"value": 0}

    def _fake_apply_async(*args, **kwargs):
        task_counter["value"] += 1
        return _FakeAsyncResult(task_counter["value"])

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            side_effect=_fake_apply_async,
        ) as mock_apply:
            result = await prompt_document_chunks(
                {
                    "document_id": doc.id,
                    "prompt": SUMMARY_PROMPT,
                    "target_chars": 120,
                    "overlap_chars": 20,
                    "prefer_page_boundaries": True,
                    "template_doc_id": None,
                    "llm_model": "gpt-5.4-nano",
                    "reasoning_effort": "default",
                    "include_generated_outputs": False,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert mock_apply.call_count >= 2
    for call in mock_apply.call_args_list:
        task_kwargs = call.kwargs["kwargs"]
        assert task_kwargs["start_char"] is not None
        assert task_kwargs["end_char"] is not None
        assert task_kwargs["start_page"] is None
        assert task_kwargs["end_page"] is None
    assert result["processing_scope"]["document_id"] == doc.id
    assert result["processing_scope"]["range_count"] == mock_apply.call_count
    assert "intermediate chunk artifacts only" in result["message"]
    assert [file_info["label"] for file_info in result["files"]] == [
        f"chunk_{index:03d}" for index in range(1, mock_apply.call_count + 1)
    ]
    assert result["recommended_followup"]["tool"] == "get_document_text"
    assert result["recommended_followup"]["arguments"]["document_ids"] == [
        file_info["output_document_id"] for file_info in result["files"]
    ]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_prompt_document_chunks_defaults_to_large_target_chars(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="large-source.pdf",
            extracted_text="<page_1>" + ("A" * 2000) + "</page_1>",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    class _FakeAsyncResult:
        id = "fake-task-id"

        def get(self, timeout=None):
            return {
                "success": True,
                "filename": "large-source__chunk_001.md",
                "document_id": 87654,
                "source_document_id": doc.id,
                "source_filename": doc.filename,
                "label": "chunk_001",
                "start_char": 0,
                "end_char": 2000,
                "start_page": 1,
                "end_page": 1,
                "overlap_chars": 0,
                "model_used": "gpt-5.4-nano",
            }

    with patch(
        "structlog.contextvars.get_contextvars",
        return_value={"message_next_id": "fake-id"},
    ):
        with patch(
            "chat_next.tasks.prompt_document_next.apply_async",
            return_value=_FakeAsyncResult(),
        ):
            result = await prompt_document_chunks(
                {
                    "document_id": doc.id,
                    "prompt": SUMMARY_PROMPT,
                },
                ToolContext(user=user, chat=chat),
            )

    assert result["success"] is True
    assert (
        result["processing_scope"]["target_chars"]
        == DEFAULT_PROMPT_DOCUMENT_CHUNK_TARGET_CHARS
    )


@pytest.mark.django_db
def test_document_processing_tool_schemas_keep_openai_strict_required_arrays():
    prompt_documents_schema = TOOL_REGISTRY.get("prompt_documents").to_api_schema()[
        "parameters"
    ]
    prompt_document_chunks_schema = TOOL_REGISTRY.get(
        "prompt_document_chunks"
    ).to_api_schema()["parameters"]

    assert sorted(prompt_documents_schema["required"]) == sorted(
        prompt_documents_schema["properties"].keys()
    )
    assert sorted(prompt_document_chunks_schema["required"]) == sorted(
        prompt_document_chunks_schema["properties"].keys()
    )


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_plan_document_chunks_returns_overlap_aware_ranges(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def setup_data():
        chat = Chat.objects.create(user=user)
        ds = chat.data_source
        doc = Document.objects.create(
            data_source=ds,
            filename="long-report.pdf",
            extracted_text=(
                "<page_1>" + ("A" * 90) + "</page_1>"
                "<page_2>" + ("B" * 90) + "</page_2>"
                "<page_3>" + ("C" * 90) + "</page_3>"
            ),
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        return chat, doc

    chat, doc = await setup_data()

    result = await plan_document_chunks(
        {
            "document_id": doc.id,
            "target_chars": 120,
            "overlap_chars": 20,
            "prefer_page_boundaries": True,
        },
        ToolContext(user=user, chat=chat),
    )

    assert result["document_id"] == doc.id
    assert result["estimated_chunk_count"] >= 2
    assert result["ranges"][0]["label"] == "chunk_001"
    assert result["ranges"][1]["start_char"] < result["ranges"][0]["end_char"]
    assert (
        result["prompt_document_ranges_inputs"][0]["start_char"]
        == result["ranges"][0]["start_char"]
    )
    assert (
        result["prompt_document_ranges_inputs"][0]["end_char"]
        == result["ranges"][0]["end_char"]
    )
    assert "start_page" not in result["prompt_document_ranges_inputs"][0]
    assert "end_page" not in result["prompt_document_ranges_inputs"][0]


@pytest.mark.django_db
def test_prompt_document_next_writes_range_front_matter_and_parent_link(
    all_apps_user, monkeypatch
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    out_message = Message.objects.create(chat=chat, text="Working", is_bot=True)
    source_doc = Document.objects.create(
        data_source=chat.data_source,
        filename="investigation.pdf",
        extracted_text=(
            "<page_1>abcdefghijABCDEFGHIJ</page_1><page_2>klmnopqrstKLMNOPQRST</page_2>"
        ),
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )

    class _FakeLLM:
        deployment_name = "fake-deployment"
        reasoning = False
        model_id = "gpt-5.4-nano"
        max_tokens_in = 272000

    class _FakeResponse:
        def __init__(self):
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(content="# Chunk findings\n\nAll good.")
                )
            ]
            self.usage = None

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: _FakeResponse())
            )

    monkeypatch.setattr("chat_next.tasks.get_openai_client", lambda: _FakeClient())
    monkeypatch.setattr("chat_next._llm.models.get_model", lambda model_id: _FakeLLM())
    monkeypatch.setattr(Document, "process", lambda self: None)

    result = prompt_document_next(
        document_id=source_doc.id,
        prompt_text=SUMMARY_PROMPT,
        message_id=str(out_message.id),
        chat_id=str(chat.id),
        model_name="gpt-5.4-nano",
        original_filename=source_doc.filename,
        start_char=0,
        end_char=20,
        range_label="chunk_001",
        overlap_chars=5,
    )

    assert result["success"] is True
    assert result["filename"] == "investigation__chunk_001.md"
    output_doc = Document.objects.get(id=result["document_id"])
    assert output_doc.parent_document_id == source_doc.id
    with output_doc.saved_file.file.open("rb") as fh:
        text = fh.read().decode("utf-8")
    assert text.startswith("```text\nsource_document_id: ")
    assert "source_filename: investigation.pdf" in text
    assert "label: chunk_001" in text
    assert "start_char: 0" in text
    assert "end_char: 20" in text
    assert "llm_model: gpt-5.4-nano" in text
    assert "source_title:" not in text
    assert "overlap_used:" not in text
