import json

from django.utils.translation import activate, deactivate

from chat_next.utils import format_processing_steps


class TestFormatProcessingStepsFunctionCallFormatting:
    def setup_method(self):
        activate("en")

    def teardown_method(self):
        deactivate()

    def test_function_call_completed_formatting(self):
        args = {"query": "cannabis", "limit": 10}
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "search_laws",
                    "arguments": json.dumps(args),
                    "output": "Found 50 results...",
                },
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1
        assert result[0]["title"] == "Used tool: Search laws"

        # Check details formatting
        details = result[0]["details"]
        assert "```json" in details
        assert '"query": "cannabis"' in details
        assert '"limit": 10' in details
        assert "```" in details

        # Verify output is NOT present
        assert "Found 50 results" not in details
        assert "Output:" not in details

    def test_function_call_invalid_json_formatting(self):
        activate("en")
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "search_laws",
                    "arguments": "raw string args",
                    "output": "output",
                },
            }
        ]
        result = format_processing_steps(steps)
        assert len(result) == 1

        details = result[0]["details"]
        assert "```" in details
        assert "raw string args" in details
        assert "```json" not in details

    def test_function_call_completed_uses_longer_fence_for_embedded_backticks(self):
        args = {
            "skill_id": 35,
            "body_en": "# Deposition Prep\n\n```\nUsed tool: Edit skill\n```\n",
        }
        steps = [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "edit_skill",
                    "arguments": json.dumps(args),
                },
            }
        ]

        result = format_processing_steps(steps)

        assert len(result) == 1
        details = result[0]["details"]
        assert details.startswith("````json\n")
        assert (
            '"body_en": "# Deposition Prep\\n\\n```\\nUsed tool: Edit skill\\n```\\n"'
            in details
        )
        assert details.endswith("\n````")
