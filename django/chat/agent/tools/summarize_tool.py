from smolagents import Tool

from chat.llm import OttoLLM
from chat.models import Chat


class SummarizeTool(Tool):
    name = "summarize_text"
    description = "Summarize text according to default or user instructions."
    inputs = {
        "text": {
            "type": "string",
            "description": "Text to summarize",
        },
        "instructions": {
            "type": "string",
            "description": "Instructions for summarization (if provided by user)",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, chat_id: int, *args, **kwargs):
        self._chat = Chat.objects.get(id=chat_id)
        super().__init__(*args, **kwargs)

    def forward(self, text: str, instructions: str = "") -> str:
        """
        Returns the summarized text
        """
        from chat.utils import summarize_long_text

        if not instructions:
            instructions = self._chat.options.summarize_prompt

        llm = OttoLLM(deployment=self._chat.options.summarize_model)
        summary_generator = summarize_long_text(
            self._chat.messages.all(),
            llm,
            summarize_prompt=instructions,
        )
        # Iterate until generator is exhausted to get the final summary
        summary = ""
        for chunk in summary_generator:
            summary = chunk
        return summary
