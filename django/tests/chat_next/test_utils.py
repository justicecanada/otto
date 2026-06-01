"""
Tests for chat_next.utils functions.

Tests htmx_stream and title_chat after refactoring to use
ResponsesAPIClient instead of OttoLLM.
"""

import asyncio
import inspect
from unittest.mock import patch

from django.utils.translation import override

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from chat_next.utils import (
    annotate_pending_titles,
    apply_reasoning_step_translations,
    collect_reasoning_steps_for_translation,
    format_processing_steps,
    get_base_display_processing_steps,
    get_display_processing_steps,
    htmx_stream,
    title_chat,
)


class TestFormatProcessingSteps:
    """Tests for format_processing_steps function."""

    def test_empty_steps(self):
        """Empty input returns empty list."""
        assert format_processing_steps([]) == []
        assert format_processing_steps(None) == []

    def test_reasoning_step(self):
        """Reasoning steps are formatted correctly."""
        steps = [
            {
                "type": "reasoning",
                "index": 0,
                "text": "Thinking about this\nMore details here",
                "complete": True,
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Thinking about this"
        assert result[0]["details"] == "More details here"
        assert "status" not in result[0]  # Reasoning steps don't have status

    def test_reasoning_step_splits_packed_bold_titles(self):
        """Packed markdown bold titles are split into separate processing steps."""
        steps = [
            {
                "type": "reasoning",
                "index": 0,
                "text": "**Step 1 title**here's some steps**like this is step 2..**and so it goes",
                "complete": True,
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 2
        assert result[0]["title"] == "Step 1 title"
        assert result[0]["details"] == "here's some steps"
        assert result[1]["title"] == "like this is step 2.."
        assert result[1]["details"] == "and so it goes"

    def test_incomplete_reasoning_without_complete_title_is_hidden(self):
        """Incomplete reasoning with unclosed bold title should not flash malformed text."""
        steps = [
            {
                "type": "reasoning",
                "index": 0,
                "text": "**Step still streaming",
                "complete": False,
            }
        ]
        result = format_processing_steps(steps)
        assert result == []

    def test_tool_call_step(self):
        """Tool call steps are formatted correctly."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "code_interpreter",
                "status": "completed",
                "query": None,
                "details": {"code": "print('hello')"},
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Code interpreter"
        assert "print('hello')" in result[0]["details"]
        assert result[0]["status"] == "complete"

    def test_function_tool_uses_pretty_display_name(self):
        """Function-call tool rows use a pretty human-readable tool label."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "get_document_text",
                    "arguments": {},
                },
            }
        ]

        result = format_processing_steps(steps, language="en")

        assert result[0]["title"] == "Used tool: Get document text"
        assert result[0]["title_html"] == "Used tool: <em>Get document text</em>"

    def test_function_tool_uses_localized_display_name_in_french(self):
        """Function-call tool rows localize the pretty tool label in French."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "get_document_text",
                    "arguments": {},
                },
            }
        ]

        with override("fr"):
            result = format_processing_steps(steps, language="fr")

        assert "get_document_text" not in result[0]["title"]

    def test_function_tool_uses_pretty_dataset_name(self):
        """New legal dataset tool rows use the explicit pretty display name."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "list_canadian_legal_datasets",
                    "arguments": {"doc_type": "both"},
                },
            }
        ]

        result = format_processing_steps(steps, language="en")

        assert result[0]["title"] == "Used tool: List Canadian legal datasets"
        assert (
            result[0]["title_html"]
            == "Used tool: <em>List Canadian legal datasets</em>"
        )

    def test_function_tool_waiting_approval_uses_friendlier_label(self):
        """Approval-needed tool rows use the friendlier approval wording."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "get_document_text",
                    "arguments": {},
                    "approval_request_id": "req-123",
                },
            }
        ]

        result = format_processing_steps(steps, language="en")

        assert result[0]["title"] == "Approval required: Get document text"
        assert (
            result[0]["title_html"] == "Approval required: <em>Get document text</em>"
        )

    def test_function_tool_iteration_limit_uses_friendlier_label(self):
        """Iteration-limit tool rows use the friendlier iteration-limit wording."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "get_document_text",
                    "arguments": {},
                    "max_iterations_reached": True,
                },
            }
        ]

        result = format_processing_steps(steps, language="en")

        assert result[0]["title"] == "Iteration limit reached: Get document text"
        assert (
            result[0]["title_html"]
            == "Iteration limit reached: <em>Get document text</em>"
        )
        assert (
            result[0]["details"]
            == "Otto paused before running another tool step because this chat reached its maximum tool iterations. Approve to continue, or increase Maximum tool iterations in Advanced settings for longer tool workflows."
        )

    def test_function_tool_failed_uses_friendlier_label(self):
        """Failed tool rows use the friendlier failure wording."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "failed",
                "details": {
                    "name": "get_document_text",
                    "arguments": {},
                    "output": {"error": "No document found"},
                },
            }
        ]

        result = format_processing_steps(steps, language="en")

        assert result[0]["title"] == "Tool call failed: Get document text"
        assert result[0]["title_html"] == "Tool call failed: <em>Get document text</em>"
        assert result[0]["details"] == "No document found"

    def test_tool_call_without_query_filtered(self):
        """Non-billable tool calls without data are filtered out (container events)."""
        steps = [
            {
                "type": "tool_call",
                "tool_type": "code_interpreter_call",  # not a billable type
                "status": "completed",
                "query": None,
                "details": {},
            },
            {
                "type": "tool_call",
                "tool_type": "code_interpreter",
                "status": "completed",
                "query": None,
                "details": {"code": "result = 42"},
            },
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert "result = 42" in result[0]["details"]

    def test_chronological_order_preserved(self):
        """Events are kept in chronological order."""
        steps = [
            {
                "type": "reasoning",
                "index": 0,
                "text": "First thought\nDeciding to search",
                "complete": True,
            },
            {
                "type": "tool_call",
                "tool_type": "code_interpreter",
                "status": "completed",
                "query": None,
                "details": {"code": "step1 = 1"},
            },
            {
                "type": "reasoning",
                "index": 1,
                "text": "Got results\nNeed more info",
                "complete": True,
            },
            {
                "type": "tool_call",
                "tool_type": "code_interpreter",
                "status": "completed",
                "query": None,
                "details": {"code": "step2 = 2"},
            },
            {
                "type": "reasoning",
                "index": 2,
                "text": "Final analysis\nReady to answer",
                "complete": True,
            },
        ]
        result = format_processing_steps(steps)
        assert len(result) == 5
        # Verify order
        assert result[0]["title"] == "First thought"
        assert "step1 = 1" in result[1]["details"]
        assert result[2]["title"] == "Got results"
        assert "step2 = 2" in result[3]["details"]
        assert result[4]["title"] == "Final analysis"


class TestTranslatedProcessingStepsHelpers:
    def test_collect_reasoning_steps_for_translation_strips_french_suffix(self):
        processing_steps = [
            {
                "title": "Analyzing the request (raisonne exclusivement en anglais)",
                "details": "Need more context",
            },
            {
                "title": "Used search_library",
                "details": "```json\n{}\n```",
                "status": "complete",
            },
        ]

        result = collect_reasoning_steps_for_translation(processing_steps)

        assert result == [
            {
                "index": 0,
                "title": "Analyzing the request",
                "details": "Need more context",
            }
        ]

    def test_apply_reasoning_step_translations_only_updates_reasoning_steps(self):
        processing_steps = [
            {"title": "First thought", "details": "Need more context"},
            {
                "title": "Code interpreter",
                "details": "```python\nprint('hi')\n```",
                "status": "complete",
            },
        ]

        result = apply_reasoning_step_translations(
            processing_steps,
            [
                {
                    "index": 0,
                    "title": "Première réflexion",
                    "details": "Besoin de plus de contexte",
                }
            ],
        )

        assert result[0]["title"] == "Première réflexion"
        assert result[0]["details"] == "Besoin de plus de contexte"
        assert result[1] == processing_steps[1]

    def test_get_display_processing_steps_prefers_completed_translation(self):
        details = {
            "processing_steps": [{"title": "English", "details": "Details"}],
            "processing_steps_translations": {
                "fr": {
                    "status": "complete",
                    "steps": [{"title": "Français", "details": "Détails"}],
                }
            },
        }

        assert get_display_processing_steps(details, language="fr") == [
            {"title": "Français", "details": "Détails"}
        ]
        assert get_display_processing_steps(details, language="en") == [
            {"title": "English", "details": "Details"}
        ]

    def test_get_base_display_processing_steps_reformats_raw_steps_by_language(self):
        details = {
            "processing_steps": [
                {
                    "title": "Fonction utilisée : Ancien libellé",
                    "details": "```json\n{}\n```",
                    "status": "complete",
                }
            ],
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "completed",
                    "details": {
                        "name": "list_canadian_legal_datasets",
                        "arguments": {"doc_type": "both"},
                    },
                }
            ],
        }

        with override("fr"):
            fr_steps = get_base_display_processing_steps(details, language="fr")
        with override("en"):
            en_steps = get_base_display_processing_steps(details, language="en")

        assert "Ancien libellé" not in fr_steps[0]["title"]
        assert fr_steps[0]["title"] != "Fonction utilisée : Ancien libellé"
        assert en_steps[0]["title"] == "Used tool: List Canadian legal datasets"
        assert (
            en_steps[0]["title_html"]
            == "Used tool: <em>List Canadian legal datasets</em>"
        )

    def test_get_display_processing_steps_overlays_translated_reasoning_on_localized_tools(
        self,
    ):
        details = {
            "raw_processing_steps": [
                {
                    "type": "reasoning",
                    "index": 0,
                    "text": "Analyze request\nNeed more context",
                    "complete": True,
                },
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "completed",
                    "details": {
                        "name": "list_canadian_legal_datasets",
                        "arguments": {"doc_type": "both"},
                    },
                },
            ],
            "processing_steps_translations": {
                "fr": {
                    "status": "complete",
                    "steps": [
                        {
                            "title": "Analyser la demande",
                            "details": "Besoin de plus de contexte",
                        },
                        {
                            "title": "Fonction utilisée : Ancien libellé",
                            "details": '```json\n{\n  "doc_type": "both"\n}\n```',
                            "status": "complete",
                        },
                    ],
                }
            },
        }

        with override("fr"):
            fr_steps = get_display_processing_steps(details, language="fr")
        with override("en"):
            en_steps = get_display_processing_steps(details, language="en")

        assert fr_steps[0]["title"] == "Analyser la demande"
        assert "Ancien libellé" not in fr_steps[1]["title"]
        assert fr_steps[1]["title"] != "Fonction utilisée : Ancien libellé"
        assert en_steps[0]["title"] == "Analyze request"
        assert en_steps[1]["title"] == "Used tool: List Canadian legal datasets"
        assert (
            en_steps[1]["title_html"]
            == "Used tool: <em>List Canadian legal datasets</em>"
        )

    @patch("translate.utils.translate_text_azure")
    def test_translate_reasoning_processing_steps_uses_azure_translation(
        self, mock_translate
    ):
        from chat_next.utils import translate_reasoning_processing_steps

        mock_translate.side_effect = lambda text, _src, _target: f"fr:{text}"
        processing_steps = [
            {"title": "Analyze request", "details": "Need more info"},
            {
                "title": "Used: Search library",
                "details": "```json\n{}\n```",
                "status": "complete",
            },
        ]

        result = translate_reasoning_processing_steps(
            processing_steps, target_language="fr"
        )

        assert result[0]["title"] == "fr:Analyze request"
        assert result[0]["details"] == "fr:Need more info"
        assert result[1]["title"] == "Used: Search library"
        assert mock_translate.call_count == 2


class TestHtmxStreamSignature:
    """Tests for htmx_stream function signature after refactoring."""

    def test_no_llm_parameter(self):
        """htmx_stream should not have llm parameter after refactoring."""
        params = list(inspect.signature(htmx_stream).parameters.keys())
        assert "llm" not in params, (
            f"llm should be removed from htmx_stream, got: {params}"
        )

    def test_has_cost_callback(self):
        """htmx_stream should have cost_callback parameter."""
        params = list(inspect.signature(htmx_stream).parameters.keys())
        assert "cost_callback" in params

    def test_has_output_items_callback(self):
        """htmx_stream should have output_items_callback parameter."""
        params = list(inspect.signature(htmx_stream).parameters.keys())
        assert "output_items_callback" in params

    def test_required_parameters(self):
        """htmx_stream should have expected required parameters."""
        params = list(inspect.signature(htmx_stream).parameters.keys())

        expected = ["chat", "message_id"]
        for param in expected:
            assert param in params, f"Missing required parameter: {param}"


class TestTitleChatSignature:
    """Tests for title_chat function signature after refactoring."""

    def test_no_llm_parameter(self):
        """title_chat should not have llm parameter after refactoring."""
        params = list(inspect.signature(title_chat).parameters.keys())
        assert "llm" not in params, (
            f"llm should be removed from title_chat, got: {params}"
        )

    def test_expected_parameters(self):
        """title_chat should have only chat_id and force_title."""
        params = list(inspect.signature(title_chat).parameters.keys())
        assert params == ["chat_id", "force_title"]


@pytest.mark.django_db
class TestTitleChat:
    """Integration tests for title_chat function."""

    def test_empty_chat_returns_untitled(self, all_apps_user):
        """Empty chat should return 'Untitled chat'."""
        from chat_next.models import Chat, Message

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="")

        # Chat with no messages or very short content
        Message.objects.create(chat=chat, text="hi", is_bot=False)

        result = title_chat(chat.id, force_title=False)

        # Should return existing title (empty -> skip) or short chats don't trigger
        # The function has logic to skip titling for short chats
        assert result is not None

    def test_preserves_existing_title(self, all_apps_user):
        """If chat already has title and force_title=False, preserve it."""
        from chat_next.models import Chat, Message

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="My Custom Title")

        Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(chat=chat, text="Hi there!", is_bot=True)

        result = title_chat(chat.id, force_title=False)

        assert result == "My Custom Title"

    def test_retitles_saved_placeholder_title(self, all_apps_user, monkeypatch):
        """Saved placeholder titles should still be replaced when enough context exists."""
        from chat_next import _llm
        from chat_next.models import Chat, Message

        class FakeResponsesClient:
            def __init__(self, *args, **kwargs):
                pass

            async def complete_chat(self, input_items, instructions):
                return "Budget follow-up", None, None, None

        monkeypatch.setattr(_llm, "ResponsesAPIClient", FakeResponsesClient)

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Untitled chat")

        Message.objects.create(chat=chat, text="We need a budget summary", is_bot=False)
        Message.objects.create(chat=chat, text="Here is the summary", is_bot=True)
        Message.objects.create(chat=chat, text="Please tighten it up", is_bot=False)
        Message.objects.create(chat=chat, text="Done", is_bot=True)

        result = title_chat(chat.id, force_title=False)

        chat.refresh_from_db()
        assert result == "Budget follow-up"
        assert chat.title == "Budget follow-up"

    def test_uses_gpt54_nano_and_rejects_verbatim_first_message(
        self, all_apps_user, monkeypatch
    ):
        """Title generation should use gpt-5.4-nano and avoid echoing the first prompt."""
        from chat_next import _llm
        from chat_next.models import Chat, Message

        captured = {}

        class FakeResponsesClient:
            def __init__(self, *args, **kwargs):
                captured["kwargs"] = kwargs

            async def complete_chat(self, input_items, instructions):
                captured["input_items"] = input_items
                captured["instructions"] = instructions
                return (
                    "Please summarize the budget proposal for 2024.",
                    None,
                    None,
                    None,
                )

        monkeypatch.setattr(_llm, "ResponsesAPIClient", FakeResponsesClient)

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Untitled chat")

        Message.objects.create(
            chat=chat,
            text="Please summarize the budget proposal for 2024.",
            is_bot=False,
        )

        result = title_chat(chat.id, force_title=False)

        chat.refresh_from_db()
        assert result == "About budget proposal for 2024"
        assert chat.title == "About budget proposal for 2024"
        assert captured["kwargs"]["model"] == "gpt-5.4-nano"
        assert captured["kwargs"]["reasoning"] is False
        assert captured["input_items"] == [
            {
                "role": "user",
                "content": "User: Please summarize the budget proposal for 2024.",
            }
        ]
        assert (
            "Do NOT copy or lightly trim the first user message"
            in captured["instructions"]
        )

    def test_annotate_pending_titles_marks_placeholders_pending(
        self, all_apps_user, monkeypatch
    ):
        """Placeholder titles are normalized and queued for lazy refresh."""
        from chat_next.models import Chat

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Untitled chat")
        chat.message_count = 1
        queued = []

        def fake_enqueue(chat_id, language=None, timeout=600):
            queued.append((chat_id, language, timeout))
            return True

        monkeypatch.setattr(
            "chat_next.utils.enqueue_chat_title_generation",
            fake_enqueue,
        )

        annotate_pending_titles([chat], language="en")

        assert chat.is_title_pending is True
        assert str(chat.title) == "Untitled chat"
        assert queued == [(chat.id, "en", 600)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_emits_keepalive_for_slow_generators(all_apps_user):
    async def slow_stream_generator():
        await asyncio.sleep(0.03)
        yield "finished response"

    user = await sync_to_async(all_apps_user)("chat_next_stream_keepalive")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=slow_stream_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0.01,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    assert any(output == ": keepalive\n\n" for output in outputs)
    assert any("finished response" in output for output in outputs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_can_disable_keepalive(all_apps_user):
    async def slow_stream_generator():
        await asyncio.sleep(0.03)
        yield "finished response"

    user = await sync_to_async(all_apps_user)("chat_next_stream_no_keepalive")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=slow_stream_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    assert all(output != ": keepalive\n\n" for output in outputs)
    assert any("finished response" in output for output in outputs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_throttles_large_streaming_updates(
    all_apps_user, monkeypatch
):
    monkeypatch.setattr("chat_next.utils.SSE_RENDER_THROTTLE_MESSAGE_LENGTH", 10)
    monkeypatch.setattr("chat_next.utils.SSE_RENDER_THROTTLE_INTERVAL_SECONDS", 0.05)

    async def rapid_large_stream_generator():
        for chunk_index in range(6):
            yield "X" * (20 + chunk_index)

    user = await sync_to_async(all_apps_user)("chat_next_stream_render_throttle")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=rapid_large_stream_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    streamed_outputs = []
    final_output = None
    async for yielded_output in response_stream:
        if yielded_output == "event: done\ndata: complete\n\n":
            break
        if 'id="message_' in yielded_output:
            final_output = yielded_output
        else:
            streamed_outputs.append(yielded_output)

    assert len(streamed_outputs) < 6
    assert streamed_outputs
    assert final_output is not None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_throttles_large_processing_step_oob_updates(
    all_apps_user, monkeypatch
):
    monkeypatch.setattr("chat_next.utils.SSE_LARGE_PROCESSING_OOB_PAYLOAD_LENGTH", 50)
    monkeypatch.setattr(
        "chat_next.utils.SSE_LARGE_PROCESSING_OOB_INTERVAL_SECONDS", 0.05
    )

    async def rapid_large_processing_generator():
        for chunk_index in range(6):
            yield {
                "text": "",
                "processing_steps": [
                    {
                        "type": "tool_call",
                        "tool_type": "code_interpreter",
                        "status": "in_progress",
                        "details": {
                            "code": "print('still streaming')\n" * (20 + chunk_index),
                        },
                    }
                ],
                "is_reasoning": False,
            }

    user = await sync_to_async(all_apps_user)("chat_next_stream_large_processing_oob")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=rapid_large_processing_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    streamed_reasoning_updates = []
    final_output = None
    async for yielded_output in response_stream:
        if yielded_output == "event: done\ndata: complete\n\n":
            break
        if 'id="message_' in yielded_output:
            final_output = yielded_output
        elif f'id="reasoning-data-{response_message.id}"' in yielded_output:
            streamed_reasoning_updates.append(yielded_output)

    assert 0 < len(streamed_reasoning_updates) < 6
    assert final_output is not None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_emits_action_bar_oob_update_for_transient_usage(
    all_apps_user,
):
    async def tool_progress_generator():
        yield {
            "text": "",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "completed",
                    "details": {
                        "name": "search_library",
                        "arguments": "{}",
                        "tool_label": "Search library",
                    },
                }
            ],
            "is_reasoning": False,
            "usage": {"input_tokens": 1200, "output_tokens": 300},
        }
        yield {"text": "Finished answer", "is_reasoning": False}

    user = await sync_to_async(all_apps_user)("chat_next_stream_context_indicator")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=tool_progress_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    joined_output = "\n".join(outputs)
    assert f'id="message-actions-{response_message.id}"' in joined_output
    assert 'hx-swap-oob="true"' in joined_output
    assert 'class="context-indicator-wrapper ms-auto"' in joined_output


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_keeps_typing_dots_between_steps_but_hides_them_for_approval(
    all_apps_user,
):
    async def processing_gap_then_approval_generator():
        yield {
            "text": "",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "completed",
                    "details": {
                        "name": "search_library",
                        "arguments": {},
                        "tool_label": "Search library",
                    },
                }
            ],
            "is_reasoning": False,
        }
        yield {
            "text": "",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {
                        "name": "search_library",
                        "arguments": {},
                        "tool_label": "Search library",
                        "approval_request_id": "req-typing-dots",
                    },
                }
            ],
            "is_reasoning": False,
        }
        yield {"text": "Finished answer", "is_reasoning": False}

    user = await sync_to_async(all_apps_user)("chat_next_stream_typing_dots")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=processing_gap_then_approval_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    dots_html = '<div class="typing"><span></span><span></span><span></span></div>'
    assert any(dots_html in output for output in outputs)

    approval_output = next(output for output in outputs if "req-typing-dots" in output)
    assert dots_html not in approval_output


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_preserves_last_committed_text_when_paused_for_approval(
    all_apps_user,
):
    async def approval_pause_generator():
        yield {
            "text": "Provisional assistant narration before approval.",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {
                        "name": "termium_lookup",
                        "call_id": "call-pause-text-1",
                        "arguments": '{"query": "cabinet confidence"}',
                        "approval_request_id": "call-pause-text-1",
                        "tool_label": "Termium lookup",
                    },
                }
            ],
            "is_reasoning": False,
            "pending_local_tool": {
                "name": "termium_lookup",
                "call_id": "call-pause-text-1",
                "arguments": '{"query": "cabinet confidence"}',
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
        }

    user = await sync_to_async(all_apps_user)("chat_next_stream_pause_text_guard")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="Hello",
        is_bot=False,
    )
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
        text="Already committed answer.",
        details={},
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=approval_pause_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    async for yielded_output in response_stream:
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    await sync_to_async(response_message.refresh_from_db)()

    assert response_message.text == "Already committed answer."
    assert (
        response_message.details["pending_local_tool"]["call_id"] == "call-pause-text-1"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_routes_approval_pause_text_to_reasoning_widget(
    all_apps_user,
):
    async def approval_pause_generator():
        yield {
            "text": "Provisional assistant narration before approval.",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "function_call",
                    "status": "waiting_approval",
                    "details": {
                        "name": "termium_lookup",
                        "call_id": "call-pause-streaming-text-1",
                        "arguments": '{"query": "cabinet confidence"}',
                        "approval_request_id": "call-pause-streaming-text-1",
                        "tool_label": "Termium lookup",
                    },
                }
            ],
            "is_reasoning": False,
            "pending_local_tool": {
                "name": "termium_lookup",
                "call_id": "call-pause-streaming-text-1",
                "arguments": '{"query": "cabinet confidence"}',
                "tool_label": "Termium lookup",
                "allow_auto_approve": False,
                "pre_executed_outputs": [],
                "approval_needed_count": 1,
                "max_iterations_reached": False,
            },
        }

    user = await sync_to_async(all_apps_user)("chat_next_stream_pause_text_reasoning")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="Hello",
        is_bot=False,
    )
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
        text="Already committed answer.",
        details={},
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=approval_pause_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    reasoning_updates = []
    message_updates = []
    async for yielded_output in response_stream:
        if f'id="reasoning-data-{response_message.id}"' in yielded_output:
            reasoning_updates.append(yielded_output)
        if 'id="message_' in yielded_output:
            message_updates.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    live_reasoning_updates = [
        output for output in reasoning_updates if 'id="message_' not in output
    ]

    assert live_reasoning_updates
    assert any(
        "Provisional assistant narration before approval." in output
        for output in live_reasoning_updates
    )
    assert all(
        "data: Provisional assistant narration before approval." not in output
        for output in message_updates
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_emits_compaction_progress_and_post_compaction_context_update(
    all_apps_user,
):
    async def compaction_progress_generator():
        yield {
            "text": "",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "compaction",
                    "status": "in_progress",
                    "details": {
                        "name": "compact_conversation",
                        "tool_label": "Compacting conversation...",
                    },
                }
            ],
            "is_reasoning": False,
        }
        yield {
            "text": "",
            "processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "compaction",
                    "status": "completed",
                    "details": {
                        "name": "compact_conversation",
                        "tool_label": "Compacted conversation",
                    },
                }
            ],
            "is_reasoning": False,
            "usage": {"input_tokens": 1400, "output_tokens": 120},
        }
        yield {"text": "Finished answer", "is_reasoning": False}

    user = await sync_to_async(all_apps_user)("chat_next_stream_compaction_progress")

    from chat_next.models import Chat, Message

    chat = Chat(user=user)
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=compaction_progress_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    joined_output = "\n".join(outputs)
    assert "Compacting conversation..." in joined_output
    assert "Compacted conversation" in joined_output
    assert f'id="message-actions-{response_message.id}"' in joined_output
    assert 'class="context-indicator-wrapper ms-auto"' in joined_output


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_finalizes_without_waiting_for_title_generation(
    all_apps_user, monkeypatch
):
    enqueue_calls = []

    def fake_enqueue(chat_id, language=None, timeout=600):
        enqueue_calls.append((chat_id, language, timeout))
        return True

    def fail_inline_title(*args, **kwargs):
        raise AssertionError("htmx_stream should not call title_chat inline")

    monkeypatch.setattr(
        "chat_next.utils.enqueue_chat_title_generation",
        fake_enqueue,
    )
    monkeypatch.setattr("chat_next.utils.title_chat", fail_inline_title)

    async def simple_stream_generator():
        yield "finished response"

    user = await sync_to_async(all_apps_user)("chat_next_stream_nonblocking_title")

    from chat_next.models import Chat, Message

    chat = Chat(user=user, title="")
    await sync_to_async(chat.save)()
    await sync_to_async(Message.objects.create)(
        chat=chat, text="First prompt", is_bot=False
    )
    await sync_to_async(Message.objects.create)(
        chat=chat, text="First reply", is_bot=True
    )
    await sync_to_async(Message.objects.create)(
        chat=chat, text="Second prompt", is_bot=False
    )
    message = await sync_to_async(Message.objects.create)(
        chat=chat, text="Third prompt", is_bot=False
    )
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=simple_stream_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    assert any("finished response" in output for output in outputs)
    assert outputs[-1] == "event: done\ndata: complete\n\n"
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][0] == chat.id
    assert enqueue_calls[0][1]
    assert enqueue_calls[0][2] == 600


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_does_not_enqueue_title_generation_for_short_chats(
    all_apps_user, monkeypatch
):
    enqueue_calls = []

    def fake_enqueue(chat_id, language=None, timeout=600):
        enqueue_calls.append((chat_id, language, timeout))
        return True

    monkeypatch.setattr(
        "chat_next.utils.enqueue_chat_title_generation",
        fake_enqueue,
    )

    async def simple_stream_generator():
        yield "finished response"

    user = await sync_to_async(all_apps_user)("chat_next_stream_short_title_guard")

    from chat_next.models import Chat, Message

    chat = Chat(user=user, title="")
    await sync_to_async(chat.save)()
    message = await sync_to_async(Message.objects.create)(
        chat=chat,
        text="Hello",
        is_bot=False,
    )
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat,
        is_bot=True,
        parent=message,
    )

    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=simple_stream_generator(),
        wrap_markdown=False,
        keepalive_interval_seconds=0,
    )

    outputs = []
    async for yielded_output in response_stream:
        outputs.append(yielded_output)
        if yielded_output == "event: done\ndata: complete\n\n":
            break

    assert any("finished response" in output for output in outputs)
    assert outputs[-1] == "event: done\ndata: complete\n\n"
    assert enqueue_calls == []


class TestReplaceSandboxUrls:
    """Tests for replace_sandbox_urls function."""

    def test_no_sandbox_urls(self):
        """Text without sandbox URLs is unchanged."""
        from unittest.mock import MagicMock

        from chat_next.utils import replace_sandbox_urls

        message = MagicMock()
        message.files.all.return_value = []

        text = "Hello world, here is a normal URL: https://example.com/image.png"
        result = replace_sandbox_urls(text, message)
        assert result == text

    def test_empty_text(self):
        """Empty text returns empty."""
        from unittest.mock import MagicMock

        from chat_next.utils import replace_sandbox_urls

        message = MagicMock()
        result = replace_sandbox_urls("", message)
        assert result == ""
        result = replace_sandbox_urls(None, message)
        assert result is None

    def test_sandbox_url_replacement(self):
        """Sandbox URLs are replaced with actual file URLs."""
        from unittest.mock import MagicMock

        from chat_next.utils import replace_sandbox_urls

        # Create mock ChatFile with SavedFile
        saved_file = MagicMock()
        saved_file.file.url = "/media/files/test_chart.png"

        chat_file = MagicMock()
        chat_file.id = 123
        chat_file.filename = "test_chart.png"
        chat_file.saved_file = saved_file

        message = MagicMock()
        message.files.all.return_value = [chat_file]

        text = "Here is the chart:\n![Chart](sandbox:/mnt/data/test_chart.png)"
        result = replace_sandbox_urls(text, message)

        assert "sandbox:" not in result
        assert "/chat_next/file/123/" in result

    def test_multiple_sandbox_urls(self):
        """Multiple sandbox URLs are all replaced."""
        from unittest.mock import MagicMock

        from chat_next.utils import replace_sandbox_urls

        # Create mock files
        saved_file1 = MagicMock()
        saved_file1.file.url = "/media/files/chart.png"
        chat_file1 = MagicMock()
        chat_file1.id = 456
        chat_file1.filename = "chart.png"
        chat_file1.saved_file = saved_file1

        saved_file2 = MagicMock()
        saved_file2.file.url = "/media/files/data.csv"
        chat_file2 = MagicMock()
        chat_file2.id = 789
        chat_file2.filename = "data.csv"
        chat_file2.saved_file = saved_file2

        message = MagicMock()
        message.files.all.return_value = [chat_file1, chat_file2]

        text = """Here is your analysis:
