from smolagents import Tool

from chat.models import Chat


class SummarizeTool(Tool):
    name = "summarize_text"
    description = "Summarize the provided text according to the instructions."
    inputs = {
        "instructions": {
            "type": "string",
            "description": "Summarization instructions (prompt)",
        }
    }
    output_type = "string"

    def __init__(self, chat_id: int, *args, **kwargs):
        self._chat = Chat.objects.get(id=chat_id)
        super().__init__(*args, **kwargs)

    def forward(self, instructions: str = "") -> str:
        """
        Returns the summarized text
        """
        if not instructions:
            instructions = self._chat.options.summarize_prompt
