"""
Tests for the chat_next library tools (Q&A library tools).

Tests the load_library_files tool which uploads library documents
to OpenAI Files API for use with Code Interpreter.

Tests the view_library_files tool which prepares library images and PDFs
for vision model analysis.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

from django.utils import timezone

import pytest
from asgiref.sync import sync_to_async
from chat_next._llm.openai_responses import ResponsesAPIClient
from chat_next._tools.qa_libraries import (
    VISION_PDF_MAX_FILE_BYTES,
    _parse_optional_page_number,
)
from chat_next.tools import (
    TOOL_REGISTRY,
    ToolContext,
    execute_tool_call,
    find_in_document,
    list_documents,
    list_libraries,
    load_library_files,
    rag_search,
    view_library_files,
)


def _make_pdf_bytes(page_count: int = 1) -> bytes:
    """Create a small valid PDF for tests."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class TestViewLibraryFilesHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (1, 1),
            (1.5, 1),
            ("2", 2),
            (" 3 ", 3),
        ],
    )
    def test_parse_optional_page_number_accepts_valid_values(self, value, expected):
        assert _parse_optional_page_number(value, "start_page") == expected

    @pytest.mark.parametrize(
        "value",
        [0, -1, "0", "abc"],
    )
    def test_parse_optional_page_number_rejects_invalid_values(self, value):
        with pytest.raises(ValueError, match="start_page must be a positive integer"):
            _parse_optional_page_number(value, "start_page")


class TestLoadLibraryFilesTool:
    """Tests for the load_library_files tool."""

    def test_tool_is_registered(self):
        """Test that load_library_files is in the tool registry."""
        tool = TOOL_REGISTRY.get("load_library_files")
        assert tool is not None
        assert tool.name == "load_library_files"

    def test_tool_schema_has_required_fields(self):
        """Test that the tool schema has the correct structure."""
        tool = TOOL_REGISTRY.get("load_library_files")
        schema = tool.to_api_schema()

        assert schema["type"] == "function"
        assert schema["name"] == "load_library_files"
        assert "description" in schema
        assert "Code Interpreter" in schema["description"]

        # Check parameters
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "document_ids" in params["properties"]
        assert params["properties"]["document_ids"]["type"] == "array"
        assert "document_ids" in params["required"]