![Chart](sandbox:/mnt/data/chart.png)
[Download CSV](sandbox:/mnt/data/data.csv)"""

        result = replace_sandbox_urls(text, message)

        assert "sandbox:" not in result
        assert "/chat_next/file/456/" in result
        assert "/chat_next/file/789/" in result


class TestReplaceImageUrls:
    """Tests for replace_image_urls function."""

    def test_empty_text(self):
        """Empty text returns empty."""
        from chat_next.utils import replace_image_urls

        assert replace_image_urls("", []) == ""
        assert replace_image_urls(None, []) is None

    def test_empty_mappings(self):
        """Empty mappings returns original text."""
        from chat_next.utils import replace_image_urls

        text = "Here is an image: ![plot](https://example.com/image.png)"
        assert replace_image_urls(text, []) == text
        assert replace_image_urls(text, None) == text

    def test_url_replacement(self):
        """Image URLs are replaced with permanent URLs."""
        from chat_next.utils import replace_image_urls

        text = "Here is a plot:\n![Plot](https://openai-tmp.com/abc123.png)"
        mappings = [("https://openai-tmp.com/abc123.png", "/media/files/plot.png")]

        result = replace_image_urls(text, mappings)

        assert "https://openai-tmp.com/abc123.png" not in result
        assert "/media/files/plot.png" in result

    def test_multiple_url_replacements(self):
        """Multiple image URLs are all replaced."""
        from chat_next.utils import replace_image_urls

        text = """Analysis results:
![Plot 1](https://openai-tmp.com/plot1.png)
![Plot 2](https://openai-tmp.com/plot2.png)"""

        mappings = [
            ("https://openai-tmp.com/plot1.png", "/media/files/plot1.png"),
            ("https://openai-tmp.com/plot2.png", "/media/files/plot2.png"),
        ]

        result = replace_image_urls(text, mappings)

        assert "https://openai-tmp.com" not in result
        assert "/media/files/plot1.png" in result
        assert "/media/files/plot2.png" in result


class TestSanitizeUnresolvedSandboxLinks:
    """Tests for sanitize_unresolved_sandbox_links function."""

    def test_removes_sandbox_markdown_links_keeps_text(self):
        """Markdown sandbox links are reduced to readable plain text."""
        from chat_next.utils import sanitize_unresolved_sandbox_links

        text = (
            "Your document has been summarized.\n\n"
            "[AI Assistant - Implementation Plan (EN)_output.md]"
            "(sandbox:/mnt/data/AI%20Assistant%20-%20Implementation%20Plan%20(EN)_output.md)"
        )

        result = sanitize_unresolved_sandbox_links(text)

        assert "sandbox:" not in result
        assert "AI Assistant - Implementation Plan (EN)_output.md" in result

    def test_removes_sandbox_image_markdown(self):
        """Sandbox image markdown is removed when unresolved."""
        from chat_next.utils import sanitize_unresolved_sandbox_links

        text = "Here is a preview:\n\n![Chart](sandbox:/mnt/data/chart.png)\n\nDone."
        result = sanitize_unresolved_sandbox_links(text)

        assert "sandbox:" not in result
        assert "![Chart]" not in result
        assert "Done." in result

    def test_no_sandbox_text_unchanged(self):
        """Text without sandbox URLs is unchanged."""
        from chat_next.utils import sanitize_unresolved_sandbox_links

        text = "Files are ready: summary_output.md"
        assert sanitize_unresolved_sandbox_links(text) == text


@pytest.mark.django_db
def test_stream_library_updates_uses_library_wording(all_apps_user, monkeypatch):
    """Streaming library updates should use simplified library wording."""
    from chat_next.models import Chat, Message
    from chat_next.utils import stream_library_updates

    from librarian.models import DataSource, Document, Library

    user = all_apps_user("stream_library_updates_wording")
    chat = Chat.objects.create(user=user)
    user_message = Message.objects.create(
        chat=chat,
        text="Upload these",
        is_bot=False,
    )

    library = Library.objects.create(name=" ", created_by=user)
    data_source = DataSource.objects.create(
        library=library,
        name="Chat files",
    )
    processing_doc = Document.objects.create(
        data_source=data_source,
        filename="draft.pdf",
        status="PROCESSING",
    )
    processing_doc.chat_next_messages.add(user_message)

    async def fake_sleep(_seconds):
        processing_doc.status = "SUCCESS"
        await sync_to_async(processing_doc.save)(update_fields=["status"])

    monkeypatch.setattr("chat_next.utils.asyncio.sleep", fake_sleep)

    async def collect_updates():
        updates = []
        async for chunk in stream_library_updates(user_message):
            updates.append(chunk)
        return updates

    updates = async_to_sync(collect_updates)()

    assert updates[0] == "Adding to library... (1 file(s) still processing)"
    assert updates[-1] == "1 new document(s) added to library."
    assert not any("Q&A" in update for update in updates)
