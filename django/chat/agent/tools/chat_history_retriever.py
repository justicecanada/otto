from asgiref.sync import async_to_sync, sync_to_async
from smolagents import Tool


class ChatHistoryTool(Tool):
    name = "chat_history"
    description = "Retrieves the chat history for the current conversation. Use when the user's question is unclear or requires context from previous messages."
    inputs = {}
    output_type = "string"

    def __init__(self, chat_history, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history_string = chat_history

    def forward(self) -> str:
        return "Chat History:\n===\n\n" + self._history_string
