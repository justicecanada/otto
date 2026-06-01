"""Minimal tests for between-message context compaction support."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asgiref.sync import async_to_sync
from chat_next._llm.openai_responses import (
    COMPACTION_PROCESSING_STEP,
    ResponsesAPIClient,
    StreamChunk,
    _is_context_window_error,
    _sanitize_compaction_input_item,
    _sanitize_input_item,
    build_conversation_input,
    create_compaction_costs,
    should_proactively_compact_tool_continuation,
)
from chat_next.responses import _compact_chat_history_if_needed, error_response
from chat_next.utils import htmx_stream
from openai import BadRequestError


def _make_message(is_bot, response_output=None, text="", msg_id=None):
    msg = MagicMock()
    msg.id = msg_id or id(msg)
    msg.is_bot = is_bot
    msg.text = text
    msg.response_output = response_output or []
    msg.sorted_files = []
    msg.details = {}
    msg.date_created = MagicMock()
    return msg


def test_sanitize_input_item_allows_compaction_items():
    compaction_item = {
        "type": "compaction",
        "encrypted_content": "abc123encrypted",
    }

    result = _sanitize_input_item(compaction_item)

    assert result is not None
    assert result["type"] == "compaction"
    assert result["encrypted_content"] == "abc123encrypted"


def test_sanitize_input_item_normalizes_assistant_text_for_api_input():
    item = {
        "role": "assistant",
        "content": "\n\n_Response stopped early. Costs may still be incurred after stopping._",
    }

    result = _sanitize_input_item(item)

    assert result["role"] == "assistant"
    assert result["type"] == "message"
    assert result["content"] == [
        {
            "type": "output_text",
            "text": "\n\n_Response stopped early. Costs may still be incurred after stopping._",
        }
    ]


def test_sanitize_compaction_input_item_preserves_function_call():
    item = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "view_library_files",
        "arguments": '{"document_ids": [1]}',
        "status": "completed",
        "parsed_arguments": {"document_ids": [1]},
    }

    result = _sanitize_compaction_input_item(item)

    assert result == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "view_library_files",
        "arguments": '{"document_ids": [1]}',
    }


def test_sanitize_compaction_input_item_preserves_function_call_output_but_strips_inline_blobs():
    item = {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [
            {"type": "input_text", "text": '{"loaded_files": []}'},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64," + "A" * 10000,
                "detail": "high",
            },
        ],
    }

    result = _sanitize_compaction_input_item(item)

    assert result["type"] == "function_call_output"
    assert result["call_id"] == "call_1"
    assert result["output"][0] == item["output"][0]
    assert "image_url" not in result["output"][1]
    assert result["output"][1]["image_url_omitted"] is True


def test_create_compaction_costs_records_usage(monkeypatch):
    cost_calls = []

    def fake_create_costs(self, model_id):
        cost_calls.append(
            {
                "model_id": model_id,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens,
                "reasoning_tokens": self.reasoning_tokens,
            }
        )
        return 0.0

    monkeypatch.setattr(
        "chat_next._llm.openai_responses.TokenUsage.create_costs",
        fake_create_costs,
    )

    create_compaction_costs(
        {
            "input_tokens": 2500,
            "output_tokens": 125,
            "cached_tokens": 25,
            "reasoning_tokens": 10,
        },
        "gpt-5.1",
    )

    assert cost_calls == [
        {
            "model_id": "gpt-5.1",
            "input_tokens": 2500,
            "output_tokens": 125,
            "cached_tokens": 25,
            "reasoning_tokens": 10,
        }
    ]


def test_should_proactively_compact_tool_continuation_matches_existing_heuristic():
    big_output = "x" * 25000

    should_compact, metrics = should_proactively_compact_tool_continuation(
        context_management_mode="compact",
        usage={"input_tokens": 240000, "output_tokens": 5000},
        tool_output_items=[
            {"type": "function_call_output", "output": big_output},
            {"type": "function_call_output", "output": big_output},
        ],
        model_id="gpt-5.4-mini",
    )

    assert should_compact is True
    assert metrics == {
        "input_tokens": 240000,
        "output_tokens": 5000,
        "estimated_tool_tokens": (len(big_output) * 2) // 3,
    }


def test_sanitize_function_call_output_strips_inline_vision_blobs():
    """Inline base64 data should be replaced with lightweight markers."""
    from chat_next._llm.openai_responses import (
        _sanitize_function_call_output_for_storage,
    )

    output_item = {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [
            {"type": "input_text", "text": '{"file_count": 1}'},
            {
                "type": "input_file",
                "file_data": "data:application/pdf;base64," + "A" * 300_000,
                "filename": "page.pdf",
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64," + "B" * 200_000,
                "detail": "high",
            },
        ],
    }

    sanitized = _sanitize_function_call_output_for_storage(output_item)

    # Original is untouched
    assert output_item["output"][1]["file_data"].startswith("data:application/pdf")
    assert output_item["output"][2]["image_url"].startswith("data:image/png")

    # Sanitized strips blobs and leaves markers
    assert "file_data" not in sanitized["output"][1]
    assert sanitized["output"][1]["file_data_omitted"] is True
    assert "image_url" not in sanitized["output"][2]
    assert sanitized["output"][2]["image_url_omitted"] is True

    # Text item is untouched
    assert sanitized["output"][0] == output_item["output"][0]

    # Serialized size is drastically smaller
    import json

    assert len(json.dumps(sanitized)) < 500


def test_proactive_compaction_not_fooled_by_inline_vision_blobs():
    """Inline base64 vision payloads should not inflate the token estimate."""
    big_blob = "data:application/pdf;base64," + "A" * 600_000

    # Raw output — would estimate ~200K tokens without sanitization
    raw_items = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": '{"file_count": 1}'},
                {"type": "input_file", "file_data": big_blob, "filename": "doc.pdf"},
            ],
        }
    ]

    # Even with very low actual input tokens, the raw blob would push past
    # the 200K threshold for gpt-5.4-mini if unsanitized.
    should_raw, metrics_raw = should_proactively_compact_tool_continuation(
        context_management_mode="compact",
        usage={"input_tokens": 5000, "output_tokens": 500},
        tool_output_items=raw_items,
        model_id="gpt-5.4-mini",
    )
    # The raw items WOULD trigger compaction (that's the bug)
    assert should_raw is True

    # Now sanitize them the same way stream_chat_for_htmx does before the check
    from chat_next._llm.openai_responses import (
        _sanitize_function_call_output_for_storage,
    )

    sanitized_items = [
        _sanitize_function_call_output_for_storage(item) for item in raw_items
    ]
    should_sanitized, metrics_sanitized = should_proactively_compact_tool_continuation(
        context_management_mode="compact",
        usage={"input_tokens": 5000, "output_tokens": 500},
        tool_output_items=sanitized_items,
        model_id="gpt-5.4-mini",
    )
    # Sanitized should NOT trigger compaction — the actual context is tiny
    assert should_sanitized is False
    assert metrics_sanitized["estimated_tool_tokens"] < 500


def test_proactive_compaction_not_fooled_by_inline_image_data_urls():
    """Inline image data URLs should not inflate proactive compaction estimates."""
    big_blob = "data:image/png;base64," + "B" * 600_000

    raw_items = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": '{"file_count": 1}'},
                {
                    "type": "input_image",
                    "image_url": big_blob,
                    "detail": "high",
                },
            ],
        }
    ]

    should_raw, _metrics_raw = should_proactively_compact_tool_continuation(
        context_management_mode="compact",
        usage={"input_tokens": 5000, "output_tokens": 500},
        tool_output_items=raw_items,
        model_id="gpt-5.4-mini",
    )
    assert should_raw is True

    from chat_next._llm.openai_responses import (
        _sanitize_function_call_output_for_storage,
    )

    sanitized_items = [
        _sanitize_function_call_output_for_storage(item) for item in raw_items
    ]
    should_sanitized, metrics_sanitized = should_proactively_compact_tool_continuation(
        context_management_mode="compact",
        usage={"input_tokens": 5000, "output_tokens": 500},
        tool_output_items=sanitized_items,
        model_id="gpt-5.4-mini",
    )

    assert should_sanitized is False
    assert metrics_sanitized["estimated_tool_tokens"] < 500


@pytest.mark.asyncio
async def test_build_request_params_enables_server_side_compaction_when_compact(mocker):
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mock_model_config.max_tokens_in = 272000
    mock_model_config.max_tokens_out = 128000
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    mock_chat = MagicMock()
    mock_chat.settings.chat_context_management = "compact"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)

    params = await client._build_request_params(
        [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}]
    )

    assert params["truncation"] == "disabled"
    assert params["context_management"] == [
        {"type": "compaction", "compact_threshold": 200000}
    ]


@pytest.mark.asyncio
async def test_compact_conversation_preserves_live_tool_pairs(mocker):
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mock_model_config.max_tokens_in = 272000
    mock_model_config.max_tokens_out = 128000
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse()

    mocker.patch(
        "chat_next._llm.openai_responses.httpx.AsyncClient",
        return_value=FakeAsyncClient(),
    )

    client = ResponsesAPIClient(model="gpt-5.1")

    items = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "view_library_files",
            "arguments": '{"document_ids": [1]}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": '{"loaded_files": []}'},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64," + "A" * 10000,
                    "detail": "high",
                },
            ],
        },
    ]

    compacted_items, usage = await client.compact_conversation(items, "Be helpful")

    assert compacted_items == [{"type": "compaction", "encrypted_content": "opaque"}]
    assert usage["input_tokens"] == 10

    compact_input = captured["json"]["input"]
    assert compact_input[0]["type"] == "function_call"
    assert compact_input[1]["type"] == "function_call_output"
    assert compact_input[1]["call_id"] == "call_1"
    assert "image_url" not in compact_input[1]["output"][1]
    assert compact_input[1]["output"][1]["image_url_omitted"] is True


@pytest.mark.asyncio
async def test_compact_conversation_retries_with_legacy_sanitization_on_400(mocker):
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mock_model_config.max_tokens_in = 272000
    mock_model_config.max_tokens_out = 128000
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    captured_bodies = []

    class FakeResponse:
        def __init__(self, status_code=200, body=None):
            self.status_code = status_code
            self._body = body or {
                "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
            self.request = httpx.Request("POST", "https://fake.azure.com")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "Client error '400 Bad Request'",
                    request=self.request,
                    response=self,
                )

        def json(self):
            return self._body

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers, timeout):
            captured_bodies.append(json)
            if len(captured_bodies) == 1:
                return FakeResponse(status_code=400)
            return FakeResponse()

    mocker.patch(
        "chat_next._llm.openai_responses.httpx.AsyncClient",
        return_value=FakeAsyncClient(),
    )

    client = ResponsesAPIClient(model="gpt-5.1")
    items = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "view_library_files",
            "arguments": '{"document_ids": [1]}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [{"type": "input_text", "text": '{"loaded_files": []}'}],
        },
    ]

    compacted_items, usage = await client.compact_conversation(items, "Be helpful")

    assert compacted_items == [{"type": "compaction", "encrypted_content": "opaque"}]
    assert usage["input_tokens"] == 10
    assert len(captured_bodies) == 2
    assert captured_bodies[0]["input"][0]["type"] == "function_call"
    assert captured_bodies[1]["input"] == []


@pytest.mark.asyncio
async def test_proactive_compaction_failure_preserves_previous_response_id(mocker):
    from chat_next._llm.openai_responses import stream_chat_for_htmx

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_settings.chat_max_iterations = 25
    mock_settings.chat_auto_approve_tools = []
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings
    mock_chat.id = "test-chat"

    client = ResponsesAPIClient(model="gpt-5.4-mini", chat=mock_chat)
    client.user = MagicMock()

    stream_call_count = 0

    async def mock_stream(input_items, instructions=None, **kwargs):
        nonlocal stream_call_count
        stream_call_count += 1
        if stream_call_count == 1:
            yield StreamChunk(
                text="",
                reasoning_steps=[],
                is_complete=True,
                usage={"input_tokens": 240000, "output_tokens": 5000},
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_document_text",
                        "arguments": '{"document_id": 1001}',
                    }
                ],
                response_id="resp_1",
            )
        else:
            assert client.previous_response_id == "resp_1"
            yield StreamChunk(
                text="Done after compaction failure",
                reasoning_steps=[],
                is_complete=True,
                output_items=[],
                response_id="resp_2",
            )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)
    mocker.patch.object(
        client,
        "compact_conversation",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Client error '400 Bad Request'",
                request=httpx.Request("POST", "https://fake.azure.com"),
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://fake.azure.com")
                ),
            )
        ),
    )
    mocker.patch(
        "chat_next.tools.execute_tool_call",
        AsyncMock(return_value={"success": True, "result": {"text": "doc text"}}),
    )

    mock_tool = MagicMock()
    mock_tool.name = "get_document_text"
    mock_tool.display_name = "Get Document Text"
    mock_tool.requires_approval = False
    mock_tool.allow_auto_approve = False
    mock_tool.hidden = False
    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_tool
    mock_registry.list_tool_names.return_value = ["get_document_text"]
    mocker.patch("chat_next.tools.TOOL_REGISTRY", mock_registry)

    mocker.patch(
        "chat_next.tools.build_function_call_output",
        return_value={
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"text": "doc text"}',
        },
    )

    chunks = []
    async for chunk in stream_chat_for_htmx(
        client,
        [{"role": "user", "content": [{"type": "input_text", "text": "read the doc"}]}],
        "instructions",
    ):
        chunks.append(chunk)

    assert stream_call_count == 2
    assert chunks[-1]["text"] == "Done after compaction failure"


@pytest.mark.asyncio
async def test_build_request_params_uses_truncate_mode_without_compaction(mocker):
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mock_model_config.max_tokens_in = 272000
    mock_model_config.max_tokens_out = 128000
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    mock_chat = MagicMock()
    mock_chat.settings.chat_context_management = "truncate"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)

    params = await client._build_request_params(
        [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}]
    )

    assert params["truncation"] == "auto"
    assert "extra_body" not in params


@pytest.mark.asyncio
async def test_build_request_params_uses_error_mode_without_compaction(mocker):
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mock_model_config.max_tokens_in = 272000
    mock_model_config.max_tokens_out = 128000
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    mock_chat = MagicMock()
    mock_chat.settings.chat_context_management = "error"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)

    params = await client._build_request_params(
        [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}]
    )

    assert params["truncation"] == "disabled"
    assert "extra_body" not in params


@pytest.mark.django_db
def test_build_conversation_input_prunes_history_before_compaction_item():
    user1 = _make_message(is_bot=False, text="Hello", msg_id=1)
    bot1 = _make_message(
        is_bot=True,
        response_output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi there"}],
            },
        ],
        msg_id=2,
    )
    user2 = _make_message(is_bot=False, text="Tell me more", msg_id=3)
    bot2 = _make_message(
        is_bot=True,
        response_output=[
            {"type": "compaction", "encrypted_content": "compacted_data_xyz"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Summary response"}],
            },
        ],
        msg_id=4,
    )
    user3 = _make_message(is_bot=False, text="Follow up", msg_id=5)

    chat = MagicMock()
    mock_qs = MagicMock()
    mock_qs.order_by.return_value = [user1, bot1, user2, bot2, user3]
    chat.messages = mock_qs

    items = build_conversation_input(chat)

    texts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for chunk in content:
                if isinstance(chunk, dict):
                    texts.append(chunk.get("text", ""))

    assert "Hello" not in texts
    assert "Follow up" in texts
    assert any(
        item.get("type") == "compaction" for item in items if isinstance(item, dict)
    )


@pytest.mark.django_db
def test_build_conversation_input_uses_persisted_compacted_prefix(all_apps_user):
    user = all_apps_user("persisted-compaction-prefix")
    chat = user.chat_next_set.create(title="Compacted chat")

    old_user = chat.messages.create(text="Old prompt", is_bot=False)
    chat.messages.create(
        text="Old answer",
        is_bot=True,
        parent=old_user,
        response_output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Old answer"}],
            }
        ],
    )
    chat.messages.create(text="Current prompt", is_bot=False)

    chat.compacted_input_items = [
        {"type": "compaction", "encrypted_content": "opaque-compacted-state"}
    ]
    chat.compacted_through_message = old_user
    chat.save(update_fields=["compacted_input_items", "compacted_through_message"])

    items = build_conversation_input(chat)

    assert items[0]["type"] == "compaction"
    texts = [item.get("content") for item in items if item.get("role") == "user"]
    assert "Current prompt" in texts
    assert "Old prompt" not in texts


@pytest.mark.django_db
def test_compact_chat_history_if_needed_compacts_between_messages(
    monkeypatch, all_apps_user
):
    user = all_apps_user("between-message-compaction")
    chat = user.chat_next_set.create(title="Compaction chat")
    chat.settings.chat_model = "gpt-5.1"
    chat.settings.chat_context_management = "compact"
    chat.settings.save(update_fields=["chat_model", "chat_context_management"])

    previous_user = chat.messages.create(text="Previous turn", is_bot=False)
    chat.messages.create(
        text="Previous answer",
        is_bot=True,
        parent=previous_user,
        details={"usage": {"input_tokens": 199500, "output_tokens": 600}},
        response_output=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Previous answer"}],
            }
        ],
    )
    current_user = chat.messages.create(text="New turn", is_bot=False)
    response_message = chat.messages.create(text="", is_bot=True, parent=current_user)

    client = MagicMock()

    async def fake_compact_conversation(input_items, instructions):
        return (
            [{"type": "compaction", "encrypted_content": "opaque-state"}],
            {"input_tokens": 2500, "output_tokens": 125, "cached_tokens": 0},
        )

    client.compact_conversation = fake_compact_conversation

    cost_calls = []

    def fake_create_compaction_costs(compaction_usage, model_id):
        cost_calls.append((compaction_usage, model_id))
        return 0.0

    monkeypatch.setattr(
        "chat_next.responses.create_compaction_costs",
        fake_create_compaction_costs,
    )

    result = _compact_chat_history_if_needed(
        chat=chat,
        response_message=response_message,
        user_message=current_user,
        input_items=[{"role": "user", "content": "New turn"}],
        instructions="Be helpful",
        client=client,
        model_id="gpt-5.1",
    )

    chat.refresh_from_db()

    compacted_items, was_compacted = result

    assert was_compacted is True
    assert compacted_items == [
        {"type": "compaction", "encrypted_content": "opaque-state"}
    ]
    assert chat.compacted_input_items == compacted_items
    assert chat.compacted_through_message_id == current_user.id
    assert cost_calls == [
        (
            {"input_tokens": 2500, "output_tokens": 125, "cached_tokens": 0},
            "gpt-5.1",
        )
    ]


@pytest.mark.asyncio
async def test_stream_chat_for_htmx_includes_initial_processing_steps(mocker):
    """Initial processing steps should carry into the final streamed payload."""
    from chat_next._llm.openai_responses import stream_chat_for_htmx

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_chat = MagicMock()
    mock_chat.settings.chat_context_management = "compact"
    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)

    async def mock_stream(input_items, instructions=None, **kwargs):
        yield StreamChunk(
            text="Done",
            reasoning_steps=[],
            is_complete=True,
            output_items=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                }
            ],
            response_id="resp_1",
        )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)

    chunks = []
    async for chunk in stream_chat_for_htmx(
        client,
        input_items=[{"role": "user", "content": "Hi"}],
        initial_processing_steps=[COMPACTION_PROCESSING_STEP],
    ):
        chunks.append(chunk)

    final_chunk = chunks[-1]
    assert final_chunk["processing_steps"]
    assert final_chunk["processing_steps"][0]["tool_type"] == "compaction"
    assert final_chunk["processing_steps"][0]["details"]["tool_label"] == (
        "Compacted conversation"
    )


@pytest.mark.django_db
def test_error_response_uses_hardcoded_context_window_message(
    monkeypatch, all_apps_user
):
    user = all_apps_user("context-window-error")
    chat = user.chat_next_set.create(title="Overflow chat")
    chat.settings.chat_model = "gpt-5.1"
    chat.settings.chat_context_management = "error"
    chat.settings.save(update_fields=["chat_model", "chat_context_management"])
    response_message = chat.messages.create(is_bot=True, text="")

    captured = {}

    def fake_htmx_stream(*args, **kwargs):
        captured.update(kwargs)
        yield "event: done\ndata: complete\n\n"

    monkeypatch.setattr("chat_next.responses.htmx_stream", fake_htmx_stream)

    err_response = MagicMock()
    err_response.status_code = 400
    error = BadRequestError(
        message="This model's maximum context length was exceeded.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    response = error_response(chat, response_message, error)
    list(response.streaming_content)

    assert "gpt-5.1" in captured["response_str"]
    assert "Switch to GPT-5.4" in captured["response_str"]
    assert "Settings > Advanced" in captured["response_str"]
    assert '"Compact"' in captured["response_str"]
    assert '"Truncate"' in captured["response_str"]
    assert "GPT-4.1" not in captured["response_str"]


@pytest.mark.django_db
def test_error_response_skips_gpt_5_4_switch_hint_when_already_selected(
    monkeypatch, all_apps_user
):
    user = all_apps_user("context-window-error-gpt54")
    chat = user.chat_next_set.create(title="Overflow chat")
    chat.settings.chat_model = "gpt-5.4"
    chat.settings.save(update_fields=["chat_model"])
    response_message = chat.messages.create(is_bot=True, text="")

    captured = {}

    def fake_htmx_stream(*args, **kwargs):
        captured.update(kwargs)
        yield "event: done\ndata: complete\n\n"

    monkeypatch.setattr("chat_next.responses.htmx_stream", fake_htmx_stream)

    err_response = MagicMock()
    err_response.status_code = 400
    error = BadRequestError(
        message="Please reduce the length of the messages.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    response = error_response(chat, response_message, error)
    list(response.streaming_content)

    assert "gpt-5.4" in captured["response_str"]
    assert "Switch to GPT-5.4" not in captured["response_str"]
    assert "Settings > Advanced" not in captured["response_str"]


@pytest.mark.django_db
def test_error_response_suggests_context_management_when_disabled_for_gpt_5_4(
    monkeypatch, all_apps_user
):
    user = all_apps_user("context-window-error-gpt54-settings")
    chat = user.chat_next_set.create(title="Overflow chat")
    chat.settings.chat_model = "gpt-5.4"
    chat.settings.chat_context_management = "error"
    chat.settings.save(update_fields=["chat_model", "chat_context_management"])
    response_message = chat.messages.create(is_bot=True, text="")

    captured = {}

    def fake_htmx_stream(*args, **kwargs):
        captured.update(kwargs)
        yield "event: done\ndata: complete\n\n"

    monkeypatch.setattr("chat_next.responses.htmx_stream", fake_htmx_stream)

    err_response = MagicMock()
    err_response.status_code = 400
    error = BadRequestError(
        message="Please reduce the length of the messages.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    response = error_response(chat, response_message, error)
    list(response.streaming_content)

    assert "Switch to GPT-5.4" not in captured["response_str"]
    assert "Settings > Advanced" in captured["response_str"]
    assert '"Compact"' in captured["response_str"]
    assert '"Truncate"' in captured["response_str"]


@pytest.mark.django_db
def test_htmx_stream_uses_hardcoded_context_window_message_for_stream_errors(
    monkeypatch, all_apps_user
):
    user = all_apps_user("context-window-stream-error")
    chat = user.chat_next_set.create(title="Overflow chat")
    chat.settings.chat_model = "gpt-5.1"
    chat.settings.chat_context_management = "error"
    chat.settings.save(update_fields=["chat_model", "chat_context_management"])
    response_message = chat.messages.create(is_bot=True, text="")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AI error summary should not be used for context errors")

    monkeypatch.setattr("otto.utils.common.generate_ai_error_summary", fail_if_called)

    err_response = MagicMock()
    err_response.status_code = 400
    error = BadRequestError(
        message="You input exceeds the context window of this model.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    async def failing_replacer():
        if False:
            yield None
        raise error

    async def consume_stream():
        chunks = []
        async for chunk in htmx_stream(
            chat,
            response_message.id,
            response_replacer=failing_replacer(),
        ):
            chunks.append(chunk)
        return chunks

    chunks = async_to_sync(consume_stream)()

    response_message.refresh_from_db()

    assert chunks
    assert "gpt-5.1" in response_message.text
    assert "Switch to GPT-5.4" in response_message.text
    assert "Settings > Advanced" in response_message.text


def test_is_context_window_error_detects_variants():
    assert _is_context_window_error(
        "you input exceeds the context window of this model"
    )
    assert _is_context_window_error("maximum context length exceeded")
    assert _is_context_window_error("error code: context_length_exceeded")
    assert _is_context_window_error("error code: input_too_long")
    assert _is_context_window_error("input exceeds the maximum context allowed")
    assert not _is_context_window_error("container not found")
    assert not _is_context_window_error("missing tool output")


@pytest.mark.asyncio
async def test_stream_chat_propagates_context_window_error(mocker):
    """stream_chat should NOT retry context window errors — it lets them
    propagate to stream_chat_for_htmx which has the full conversation."""
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)

    err_response = MagicMock()
    err_response.status_code = 400
    context_error = BadRequestError(
        message="You input exceeds the context window of this model.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    async def mock_stream_impl(input_items, instructions=None, **kwargs):
        raise context_error
        yield  # noqa

    mocker.patch.object(client, "_stream_chat_impl", side_effect=mock_stream_impl)

    input_items = [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    with pytest.raises(BadRequestError):
        async for _chunk in client.stream_chat(input_items, "system prompt"):
            pass


@pytest.mark.asyncio
async def test_tool_loop_compacts_on_context_window_overflow(mocker):
    """stream_chat_for_htmx compacts when tool outputs push past context window."""
    from chat_next._llm.openai_responses import stream_chat_for_htmx

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_settings.chat_max_iterations = 25
    mock_settings.chat_auto_approve_tools = []
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings
    mock_chat.id = "test-chat"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)
    client.user = MagicMock()

    err_response = MagicMock()
    err_response.status_code = 400
    context_error = BadRequestError(
        message="You input exceeds the context window of this model.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    stream_call_count = 0

    async def mock_stream(input_items, instructions=None, **kwargs):
        nonlocal stream_call_count
        stream_call_count += 1
        if stream_call_count == 1:
            # First call: model returns a function_call
            yield StreamChunk(
                text="",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_document_text",
                        "arguments": '{"doc_id": 1}',
                    }
                ],
                response_id="resp_1",
            )
        elif stream_call_count == 2:
            # Second call (tool continuation): context overflow
            raise context_error
        else:
            # Third call (after compaction): success
            yield StreamChunk(
                text="Done after compaction",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Done after compaction"}
                        ],
                    }
                ],
                response_id="resp_3",
            )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)

    compacted_items = [{"type": "compaction", "encrypted_content": "compacted_data"}]
    mock_compact = AsyncMock(return_value=(compacted_items, {"input_tokens": 50}))
    mocker.patch.object(client, "compact_conversation", mock_compact)

    cost_calls = []

    def fake_create_costs(self, model_id):
        cost_calls.append((model_id, self.input_tokens, self.output_tokens))
        return 0.0

    mocker.patch(
        "chat_next._llm.openai_responses.TokenUsage.create_costs",
        side_effect=fake_create_costs,
        autospec=True,
    )

    # Mock tool execution (lazy-imported inside stream_chat_for_htmx)
    mock_execute = AsyncMock(
        return_value={"success": True, "result": {"text": "big doc"}}
    )
    mocker.patch("chat_next.tools.execute_tool_call", mock_execute)

    # Mock tool registry
    mock_tool = MagicMock()
    mock_tool.name = "get_document_text"
    mock_tool.display_name = "Get Document Text"
    mock_tool.requires_approval = False
    mock_tool.allow_auto_approve = False
    mock_tool.hidden = False
    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_tool
    mock_registry.list_tool_names.return_value = ["get_document_text"]
    mocker.patch("chat_next.tools.TOOL_REGISTRY", mock_registry)

    # Mock build_function_call_output (lazy-imported)
    mocker.patch(
        "chat_next.tools.build_function_call_output",
        return_value={
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"text": "big doc"}',
        },
    )

    original_input = [
        {"role": "user", "content": [{"type": "input_text", "text": "read the doc"}]}
    ]
    chunks = []
    async for chunk in stream_chat_for_htmx(client, original_input, "instructions"):
        chunks.append(chunk)

    # Compaction should have been called with the full conversation + tool outputs
    assert mock_compact.called
    compact_args = mock_compact.call_args[0]
    # The compacted input should include the original items plus the accumulated output
    assert any(
        isinstance(item, dict) and item.get("role") == "user"
        for item in compact_args[0]
    )

    # Should have gotten a successful final response after compaction
    final_chunks = [c for c in chunks if c.get("output_items")]
    assert final_chunks
    assert stream_call_count == 3
    assert cost_calls == [("gpt-5.1", 50, 0)]


@pytest.mark.asyncio
async def test_proactive_compaction_before_tool_continuation(mocker):
    """stream_chat_for_htmx proactively compacts when estimated context would overflow."""
    from chat_next._llm.openai_responses import stream_chat_for_htmx

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_settings.chat_max_iterations = 25
    mock_settings.chat_auto_approve_tools = []
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings
    mock_chat.id = "test-chat"

    # Use gpt-5.4-mini (272K max) so our usage numbers trigger the 90% threshold
    client = ResponsesAPIClient(model="gpt-5.4-mini", chat=mock_chat)
    client.user = MagicMock()

    stream_call_count = 0

    async def mock_stream(input_items, instructions=None, **kwargs):
        nonlocal stream_call_count
        stream_call_count += 1
        if stream_call_count == 1:
            # First call: model returns 2 parallel function_calls.
            # Report usage showing context is already at ~240K/272K tokens.
            # After adding ~16K estimated tool tokens the total (~261K)
            # exceeds the 90% threshold (244K) triggering proactive compaction.
            yield StreamChunk(
                text="",
                reasoning_steps=[],
                is_complete=True,
                usage={
                    "input_tokens": 240000,
                    "output_tokens": 5000,
                },
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_document_text",
                        "arguments": '{"doc_id": 1}',
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_2",
                        "name": "get_document_text",
                        "arguments": '{"doc_id": 2}',
                    },
                ],
                response_id="resp_1",
            )
        else:
            # Second call (after proactive compaction): success
            yield StreamChunk(
                text="Done after proactive compaction",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Done after proactive compaction",
                            }
                        ],
                    }
                ],
                response_id="resp_2",
            )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)

    compacted_items = [{"type": "compaction", "encrypted_content": "compacted"}]
    mock_compact = AsyncMock(return_value=(compacted_items, {"input_tokens": 30000}))
    mocker.patch.object(client, "compact_conversation", mock_compact)

    # Mock tool execution returning large results (~25K chars each = ~8K tokens each)
    big_text = "x" * 25000
    mock_execute = AsyncMock(
        return_value={"success": True, "result": {"text": big_text}}
    )
    mocker.patch("chat_next.tools.execute_tool_call", mock_execute)

    mock_tool = MagicMock()
    mock_tool.name = "get_document_text"
    mock_tool.display_name = "Get Document Text"
    mock_tool.requires_approval = False
    mock_tool.allow_auto_approve = False
    mock_tool.hidden = False
    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_tool
    mock_registry.list_tool_names.return_value = ["get_document_text"]
    mocker.patch("chat_next.tools.TOOL_REGISTRY", mock_registry)

    mocker.patch(
        "chat_next.tools.build_function_call_output",
        side_effect=lambda call_id, output: {
            "type": "function_call_output",
            "call_id": call_id,
            "output": big_text,
        },
    )

    original_input = [
        {"role": "user", "content": [{"type": "input_text", "text": "add SCC 5 and 6"}]}
    ]
    chunks = []
    async for chunk in stream_chat_for_htmx(client, original_input, "instructions"):
        chunks.append(chunk)

    # Proactive compaction should have fired (240K + 5K + ~16K estimated > 90% of 272K)
    assert mock_compact.called
    # The continuation should NOT have raised an error — stream_call_count == 2
    # (no 3rd call because proactive compaction avoided the error)
    assert stream_call_count == 2

    final_chunks = [c for c in chunks if c.get("output_items")]
    assert final_chunks
    assert final_chunks[-1]["text"] == "Done after proactive compaction"

    # A "Compacted conversation" processing step should be present
    compaction_steps = [
        step
        for chunk in chunks
        for step in chunk.get("processing_steps", [])
        if step.get("tool_type") == "compaction"
    ]
    assert compaction_steps, "Expected a compaction processing step"
    assert compaction_steps[0]["status"] == "completed"

    # Previous tool-call processing steps should still be present (not cleared)
    tool_steps = [
        step
        for chunk in chunks
        for step in chunk.get("processing_steps", [])
        if step.get("tool_type") == "function_call"
    ]
    assert tool_steps, "Previous tool-call steps should be preserved"


@pytest.mark.asyncio
async def test_reactive_compaction_catches_api_status_error(mocker):
    """stream_chat_for_htmx reactive handler catches APIStatusError (not just BadRequestError)."""
    from chat_next._llm.openai_responses import stream_chat_for_htmx
    from openai import APIStatusError

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_settings.chat_max_iterations = 25
    mock_settings.chat_auto_approve_tools = []
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings
    mock_chat.id = "test-chat"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)
    client.user = MagicMock()

    # Use APIStatusError (parent) instead of BadRequestError
    err_response = MagicMock()
    err_response.status_code = 413
    err_response.headers = {}
    err_response.is_closed = True
    err_response.read = MagicMock(return_value=b"")
    context_error = APIStatusError(
        message="Request payload exceeds maximum context length",
        response=err_response,
        body={"error": {"code": "input_too_long"}},
    )

    stream_call_count = 0

    async def mock_stream(input_items, instructions=None, **kwargs):
        nonlocal stream_call_count
        stream_call_count += 1
        if stream_call_count == 1:
            yield StreamChunk(
                text="",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_document_text",
                        "arguments": '{"doc_id": 1}',
                    }
                ],
                response_id="resp_1",
            )
        elif stream_call_count == 2:
            raise context_error
        else:
            yield StreamChunk(
                text="Done after compaction",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Done after compaction"}
                        ],
                    }
                ],
                response_id="resp_3",
            )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)

    compacted_items = [{"type": "compaction", "encrypted_content": "compacted"}]
    mock_compact = AsyncMock(return_value=(compacted_items, {"input_tokens": 50}))
    mocker.patch.object(client, "compact_conversation", mock_compact)

    mock_execute = AsyncMock(
        return_value={"success": True, "result": {"text": "doc text"}}
    )
    mocker.patch("chat_next.tools.execute_tool_call", mock_execute)

    mock_tool = MagicMock()
    mock_tool.name = "get_document_text"
    mock_tool.display_name = "Get Document Text"
    mock_tool.requires_approval = False
    mock_tool.allow_auto_approve = False
    mock_tool.hidden = False
    mock_registry = MagicMock()
    mock_registry.get.return_value = mock_tool
    mock_registry.list_tool_names.return_value = ["get_document_text"]
    mocker.patch("chat_next.tools.TOOL_REGISTRY", mock_registry)

    mocker.patch(
        "chat_next.tools.build_function_call_output",
        return_value={
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"text": "doc text"}',
        },
    )

    original_input = [
        {"role": "user", "content": [{"type": "input_text", "text": "read the doc"}]}
    ]
    chunks = []
    async for chunk in stream_chat_for_htmx(client, original_input, "instructions"):
        chunks.append(chunk)

    # Compaction should have been called despite non-BadRequestError error type
    assert mock_compact.called
    assert stream_call_count == 3

    final_chunks = [c for c in chunks if c.get("output_items")]
    assert final_chunks


@pytest.mark.asyncio
async def test_full_input_items_used_for_compaction_in_approval_flow(mocker):
    """stream_chat_for_htmx uses full_input_items (not just function_call_output) for compaction."""
    from chat_next._llm.openai_responses import stream_chat_for_htmx

    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_settings = MagicMock()
    mock_settings.chat_context_management = "compact"
    mock_settings.chat_max_iterations = 25
    mock_settings.chat_auto_approve_tools = []
    mock_chat = MagicMock()
    mock_chat.settings = mock_settings
    mock_chat.id = "test-chat"

    client = ResponsesAPIClient(model="gpt-5.1", chat=mock_chat)
    client.user = MagicMock()
    client.previous_response_id = "resp_prev"

    err_response = MagicMock()
    err_response.status_code = 400
    context_error = BadRequestError(
        message="You input exceeds the context window of this model.",
        response=err_response,
        body={"error": {"code": "context_length_exceeded"}},
    )

    stream_call_count = 0

    async def mock_stream(input_items, instructions=None, **kwargs):
        nonlocal stream_call_count
        stream_call_count += 1
        if stream_call_count == 1:
            # First call (continuation with function_call_output): context overflow
            raise context_error
        else:
            # After compaction: success
            yield StreamChunk(
                text="Done",
                reasoning_steps=[],
                is_complete=True,
                output_items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}],
                    }
                ],
                response_id="resp_2",
            )

    mocker.patch.object(client, "stream_chat", side_effect=mock_stream)

    compacted_items = [{"type": "compaction", "encrypted_content": "compacted"}]
    mock_compact = AsyncMock(return_value=(compacted_items, {"input_tokens": 50}))
    mocker.patch.object(client, "compact_conversation", mock_compact)

    # Simulate: input_items are function_call_output (from approval flow),
    # full_input_items is the full conversation.
    tool_outputs = [
        {"type": "function_call_output", "call_id": "call_1", "output": "big result"}
    ]
    full_conversation = [
        {"role": "user", "content": [{"type": "input_text", "text": "do stuff"}]},
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "I'll call tools"}],
        },
    ]
    instructions = "system prompt"

    chunks = []
    async for chunk in stream_chat_for_htmx(
        client,
        input_items=tool_outputs,
        instructions=instructions,
        full_input_items=full_conversation,
    ):
        chunks.append(chunk)

    assert mock_compact.called
    # The compact call should receive the full conversation, NOT just the tool outputs
    compact_args = mock_compact.call_args[0]
    compact_input = compact_args[0]
    assert any(
        isinstance(item, dict) and item.get("role") == "user" for item in compact_input
    ), (
        "Compaction input should include full conversation, not just function_call_output"
    )
    # Instructions should be passed to compact_conversation
    assert compact_args[1] == instructions

    assert stream_call_count == 2
    final_chunks = [c for c in chunks if c.get("output_items")]
    assert final_chunks