class TestSearchToolSchemas:
    def test_search_tools_expose_higher_top_k_limit(self):
        tool = TOOL_REGISTRY.get("rag_search")
        schema = tool.to_api_schema()
        top_k = schema["parameters"]["properties"]["top_k"]

        assert top_k["type"] == "integer"
        assert top_k["minimum"] == 1
        assert top_k["maximum"] == 200

        params = schema["parameters"]["properties"]
        assert "library_id" in params
        assert "data_source_ids" in params
        assert params["data_source_ids"]["type"] == "array"
        assert "document_ids" in params
        assert params["document_ids"]["type"] == "array"
        assert "data_source_id" not in params
        assert "document_id" not in params

    def test_get_document_text_schema_preserves_read_coverage_guidance(self):
        tool = TOOL_REGISTRY.get("get_document_text")
        schema = tool.to_api_schema()

        assert "COVERAGE object" in schema["description"]
        assert "coverage_pct" in schema["description"]
        assert "full document" in schema["description"]
        document_ids_description = schema["parameters"]["properties"]["document_ids"][
            "description"
        ]
        assert "ordered" in document_ids_description
        assert "one call" in document_ids_description

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_document_ids(self, all_apps_user):
        """Test error when document_ids is empty."""
        user = await sync_to_async(all_apps_user)()

        # Create a mock responses client
        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        result = await load_library_files({"document_ids": []}, context)
        assert "error" in result
        assert "document_ids is required" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_code_interpreter_not_enabled(self, all_apps_user):
        """Test error when Code Interpreter is not enabled."""
        user = await sync_to_async(all_apps_user)()

        # Create a mock responses client without code_interpreter
        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["web_search_preview"]  # No code_interpreter
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        result = await load_library_files({"document_ids": [1]}, context)
        assert "error" in result
        assert "Code Interpreter is not enabled" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_responses_client(self, all_apps_user):
        """Test error when responses_client is not in context."""
        user = await sync_to_async(all_apps_user)()

        context = ToolContext(
            user=user,
            extra={},  # No responses_client
        )

        result = await load_library_files({"document_ids": [1]}, context)
        assert "error" in result
        assert "context not available" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_too_many_documents(self, all_apps_user):
        """Test error when more than 20 documents requested."""
        user = await sync_to_async(all_apps_user)()

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        # Request 21 documents
        result = await load_library_files({"document_ids": list(range(1, 22))}, context)
        assert "error" in result
        assert "Maximum of 20" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_nonexistent_document(self, all_apps_user):
        """Test error handling for documents that don't exist."""
        user = await sync_to_async(all_apps_user)()

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        result = await load_library_files({"document_ids": [99999]}, context)
        # Should return with errors but not crash
        assert "errors" in result
        assert any("not found" in e for e in result["errors"])

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_successful_file_upload(self, all_apps_user):
        """Test successful upload of a document file to OpenAI."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        # Create a library with a document that has a file
        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create a SavedFile with content
            saved_file = SavedFile.objects.create(
                file=ContentFile(b"test content", name="test.txt"),
                content_type="text/plain",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_file.txt",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        # Mock the OpenAI client
        mock_openai_response = MagicMock()
        mock_openai_response.id = "file-test123"

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        with patch("openai.AzureOpenAI") as mock_azure:
            mock_azure_instance = MagicMock()
            mock_azure_instance.files.create.return_value = mock_openai_response
            mock_azure.return_value = mock_azure_instance

            result = await load_library_files({"document_ids": [doc_id]}, context)

        # Verify the result
        assert "error" not in result
        assert result["file_count"] == 1
        assert len(result["loaded_files"]) == 1
        assert result["loaded_files"][0]["filename"] == "test_file.txt"
        assert result["loaded_files"][0]["document_id"] == doc_id

        # Verify file_id was added to the client
        assert "file-test123" in mock_client.code_interpreter_file_ids

        # Verify the SavedFile was updated with the OpenAI file ID
        from librarian.models import SavedFile as SF

        @sync_to_async
        def verify_saved_file():
            sf = SF.objects.get(id=saved_file_id)
            return sf.openai_file_id

        updated_file_id = await verify_saved_file()
        assert updated_file_id == "file-test123"

        # Cleanup - delete library cascades to data_source and document
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SF.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_reuses_existing_openai_file_id(self, all_apps_user):
        """Test that existing openai_file_id is reused, not re-uploaded."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        # Create a library with a document that already has an openai_file_id
        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(b"test content", name="test.txt"),
                content_type="text/plain",
                openai_file_id="file-existing123",  # Already uploaded
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_file.txt",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        with patch("openai.AzureOpenAI") as mock_azure:
            mock_azure_instance = MagicMock()
            mock_azure.return_value = mock_azure_instance

            await load_library_files({"document_ids": [doc_id]}, context)

            # Verify files.create was NOT called (reusing existing ID)
            mock_azure_instance.files.create.assert_not_called()

        # Verify the existing file_id was used
        assert "file-existing123" in mock_client.code_interpreter_file_ids

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_permission_check_for_private_library(self, all_apps_user):
        """Test that users can't access documents from libraries they don't have access to."""
        from django.core.files.base import ContentFile

        from otto.models import User

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            unique_suffix = uuid.uuid4().hex[:8]
            other_user = User.objects.create(
                upn=f"other-{unique_suffix}@test.com",
                email=f"other-{unique_suffix}@test.com",
            )

            # Create a private library owned by other_user
            library = Library.objects.create(
                name="Private Library",
                created_by=other_user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(b"test content", name="test.txt"),
                content_type="text/plain",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="private_file.txt",
                status="INDEXED",
            )
            return other_user, library, data_source, saved_file, document

        (
            other_user,
            library,
            data_source,
            saved_file,
            document,
        ) = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        context = ToolContext(
            user=user,  # Different user than library owner
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id
        other_user_id = other_user.id

        result = await load_library_files({"document_ids": [doc_id]}, context)

        # Should have permission error
        assert "errors" in result
        assert any("No permission" in e for e in result["errors"])
        assert result["file_count"] == 0

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()
            User.objects.filter(id=other_user_id).delete()

        await cleanup()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_documents_requires_explicit_scope_ids(all_apps_user):
    """list_documents no longer supports alias shortcuts like this_chat/chat_files."""

    user = await sync_to_async(all_apps_user)()

    result = await list_documents({}, ToolContext(user=user))

    assert "error" in result
    assert "library_id or data_source_id is required" in result["error"]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_libraries_includes_team_contributor_library(all_apps_user):
    from otto.models import Team, TeamMembership

    from librarian.models import DataSource, Document, Library, LibraryTeamRole

    owner = await sync_to_async(all_apps_user)("team-library-owner")
    member = await sync_to_async(all_apps_user)("team-library-member")

    @sync_to_async
    def create_test_data():
        import uuid

        team = Team.objects.create(
            name=f"Library Tool Team {uuid.uuid4().hex[:8]}",
            created_by=owner,
        )
        TeamMembership.objects.create(team=team, user=owner, role="admin")
        TeamMembership.objects.create(team=team, user=member, role="member")

        library = Library.objects.create(
            name="Team Tool Library",
            created_by=owner,
            is_public=False,
        )
        LibraryTeamRole.objects.create(
            library=library,
            team=team,
            role="contributor",
        )
        data_source = DataSource.objects.create(library=library, name="Shared Folder")
        Document.objects.create(
            data_source=data_source,
            filename="team-library.txt",
            extracted_text="Shared document",
            status="SUCCESS",
            is_container=False,
        )
        return library.id

    library_id = await create_test_data()

    result = await list_libraries({}, ToolContext(user=member))

    assert any(item["id"] == library_id for item in result)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_libraries_includes_global_skill_defaults_library_for_regular_user(
    all_apps_user,
):
    from librarian.models import Library

    user = await sync_to_async(all_apps_user)("defaults-library-runtime-user")
    library_id = await sync_to_async(
        lambda: Library.objects.get(name_en="Skill files (Otto defaults)").id
    )()

    result = await list_libraries({}, ToolContext(user=user))

    assert any(item["id"] == library_id for item in result)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_libraries_no_longer_returns_aliases(all_apps_user):
    user = await sync_to_async(all_apps_user)()

    result = await list_libraries({}, ToolContext(user=user))

    assert result
    assert all("aliases" not in item for item in result)


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_libraries_includes_personal_library(all_apps_user):
    user = await sync_to_async(all_apps_user)()
    personal_library_id = await sync_to_async(lambda: user.personal_library.id)()

    result = await list_libraries({}, ToolContext(user=user))

    assert any(item.get("id") == personal_library_id for item in result)
    personal_entry = next(
        item for item in result if item.get("id") == personal_library_id
    )
    assert personal_entry["is_personal"] is True


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_list_documents_supports_data_source_id_without_library_id(
    all_apps_user,
):
    """list_documents can infer the library directly from data_source_id."""

    from librarian.models import DataSource, Document, Library

    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def create_test_data():
        import uuid

        library = Library.objects.create(
            name=f"Test Library {uuid.uuid4().hex[:8]}",
            created_by=user,
            is_public=False,
        )
        data_source = DataSource.objects.create(
            library=library,
            name="Test Folder",
        )
        document = Document.objects.create(
            data_source=data_source,
            filename="notes.txt",
            extracted_text="hello world",
            status="INDEXED",
            is_container=False,
        )
        return library, data_source, document

    library, data_source, document = await create_test_data()

    context = ToolContext(user=user)
    result = await list_documents(
        {"data_source_id": data_source.id, "limit": 200, "compact": True},
        context,
    )

    assert "error" not in result
    assert result["library_name"] == str(library)
    assert result["folder_name"] == data_source.name
    assert result["document_count"] == 1
    assert result["returned_count"] == 1
    assert result["limit"] == 200
    assert result["fields"] == [
        "id",
        "title",
        "filename",
        "status",
        "num_chunks",
        "created_at",
        "text_length",
        "is_container",
        "is_loadable",
    ]
    assert len(result["rows"]) == 1
    assert result["rows"][0][0] == document.id
    assert result["rows"][0][2] == "notes.txt"

    @sync_to_async
    def cleanup():
        Library.objects.filter(id=library.id).delete()

    await cleanup()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_rag_search_requires_exactly_one_scope_identifier(
    all_apps_user,
):
    """rag_search should reject ambiguous scope selection."""

    from librarian.models import DataSource, Document, Library

    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def create_test_data():
        import uuid

        other_library = Library.objects.create(
            name=f"Other Library {uuid.uuid4().hex[:8]}",
            created_by=user,
            is_public=False,
        )
        data_source = DataSource.objects.create(
            library=other_library,
            name="Delegated Folder",
        )
        document = Document.objects.create(
            data_source=data_source,
            filename="delegated.txt",
            extracted_text="hello delegated world",
            status="INDEXED",
            is_container=False,
        )
        return other_library, data_source, document

    other_library, data_source, _document = await create_test_data()

    context = ToolContext(user=user)
    result = await rag_search(
        {
            "library_id": other_library.id,
            "data_source_ids": [data_source.id],
            "query": "delegated",
            "top_k": 5,
            "vector_weight": 0.6,
        },
        context,
    )

    assert "error" in result
    assert (
        "Provide exactly one of library_id, data_source_ids, or document_ids"
        in result["error"]
    )

    @sync_to_async
    def cleanup():
        Library.objects.filter(id=other_library.id).delete()

    await cleanup()


class TestToolIntegration:
    """Integration tests for tool execution via execute_tool_call."""

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_execute_tool_call_passes_client_context(self, all_apps_user):
        """Test that execute_tool_call properly passes extra_context."""
        user = await sync_to_async(all_apps_user)()

        mock_client = MagicMock(spec=ResponsesAPIClient)
        mock_client.tools = ["code_interpreter"]
        mock_client.code_interpreter_file_ids = []

        # Execute with extra_context containing responses_client
        result = await execute_tool_call(
            tool_name="load_library_files",
            arguments={"document_ids": []},
            user=user,
            chat=None,
            extra_context={"responses_client": mock_client},
        )

        # The tool executes successfully but returns an error in the result dict
        # execute_tool_call returns success=True if tool ran without exception
        assert result["success"] is True
        assert "error" in result["result"]
        assert "document_ids is required" in result["result"]["error"]


class TestBuildFunctionCallOutput:
    """Tests for build_function_call_output with vision content."""

    def test_standard_dict_output(self):
        """Test that regular dict output is JSON-encoded as string."""
        from chat_next.tools import build_function_call_output

        output = {"status": "success", "count": 5}
        result = build_function_call_output("call-123", output)

        assert result["type"] == "function_call_output"
        assert result["call_id"] == "call-123"
        assert isinstance(result["output"], str)
        assert '"status": "success"' in result["output"]

    def test_string_output(self):
        """Test that string output is passed through as-is."""
        from chat_next.tools import build_function_call_output

        result = build_function_call_output("call-123", "simple text")

        assert result["output"] == "simple text"

    def test_vision_output_creates_array(self):
        """Test that _vision_output creates an array output with vision items."""
        from chat_next.tools import build_function_call_output

        output = {
            "loaded_files": [{"filename": "test.png", "type": "image"}],
            "file_count": 1,
            "_vision_output": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,iVBORw0KGgo=",
                    "detail": "high",
                }
            ],
        }
        result = build_function_call_output("call-123", output)

        assert result["type"] == "function_call_output"
        assert result["call_id"] == "call-123"
        # Output should be an array
        assert isinstance(result["output"], list)
        assert len(result["output"]) == 2  # text + image

        # First item should be input_text with JSON
        assert result["output"][0]["type"] == "input_text"
        assert "loaded_files" in result["output"][0]["text"]
        # _vision_output should not be in the text
        assert "_vision_output" not in result["output"][0]["text"]

        # Second item should be the input_image
        assert result["output"][1]["type"] == "input_image"
        assert "data:image/png;base64," in result["output"][1]["image_url"]

    def test_vision_output_with_pdf(self):
        """Test that _vision_output works with PDF file_id references."""
        from chat_next.tools import build_function_call_output

        output = {
            "loaded_files": [{"filename": "doc.pdf", "type": "pdf"}],
            "file_count": 1,
            "_vision_output": [
                {
                    "type": "input_file",
                    "file_id": "file-abc123",
                }
            ],
        }
        result = build_function_call_output("call-123", output)

        assert isinstance(result["output"], list)
        assert len(result["output"]) == 2

        # Check the input_file item
        assert result["output"][1]["type"] == "input_file"
        assert result["output"][1]["file_id"] == "file-abc123"


