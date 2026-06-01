"""
Tests for the chat_next._llm.openai_responses module.

Tests the ResponsesAPIClient and helper functions for building
conversation input from the database.
"""

import asyncio
import json

from django.core.cache import cache
from django.urls import reverse

import httpx
import pytest
from chat_next._llm import (
    COMPACTION_PROCESSING_STEP,
    ResponsesAPIClient,
    StreamChunk,
    TokenUsage,
    ToolCall,
    bot_message_from_db,
    build_conversation_input,
    build_system_prompt,
    create_tool_costs,
    stream_chat_for_htmx,
    user_message,
    user_message_from_db,
)
from chat_next._llm.openai_responses import _build_context_hints_prompt
from chat_next._tools.base import TOOL_REGISTRY
from chat_next.models import Chat, Message, Skill
from openai import APIError, BadRequestError

from chat._views.load_test import exhaust_streaming_response


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_tool_call_defaults(self):
        tc = ToolCall(tool_type="web_search_preview")
        assert tc.tool_type == "web_search_preview"
        assert tc.status == "in_progress"
        assert tc.query is None
        assert tc.details == {}

    def test_tool_call_with_values(self):
        tc = ToolCall(
            tool_type="web_search_preview",
            status="searching",
            query="test query",
            details={"extra": "info"},
        )
        assert tc.tool_type == "web_search_preview"
        assert tc.status == "searching"
        assert tc.query == "test query"
        assert tc.details == {"extra": "info"}


class TestCreateToolCosts:
    """Tests for create_tool_costs function."""

    @pytest.mark.django_db
    def test_web_search_cost(self):
        """Web search is no longer supported; tool_costs should be 0."""
        tool_calls = [
            ToolCall(tool_type="web_search_preview", status="searching", query="test"),
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert isinstance(usd_cost, float)
        assert usd_cost == 0.0

    @pytest.mark.django_db
    def test_multiple_web_searches(self):
        """Web search is no longer supported; tool_costs should be 0."""
        tool_calls = [
            ToolCall(tool_type="web_search_preview", status="searching", query="test1"),
            ToolCall(tool_type="web_search_preview", status="searching", query="test2"),
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert usd_cost == 0.0

    @pytest.mark.django_db
    def test_dict_tool_calls(self):
        """Test that dict format tool calls also work (web search no longer has cost)."""
        tool_calls = [
            {"tool_type": "web_search_preview", "status": "searching", "query": "test"},
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert usd_cost == 0.0

    @pytest.mark.django_db
    def test_unknown_tool_no_cost(self):
        """Test that unknown tool types don't create costs."""
        tool_calls = [
            ToolCall(tool_type="unknown_tool", status="complete"),
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert usd_cost == 0.0

    @pytest.mark.django_db
    def test_web_search_without_query_no_cost(self):
        """Web search is no longer supported; all web_search_preview calls cost 0."""
        tool_calls = [
            ToolCall(tool_type="web_search_preview", status="completed", query=None),
            ToolCall(tool_type="web_search_preview", status="completed", query="test"),
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert usd_cost == 0.0

    @pytest.mark.django_db
    def test_code_interpreter_cost(self):
        """Test that code interpreter creates a cost."""
        tool_calls = [
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "print('hello')"},
            ),
        ]
        usd_cost = create_tool_costs(tool_calls)
        assert isinstance(usd_cost, float)
        assert usd_cost == 0.0363  # $0.0363 per session

    @pytest.mark.django_db
    def test_multiple_code_interpreter_calls_single_session(self):
        """Test that multiple code interpreter calls with same container_id charge once."""
        tool_calls = [
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 1"},
                container_id="container_abc123",
            ),
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 2"},
                container_id="container_abc123",
            ),
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 3"},
                container_id="container_abc123",
            ),
        ]
        # All same container_id = 1 session = $0.0363
        usd_cost = create_tool_costs(tool_calls, reuse_container=False)
        assert usd_cost == 0.0363

    @pytest.mark.django_db
    def test_multiple_code_interpreter_sessions(self):
        """Test that multiple container_ids result in multiple session charges."""
        tool_calls = [
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 1"},
                container_id="container_abc123",
            ),
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 2"},
                container_id="container_xyz789",  # Different container!
            ),
        ]
        # 2 different container_ids = 2 sessions = $0.0726
        usd_cost = create_tool_costs(tool_calls, reuse_container=False)
        assert usd_cost == 0.0726

    @pytest.mark.django_db
    def test_code_interpreter_sessions_override(self):
        """Test that explicit session count overrides tool call counting."""
        tool_calls = [
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 1"},
            ),
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 2"},
            ),
        ]
        # Explicit session count of 1 should be used
        usd_cost = create_tool_costs(
            tool_calls, reuse_container=False, code_interpreter_sessions=1
        )
        assert usd_cost == 0.0363

        # Explicit session count of 3 should charge for 3 sessions
        usd_cost = create_tool_costs(
            tool_calls, reuse_container=False, code_interpreter_sessions=3
        )
        assert usd_cost == 0.1089  # 3 * 0.0363

    @pytest.mark.django_db
    def test_code_interpreter_fallback_no_container_ids(self):
        """Test that when no container_ids are available, we default to 1 session."""
        tool_calls = [
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 1"},
                # No container_id
            ),
            ToolCall(
                tool_type="code_interpreter",
                status="completed",
                details={"code": "step 2"},
                # No container_id
            ),
        ]
        # No container_ids available, should default to 1 session
        usd_cost = create_tool_costs(tool_calls, reuse_container=False)
        assert usd_cost == 0.0363


class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_token_usage_defaults(self):
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.cached_tokens == 0

    def test_token_usage_with_values(self):
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=25,
            cached_tokens=10,
        )
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 25
        assert usage.cached_tokens == 10

    @pytest.mark.django_db
    def test_create_costs(self):
        """Test that create_costs creates Cost objects correctly."""
        usage = TokenUsage(input_tokens=100, output_tokens=50, cached_tokens=10)

        # This will create Cost objects in the database
        usd_cost = usage.create_costs("gpt-5.1")

        # Should return a float cost
        assert isinstance(usd_cost, float)
        assert usd_cost >= 0


class TestStreamChunk:
    """Tests for StreamChunk dataclass."""

    def test_stream_chunk_defaults(self):
        chunk = StreamChunk()
        assert chunk.text == ""
        assert chunk.reasoning_steps == []
        assert chunk.tool_calls == []
        assert chunk.processing_steps == []
        assert chunk.is_reasoning is False
        assert chunk.is_complete is False
        assert chunk.usage is None
        assert chunk.output_items is None

    def test_stream_chunk_with_values(self):
        chunk = StreamChunk(
            text="Hello, world!",
            reasoning_steps=[{"index": 0, "text": "Thinking..."}],
            tool_calls=[
                {
                    "tool_type": "web_search_preview",
                    "status": "searching",
                    "query": "test",
                }
            ],
            processing_steps=[
                {
                    "type": "reasoning",
                    "index": 0,
                    "text": "Thinking...",
                    "complete": True,
                },
                {
                    "type": "tool_call",
                    "tool_type": "web_search_preview",
                    "status": "searching",
                    "query": "test",
                },
            ],
            is_reasoning=True,
            is_complete=False,
        )
        assert chunk.text == "Hello, world!"
        assert len(chunk.reasoning_steps) == 1
        assert len(chunk.tool_calls) == 1
        assert len(chunk.processing_steps) == 2
        assert chunk.processing_steps[0]["type"] == "reasoning"
        assert chunk.processing_steps[1]["type"] == "tool_call"
        assert chunk.tool_calls[0]["tool_type"] == "web_search_preview"
        assert chunk.is_reasoning is True


class TestUserMessage:
    """Tests for user_message helper function."""

    def test_user_message_simple(self):
        msg = user_message("Hello")
        assert msg == {"role": "user", "content": "Hello"}

    def test_user_message_empty(self):
        msg = user_message("")
        assert msg == {"role": "user", "content": ""}


