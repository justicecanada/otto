"""
Tests for the chat_next transcription tool (Azure Batch Speech-to-Text).
"""

from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import sync_to_async
from chat_next.tools import (
    TOOL_REGISTRY,
    ToolContext,
    transcribe_files,
)


class TestTranscribeFilesTool:
    """Tests for the transcribe_files tool."""

    def test_tool_is_registered(self):
        """Test that transcribe_files is in the tool registry."""
        tool = TOOL_REGISTRY.get("transcribe_files")
        assert tool is not None
        assert tool.name == "transcribe_files"

    def test_tool_schema_has_required_fields(self):
        """Test that the tool schema has the correct structure."""
        tool = TOOL_REGISTRY.get("transcribe_files")
        schema = tool.to_api_schema()

        assert schema["type"] == "function"
        assert schema["name"] == "transcribe_files"
        assert "description" in schema
        assert "transcri" in schema["description"].lower()

        params = schema["parameters"]
        assert params["type"] == "object"
        assert "document_ids" in params["properties"]
        assert params["properties"]["document_ids"]["type"] == "array"
        assert "document_ids" in params["required"]
        assert "main_language" in params["properties"]
        assert "min_speakers" in params["properties"]
        assert "max_speakers" in params["properties"]

    def test_tool_category_is_transcription(self):
        """Test that the tool is in the transcription category."""
        from chat_next.models import TOOL_CATEGORY_TRANSCRIPTION

        tool = TOOL_REGISTRY.get("transcribe_files")
        assert tool.category == TOOL_CATEGORY_TRANSCRIPTION

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_document_ids(self, all_apps_user):
        """Test error when document_ids is empty."""
        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await transcribe_files({"document_ids": []}, context)
        assert "error" in result
        assert "No document IDs" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_too_many_files(self, all_apps_user):
        """Test error when more than 5 files are provided."""
        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await transcribe_files({"document_ids": [1, 2, 3, 4, 5, 6]}, context)
        assert "error" in result
        assert "Maximum of 5" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_message_context(self, all_apps_user):
        """Test error when message_next_id is not in contextvars."""
        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        with patch("structlog.contextvars.get_contextvars", return_value={}):
            result = await transcribe_files({"document_ids": [1]}, context)
        assert "error" in result
        assert "No message context" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_nonexistent_document(self, all_apps_user):
        """Test error when document ID doesn't exist."""
        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        with patch(
            "structlog.contextvars.get_contextvars",
            return_value={"message_next_id": "fake-id"},
        ):
            result = await transcribe_files({"document_ids": [999999]}, context)

        assert result["success"] is False
        assert any("not found" in f.get("error", "") for f in result["files"])

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_unsupported_file_type(self, all_apps_user):
        """Test error when file type is not supported for transcription."""
        import uuid

        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_doc():
            library = Library.objects.create(
                name=f"Test Lib {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            ds = DataSource.objects.create(library=library, name="Test DS")
            sf = SavedFile.objects.create()
            sf.file.save("test.pdf", ContentFile(b"dummy"))
            doc = Document.objects.create(
                data_source=ds,
                filename="test.pdf",
                saved_file=sf,
            )
            return doc

        doc = await create_test_doc()

        context = ToolContext(user=user)
        with patch(
            "structlog.contextvars.get_contextvars",
            return_value={"message_next_id": "fake-id"},
        ):
            result = await transcribe_files({"document_ids": [doc.id]}, context)

        assert result["success"] is False
        assert any("not supported" in f.get("error", "") for f in result["files"])


class TestTranscribeFileNextTask:
    """Tests for the transcribe_file_next Celery task."""

    def test_task_returns_error_when_no_credentials(self, settings):
        """Test task returns error when speech credentials are missing."""
        from chat_next.tasks import transcribe_file_next

        settings.AZURE_SPEECH_TO_TEXT_ENDPOINT = None
        settings.AZURE_AI_SERVICES_KEY = "test-key"

        result = transcribe_file_next(
            file_path="/tmp/fake.wav", message_id="fake-id", chat_id=""
        )
        assert result["success"] is False
        assert "credentials" in result["error"].lower()

    def test_task_returns_error_when_no_storage(self, settings):
        """Test task returns error when storage is not configured."""
        from chat_next.tasks import transcribe_file_next

        settings.AZURE_SPEECH_TO_TEXT_ENDPOINT = "https://example.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = ""
        settings.AZURE_ACCOUNT_KEY = ""
        settings.AZURE_CONTAINER = ""

        result = transcribe_file_next(
            file_path="/tmp/fake.wav", message_id="fake-id", chat_id=""
        )
        assert result["success"] is False
        assert "storage" in result["error"].lower()

    def test_cleanup_blob_called_in_finally_on_error(self, settings):
        """The finally block must spawn a cleanup thread even when the task fails mid-way."""
        from chat_next.tasks import transcribe_file_next

        settings.AZURE_SPEECH_TO_TEXT_ENDPOINT = "https://canadaeast.stt.speech.microsoft.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"

        mock_blob_service = MagicMock()
        mock_container_client = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        # BlobServiceClient succeeds (so blob_name is set), but open() fails since file
        # doesn't exist — blob_name is assigned before open(), so finally will have it.
        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ), patch("threading.Thread") as mock_thread:
            result = transcribe_file_next(
                file_path="/tmp/nonexistent_fake.wav", message_id="fake-id", chat_id=""
            )

        # Task fails because the file doesn't exist
        assert result["success"] is False
        # The finally block must have spawned exactly one cleanup thread
        mock_thread.assert_called_once()

    def test_cleanup_blob_called_in_finally_when_api_fails(self, settings):
        """The finally block must spawn a cleanup thread even when the transcription API fails."""
        from chat_next.tasks import transcribe_file_next

        settings.AZURE_SPEECH_TO_TEXT_ENDPOINT = "https://canadaeast.stt.speech.microsoft.com/"
        settings.AZURE_AI_SERVICES_KEY = "test-key"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"

        mock_blob_service = MagicMock()
        mock_container_client = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client
        mock_container_client.upload_blob.return_value = None

        import io

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ), patch(
            "azure.storage.blob.generate_blob_sas", return_value="sas-token"
        ), patch(
            "builtins.open", return_value=io.BytesIO(b"fake audio data")
        ), patch(
            "requests.post", side_effect=Exception("Transcription API failure")
        ), patch(
            "threading.Thread"
        ) as mock_thread:
            result = transcribe_file_next(
                file_path="/tmp/fake.wav", message_id="fake-id", chat_id=""
            )

        assert result["success"] is False
        # The finally block must always spawn the cleanup thread
        mock_thread.assert_called_once()


class TestTranscriptionCategory:
    """Tests for the transcription tool category in models."""

    def test_transcription_category_in_local_categories(self):
        from chat_next.models import (
            LOCAL_TOOL_CATEGORIES,
            TOOL_CATEGORY_TRANSCRIPTION,
        )

        assert TOOL_CATEGORY_TRANSCRIPTION in LOCAL_TOOL_CATEGORIES

    def test_transcription_category_not_yet_in_available_tools(self):
        """Transcription is registered but intentionally excluded from AVAILABLE_TOOLS
        pending management approval."""
        from chat_next.models import AVAILABLE_TOOLS, TOOL_CATEGORY_TRANSCRIPTION

        tool_ids = [t[0] for t in AVAILABLE_TOOLS]
        assert TOOL_CATEGORY_TRANSCRIPTION not in tool_ids