class TestViewLibraryFilesTool:
    """Tests for the view_library_files tool."""

    def test_tool_is_registered(self):
        """Test that view_library_files is in the tool registry."""
        tool = TOOL_REGISTRY.get("view_library_files")
        assert tool is not None
        assert tool.name == "view_library_files"

    def test_tool_schema_has_required_fields(self):
        """Test that the tool schema has the correct structure."""
        tool = TOOL_REGISTRY.get("view_library_files")
        schema = tool.to_api_schema()

        assert schema["type"] == "function"
        assert schema["name"] == "view_library_files"
        assert "description" in schema
        assert "vision" in schema["description"].lower()

        # Check parameters
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "document_ids" in params["properties"]
        assert params["properties"]["document_ids"]["type"] == "array"
        assert "document_ids" in params["required"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_document_ids(self, all_apps_user):
        """Test error when document_ids is empty."""
        user = await sync_to_async(all_apps_user)()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        result = await view_library_files({"document_ids": []}, context)
        assert "error" in result
        assert "document_ids is required" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_too_many_documents(self, all_apps_user):
        """Test error when more than 20 documents requested."""
        user = await sync_to_async(all_apps_user)()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        # Request 21 documents (exceeds limit of 20)
        result = await view_library_files({"document_ids": list(range(1, 22))}, context)
        assert "error" in result
        assert "Maximum of 20" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_nonexistent_document(self, all_apps_user):
        """Test error handling for documents that don't exist."""
        user = await sync_to_async(all_apps_user)()

        context = ToolContext(
            user=user,
            extra={},
        )

        result = await view_library_files({"document_ids": [99999]}, context)
        # Should return with errors but not crash
        assert "errors" in result
        assert any("not found" in e for e in result["errors"])

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_unsupported_file_type(self, all_apps_user):
        """Test error when file is not an image or PDF."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create a text file (not image or PDF)
            saved_file = SavedFile.objects.create(
                file=ContentFile(b"test content", name="test.txt"),
                content_type="text/plain",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_file.txt",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        result = await view_library_files({"document_ids": [doc_id]}, context)

        # Should have error about unsupported file type
        assert "errors" in result
        assert any("not an image or PDF" in e for e in result["errors"])

        # No vision items should be in the output
        assert "_vision_output" not in result

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_successful_image_load(self, all_apps_user):
        """Test successful loading of an image file for vision."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create an image file (PNG header for validity)
            png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            saved_file = SavedFile.objects.create(
                file=ContentFile(png_header, name="test.png"),
                content_type="image/png",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_image.png",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        result = await view_library_files({"document_ids": [doc_id]}, context)

        # Verify the result
        assert "error" not in result
        assert result["file_count"] == 1
        assert len(result["loaded_files"]) == 1
        assert result["loaded_files"][0]["filename"] == "test_image.png"
        assert result["loaded_files"][0]["type"] == "image"

        # Verify vision item is in _vision_output
        assert "_vision_output" in result
        assert len(result["_vision_output"]) == 1
        vision_item = result["_vision_output"][0]
        assert vision_item["type"] == "input_image"
        assert "data:image/png;base64," in vision_item["image_url"]
        assert vision_item["detail"] == "high"

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_successful_pdf_load(self, all_apps_user):
        """Test successful loading of a PDF file for vision."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create a PDF file (valid minimal PDF content)
            pdf_content = _make_pdf_bytes(page_count=1)
            saved_file = SavedFile.objects.create(
                file=ContentFile(pdf_content, name="test.pdf"),
                content_type="application/pdf",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_document.pdf",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        # Mock the OpenAI client for file upload
        mock_openai_response = MagicMock()
        mock_openai_response.id = "file-pdf123"

        with patch("openai.AzureOpenAI") as mock_azure:
            mock_azure_instance = MagicMock()
            mock_azure_instance.files.create.return_value = mock_openai_response
            mock_azure.return_value = mock_azure_instance

            result = await view_library_files({"document_ids": [doc_id]}, context)

        # Verify the result
        assert "error" not in result
        assert result["file_count"] == 1
        assert len(result["loaded_files"]) == 1
        assert result["loaded_files"][0]["filename"] == "test_document.pdf"
        assert result["loaded_files"][0]["type"] == "pdf"

        # Verify vision item is in _vision_output
        assert "_vision_output" in result
        assert len(result["_vision_output"]) == 1
        vision_item = result["_vision_output"][0]
        assert vision_item["type"] == "input_file"
        assert vision_item["file_id"] == "file-pdf123"

        # Verify the SavedFile was updated with the OpenAI file ID
        from librarian.models import SavedFile as SF

        @sync_to_async
        def verify_saved_file():
            sf = SF.objects.get(id=saved_file_id)
            return sf.openai_file_id

        updated_file_id = await verify_saved_file()
        assert updated_file_id == "file-pdf123"

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SF.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_reuses_existing_openai_file_id_for_pdf(self, all_apps_user):
        """Test that existing openai_file_id is reused for PDFs."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create a PDF with existing openai_file_id
            pdf_content = _make_pdf_bytes(page_count=1)
            saved_file = SavedFile.objects.create(
                file=ContentFile(pdf_content, name="test.pdf"),
                content_type="application/pdf",
                openai_file_id="file-existing-pdf456",  # Already uploaded
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="test_document.pdf",
                status="INDEXED",
            )
            return library, data_source, saved_file, document

        library, data_source, saved_file, document = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id

        with patch("openai.AzureOpenAI") as mock_azure:
            mock_azure_instance = MagicMock()
            mock_azure.return_value = mock_azure_instance

            result = await view_library_files({"document_ids": [doc_id]}, context)

            # Verify files.create was NOT called (reusing existing ID)
            mock_azure_instance.files.create.assert_not_called()

        # Verify the existing file_id was used in _vision_output
        assert "_vision_output" in result
        assert len(result["_vision_output"]) == 1
        assert result["_vision_output"][0]["file_id"] == "file-existing-pdf456"

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_pdf_page_range_uploads_sliced_pdf_and_schedules_cleanup(
        self, all_apps_user
    ):
        """Page-range PDF viewing should upload a sliced PDF and schedule deletion."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(_make_pdf_bytes(page_count=3), name="three-pages.pdf"),
                content_type="application/pdf",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="three-pages.pdf",
                extracted_text="<page_1>One</page_1><page_2>Two</page_2><page_3>Three</page_3>",
                status="INDEXED",
            )
            return library, saved_file, document

        library, saved_file, document = await create_test_data()
        context = ToolContext(user=user, extra={"responses_client": MagicMock()})
        saved_file_id = saved_file.id

        mock_file_response = MagicMock()
        mock_file_response.id = "file-sliced-abc123"

        with (
            patch("openai.AzureOpenAI") as mock_azure,
            patch("chat_next.tasks.delete_openai_file_async") as mock_cleanup,
        ):
            mock_azure_instance = MagicMock()
            mock_azure_instance.files.create.return_value = mock_file_response
            mock_azure.return_value = mock_azure_instance

            result = await view_library_files(
                {"document_ids": [document.id], "start_page": 2},
                context,
            )

            # Should have uploaded the sliced PDF
            mock_azure_instance.files.create.assert_called_once()
            call_kwargs = mock_azure_instance.files.create.call_args
            assert call_kwargs[1]["purpose"] == "assistants"

            # Should have scheduled delayed async cleanup
            mock_cleanup.apply_async.assert_called_once_with(
                args=["file-sliced-abc123"],
                countdown=24 * 60 * 60,
            )

        assert "error" not in result
        assert result["file_count"] == 1
        assert result["loaded_files"][0]["type"] == "pdf"
        assert result["loaded_files"][0]["pages_viewed"] == "2-2"
        assert "_vision_output" in result
        assert len(result["_vision_output"]) == 1
        vision_item = result["_vision_output"][0]
        assert vision_item["type"] == "input_file"
        assert vision_item["file_id"] == "file-sliced-abc123"
        # Should NOT have inline file_data
        assert "file_data" not in vision_item

        # Original SavedFile.openai_file_id should remain untouched
        from librarian.models import SavedFile as SF

        @sync_to_async
        def verify_saved_file():
            sf = SF.objects.get(id=saved_file_id)
            return sf.openai_file_id

        assert await verify_saved_file() is None

        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SF.objects.filter(id=saved_file_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_pdf_page_range_errors_when_out_of_bounds(self, all_apps_user):
        """Out-of-bounds page ranges should fail cleanly per document."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(_make_pdf_bytes(page_count=2), name="two-pages.pdf"),
                content_type="application/pdf",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="two-pages.pdf",
                status="INDEXED",
            )
            return library, saved_file, document

        library, saved_file, document = await create_test_data()

        result = await view_library_files(
            {"document_ids": [document.id], "start_page": 3},
            ToolContext(user=user, extra={}),
        )

        assert result["file_count"] == 0
        assert "errors" in result
        assert any("exceeds PDF length" in e for e in result["errors"])
        assert "_vision_output" not in result

        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file.id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_pdf_page_range_auto_splits_on_configured_limit_errors(
        self, all_apps_user
    ):
        """A configured upload-limit error should trigger recursive range splitting."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(_make_pdf_bytes(page_count=4), name="four-pages.pdf"),
                content_type="application/pdf",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="four-pages.pdf",
                status="INDEXED",
            )
            return library, saved_file, document

        library, saved_file, document = await create_test_data()

        limit_mib = VISION_PDF_MAX_FILE_BYTES / (1024 * 1024)
        split_call_results = [
            ValueError(f"Requested page range exceeds {limit_mib:g}MB upload limit"),
            ({"type": "input_file", "file_id": "file-left"}, 4, 1, 2),
            ({"type": "input_file", "file_id": "file-right"}, 4, 3, 4),
        ]

        with patch(
            "chat_next._tools.qa_libraries._build_pdf_page_range_vision_item",
            side_effect=split_call_results,
        ) as mock_build:
            result = await view_library_files(
                {
                    "document_ids": [document.id],
                    "start_page": 1,
                    "end_page": 4,
                },
                ToolContext(user=user, extra={}),
            )

        assert "error" not in result
        assert result["file_count"] == 2
        assert [item["file_id"] for item in result["_vision_output"]] == [
            "file-left",
            "file-right",
        ]

        call_ranges = [
            (call.kwargs["start_page"], call.kwargs["end_page"])
            for call in mock_build.call_args_list
        ]
        assert call_ranges == [(1, 4), (1, 2), (3, 4)]

        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file.id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_pdf_page_range_does_not_split_on_non_limit_errors(
        self, all_apps_user
    ):
        """Non-size errors should not trigger splitting and should surface as document errors."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            saved_file = SavedFile.objects.create(
                file=ContentFile(_make_pdf_bytes(page_count=4), name="four-pages.pdf"),
                content_type="application/pdf",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="four-pages.pdf",
                status="INDEXED",
            )
            return library, saved_file, document

        library, saved_file, document = await create_test_data()

        with patch(
            "chat_next._tools.qa_libraries._build_pdf_page_range_vision_item",
            side_effect=RuntimeError("upstream timeout"),
        ) as mock_build:
            result = await view_library_files(
                {
                    "document_ids": [document.id],
                    "start_page": 1,
                    "end_page": 4,
                },
                ToolContext(user=user, extra={}),
            )

        assert result["file_count"] == 0
        assert "errors" in result
        assert any(
            "Failed to load four-pages.pdf" in message for message in result["errors"]
        )
        assert "_vision_output" not in result
        assert mock_build.call_count == 1

        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file.id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_auto_split_proceeds_when_file_size_unavailable(
        self, pdf_library_document
    ):
        """Auto-split should not raise TypeError when file size cannot be read."""
        from chat_next._tools.qa_libraries import (
            VISION_PDF_MAX_PAGES_PER_SLICE,  # noqa: F401
        )

        user, library, saved_file, document = pdf_library_document

        class _NoSizeFile:
            """Minimal wrapper that raises on .size access."""

            def __init__(self, real_file):
                self._real = real_file

            @property
            def size(self):
                raise OSError("size unavailable")

            def open(self, mode="rb"):
                return self._real.open(mode)

            def __enter__(self):
                return self._real.__enter__()

            def __exit__(self, *args):
                return self._real.__exit__(*args)

        with patch(
            "chat_next._tools.qa_libraries._build_pdf_page_range_vision_item",
            return_value=(
                {"type": "input_file", "file_id": "file-nosizepdf"},
                4,
                1,
                4,
            ),
        ):
            saved_file.file = _NoSizeFile(saved_file.file)

            result = await view_library_files(
                {"document_ids": [document.id]},
                ToolContext(user=user, extra={}),
            )

        # No TypeError — auto-split completed using the fallback pages_per_slice.
        assert "errors" not in result or not any(
            "TypeError" in e for e in result.get("errors", [])
        )

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_permission_check_for_private_library(self, all_apps_user):
        """Test that users can't access documents from libraries they don't have access to."""
        from django.core.files.base import ContentFile

        from otto.models import User

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            unique_suffix = uuid.uuid4().hex[:8]
            other_user = User.objects.create(
                upn=f"view-other-{unique_suffix}@test.com",
                email=f"view-other-{unique_suffix}@test.com",
            )

            # Create a private library owned by other_user
            library = Library.objects.create(
                name="Private Library",
                created_by=other_user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create an image file
            png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            saved_file = SavedFile.objects.create(
                file=ContentFile(png_header, name="test.png"),
                content_type="image/png",
            )

            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
                filename="private_image.png",
                status="INDEXED",
            )
            return other_user, library, data_source, saved_file, document

        (
            other_user,
            library,
            data_source,
            saved_file,
            document,
        ) = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,  # Different user than library owner
            extra={"responses_client": mock_client},
        )

        doc_id = document.id
        saved_file_id = saved_file.id
        other_user_id = other_user.id

        result = await view_library_files({"document_ids": [doc_id]}, context)

        # Should have permission error
        assert "errors" in result
        assert any("No permission" in e for e in result["errors"])
        assert result["file_count"] == 0

        # No vision items should be in the output
        assert "_vision_output" not in result

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_id).delete()
            User.objects.filter(id=other_user_id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_mixed_image_and_pdf_load(self, all_apps_user):
        """Test loading both images and PDFs in one call."""
        from django.core.files.base import ContentFile

        from librarian.models import DataSource, Document, Library, SavedFile

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_data():
            import uuid

            library = Library.objects.create(
                name=f"Test Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )

            # Create an image file
            png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            saved_file_image = SavedFile.objects.create(
                file=ContentFile(png_header, name="test.png"),
                content_type="image/png",
            )
            document_image = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file_image,
                filename="test_image.png",
                status="INDEXED",
            )

            # Create a PDF file
            pdf_content = _make_pdf_bytes(page_count=1)
            saved_file_pdf = SavedFile.objects.create(
                file=ContentFile(pdf_content, name="test.pdf"),
                content_type="application/pdf",
            )
            document_pdf = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file_pdf,
                filename="test_document.pdf",
                status="INDEXED",
            )

            return (
                library,
                saved_file_image,
                saved_file_pdf,
                document_image,
                document_pdf,
            )

        (
            library,
            saved_file_image,
            saved_file_pdf,
            document_image,
            document_pdf,
        ) = await create_test_data()

        mock_client = MagicMock(spec=ResponsesAPIClient)

        context = ToolContext(
            user=user,
            extra={"responses_client": mock_client},
        )

        image_doc_id = document_image.id
        pdf_doc_id = document_pdf.id
        saved_file_image_id = saved_file_image.id
        saved_file_pdf_id = saved_file_pdf.id

        # Mock the OpenAI client for PDF upload
        mock_openai_response = MagicMock()
        mock_openai_response.id = "file-pdf789"

        with patch("openai.AzureOpenAI") as mock_azure:
            mock_azure_instance = MagicMock()
            mock_azure_instance.files.create.return_value = mock_openai_response
            mock_azure.return_value = mock_azure_instance

            result = await view_library_files(
                {"document_ids": [image_doc_id, pdf_doc_id]}, context
            )

        # Verify the result
        assert "error" not in result
        assert result["file_count"] == 2
        assert len(result["loaded_files"]) == 2

        # Check that both image and PDF are in the result
        file_types = {f["type"] for f in result["loaded_files"]}
        assert file_types == {"image", "pdf"}

        # Verify both vision items are in _vision_output
        assert "_vision_output" in result
        assert len(result["_vision_output"]) == 2

        # Check for one input_image and one input_file
        vision_types = {item["type"] for item in result["_vision_output"]}
        assert vision_types == {"input_image", "input_file"}

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()
            SavedFile.objects.filter(id=saved_file_image_id).delete()
            SavedFile.objects.filter(id=saved_file_pdf_id).delete()

        await cleanup()