@pytest.mark.django_db
class TestUserMessageFromDb:
    """Tests for user_message_from_db function."""

    def test_with_response_output(self, all_apps_user):
        """If response_output is set, use it directly."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        stored_output = [{"role": "user", "content": "stored content"}]
        msg = Message.objects.create(
            chat=chat,
            text="original text",
            is_bot=False,
            response_output=stored_output,
        )

        result, file_ids = user_message_from_db(msg)
        assert result == stored_output
        assert file_ids == []

    def test_without_response_output(self, all_apps_user):
        """If no response_output, build from text."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            text="Hello world",
            is_bot=False,
        )

        result, file_ids = user_message_from_db(msg)
        assert result == [{"role": "user", "content": "Hello world"}]
        assert file_ids == []

    def test_empty_message(self, all_apps_user):
        """Empty messages return empty list."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            text="",
            is_bot=False,
        )

        result, file_ids = user_message_from_db(msg)
        assert result == []
        assert file_ids == []


@pytest.mark.django_db
class TestBotMessageFromDb:
    """Tests for bot_message_from_db function."""

    def test_with_response_output(self, all_apps_user):
        """If response_output is set, use it directly (sanitized - reasoning removed for API input)."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        stored_output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi!"}],
            },
            {"type": "reasoning", "encrypted_content": "base64data"},
        ]
        msg = Message.objects.create(
            chat=chat,
            text="Hi!",
            is_bot=True,
            response_output=stored_output,
        )

        result = bot_message_from_db(msg)
        # Reasoning items are filtered out by _sanitize_input_items, so only message remains
        assert len(result) == 1
        assert result[0]["type"] == "message"

    def test_without_response_output(self, all_apps_user):
        """If no response_output, build simple assistant message."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            text="I am an assistant",
            is_bot=True,
        )

        result = bot_message_from_db(msg)
        assert result == [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I am an assistant"}],
            }
        ]


@pytest.mark.django_db
class TestBuildConversationInput:
    """Tests for build_conversation_input function."""

    def test_simple_conversation(self, all_apps_user):
        """Build input from a simple back-and-forth conversation."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(chat=chat, text="Hi there!", is_bot=True)
        Message.objects.create(chat=chat, text="How are you?", is_bot=False)

        input_items = build_conversation_input(chat)

        assert len(input_items) == 3
        assert input_items[0] == {"role": "user", "content": "Hello"}
        assert input_items[1] == {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi there!"}],
        }
        assert input_items[2] == {"role": "user", "content": "How are you?"}

    def test_stopped_or_error_bot_message_is_normalized_for_api_input(
        self, all_apps_user
    ):
        """Fallback assistant text must use output_text, not input_text, on rebuilt turns."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(
            chat=chat,
            text="\n\n_Response stopped early. Costs may still be incurred after stopping._",
            is_bot=True,
        )

        input_items = build_conversation_input(chat)

        assert input_items[1]["role"] == "assistant"
        assert input_items[1]["content"][0]["type"] == "output_text"
        assert "stopped early" in input_items[1]["content"][0]["text"]

    def test_with_stored_response_output(self, all_apps_user):
        """Preserve stored response_output (reasoning items filtered out for API input)."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(
            chat=chat,
            text="Hi!",
            is_bot=True,
            response_output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hi!"}],
                },
                {"type": "reasoning", "encrypted_content": "encrypted_data"},
            ],
        )

        input_items = build_conversation_input(chat)

        # Reasoning items are filtered out during sanitization, so we get:
        # user message + assistant message (with reasoning filtered out)
        assert len(input_items) == 2  # user + message (no reasoning)
        assert input_items[0] == {"role": "user", "content": "Hello"}
        assert input_items[1]["type"] == "message"
        # No reasoning item in the input (filtered by _sanitize_input_items)

    def test_skips_empty_trailing_assistant(self, all_apps_user):
        """Skip empty trailing assistant message (one being generated)."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(
            chat=chat, text="", is_bot=True
        )  # Empty, being generated

        input_items = build_conversation_input(chat)

        # Should only have the user message
        assert len(input_items) == 1
        assert input_items[0] == {"role": "user", "content": "Hello"}


@pytest.mark.django_db
class TestBuildSystemPrompt:
    """Tests for build_system_prompt function."""

    def test_builds_system_prompt(self, all_apps_user):
        """System prompt includes current date and user's custom prompt."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        prompt = build_system_prompt(chat)

        # Should contain current date
        assert "Current date:" in prompt or "date" in prompt.lower()
        # Should be non-empty
        assert len(prompt) > 0

    def test_system_prompt_blocks_internal_ids(self, all_apps_user):
        """System prompt must explicitly forbid exposing internal IDs to users."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        prompt = build_system_prompt(chat)

        assert "never expose internal ids" in prompt.lower()
        assert "document_id" in prompt

    def test_system_prompt_includes_saved_personalization(self, all_apps_user):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        chat.settings.user_display_name = "Jules Kuehn"
        chat.settings.job_description = "Policy analyst"
        chat.settings.global_instructions = "Prefer concise answers."
        chat.settings.send_name_to_model = True
        chat.settings.save()

        prompt = build_system_prompt(chat)

        assert "USER PERSONALIZATION" in prompt
        assert "User name: Jules Kuehn" in prompt
        assert "Job description: Policy analyst" in prompt
        assert "Global instructions: Prefer concise answers." in prompt

    def test_system_prompt_omits_name_when_disabled(self, all_apps_user):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        chat.settings.user_display_name = "Jules Kuehn"
        chat.settings.job_description = "Policy analyst"
        chat.settings.send_name_to_model = False
        chat.settings.save()

        prompt = build_system_prompt(chat)

        assert "Job description: Policy analyst" in prompt
        assert "User name: Jules Kuehn" not in prompt

    def test_system_prompt_includes_runtime_resource_ids(self, all_apps_user):
        from librarian.models import Library

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Prompt IDs")

        prompt = build_system_prompt(chat)

        corporate_library_id = Library.objects.get(is_default_library=True).id
        assert str(corporate_library_id) in prompt
        assert str(user.personal_library.id) in prompt
        assert str(chat.data_source.id) in prompt


@pytest.mark.django_db
class TestContextHintsPersistence:
    """Tests for persistent context hint behavior across turns."""

    def test_user_message_from_db_embeds_context_hints(self, all_apps_user):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        msg = Message.objects.create(
            chat=chat,
            is_bot=False,
            text="Summarize this",
            details={
                "context_hints": [
                    {"type": "document", "id": 42, "name": "Budget Memo"},
                    {
                        "type": "library",
                        "id": 7,
                        "name": "Policy Library",
                    },
                ]
            },
        )

        items, _ = user_message_from_db(msg)
        assert len(items) == 1
        content = items[0]["content"]
        assert "Summarize this" in content
        assert "User-selected context hints" in content
        assert "Budget Memo" in content
        assert "document_id=42" in content
        assert "Policy Library" in content
        assert "library_id=7" in content

    def test_context_hints_prompt_is_turn_local_without_new_hints(self, all_apps_user):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # First turn has context hints
        Message.objects.create(
            chat=chat,
            is_bot=False,
            text="Summarize this",
            details={
                "context_hints": [
                    {"type": "document", "id": 99, "name": "Annual Report"}
                ]
            },
        )

        # Later user message has no hints
        Message.objects.create(
            chat=chat,
            is_bot=False,
            text="Can you make it shorter?",
            details={},
        )

        prompt = _build_context_hints_prompt(chat)
        assert prompt == ""

    def test_invalid_tool_context_hints_are_stripped_from_prompt_and_input(
        self, all_apps_user
    ):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Invalid tool hint test")

        msg = Message.objects.create(
            chat=chat,
            is_bot=False,
            text="Summarize this",
            details={
                "context_hints": [
                    {
                        "type": "tool",
                        "id": "get_document_text",
                        "name": "Get document text",
                    },
                    {"type": "document", "id": 42, "name": "Budget Memo"},
                ]
            },
        )

        prompt = _build_context_hints_prompt(chat)
        items, _ = user_message_from_db(msg)
        content = items[0]["content"]

        assert "get_document_text" not in prompt
        assert "tool_category=get_document_text" not in content
        assert "Budget Memo" in prompt
        assert "Budget Memo" in content

    def test_skill_context_hints_prompt_uses_numeric_skill_ids(self, all_apps_user):
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Skill hint test")
        skill = Skill.objects.create(
            display_name="Skill Creator",
            description="Build and edit skills.",
            body="Load me first.",
            owner=user,
        )

        Message.objects.create(
            chat=chat,
            is_bot=False,
            text="Help me create a skill",
            details={
                "context_hints": [
                    {
                        "type": "skill",
                        "id": str(skill.id),
                        "name": "Skill Creator",
                    }
                ]
            },
        )

        prompt = _build_context_hints_prompt(chat)

        assert "load_skill_instructions" in prompt
        assert "Skill Creator" in prompt
        assert f"[id={skill.id}]" in prompt
        assert f"skill_id={skill.id}" in prompt


class TestResponsesAPIClient:
    """Tests for ResponsesAPIClient class."""

    def test_client_creation_defaults(self):
        """Test client creation with default parameters."""
        client = ResponsesAPIClient(model="gpt-5.1")

        assert client.model == "gpt-5.1"
        assert client.reasoning is False
        assert client.reasoning_effort is None

    def test_client_creation_reasoning(self):
        """Test client creation for reasoning models."""
        client = ResponsesAPIClient(
            model="o3",
            reasoning=True,
            reasoning_effort="medium",
        )

        assert client.model == "o3"
        assert client.reasoning is True
        assert client.reasoning_effort == "medium"

    def test_build_request_params_basic(self):
        """Test request parameter building."""
        client = ResponsesAPIClient(model="gpt-5.1")

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Hello"}],
                instructions="Be helpful",
            )
        )

        assert params["model"] == "gpt-5.1"
        assert params["input"] == [{"role": "user", "content": "Hello"}]
        assert params["instructions"] == "Be helpful"
        assert params["store"] is True  # Enabled for caching optimization
        # Note: stream is not in params - streaming is implied by using .stream() method
        # When no tools are specified, the tools param should not be present
        assert "tools" not in params

    def test_build_request_params_with_tools(self):
        """Test request parameter building with tools enabled."""
        client = ResponsesAPIClient(
            model="gpt-5.1",
            tools=["web_search_preview", "code_interpreter"],
        )

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Hello"}],
            )
        )

        assert params["model"] == "gpt-5.1"
        assert "tools" in params
        assert params["tools"] == [
            {"type": "web_search_preview"},
            {"type": "code_interpreter", "container": {"type": "auto"}},
        ]

    @pytest.mark.django_db
    def test_build_request_params_exposes_simplified_document_processing_tools(
        self, all_apps_user
    ):
        """Batch document processing should expose only the simplified public toolset."""
        user = all_apps_user("document-processing-tools")
        chat = Chat.objects.create(user=user, title="Document processing")

        client = ResponsesAPIClient(
            model="gpt-5.4-mini",
            tools=["local_document_processing"],
            user=user,
            chat=chat,
        )

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Process this large PDF"}],
            )
        )

        function_names = {
            tool["name"]
            for tool in params["tools"]
            if isinstance(tool, dict) and tool.get("type") == "function"
        }

        assert "prompt_documents" in function_names
        assert "prompt_document_chunks" in function_names
        assert "prompt_document_ranges" not in function_names
        assert "plan_document_chunks" not in function_names

    def test_build_request_params_with_empty_tools(self):
        """Test request parameter building with empty tools list."""
        client = ResponsesAPIClient(model="gpt-5.1", tools=[])

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Hello"}],
            )
        )

        # Empty tools list should not add tools param
        assert "tools" not in params

    @pytest.mark.django_db
    def test_build_request_params_includes_unlocked_local_skill_tools(
        self,
        all_apps_user,
    ):
        """Unlocked `local_skills` must expose the actual skill-management functions."""
        user = all_apps_user("unlocked-local-skills-user")
        chat = Chat.objects.create(user=user, title="Unlocked local skills")

        client = ResponsesAPIClient(
            model="gpt-5.1",
            tools=["local_skills"],
            user=user,
            chat=chat,
        )
        client.unlocked_local_skill_tools = True

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Help me create a skill"}],
                instructions="Be helpful",
            )
        )

        function_names = {
            tool["name"]
            for tool in params["tools"]
            if isinstance(tool, dict) and tool.get("type") == "function"
        }

        assert "load_skill_instructions" in function_names
        assert "create_skill" in function_names
        assert "edit_skill" in function_names
        assert "create_skill_from_preset" in function_names
        assert "list_presets" in function_names
        assert "read_preset" in function_names

    def test_build_request_params_reasoning(self):
        """Test request parameter building for reasoning models."""
        client = ResponsesAPIClient(
            model="o3",
            reasoning=True,
            reasoning_effort="high",
            reasoning_summary="auto",
        )

        params = asyncio.run(
            client._build_request_params(
                input_items=[{"role": "user", "content": "Think hard"}],
            )
        )

        assert "reasoning" in params
        assert params["reasoning"]["effort"] == "high"
        assert params["reasoning"]["summary"] == "auto"
        assert params["include"] == ["reasoning.encrypted_content"]

    def test_build_request_params_with_previous_response_id(self):
        """Test request parameter building with previous_response_id for caching."""
        client = ResponsesAPIClient(
            model="gpt-5.1",
            previous_response_id="resp_123abc456def",
        )

        # Build full conversation input (simulating a multi-turn chat)
        input_items = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up question"},
        ]

        params = asyncio.run(client._build_request_params(input_items=input_items))

        # Should have previous_response_id set
        assert params["previous_response_id"] == "resp_123abc456def"
        # Should only include the new user message (after the last assistant message)
        assert params["input"] == [{"role": "user", "content": "Follow-up question"}]
        assert params["store"] is True

    def test_build_request_params_omits_empty_input_when_chaining(self):
        """Chained continuations may legitimately have no new input items."""
        client = ResponsesAPIClient(
            model="gpt-5.1",
            previous_response_id="resp_123abc456def",
        )

        params = asyncio.run(client._build_request_params(input_items=[]))

        assert params["previous_response_id"] == "resp_123abc456def"
        assert "input" not in params
        assert params["store"] is True

    @pytest.mark.django_db
    def test_first_turn_request_includes_context_hints_in_user_message(
        self, all_apps_user
    ):
        """Initial request content must embed context hints so OpenAI stores them."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        Message.objects.create(
            chat=chat,
            text="Summarize this",
            is_bot=False,
            details={
                "context_hints": [
                    {"type": "document", "id": 123, "name": "Roadmap"},
                    {"type": "library", "id": 9, "name": "Team Library"},
                ]
            },
        )

        input_items = build_conversation_input(chat)
        params = asyncio.run(
            ResponsesAPIClient(model="gpt-5.1")._build_request_params(
                input_items=input_items
            )
        )

        assert len(params["input"]) == 1
        content = params["input"][0]["content"]
        assert "Summarize this" in content
        assert "User-selected context hints" in content
        assert "Roadmap" in content
        assert "document_id=123" in content
        assert "Team Library" in content
        assert "library_id=9" in content

    @pytest.mark.django_db
    def test_chained_follow_up_only_sends_new_user_message(self, all_apps_user):
        """Follow-up turns should chain via previous_response_id and send only new text."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        Message.objects.create(
            chat=chat,
            text="Summarize this",
            is_bot=False,
            details={
                "context_hints": [
                    {"type": "document", "id": 123, "name": "Roadmap"},
                ]
            },
        )
        Message.objects.create(chat=chat, text="Here is the summary.", is_bot=True)
        Message.objects.create(
            chat=chat,
            text="Make it shorter",
            is_bot=False,
            details={},
        )

        input_items = build_conversation_input(chat)
        params = asyncio.run(
            ResponsesAPIClient(
                model="gpt-5.1",
                previous_response_id="resp_123abc456def",
            )._build_request_params(input_items=input_items)
        )

        assert params["previous_response_id"] == "resp_123abc456def"
        assert params["input"] == [{"role": "user", "content": "Make it shorter"}]

    def test_build_request_params_without_previous_response_id(self):
        """Test request parameter building without previous_response_id (full rebuild)."""
        client = ResponsesAPIClient(model="gpt-5.1")

        input_items = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up question"},
        ]

        params = asyncio.run(client._build_request_params(input_items=input_items))

        # Should NOT have previous_response_id
        assert "previous_response_id" not in params
        # Should include ALL input items
        assert params["input"] == [
            {"role": "user", "content": "First question"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "First answer"}],
            },
            {"role": "user", "content": "Follow-up question"},
        ]
        assert params["store"] is True

    def test_build_reasoning_steps(self):
        """Test reasoning steps formatting."""
        client = ResponsesAPIClient(model="o3")

        summaries = {0: "First thought", 1: "Second thought"}
        complete = {0}

        steps = client._build_reasoning_steps(summaries, complete)

        assert len(steps) == 2
        assert steps[0] == {"index": 0, "text": "First thought", "complete": True}
        assert steps[1] == {"index": 1, "text": "Second thought", "complete": False}


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_dedupes_duplicate_function_calls(monkeypatch):
    """Duplicate function_call entries should be executed/displayed only once."""

    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"ok": True}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.model = "gpt-5.4"

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                # Same function call appears multiple times in the same response output.
                # - first and second are exact duplicate by call_id
                # - third is duplicate by same name+arguments (different call_id)
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_1",
                            "arguments": '{"document_id": 1133, "start_char": 0, "end_char": -1}',
                        },
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_1",
                            "arguments": '{"document_id": 1133, "start_char": 0, "end_char": -1}',
                        },
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_2",
                            "arguments": '{"document_id": 1133, "start_char": 0, "end_char": -1}',
                        },
                    ],
                    response_id="resp_1",
                )
            else:
                # Final response after tool output continuation
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_2",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    # call_1 appears twice (dedup by call_id); call_2 has a new call_id but the
    # same function signature, so it is also deduped within the batch.
    assert len(executed_calls) == 1

    # Final processing steps should contain one completed function_call entry.
    final_steps = outputs[-1]["processing_steps"]
    completed_function_steps = [
        step
        for step in final_steps
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_function_steps) == 1


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_different_call_ids_use_cache(
    monkeypatch,
):
    """Identical local function calls across iterations use cached results.

    Signature-based caching prevents re-execution of the same function + args.
    The second iteration gets a cached result without calling execute_tool_call again.
    """

    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"ok": True, "tool_name": tool_name}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    function_call = {
        "type": "function_call",
        "name": "get_document_text",
        "call_id": "call_1",
        "arguments": '{"document_id": 1133, "start_char": 0, "end_char": -1}',
    }

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.model = "gpt-5.4"

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration in (1, 2):
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            **function_call,
                            "call_id": f"call_{self._iteration}",
                        }
                    ],
                    response_id=f"resp_{self._iteration}",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_3",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    # First iteration executes; second iteration uses cached result
    assert len(executed_calls) == 1

    # Only one processing step is added (for the actual execution, not the cache hit)
    final_steps = outputs[-1]["processing_steps"]
    completed_function_steps = [
        step
        for step in final_steps
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_function_steps) == 1


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_strips_inline_vision_payloads_from_stored_output(
    monkeypatch,
):
    """Stored function_call_output items should omit bulky inline vision data.

    This covers any tool returning inline base64 payloads (e.g. images).
    PDF page-range views now use file_id uploads, but the sanitization safety
    net must still work for inline content items.
    """

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        return {
            "success": True,
            "result": {
                "loaded_files": [{"filename": "page.pdf", "type": "pdf"}],
                "file_count": 1,
                "_vision_output": [
                    {
                        "type": "input_file",
                        "file_data": "data:application/pdf;base64,ZmFrZS1wZGY=",
                        "filename": "page.pdf",
                    }
                ],
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    captured_continuation_inputs = []

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.model = "gpt-5.4"

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "view_library_files",
                            "call_id": "call_vision_1",
                            "arguments": '{"document_ids": [72], "start_page": 1, "end_page": 1}',
                        }
                    ],
                    response_id="resp_vision_1",
                )
            else:
                captured_continuation_inputs.append(input_items)
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_vision_2",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    assert len(captured_continuation_inputs) == 1
    live_output_item = captured_continuation_inputs[0][0]
    assert live_output_item["type"] == "function_call_output"
    assert live_output_item["output"][1]["file_data"].startswith(
        "data:application/pdf;base64,"
    )

    stored_output_item = next(
        item
        for item in outputs[-1]["output_items"]
        if item.get("type") == "function_call_output"
    )
    assert stored_output_item["type"] == "function_call_output"
    assert "file_data" not in stored_output_item["output"][1]
    assert stored_output_item["output"][1]["file_data_omitted"] is True


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_honors_chat_max_iterations(monkeypatch):
    """At max iterations, pending tool calls should trigger the approval gate
    instead of executing and breaking the loop with unanswered tool calls."""

    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"ok": True}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 2
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()
        loaded_skill_state = {}

        def save(self, update_fields=None):
            return None

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.local_tool_result_cache = {}
            self.model = "gpt-5.4"

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            # Always request one function call so the wrapper loop keeps iterating
            yield StreamChunk(
                text="",
                tool_calls=[],
                processing_steps=[],
                is_reasoning=False,
                is_complete=True,
                output_items=[
                    {
                        "type": "function_call",
                        "name": "get_document_text",
                        "call_id": f"call_{self._iteration}",
                        "arguments": f'{{"document_id": {100 + self._iteration}}}',
                    }
                ],
                response_id=f"resp_{self._iteration}",
            )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    # Iteration 1: auto-approved and executed normally
    # Iteration 2: at max_iterations, forced to approval_needed → not executed
    assert len(executed_calls) == 1

    # Final output should have pending_local_tool (approval gate) with max_iterations flag
    final = outputs[-1]
    assert "pending_local_tool" in final
    assert final["pending_local_tool"]["max_iterations_reached"] is True
    assert final["pending_local_tool"]["name"] == "get_document_text"

    # The waiting_approval step should have max_iterations_reached flag
    waiting_steps = [
        step
        for step in final["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert len(waiting_steps) == 1
    assert waiting_steps[0]["details"]["max_iterations_reached"] is True
    assert waiting_steps[0]["details"]["approval_source"] == "manual"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_limits_proactive_compaction_churn(monkeypatch):
    """Only one proactive compaction should run in a response tool loop.

    If the model asks for more tools after a proactive compaction, the wrapper
    should continue with function_call_output items instead of repeatedly
    compacting and stalling progress.
    """

    execute_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        execute_calls.append((tool_name, arguments))
        return {"success": True, "result": {"text": "x" * 5000}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []
        chat_context_management = "compact"

    class DummyChat:
        settings = DummyOptions()
        loaded_skill_state = {}

        def save(self, update_fields=None):
            return None

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.local_tool_result_cache = {}
            self.model = "gpt-5.4-mini"
            self.compaction_calls = 0

        async def compact_conversation(self, input_items, instructions=None):
            self.compaction_calls += 1
            return ([{"type": "compaction", "encrypted_content": "opaque"}], {})

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration in (1, 2):
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": f"call_{self._iteration}",
                            "arguments": '{"document_id": 100}',
                        }
                    ],
                    usage={"input_tokens": 240000, "output_tokens": 5000},
                    response_id=f"resp_{self._iteration}",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1000, "output_tokens": 200},
                    output_items=[],
                    response_id="resp_3",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    assert execute_calls
    assert client.compaction_calls == 1
    assert outputs[-1]["text"] == "Done"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_allows_second_proactive_compaction_for_new_outputs(
    monkeypatch,
):
    """A later batch of new uncached tool outputs may proactively compact again.

    Cached replays after a compaction should not trigger another proactive
    compaction, but genuinely new tool outputs still should.
    """

    execute_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        execute_calls.append((tool_name, arguments))
        return {"success": True, "result": {"text": "x" * 5000}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 8
        chat_auto_approve_tools = []
        chat_context_management = "compact"

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.local_tool_result_cache = {}
            self.model = "gpt-5.4-mini"
            self.compaction_calls = 0

        async def compact_conversation(self, input_items, instructions=None):
            self.compaction_calls += 1
            return ([{"type": "compaction", "encrypted_content": "opaque"}], {})

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_1",
                            "arguments": '{"document_id": 100}',
                        }
                    ],
                    usage={"input_tokens": 240000, "output_tokens": 5000},
                    response_id="resp_1",
                )
            elif self._iteration == 2:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_2",
                            "arguments": '{"document_id": 100}',
                        }
                    ],
                    usage={"input_tokens": 19000, "output_tokens": 250},
                    response_id="resp_2",
                )
            elif self._iteration == 3:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_3",
                            "arguments": '{"document_id": 101}',
                        }
                    ],
                    usage={"input_tokens": 199000, "output_tokens": 250},
                    response_id="resp_3",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1000, "output_tokens": 200},
                    output_items=[],
                    response_id="resp_4",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    assert execute_calls == [
        ("get_document_text", '{"document_id": 100}'),
        ("get_document_text", '{"document_id": 101}'),
    ]
    assert client.compaction_calls == 2
    assert outputs[-1]["text"] == "Done"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_reactively_compacts_apierror_context_overflow(
    monkeypatch,
):
    """Streaming context-window APIError should trigger reactive compaction."""

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        return {"success": True, "result": {"text": "x" * 5000}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 8
        chat_auto_approve_tools = []
        chat_context_management = "compact"

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.local_tool_result_cache = {}
            self.model = "gpt-5.4-mini"
            self.compaction_calls = 0

        async def compact_conversation(self, input_items, instructions=None):
            self.compaction_calls += 1
            return (
                [{"type": "compaction", "encrypted_content": "opaque-reactive"}],
                {},
            )

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": "call_1",
                            "arguments": '{"document_id": 100}',
                        }
                    ],
                    usage={"input_tokens": 1000, "output_tokens": 100},
                    response_id="resp_1",
                )
            elif self._iteration == 2:
                raise APIError(
                    "Your input exceeds the context window of this model. Please adjust your input and try again.",
                    request=httpx.Request(
                        "POST", "https://example.invalid/openai/v1/responses"
                    ),
                    body={"code": "context_length_exceeded"},
                )
            else:
                yield StreamChunk(
                    text="Recovered",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1000, "output_tokens": 200},
                    output_items=[],
                    response_id="resp_2",
                )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    assert client.compaction_calls == 1
    assert outputs[-1]["text"] == "Recovered"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_allows_reactive_compaction_after_proactive(
    monkeypatch,
):
    """A post-tool context overflow should still recover after proactive compaction.

    This guards the case where one proactive compaction already occurred, then
    later model/tool activity triggers another context overflow.
    """
    from unittest.mock import MagicMock

    execute_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        execute_calls.append((tool_name, arguments))
        return {"success": True, "result": {"text": "x" * 5000}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 8
        chat_auto_approve_tools = []
        chat_context_management = "compact"

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.local_tool_result_cache = {}
            self.model = "gpt-5.4-mini"
            self.compaction_calls = 0

        async def compact_conversation(self, input_items, instructions=None):
            self.compaction_calls += 1
            return (
                [
                    {
                        "type": "compaction",
                        "encrypted_content": f"opaque-{self.compaction_calls}",
                    }
                ],
                {},
            )

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration in (1, 2):
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "get_document_text",
                            "call_id": f"call_{self._iteration}",
                            "arguments": '{"document_id": 100}',
                        }
                    ],
                    usage={"input_tokens": 240000, "output_tokens": 5000},
                    response_id=f"resp_{self._iteration}",
                )
                return

            if self._iteration == 3:
                err_response = MagicMock()
                err_response.status_code = 400
                raise BadRequestError(
                    message="This model's maximum context length was exceeded.",
                    response=err_response,
                    body={"error": {"code": "context_length_exceeded"}},
                )

            yield StreamChunk(
                text="Done",
                tool_calls=[],
                processing_steps=[],
                is_reasoning=False,
                is_complete=True,
                usage={"input_tokens": 1000, "output_tokens": 200},
                output_items=[],
                response_id="resp_final",
            )

    client = DummyClient()

    outputs = []
    async for update in stream_chat_for_htmx(client, input_items=[{"role": "user"}]):
        outputs.append(update)

    assert execute_calls
    assert client.compaction_calls == 2  # one proactive + one reactive
    assert outputs[-1]["text"] == "Done"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_approves_safe_a2aj_coverage_query(
    monkeypatch,
):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"doc_type": "cases", "result_count": 1}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "list_canadian_legal_datasets",
                            "call_id": "call_coverage_cases",
                            "arguments": '{"doc_type": "cases"}',
                        }
                    ],
                    response_id="resp_cases_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_cases_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "Show legal datasets"}]
    ):
        outputs.append(update)

    assert executed_calls == [("list_canadian_legal_datasets", '{"doc_type": "cases"}')]

    completed_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_steps) == 1
    assert completed_steps[0]["details"]["approval_source"] == "query_policy"
    assert (
        completed_steps[0]["details"]["approval_rule"]
        == "a2aj_coverage_doc_type_safe_list"
    )


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_approves_broad_a2aj_coverage(
    monkeypatch,
):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"doc_type": "both", "result_count": 2}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            yield StreamChunk(
                text="",
                tool_calls=[],
                processing_steps=[],
                is_reasoning=False,
                is_complete=True,
                output_items=[
                    {
                        "type": "function_call",
                        "name": "list_canadian_legal_datasets",
                        "call_id": "call_coverage_both",
                        "arguments": '{"doc_type": "both"}',
                    }
                ],
                response_id="resp_both_1",
            )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[{"role": "user", "content": "Show all legal coverage"}],
    ):
        outputs.append(update)

    assert executed_calls == [("list_canadian_legal_datasets", '{"doc_type": "both"}')]

    completed_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_steps) == 1
    assert completed_steps[0]["details"]["approval_source"] == "query_policy"
    assert (
        completed_steps[0]["details"]["approval_rule"]
        == "a2aj_coverage_doc_type_safe_list"
    )


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_user_auto_approves_broad_a2aj_coverage(
    monkeypatch,
):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"doc_type": "both", "result_count": 2}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = ["list_canadian_legal_datasets"]

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "list_canadian_legal_datasets",
                            "call_id": "call_coverage_both_auto",
                            "arguments": '{"doc_type": "both"}',
                        }
                    ],
                    response_id="resp_both_auto_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_both_auto_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[{"role": "user", "content": "Show all legal coverage"}],
    ):
        outputs.append(update)

    assert executed_calls == [("list_canadian_legal_datasets", '{"doc_type": "both"}')]

    waiting_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert waiting_steps == []


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_approves_safe_a2aj_case_fetch(monkeypatch):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {"success": True, "result": {"citation_requested": "2020 SCC 5"}}

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "fetch_canadian_case_by_citation",
                            "call_id": "call_case_fetch_safe",
                            "arguments": '{"citation": "2020 SCC 5"}',
                        }
                    ],
                    response_id="resp_case_fetch_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_case_fetch_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "Fetch 2020 SCC 5"}]
    ):
        outputs.append(update)

    assert executed_calls == [
        ("fetch_canadian_case_by_citation", '{"citation": "2020 SCC 5"}')
    ]

    completed_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_steps) == 1
    assert completed_steps[0]["details"]["approval_source"] == "query_policy"
    assert (
        completed_steps[0]["details"]["approval_rule"]
        == "a2aj_safe_case_citation_fetch"
    )


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_approves_multiple_exact_a2aj_case_fetch_slices(
    monkeypatch,
):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {
            "success": True,
            "result": {"citation_requested": json.loads(arguments)["citation"]},
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "fetch_canadian_case_by_citation",
                            "call_id": "call_case_fetch_full_1",
                            "arguments": '{"citation": "2026 SCC 9", "output_language": "en", "start_char": 25000, "end_char": 50000}',
                        },
                        {
                            "type": "function_call",
                            "name": "fetch_canadian_case_by_citation",
                            "call_id": "call_case_fetch_full_2",
                            "arguments": '{"citation": "2026 SCC 8", "output_language": "en", "start_char": 50000, "end_char": 75000}',
                        },
                        {
                            "type": "function_call",
                            "name": "fetch_canadian_case_by_citation",
                            "call_id": "call_case_fetch_full_3",
                            "arguments": '{"citation": "2026 SCC 7", "output_language": "en", "start_char": 75000, "end_char": -1}',
                        },
                    ],
                    response_id="resp_case_fetch_full_batch_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_case_fetch_full_batch_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[
            {"role": "user", "content": "Fetch 2026 SCC 9, 2026 SCC 8, and 2026 SCC 7"}
        ],
    ):
        outputs.append(update)

    assert executed_calls == [
        (
            "fetch_canadian_case_by_citation",
            '{"citation": "2026 SCC 9", "output_language": "en", "start_char": 25000, "end_char": 50000}',
        ),
        (
            "fetch_canadian_case_by_citation",
            '{"citation": "2026 SCC 8", "output_language": "en", "start_char": 50000, "end_char": 75000}',
        ),
        (
            "fetch_canadian_case_by_citation",
            '{"citation": "2026 SCC 7", "output_language": "en", "start_char": 75000, "end_char": -1}',
        ),
    ]

    final = outputs[-1]
    waiting_steps = [
        step
        for step in final["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert waiting_steps == []
    assert "pending_local_tool" not in final

    completed_steps = [
        step
        for step in final["processing_steps"]
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_steps) == 3
    assert all(
        step["details"]["approval_source"] == "query_policy" for step in completed_steps
    )


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_approves_safe_a2aj_legislation_fetch(
    monkeypatch,
):
    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {
            "success": True,
            "result": {"citation_requested": "RSC 1985, c C-46", "section": "219"},
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "fetch_canadian_legislation_by_citation",
                            "call_id": "call_law_fetch_safe",
                            "arguments": '{"citation": "RSC 1985, c C-46", "section": "219"}',
                        }
                    ],
                    response_id="resp_law_fetch_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_law_fetch_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[
            {"role": "user", "content": "Fetch section 219 of the Criminal Code"}
        ],
    ):
        outputs.append(update)

    assert executed_calls == [
        (
            "fetch_canadian_legislation_by_citation",
            '{"citation": "RSC 1985, c C-46", "section": "219"}',
        )
    ]

    completed_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("type") == "tool_call"
        and step.get("tool_type") == "function_call"
        and step.get("status") == "completed"
    ]
    assert len(completed_steps) == 1
    assert completed_steps[0]["details"]["approval_source"] == "query_policy"
    assert (
        completed_steps[0]["details"]["approval_rule"]
        == "a2aj_safe_legislation_citation_fetch"
    )


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_requires_manual_approval_for_ambiguous_a2aj_legislation_fetch():
    class DummyOptions:
        chat_max_iterations = 5
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            yield StreamChunk(
                text="",
                tool_calls=[],
                processing_steps=[],
                is_reasoning=False,
                is_complete=True,
                output_items=[
                    {
                        "type": "function_call",
                        "name": "fetch_canadian_legislation_by_citation",
                        "call_id": "call_law_fetch_ambiguous",
                        "arguments": '{"citation": "C-46"}',
                    }
                ],
                response_id="resp_law_fetch_ambiguous_1",
            )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[{"role": "user", "content": "Fetch C-46"}],
    ):
        outputs.append(update)

    final = outputs[-1]
    waiting_steps = [
        step
        for step in final["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert len(waiting_steps) == 1
    assert (
        waiting_steps[0]["details"]["name"] == "fetch_canadian_legislation_by_citation"
    )
    assert waiting_steps[0]["details"]["approval_source"] == "manual"
    assert final["pending_local_tool"]["approval_source"] == "manual"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_uses_approval_label_for_pending_tools():
    """Approval-required tools should expose a friendly tool label without crashing."""

    termium_tool = TOOL_REGISTRY.get("termium_lookup")
    assert termium_tool is not None
    assert termium_tool.display_name == "Termium lookup"

    class DummyOptions:
        chat_max_iterations = 2
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "termium_lookup",
                            "call_id": "call_termium_1",
                            "arguments": '{"query": "deputy head", "index": "ent"}',
                        }
                    ],
                    response_id="resp_approval_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_approval_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "look it up"}]
    ):
        outputs.append(update)

    approval_update = outputs[-1]
    pending_tool = approval_update["pending_local_tool"]

    assert pending_tool["name"] == "termium_lookup"
    assert pending_tool["tool_label"] == "Termium lookup"
    assert pending_tool["call_id"] == "call_termium_1"

    waiting_steps = [
        step
        for step in approval_update["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert len(waiting_steps) == 1
    assert waiting_steps[0]["details"]["tool_label"] == "Termium lookup"
    assert waiting_steps[0]["details"]["approval_source"] == "manual"


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_attaches_risk_review_to_pending_external_approval():
    class DummyOptions:
        chat_max_iterations = 2
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "termium_lookup",
                            "call_id": "call_termium_risky_1",
                            "arguments": '{"query": "cabinet confidence jane.doe@example.com", "index": "ent"}',
                        }
                    ],
                    response_id="resp_risky_approval_1",
                )
            else:
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_risky_approval_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "translate it"}]
    ):
        outputs.append(update)

    approval_update = outputs[-1]
    waiting_steps = [
        step
        for step in approval_update["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]

    assert len(waiting_steps) == 1
    assert waiting_steps[0]["details"]["pii_flagged"] is True
    assert waiting_steps[0]["details"]["risk_review"]["flagged"] is True
    assert (
        "Email" in waiting_steps[0]["details"]["risk_review"]["pii_entity_categories"]
    )
    assert (
        "privileged_or_classified"
        in waiting_steps[0]["details"]["risk_review"]["matched_marker_ids"]
    )
    assert any(
        "personal information" in item.lower()
        for item in waiting_steps[0]["details"]["risk_review"]["summary_items"]
    )
    assert approval_update["pending_local_tool"]["pii_flagged"] is True
    assert approval_update["pending_local_tool"]["risk_review"]["flagged"] is True


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_auto_executes_prompt_documents_below_cost_threshold(
    monkeypatch,
):
    """prompt_documents should run without manual approval unless the cost gate trips."""

    executed_calls = []

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        executed_calls.append((tool_name, arguments))
        return {
            "success": True,
            "result": {
                "success": True,
                "message": "Processed 1 document.",
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 2
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "prompt_documents",
                            "call_id": "call_prompt_docs_1",
                            "arguments": '{"document_ids": [1], "prompt": "Summarize each document in markdown.", "template_doc_id": null, "llm_model": null, "reasoning_effort": "default", "truncate_chars": null, "include_generated_outputs": false}',
                        }
                    ],
                    response_id="resp_prompt_docs_1",
                )
            else:
                assert any(
                    item.get("type") == "function_call_output" for item in input_items
                )
                yield StreamChunk(
                    text="Done",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_prompt_docs_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[{"role": "user", "content": "summarize each file"}],
    ):
        outputs.append(update)

    assert executed_calls == [
        (
            "prompt_documents",
            '{"document_ids": [1], "prompt": "Summarize each document in markdown.", "template_doc_id": null, "llm_model": null, "reasoning_effort": "default", "truncate_chars": null, "include_generated_outputs": false}',
        )
    ]
    assert outputs[-1]["text"] == "Done"
    assert outputs[-1].get("pending_local_tool") is None
    waiting_steps = [
        step
        for step in outputs[-1]["processing_steps"]
        if step.get("status") == "waiting_approval"
    ]
    assert waiting_steps == []


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_prepends_open_skill_link_when_missing(monkeypatch):
    """Created skills should always surface an open-skill token near the top."""

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        assert tool_name == "create_skill"
        return {
            "success": True,
            "result": {
                "skill_id": 42,
                "display_name_en": "Policy Helper",
                "edit_url": "skill://42",
                "edit_link_token": "[[OPEN_SKILL:42|Policy Helper]]",
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.unlocked_local_skill_tools = True

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "create_skill",
                            "call_id": "call_create_1",
                            "arguments": '{"display_name_en": "Policy Helper", "description_en": "Helps with policy", "body_en": "Do policy work."}',
                        }
                    ],
                    response_id="resp_create_1",
                )
            else:
                yield StreamChunk(
                    text="Your skill is ready. I also drafted a concise description.",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_create_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "Create a skill"}]
    ):
        outputs.append(update)

    final_text = outputs[-1]["text"]
    assert final_text.startswith("[[OPEN_SKILL:42|Policy Helper]]\n\n")


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_prepends_open_skill_link_for_edit_results(
    monkeypatch,
):
    """Edited skills should also surface an open-skill token near the top."""

    from chat_next._tools.approval import ApprovalDecision

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        assert tool_name == "edit_skill"
        return {
            "success": True,
            "result": {
                "skill_id": 42,
                "skill_name": "policy-helper",
                "display_name_en": "Policy Helper",
                "edit_url": "skill://42",
                "edit_link_token": "[[OPEN_SKILL:42|Policy Helper]]",
                "updated_fields": ["body_en"],
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    async def fake_evaluate_approval_policy(tool, user, chat, function_call):
        return ApprovalDecision(
            auto_approve=True,
            approval_source="query_policy",
            matched_rule="test-auto-approve-edit-skill",
        )

    monkeypatch.setattr(
        "chat_next._llm.openai_responses.evaluate_approval_policy",
        fake_evaluate_approval_policy,
    )

    class DummySettings:
        chat_max_iterations = 25
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummySettings()
        loaded_skill_state = {}

        def save(self, update_fields=None):
            return None

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.unlocked_local_skill_tools = True

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1

            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "edit_skill",
                            "call_id": "call_edit_1",
                            "arguments": '{"skill_id": 42, "body_en": "Updated"}',
                        }
                    ],
                    response_id="resp_edit_1",
                )
            else:
                yield StreamChunk(
                    text="I updated the skill instructions and kept the existing context hints.",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_edit_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(), input_items=[{"role": "user", "content": "Edit this skill"}]
    ):
        outputs.append(update)

    final_text = outputs[-1]["text"]
    assert final_text.startswith("[[OPEN_SKILL:42|Policy Helper]]\n\n")


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_unlocks_and_injects_valid_skill_tools_from_loader(
    monkeypatch,
):
    """Loaded skill output should unlock hidden skill tools and inject only valid categories."""

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        assert tool_name == "load_skill_instructions"
        return {
            "success": True,
            "result": {
                "instructions": "Use the skill creator workflow.",
                "required_tools": ["local_skills", "local_document_processing"],
                "context_hints": [
                    {
                        "type": "tool",
                        "id": "local_qa_libraries",
                        "name": "Libraries",
                    },
                    {
                        "type": "tool",
                        "id": "get_document_text",
                        "name": "Get document text",
                    },
                ],
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.tools = []
            self.unlocked_local_skill_tools = False

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "load_skill_instructions",
                            "call_id": "call_load_1",
                            "arguments": '{"skill_id": 7}',
                        }
                    ],
                    response_id="resp_load_1",
                )
            else:
                assert self.previous_response_id == "resp_load_1"
                assert len(input_items) == 1
                assert input_items[0]["type"] == "function_call_output"
                assert input_items[0]["call_id"] == "call_load_1"
                assert json.loads(input_items[0]["output"]) == {
                    "instructions": "Use the skill creator workflow.",
                    "required_tools": [
                        "local_skills",
                        "local_document_processing",
                    ],
                    "context_hints": [
                        {
                            "type": "tool",
                            "id": "local_qa_libraries",
                            "name": "Libraries",
                        },
                        {
                            "type": "tool",
                            "id": "get_document_text",
                            "name": "Get document text",
                        },
                    ],
                }
                assert "local_skills" in self.tools
                assert "local_document_processing" in self.tools
                assert "local_qa_libraries" in self.tools
                assert "get_document_text" not in self.tools
                assert self.unlocked_local_skill_tools is True
                assert "LOADED SKILL:" in instructions
                assert "id=7" in instructions
                assert "Use the skill creator workflow." in instructions
                assert "do not reload unless the user asks" in instructions
                yield StreamChunk(
                    text="Unlocked.",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_load_2",
                )

    dummy = DummyClient()
    outputs = []
    async for update in stream_chat_for_htmx(
        dummy,
        input_items=[{"role": "user", "content": "Help me create a skill"}],
    ):
        outputs.append(update)

    assert outputs[-1]["text"] == "Unlocked."
    assert dummy.unlocked_local_skill_tools is True


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_continues_after_skill_load_with_updated_tools(
    monkeypatch,
):
    """Skill loads should continue on the same response chain with updated tools."""

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        assert tool_name == "load_skill_instructions"
        return {
            "success": True,
            "result": {
                "instructions": "Use create_skill to create the requested skill.",
                "required_tools": ["local_skills"],
                "context_hints": [
                    {
                        "type": "tool",
                        "id": "get_document_text",
                        "name": "Get document text",
                    }
                ],
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = None
            self.user = None
            self.previous_response_id = None
            self.tools = []
            self.unlocked_local_skill_tools = False

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                assert self.previous_response_id is None
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "load_skill_instructions",
                            "call_id": "call_load_restart_1",
                            "arguments": '{"skill_id": 7}',
                        }
                    ],
                    response_id="resp_load_restart_1",
                )
            else:
                assert self.previous_response_id == "resp_load_restart_1"
                assert len(input_items) == 1
                assert input_items[0]["type"] == "function_call_output"
                assert input_items[0]["call_id"] == "call_load_restart_1"
                assert json.loads(input_items[0]["output"]) == {
                    "instructions": "Use create_skill to create the requested skill.",
                    "required_tools": ["local_skills"],
                    "context_hints": [
                        {
                            "type": "tool",
                            "id": "get_document_text",
                            "name": "Get document text",
                        }
                    ],
                }
                assert self.unlocked_local_skill_tools is True
                assert "local_skills" in self.tools
                assert "LOADED SKILL:" in instructions
                assert "id=7" in instructions
                assert "Use create_skill to create the requested skill." in instructions
                assert "do not reload unless the user asks" in instructions
                assert "get_document_text" not in instructions
                yield StreamChunk(
                    text="create_skill\nedit_skill\nlist_presets",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    usage={"input_tokens": 1, "output_tokens": 1},
                    output_items=[],
                    response_id="resp_load_restart_2",
                )

    dummy = DummyClient()
    outputs = []
    async for update in stream_chat_for_htmx(
        dummy,
        input_items=[{"role": "user", "content": "Help me create a skill"}],
    ):
        outputs.append(update)

    assert outputs[-1]["text"] == "create_skill\nedit_skill\nlist_presets"
    assert dummy._iteration == 2


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_persists_loaded_skill_state_for_approval_resume(
    monkeypatch,
):
    """Approval pauses should carry forward dynamically loaded skill state."""

    async def fake_execute_tool_call(
        tool_name,
        arguments,
        user,
        chat,
        extra_context=None,
    ):
        assert tool_name == "load_skill_instructions"
        return {
            "success": True,
            "result": {
                "instructions": "Use edit_skill after approval.",
                "required_tools": ["local_skills"],
                "context_hints": [
                    {
                        "type": "tool",
                        "id": "local_qa_libraries",
                        "name": "Libraries",
                    }
                ],
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)

    class DummyOptions:
        chat_max_iterations = 25
        chat_auto_approve_tools = []

    class DummyChat:
        settings = DummyOptions()
        loaded_skill_state = {}

        def save(self, update_fields=None):
            return None

    class DummyClient:
        def __init__(self):
            self._iteration = 0
            self.chat = DummyChat()
            self.user = None
            self.previous_response_id = None
            self.tools = []
            self.unlocked_local_skill_tools = False
            self.model = "gpt-5.4"

        async def stream_chat(self, input_items, instructions=None):
            self._iteration += 1
            if self._iteration == 1:
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "load_skill_instructions",
                            "call_id": "call_load_state_1",
                            "arguments": '{"skill_id": 13}',
                        }
                    ],
                    response_id="resp_load_state_1",
                )
            else:
                assert self.previous_response_id == "resp_load_state_1"
                assert self.unlocked_local_skill_tools is True
                assert "local_skills" in self.tools
                yield StreamChunk(
                    text="",
                    tool_calls=[],
                    processing_steps=[],
                    is_reasoning=False,
                    is_complete=True,
                    output_items=[
                        {
                            "type": "function_call",
                            "name": "edit_skill",
                            "call_id": "call_edit_skill_1",
                            "arguments": '{"skill_id": 1, "display_name_en": "Updated"}',
                        }
                    ],
                    response_id="resp_load_state_2",
                )

    outputs = []
    async for update in stream_chat_for_htmx(
        DummyClient(),
        input_items=[{"role": "user", "content": "Help me edit the skill"}],
    ):
        outputs.append(update)

    pending_local_tool = outputs[-1]["pending_local_tool"]
    loaded_skill_state = pending_local_tool["loaded_skill_state"]

    assert pending_local_tool["name"] == "edit_skill"
    assert set(loaded_skill_state["tool_ids"]) == {
        "local_skills",
        "local_qa_libraries",
    }
    assert loaded_skill_state["skill_names"] == ["id=13"]
    assert any(
        "Use edit_skill after approval." in block
        for block in loaded_skill_state["instruction_blocks"]
    )
    assert "local_skills" in loaded_skill_state["tool_prompt_ids"]


@pytest.mark.django_db
def test_get_effective_enabled_tools_includes_persisted_loaded_skill_tools(
    all_apps_user,
):
    from chat_next.prompts import get_effective_enabled_tools

    user = all_apps_user("persisted-loaded-skill-tools")
    chat = Chat.objects.create(user=user, title="Persisted skill tools")
    chat.loaded_skill_state = {
        "tool_ids": ["local_skills", "local_document_processing"],
        "skill_names": ["Skill Creator [id=7]"],
        "instruction_blocks": ["---\nLOADED SKILL:\nSkill Creator [id=7]\n..."],
        "tool_prompt_ids": ["local_skills", "local_document_processing"],
    }
    chat.save(update_fields=["loaded_skill_state"])

    enabled_tools = get_effective_enabled_tools(
        chat.settings,
        chat=chat,
        user=user,
    )

    assert "local_skills" in enabled_tools
    assert "local_document_processing" in enabled_tools


@pytest.mark.django_db
def test_build_system_prompt_restores_persisted_loaded_skill_instructions(
    all_apps_user,
):
    from chat_next._llm.openai_responses import build_system_prompt

    user = all_apps_user("persisted-loaded-skill-prompt")
    chat = Chat.objects.create(user=user, title="Persisted skill prompt")
    chat.loaded_skill_state = {
        "tool_ids": ["local_skills"],
        "skill_names": ["Skill Editor [id=13]"],
        "instruction_blocks": [
            "---\nLOADED SKILL:\nSkill Editor [id=13]\nAlready loaded for this turn; do not reload unless the user asks.\n\nUse edit_skill after approval.\n---"
        ],
        "tool_prompt_ids": ["local_skills"],
    }
    chat.save(update_fields=["loaded_skill_state"])

    prompt = build_system_prompt(chat)

    assert "LOADED SKILL:" in prompt
    assert "Skill Editor [id=13]" in prompt
    assert "Use edit_skill after approval." in prompt


@pytest.mark.django_db
def test_persisted_loaded_skill_state_is_chat_scoped(all_apps_user):
    from chat_next.prompts import get_effective_enabled_tools

    user = all_apps_user("persisted-loaded-skill-chat-scope")
    chat_with_skill = Chat.objects.create(user=user, title="Chat A")
    other_chat = Chat.objects.create(user=user, title="Chat B")

    chat_with_skill.loaded_skill_state = {
        "tool_ids": ["local_skills"],
        "skill_names": ["Document Skill [id=99]"],
        "instruction_blocks": [],
        "tool_prompt_ids": ["local_skills"],
    }
    chat_with_skill.save(update_fields=["loaded_skill_state"])

    enabled_in_chat_a = get_effective_enabled_tools(
        chat_with_skill.settings,
        chat=chat_with_skill,
        user=user,
    )

    assert "local_skills" in enabled_in_chat_a
    assert other_chat.loaded_skill_state == {}


@pytest.mark.django_db
def test_approval_stream_adds_manual_function_outputs(
    client, all_apps_user, monkeypatch
):
    """Manual approvals must persist executed tool outputs in response_output."""

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream test")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the tool call",
        is_bot=False,
    )

    pending_call_id = "call_manual_1"
    pending_tool_name = "termium_lookup"

    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == pending_tool_name
        return {"success": True, "result": {"answer": "Termium definition"}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        assert any(item.get("type") == "function_call_output" for item in input_items)
        yield {
            "text": "Approved response",
            "processing_steps": [],
            "reasoning_steps": [],
            "tool_calls": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_manual",
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    bot_message.refresh_from_db()
    output_items = bot_message.response_output
    assert len(output_items) == 2
    assert output_items[0]["type"] == "function_call"
    assert output_items[1]["type"] == "function_call_output"
    assert output_items[1]["call_id"] == pending_call_id
    assert output_items[1]["output"]["answer"] == "Termium definition"


@pytest.mark.django_db
def test_approval_stream_restores_loaded_skill_state(
    client, all_apps_user, monkeypatch
):
    """Approval resume should rehydrate dynamically loaded skill tools/instructions."""

    user = all_apps_user("approval-stream-loaded-skill-state")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval skill-state restore test")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the skill edit",
        is_bot=False,
    )

    pending_call_id = "call_restore_skill_state_1"
    pending_tool_name = "edit_skill"
    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"skill_id": 1, "display_name_en": "Updated"}',
    }
    loaded_instruction_block = (
        "---\nLOADED SKILL:\n"
        "Skill Editor [id=1]\n"
        "Already loaded for this turn; do not reload unless the user asks.\n\n"
        "Use edit_skill after approval.\n"
        "---"
    )

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_restore_original",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Edit skill",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
                "loaded_skill_state": {
                    "tool_ids": ["local_skills"],
                    "skill_names": ["Skill Editor [id=1]"],
                    "instruction_blocks": [loaded_instruction_block],
                    "tool_prompt_ids": ["local_skills"],
                },
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == pending_tool_name
        return {"success": True, "result": {"updated": True}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.unlocked_local_skill_tools = False
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        assert any(item.get("type") == "function_call_output" for item in input_items)
        assert client_obj.unlocked_local_skill_tools is True
        assert "LOADED SKILL:" in instructions
        assert "skill-editor" in instructions
        assert "Use edit_skill after approval." in instructions
        yield {
            "text": "Approved response",
            "processing_steps": [],
            "reasoning_steps": [],
            "tool_calls": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_restore",
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)


@pytest.mark.django_db
def test_loaded_skill_prompt_includes_document_resource_hints():
    from chat_next._llm.openai_responses import (
        _format_loaded_skill_instructions_for_prompt,
    )

    prompt = _format_loaded_skill_instructions_for_prompt(
        "translate-workflow",
        {
            "instructions": "Use the glossary before translating.",
            "context_hints_note": (
                "Use existing tools to access these hinted resources."
            ),
            "context_hints": [
                {
                    "type": "document",
                    "id": "6",
                    "name": "JUS Translation Glossary",
                }
            ],
        },
    )

    assert "LOADED SKILL:" in prompt
    assert "translate-workflow" in prompt
    assert "Use the glossary before translating." in prompt
    assert "Skill resource hints:" in prompt
    assert "JUS Translation Glossary[6]" in prompt
    assert "Never expose internal IDs." in prompt


@pytest.mark.django_db
def test_approval_stream_rebuilds_full_conversation_when_no_outputs_and_no_response_id(
    client, all_apps_user, monkeypatch
):
    """Approval resume should not call the API with empty input and no response_id."""

    user = all_apps_user("approval-stream-empty-continuation")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval empty continuation test")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the tool call",
        is_bot=False,
    )

    pending_call_id = "call_empty_resume_1"
    pending_tool_name = "termium_lookup"

    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }
    function_call_output_item = {
        "type": "function_call_output",
        "call_id": pending_call_id,
        "output": {"answer": "Already have tool output"},
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item, function_call_output_item],
        response_id="",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    captured = {}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        captured["input_items"] = input_items
        captured["full_input_items"] = full_input_items
        yield {
            "text": "Approved response after rebuild",
            "processing_steps": [],
            "reasoning_steps": [],
            "tool_calls": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_rebuild",
        }

    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    assert captured["input_items"] == captured["full_input_items"]
    assert any(
        isinstance(item, dict) and item.get("role") == "user"
        for item in captured["input_items"]
    )


@pytest.mark.django_db
def test_approval_stream_error_clears_stale_waiting_approval_state(
    client, all_apps_user, monkeypatch
):
    """Refresh should not resurrect approval buttons after an approved stream errors."""

    user = all_apps_user("approval-stream-error")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream error test")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the tool call",
        is_bot=False,
    )

    pending_call_id = "call_manual_error_1"
    pending_tool_name = "termium_lookup"

    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "processing_steps": [
                {
                    "title": "Approval required: Search Canadian case law",
                    "status": "waiting_approval",
                    "is_approval_request": True,
                    "approval_status": "pending",
                }
            ],
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == pending_tool_name
        return {"success": True, "result": {"answer": "Termium definition"}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        err_response = type("Resp", (), {"status_code": 400})()
        raise BadRequestError(
            message="You input exceeds the context window of this model.",
            response=err_response,
            body={"error": {"code": "context_length_exceeded"}},
        )
        yield  # pragma: no cover

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    bot_message.refresh_from_db()

    assert "pending_local_tool" not in bot_message.details
    assert bot_message.details.get("processing_steps") in (None, [])
    assert bot_message.details.get("raw_processing_steps") in (None, [])
    assert bot_message.text


@pytest.mark.django_db
def test_approval_stream_proactively_compacts_manual_outputs_before_continuation(
    client, all_apps_user, monkeypatch
):
    """Manual approval should trigger the same proactive compaction gate as auto-approved tools."""

    user = all_apps_user("approval-stream-proactive-compaction")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream compaction parity")
    chat.settings.chat_model = "gpt-5.4-mini"
    chat.settings.chat_context_management = "compact"
    chat.settings.save(update_fields=["chat_model", "chat_context_management"])

    user_message = Message.objects.create(
        chat=chat,
        text="Read these very large documents",
        is_bot=False,
    )

    function_call_item = {
        "type": "function_call",
        "name": "get_document_text",
        "call_id": "call_manual_compact_1",
        "arguments": '{"document_id": 1001}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "usage": {"input_tokens": 240000, "output_tokens": 5000},
            "pending_local_tool": {
                "name": "get_document_text",
                "call_id": "call_manual_compact_1",
                "arguments": function_call_item["arguments"],
                "tool_label": "Get Document Text",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": "get_document_text"},
                }
            ],
        },
    )

    big_text = "x" * 25000
    captured = {"stream_calls": 0}

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == "get_document_text"
        return {"success": True, "result": {"text": big_text}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.model = kwargs.get("model")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

        async def compact_conversation(self, input_items, instructions=None):
            captured["compact_input_items"] = input_items
            captured["compact_instructions"] = instructions
            return (
                [{"type": "compaction", "encrypted_content": "opaque-approval"}],
                {"input_tokens": 30000, "output_tokens": 100},
            )

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        captured["stream_calls"] += 1
        captured["stream_input_items"] = input_items
        captured["stream_full_input_items"] = full_input_items
        captured["initial_processing_steps"] = initial_processing_steps or []
        yield {
            "text": "Approved response after compaction",
            "processing_steps": initial_processing_steps or [],
            "reasoning_steps": [],
            "tool_calls": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_manual_compaction",
        }

    compaction_costs = []

    def fake_create_compaction_costs(compaction_usage, model_id):
        compaction_costs.append((compaction_usage, model_id))
        return 0.0

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )
    monkeypatch.setattr(
        "chat_next.responses.create_compaction_costs",
        fake_create_compaction_costs,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    assert captured["stream_calls"] == 1
    assert captured["stream_input_items"] == [
        {"type": "compaction", "encrypted_content": "opaque-approval"}
    ]
    assert captured["initial_processing_steps"] == [COMPACTION_PROCESSING_STEP]
    assert any(
        isinstance(item, dict) and item.get("role") == "user"
        for item in captured["compact_input_items"]
    )
    assert any(
        isinstance(item, dict) and item.get("type") == "function_call_output"
        for item in captured["compact_input_items"]
    )
    assert compaction_costs == [
        ({"input_tokens": 30000, "output_tokens": 100}, "gpt-5.4-mini")
    ]

    bot_message.refresh_from_db()
    output_items = bot_message.response_output
    assert len(output_items) == 2
    assert output_items[0]["type"] == "function_call"
    assert output_items[1]["type"] == "function_call_output"
    assert output_items[1]["call_id"] == "call_manual_compact_1"


@pytest.mark.django_db
def test_approval_stream_success_clears_stale_waiting_approval_state_without_new_steps(
    client, all_apps_user, monkeypatch
):
    """Successful approval continuation should clear persisted waiting-approval state even when the resumed stream returns no processing steps."""

    user = all_apps_user("approval-stream-success-clears-stale-state")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream stale state cleanup")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the tool call",
        is_bot=False,
    )

    pending_call_id = "call_manual_success_cleanup_1"
    pending_tool_name = "termium_lookup"

    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "processing_steps": [
                {
                    "title": "Approval required: Search Canadian case law",
                    "status": "waiting_approval",
                    "is_approval_request": True,
                    "approval_status": "pending",
                }
            ],
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == pending_tool_name
        return {"success": True, "result": {"answer": "Termium definition"}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        yield {
            "text": "Approved response without replacement processing steps",
            "reasoning_steps": [],
            "tool_calls": [],
            "processing_steps": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_manual_cleanup",
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    bot_message.refresh_from_db()
    processing_steps = bot_message.details.get("processing_steps") or []
    raw_processing_steps = bot_message.details.get("raw_processing_steps") or []

    assert "pending_local_tool" not in bot_message.details
    assert processing_steps
    assert raw_processing_steps
    assert all(step.get("status") != "waiting_approval" for step in processing_steps)
    assert all(
        step.get("status") != "waiting_approval" for step in raw_processing_steps
    )
    assert bot_message.text == "Approved response without replacement processing steps"


@pytest.mark.django_db
def test_approval_stream_ignores_stale_stop_cache_for_reused_message_id(
    client, all_apps_user, monkeypatch
):
    """A stale stop flag must not abort a fresh approval continuation.

    Test suites can reuse message ids across cases while the Django cache still
    retains `stop_response_<id>` from an earlier run. A new approval stream for
    the reused id should clear that stale flag and continue normally.
    """

    user = all_apps_user("approval-stream-stale-stop-cache")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream stale stop cache")
    user_message = Message.objects.create(
        chat=chat,
        text="Please approve the tool call",
        is_bot=False,
    )

    pending_call_id = "call_manual_stale_stop_1"
    pending_tool_name = "termium_lookup"

    function_call_item = {
        "type": "function_call",
        "name": pending_tool_name,
        "call_id": pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "pending_local_tool": {
                "name": pending_tool_name,
                "call_id": pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": pending_tool_name},
                }
            ],
        },
    )

    captured = {"stream_calls": 0}

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == pending_tool_name
        return {"success": True, "result": {"answer": "Termium definition"}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        captured["stream_calls"] += 1
        yield {
            "text": "Approved response after clearing stale stop cache",
            "reasoning_steps": [],
            "tool_calls": [],
            "processing_steps": [],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_after_stale_stop_cleanup",
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    cache.set(f"stop_response_{bot_message.id}", True, timeout=60)

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    assert captured["stream_calls"] == 1
    bot_message.refresh_from_db()
    assert bot_message.text == "Approved response after clearing stale stop cache"


@pytest.mark.django_db
def test_approval_stream_preserves_committed_text_when_resumed_stream_pauses_again(
    client, all_apps_user, monkeypatch
):
    """A resumed approval flow should not persist provisional text from a new pause."""

    user = all_apps_user("approval-stream-second-pause-text-guard")
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Approval stream second pause guard")
    user_message = Message.objects.create(
        chat=chat,
        text="Please continue the research",
        is_bot=False,
    )

    first_pending_call_id = "call_manual_resume_text_1"
    second_pending_call_id = "call_manual_resume_text_2"
    first_pending_tool_name = "termium_lookup"
    second_pending_tool_name = "search_canadian_case_law"
    stable_text = "Already committed answer.\n\n"

    function_call_item = {
        "type": "function_call",
        "name": first_pending_tool_name,
        "call_id": first_pending_call_id,
        "arguments": '{"query": "deputy head"}',
    }

    bot_message = Message.objects.create(
        chat=chat,
        text=stable_text,
        is_bot=True,
        parent=user_message,
        response_output=[function_call_item],
        response_id="resp_original",
        details={
            "pending_local_tool": {
                "name": first_pending_tool_name,
                "call_id": first_pending_call_id,
                "arguments": function_call_item["arguments"],
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {"name": first_pending_tool_name},
                }
            ],
        },
    )

    async def fake_execute_tool_call(
        tool_name, arguments, user, chat, extra_context=None
    ):
        assert tool_name == first_pending_tool_name
        return {"success": True, "result": {"answer": "Termium definition"}}

    def fake_build_function_call_output(call_id, output):
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    class DummyResponsesClient:
        def __init__(self, **kwargs):
            self.previous_response_id = kwargs.get("previous_response_id")
            self.code_interpreter_container_id = kwargs.get(
                "code_interpreter_container_id"
            )
            self.tools = kwargs.get("tools", [])
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")
            self.last_usage = None
            self.last_tool_calls = None
            self.last_code_interpreter_sessions = 0

    async def fake_stream_chat_for_htmx(
        client_obj,
        input_items,
        instructions=None,
        full_input_items=None,
        initial_processing_steps=None,
    ):
        yield {
            "text": "Provisional follow-up before second approval.",
            "reasoning_steps": [],
            "tool_calls": [],
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {
                        "name": second_pending_tool_name,
                        "call_id": second_pending_call_id,
                        "arguments": '{"query": "Charter AND rights"}',
                        "approval_request_id": second_pending_call_id,
                        "tool_label": "Search Canadian case law",
                        "allow_auto_approve": False,
                    },
                }
            ],
            "is_reasoning": False,
            "is_complete": True,
            "output_items": [],
            "response_id": "resp_second_pause",
            "pending_local_tool": {
                "name": second_pending_tool_name,
                "call_id": second_pending_call_id,
                "arguments": '{"query": "Charter AND rights"}',
                "tool_label": "Search Canadian case law",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
        }

    monkeypatch.setattr("chat_next.tools.execute_tool_call", fake_execute_tool_call)
    monkeypatch.setattr(
        "chat_next.tools.build_function_call_output",
        fake_build_function_call_output,
    )
    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", DummyResponsesClient)
    monkeypatch.setattr(
        "chat_next.responses.stream_chat_for_htmx",
        fake_stream_chat_for_htmx,
    )

    url = reverse("chat_next:approval_stream", args=[bot_message.id])
    response = client.get(url, {"approved": "true"})
    assert response.status_code == 200

    exhaust_streaming_response(response)

    bot_message.refresh_from_db()

    assert bot_message.text == stable_text
    assert "Provisional follow-up before second approval." not in bot_message.text
    assert (
        bot_message.details["pending_local_tool"]["call_id"] == second_pending_call_id
    )
