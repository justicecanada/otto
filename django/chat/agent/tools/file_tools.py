from smolagents import Tool

from chat.models import ChatFile
from otto.models import User


class FileReaderTool(Tool):
    name = "file_reader"
    description = "Outputs markdown text from a specified file in the chat. (Use the chat_history tool to find the appropriate file ID.)"
    inputs = {"file_id": {"type": "integer", "description": "ID of the file to read"}}
    output_type = "string"

    def __init__(self, user_id: int, *args, **kwargs):
        self._user = User.objects.get(id=user_id)
        super().__init__(*args, **kwargs)

    def forward(self, file_id: int) -> str:
        try:
            file = ChatFile.objects.get(id=file_id)
        except ChatFile.DoesNotExist:
            return "File not found. Did you provide the right ID?"
        # Check that user has permissions to read the file
        if not self._user.has_perm("chat.access_file", file):
            return "User does not have permission to access this file. Did you provide the right ID?"
        # We now have a file to read.
        if not file.text:
            file.extract_text()
        return file.text
