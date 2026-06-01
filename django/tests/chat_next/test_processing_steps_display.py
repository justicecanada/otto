from django.utils.translation import activate, deactivate

from chat_next.utils import format_processing_steps


class TestFormatProcessingStepsFunctionCalls:
    def setup_method(self):
        activate("en")

    def teardown_method(self):
        deactivate()

    def test_function_call_in_progress(self):
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "in_progress",
                "details": {
                    "name": "search_laws",
                },
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Using tool: Search laws..."
        assert result[0]["details"] == ""

    def test_function_call_completed(self):
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "search_laws",
                    "arguments": '{"query": "cannabis"}',
                    "output": "Found 50 results...",
                },
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Used tool: Search laws"
        # Arguments are formatted as code block
        assert '"query": "cannabis"' in result[0]["details"]

    def test_function_call_failed(self):
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "failed",
                "details": {
                    "name": "search_laws",
                    "output": {"error": "Timeout"},
                },
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Tool call failed: Search laws"
        assert result[0]["details"] == "Timeout"

    def test_compaction_step_is_visible(self):
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "search_laws",
                    "arguments": '{"query": "cannabis"}',
                },
            },
            {
                "type": "tool_call",
                "tool_type": "compaction",
                "status": "completed",
                "details": {
                    "name": "compact_conversation",
                    "tool_label": "Compacted conversation",
                },
            },
        ]
        result = format_processing_steps(steps)
        assert len(result) == 2
        assert result[1]["title"] == "Compacted conversation"
        assert result[1]["status"] == "complete"
        assert result[1]["details"] == ""

    def test_compaction_step_in_progress_uses_live_label(self):
        steps = [
            {
                "type": "tool_call",
                "tool_type": "compaction",
                "status": "in_progress",
                "details": {
                    "name": "compact_conversation",
                    "tool_label": "Compacting conversation...",
                },
            }
        ]

        result = format_processing_steps(steps)

        assert len(result) == 1
        assert result[0]["title"] == "Compacting conversation..."
        assert result[0]["status"] == "in_progress"
        assert result[0]["details"] == ""
