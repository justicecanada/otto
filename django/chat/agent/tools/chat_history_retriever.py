from smolagents import Tool

from chat.models import Chat


class ChatHistoryTool(Tool):
    name = "chat_history"
    description = "Retrieves the current chat history including uploaded filenames/IDs. Use when the user's question is unclear or requires context from previous messages."
    inputs = {}
    output_type = "string"

    def __init__(self, chat_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._chat_id = chat_id

    def forward(self) -> str:
        chat = Chat.objects.get(id=self._chat_id)
        return "Chat History:\n===\n\n" + chat.get_history_string(
            include_system_prompt=False
        )