class TestFindInDocumentTool:
    """Tests for the find_in_document tool."""

    def test_tool_is_registered(self):
        """Test that find_in_document is in the tool registry."""
        tool = TOOL_REGISTRY.get("find_in_document")
        assert tool is not None
        assert tool.name == "find_in_document"

    def test_tool_schema_has_required_fields(self):
        """Test that the tool schema has the correct structure."""
        tool = TOOL_REGISTRY.get("find_in_document")
        schema = tool.to_api_schema()

        assert schema["type"] == "function"
        assert schema["name"] == "find_in_document"
        assert "description" in schema
        assert "character positions" in schema["description"].lower()

        # Check parameters
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "document_id" in params["properties"]
        assert "search_text" in params["properties"]
        assert params["properties"]["document_id"]["type"] == "integer"
        assert params["properties"]["search_text"]["type"] == "string"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_document_id(self, all_apps_user):
        """Test error when document_id is missing."""
        from chat_next.tools import find_in_document

        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await find_in_document({"search_text": "test"}, context)
        assert "error" in result
        assert "document_id is required" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_when_no_search_text(self, all_apps_user):
        """Test error when search_text is missing."""
        from chat_next.tools import find_in_document

        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await find_in_document({"document_id": 1}, context)
        assert "error" in result
        assert "search_text is required" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_short_search_text(self, all_apps_user):
        """Test error when search_text is too short."""
        from chat_next.tools import find_in_document

        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await find_in_document(
            {"document_id": 1, "search_text": "ab"}, context
        )
        assert "error" in result
        assert "at least 3 characters" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_returns_error_for_nonexistent_document(self, all_apps_user):
        """Test error when document doesn't exist."""
        from chat_next.tools import find_in_document

        user = await sync_to_async(all_apps_user)()
        context = ToolContext(user=user)

        result = await find_in_document(
            {"document_id": 99999, "search_text": "test"}, context
        )
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_successful_find_with_matches(self, all_apps_user):
        """Test successful text search with matches."""
        from chat_next.tools import find_in_document

        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        # Create a public library with a document containing searchable text
        @sync_to_async
        def create_test_document():
            import uuid

            library = Library.objects.create(
                created_by=user,
                is_public=True,
                name_en=f"Test Library {uuid.uuid4().hex[:8]}",
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )
            doc = Document.objects.create(
                data_source=data_source,
                filename="test.txt",
                extracted_text="This is a test document. It contains test content for testing purposes. The word test appears multiple times.",
            )
            return library, doc

        library, doc = await create_test_document()

        context = ToolContext(user=user)

        result = await find_in_document(
            {"document_id": doc.id, "search_text": "test"}, context
        )

        # Verify the result structure
        assert "error" not in result
        assert result["document_id"] == doc.id
        assert result["search_text"] == "test"
        assert result["case_sensitive"] is False  # Default
        assert result["total_matches"] > 0
        assert result["matches_returned"] > 0
        assert "matches" in result
        assert len(result["matches"]) > 0

        # Verify match structure
        first_match = result["matches"][0]
        assert "char_start" in first_match
        assert "char_end" in first_match
        assert "match" in first_match
        assert "context" in first_match
        assert "read_more" not in first_match  # Should not have this field
        assert first_match["match"].lower() == "test"
        assert first_match["char_start"] >= 0
        assert first_match["char_end"] > first_match["char_start"]

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()

        await cleanup()


