from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from smolagents import Tool

from chat.llm import OttoLLM
from chat.models import Chat, ChatFile
from otto.models import User


class SummarizeFileTool(Tool):
    name = "summarize_file"
    description = (
        "Summarize full text of file according to default or user instructions."
    )
    inputs = {
        "file_id": {
            "type": "integer",
            "description": "ID of file to summarize",
        },
        "instructions": {
            "type": "string",
            "description": "User's instructions for summary. ONLY INCLUDE IF USER GIVES SPECIFIC INSTRUCTIONS.",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, chat_id, *args, **kwargs):
        self._chat = Chat.objects.get(id=chat_id)
        self._user = self._chat.user
        super().__init__(*args, **kwargs)

    def forward(self, file_id: int, instructions: str = "") -> str:
        """
        Returns the summarized text
        """
        from chat.utils import final_response, summarize_long_text

        file = ChatFile.objects.get(id=file_id)
        # Check permissions
        if not self._user.has_perm("chat.access_file", file):
            raise PermissionDenied(_("You do not have permission to access this file."))

        if not instructions:
            instructions = self._chat.options.summarize_prompt

        if not file.text:
            file.extract_text()

        llm = OttoLLM(deployment=self._chat.options.summarize_model)
        summary_generator = summarize_long_text(
            file.text,
            llm,
            summarize_prompt=instructions,
        )

        summary = final_response(summary_generator)
        return summary