@pytest.mark.django_db
@pytest.mark.asyncio
class TestLibraryToolRetentionReset:
    async def _create_library_fixture(self, all_apps_user, *, extracted_text=None):
        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_data():
            import uuid

            library = Library.objects.create(
                name=f"Retention Library {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Retention Folder",
            )
            document = Document.objects.create(
                data_source=data_source,
                filename="retention.txt",
                extracted_text=extracted_text,
                status="INDEXED",
                is_container=False,
            )
            stale_time = timezone.now() - timezone.timedelta(days=120)
            Library.objects.filter(pk=library.pk).update(accessed_at=stale_time)
            library.refresh_from_db()
            return user, library, data_source, document, stale_time

        return await create_data()

    @staticmethod
    async def _refresh_accessed_at(library_id):
        from librarian.models import Library

        @sync_to_async
        def refresh():
            return Library.objects.get(pk=library_id).accessed_at

        return await refresh()

    @staticmethod
    async def _delete_library(library_id):
        from librarian.models import Library

        @sync_to_async
        def cleanup():
            Library.objects.filter(pk=library_id).delete()

        await cleanup()

    async def test_list_folders_resets_library_accessed_at(self, all_apps_user):
        (
            user,
            library,
            _data_source,
            _document,
            stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        context = ToolContext(user=user)
        result = await TOOL_REGISTRY.get("list_folders").execute(
            {"library_id": library.id}, context
        )

        assert "error" not in result
        assert (await self._refresh_accessed_at(library.id)) > stale_time

        await self._delete_library(library.id)

    async def test_rag_search_resets_library_accessed_at(self, all_apps_user):
        (
            user,
            library,
            _data_source,
            document,
            stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        context = ToolContext(user=user)
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MagicMock(
                text="A relevant excerpt",
                score=0.91,
                metadata={
                    "doc_id": document.id,
                    "start_char": 0,
                    "start_page": 1,
                    "filename": document.filename,
                },
            )
        ]

        with patch("chat._llm.OttoLLM.get_retriever", return_value=mock_retriever):
            result = await rag_search(
                {"library_id": library.id, "query": "relevant", "top_k": 3},
                context,
            )

        assert "error" not in result
        assert result["library_name"] == str(library)
        assert mock_retriever.retrieve.called
        assert (await self._refresh_accessed_at(library.id)) > stale_time

        await self._delete_library(library.id)

    async def test_rag_search_accepts_vector_weight_argument(self, all_apps_user):
        """rag_search forwards vector_weight to the retriever."""
        (
            user,
            library,
            _data_source,
            document,
            _stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        context = ToolContext(user=user)
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MagicMock(
                text="A relevant excerpt",
                score=0.91,
                metadata={
                    "doc_id": document.id,
                    "start_char": 0,
                    "start_page": 1,
                    "filename": document.filename,
                },
            )
        ]

        with patch(
            "chat._llm.OttoLLM.get_retriever", return_value=mock_retriever
        ) as mock_get_retriever:
            result = await rag_search(
                {
                    "library_id": library.id,
                    "query": "relevant",
                    "top_k": 3,
                    "vector_weight": 0.2,
                },
                context,
            )

        assert "error" not in result
        assert mock_get_retriever.call_args.kwargs["vector_weight"] == 0.2

        await self._delete_library(library.id)

    async def test_rag_search_warns_about_paused_documents(self, all_apps_user):
        """Semantic search should warn when paused documents are excluded from recall."""
        from librarian.models import Document

        (
            user,
            library,
            data_source,
            document,
            _stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        await sync_to_async(Document.objects.create)(
            data_source=data_source,
            filename="large.csv",
            status="PAUSED",
            extracted_text="header1,header2\nvalue1,value2",
        )

        context = ToolContext(user=user)
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MagicMock(
                text="A relevant excerpt",
                score=0.91,
                metadata={
                    "doc_id": document.id,
                    "start_char": 0,
                    "start_page": 1,
                    "filename": document.filename,
                },
            )
        ]

        with patch("chat._llm.OttoLLM.get_retriever", return_value=mock_retriever):
            result = await rag_search(
                {"library_id": library.id, "query": "relevant", "top_k": 3},
                context,
            )

        assert "error" not in result
        assert "WARNING" in result
        assert (
            "will NOT appear in semantic search results until embedded"
            in result["WARNING"]
        )

        await self._delete_library(library.id)

    async def test_rag_search_rejects_invalid_vector_weight(self, all_apps_user):
        """rag_search validates vector_weight range."""
        (
            user,
            library,
            _data_source,
            _document,
            _stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        context = ToolContext(user=user)
        result = await rag_search(
            {
                "library_id": library.id,
                "query": "relevant",
                "top_k": 3,
                "vector_weight": 1.5,
            },
            context,
        )

        assert "error" in result
        assert "vector_weight must be between 0 and 1" in result["error"]

        await self._delete_library(library.id)

    async def test_rag_search_supports_folder_scope(self, all_apps_user):
        (
            user,
            library,
            data_source,
            document,
            _stale_time,
        ) = await self._create_library_fixture(all_apps_user)

        context = ToolContext(user=user)
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MagicMock(
                text="A folder-specific excerpt",
                score=0.88,
                metadata={
                    "doc_id": document.id,
                    "start_char": 4,
                    "start_page": 1,
                    "filename": document.filename,
                },
            )
        ]

        with patch("chat._llm.OttoLLM.get_retriever", return_value=mock_retriever):
            result = await rag_search(
                {
                    "data_source_ids": [data_source.id],
                    "query": "folder",
                    "top_k": 3,
                    "vector_weight": 0.6,
                },
                context,
            )

        assert "error" not in result
        assert result["scope_type"] == "folder"
        assert result["library_name"] == str(library)
        assert result["folder_name"] == data_source.name
        assert result["results"][0]["document_id"] == document.id

        await self._delete_library(library.id)

    async def test_rag_search_supports_document_scope(self, all_apps_user):
        (
            user,
            library,
            _data_source,
            document,
            _stale_time,
        ) = await self._create_library_fixture(
            all_apps_user,
            extracted_text="<page_1>Alpha beta gamma</page_1>",
        )

        context = ToolContext(user=user)
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [
            MagicMock(
                text="beta gamma",
                score=0.94,
                metadata={
                    "chunk_number": 1,
                },
            )
        ]

        with patch("chat._llm.OttoLLM.get_retriever", return_value=mock_retriever):
            result = await rag_search(
                {
                    "document_ids": [document.id],
                    "query": "beta",
                    "top_k": 3,
                    "vector_weight": 0.6,
                },
                context,
            )

        assert "error" not in result
        assert result["scope_type"] == "document"
        assert result["document_id"] == document.id
        assert result["document_title"] == (document.title or document.filename)
        assert result["results"][0]["chunk_number"] == 1
        assert result["results"][0]["start_char"] is not None

        await self._delete_library(library.id)

    async def test_rag_search_supports_multiple_document_ids_across_libraries(
        self,
        all_apps_user,
    ):
        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_data():
            import uuid

            library_one = Library.objects.create(
                name=f"Cross Library One {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            library_two = Library.objects.create(
                name=f"Cross Library Two {uuid.uuid4().hex[:8]}",
                created_by=user,
                is_public=False,
            )
            data_source_one = DataSource.objects.create(
                library=library_one,
                name="Folder One",
            )
            data_source_two = DataSource.objects.create(
                library=library_two,
                name="Folder Two",
            )
            document_one = Document.objects.create(
                data_source=data_source_one,
                filename="one.txt",
                extracted_text="alpha one",
                status="INDEXED",
                is_container=False,
            )
            document_two = Document.objects.create(
                data_source=data_source_two,
                filename="two.txt",
                extracted_text="beta two",
                status="INDEXED",
                is_container=False,
            )
            return library_one, library_two, document_one, document_two

        library_one, library_two, document_one, document_two = await create_data()

        context = ToolContext(user=user)

        def _build_retriever(result_text, document_id, filename):
            retriever = MagicMock()
            retriever.retrieve.return_value = [
                MagicMock(
                    text=result_text,
                    score=0.85,
                    metadata={
                        "doc_id": document_id,
                        "start_char": 0,
                        "start_page": 1,
                        "filename": filename,
                    },
                )
            ]
            return retriever

        retrievers = {
            library_one.uuid_hex: _build_retriever(
                "alpha one", document_one.id, document_one.filename
            ),
            library_two.uuid_hex: _build_retriever(
                "beta two", document_two.id, document_two.filename
            ),
        }

        def _get_retriever(vector_store_table, *args, **kwargs):
            return retrievers[vector_store_table]

        with patch("chat._llm.OttoLLM.get_retriever", side_effect=_get_retriever):
            result = await rag_search(
                {
                    "document_ids": [document_one.id, document_two.id],
                    "query": "alpha beta",
                    "top_k": 5,
                    "vector_weight": 0.6,
                },
                context,
            )

        assert "error" not in result
        assert result["scope_type"] == "document"
        assert sorted(result["document_ids"]) == sorted(
            [document_one.id, document_two.id]
        )
        assert len(result["results"]) == 2
        assert sorted(result["library_names"]) == sorted(
            [str(library_one), str(library_two)]
        )

        await self._delete_library(library_one.id)
        await self._delete_library(library_two.id)


class TestRagSearchToolSchema(TestLibraryToolRetentionReset):
    """Schema tests for rag_search tool."""

    async def test_rag_search_schema_exposes_vector_weight(self):
        tool = TOOL_REGISTRY.get("rag_search")
        schema = tool.to_api_schema()

        params = schema["parameters"]
        assert "vector_weight" in params["properties"]
        assert params["properties"]["vector_weight"]["type"] == "number"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_get_document_text_resets_library_accessed_at(self, all_apps_user):
        (
            user,
            library,
            _data_source,
            document,
            stale_time,
        ) = await self._create_library_fixture(
            all_apps_user,
            extracted_text="This is the extracted text used for retention testing.",
        )

        context = ToolContext(user=user)
        result = await TOOL_REGISTRY.get("get_document_text").execute(
            {"document_id": document.id}, context
        )

        assert "error" not in result
        assert "text" in result
        assert (await self._refresh_accessed_at(library.id)) > stale_time

        await self._delete_library(library.id)

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_get_document_text_reads_ordered_document_ids(self, all_apps_user):
        from librarian.models import Document

        (
            user,
            library,
            data_source,
            _document,
            _stale_time,
        ) = await self._create_library_fixture(
            all_apps_user,
            extracted_text="placeholder",
        )

        first_doc = await sync_to_async(Document.objects.create)(
            data_source=data_source,
            filename="first.txt",
            extracted_text="First document text.",
        )
        second_doc = await sync_to_async(Document.objects.create)(
            data_source=data_source,
            filename="second.txt",
            extracted_text="Second document text.",
        )

        context = ToolContext(user=user)
        result = await TOOL_REGISTRY.get("get_document_text").execute(
            {"document_ids": [second_doc.id, first_doc.id]},
            context,
        )

        assert result["ordering_preserved"] is True
        assert result["requested_document_ids"] == [second_doc.id, first_doc.id]
        assert result["success_count"] == 2
        assert [item["document_id"] for item in result["results"]] == [
            second_doc.id,
            first_doc.id,
        ]
        assert result["results"][0]["text"] == "Second document text."
        assert result["results"][1]["text"] == "First document text."

        await self._delete_library(library.id)

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_find_in_document_resets_library_accessed_at(self, all_apps_user):
        (
            user,
            library,
            _data_source,
            document,
            stale_time,
        ) = await self._create_library_fixture(
            all_apps_user,
            extracted_text="needle in a haystack with another needle nearby",
        )

        context = ToolContext(user=user)
        result = await find_in_document(
            {"document_id": document.id, "search_text": "needle"},
            context,
        )

        assert "error" not in result
        assert result["total_matches"] == 2
        assert (await self._refresh_accessed_at(library.id)) > stale_time

        await self._delete_library(library.id)

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_case_insensitive_search(self, all_apps_user):
        """Test that search is case-insensitive by default."""
        from chat_next.tools import find_in_document

        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_document():
            import uuid

            library = Library.objects.create(
                created_by=user,
                is_public=True,
                name_en=f"Test Library {uuid.uuid4().hex[:8]}",
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )
            doc = Document.objects.create(
                data_source=data_source,
                filename="test.txt",
                extracted_text="Hello World HELLO world HeLLo WoRLd",
            )
            return library, doc

        library, doc = await create_test_document()

        context = ToolContext(user=user)

        # Search for "hello" (lowercase) should find all three instances
        result = await find_in_document(
            {"document_id": doc.id, "search_text": "hello"}, context
        )

        assert "error" not in result
        assert result["total_matches"] == 3
        assert result["case_sensitive"] is False

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_case_sensitive_search(self, all_apps_user):
        """Test case-sensitive search when enabled."""
        from chat_next.tools import find_in_document

        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_document():
            import uuid

            library = Library.objects.create(
                created_by=user,
                is_public=True,
                name_en=f"Test Library {uuid.uuid4().hex[:8]}",
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )
            doc = Document.objects.create(
                data_source=data_source,
                filename="test.txt",
                extracted_text="Hello World HELLO world HeLLo WoRLd",
            )
            return library, doc

        library, doc = await create_test_document()

        context = ToolContext(user=user)

        # Case-sensitive search for "Hello" should find only one instance
        result = await find_in_document(
            {"document_id": doc.id, "search_text": "Hello", "case_sensitive": True},
            context,
        )

        assert "error" not in result
        assert result["total_matches"] == 1
        assert result["case_sensitive"] is True
        assert result["matches"][0]["match"] == "Hello"

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()

        await cleanup()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_no_matches_found(self, all_apps_user):
        """Test response when no matches are found."""
        # from chat_next.tools import find_in_document

        from librarian.models import DataSource, Document, Library

        user = await sync_to_async(all_apps_user)()

        @sync_to_async
        def create_test_document():
            import uuid

            library = Library.objects.create(
                created_by=user,
                is_public=True,
                name_en=f"Test Library {uuid.uuid4().hex[:8]}",
            )
            data_source = DataSource.objects.create(
                library=library,
                name="Test Folder",
            )
            doc = Document.objects.create(
                data_source=data_source,
                filename="test.txt",
                extracted_text="This is some content without the search term.",
            )
            return library, doc

        library, doc = await create_test_document()

        context = ToolContext(user=user)

        result = await find_in_document(
            {"document_id": doc.id, "search_text": "nonexistent"}, context
        )

        assert "error" not in result
        assert result["total_matches"] == 0
        assert result["matches_returned"] == 0
        assert result["matches"] == []

        # Cleanup
        @sync_to_async
        def cleanup():
            Library.objects.filter(id=library.id).delete()

        await cleanup()
