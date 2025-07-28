import time

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from smolagents import Tool

from chat.models import Chat, ChatFile, Message
from chat.tasks import translate_file
from otto.models import User


class TranslateFileTool(Tool):
    """
    Enqueues celery task to translate a file.
    Returns the ID of the created file.
    """

    name = "translate_file"
    description = (
        "Translate a file using Azure Translation API."
        "Creates a ChatFile in the response message."
    )
    inputs = {
        "target_language": {
            "type": "string",
            "description": "Target language (usually 'fr' or 'en')",
        },
        "file_id": {"type": "integer", "description": "ID of the file to translate"},
    }
    output_type = "string"

    def __init__(self, user_id: int, response_message_id: int, *args, **kwargs):
        self._user = User.objects.get(id=user_id)
        self._response_message_id = response_message_id
        super().__init__(*args, **kwargs)

    def forward(self, target_language: str, file_id: int) -> int:
        # Initiate the Celery task for translating the file with Azure
        file = ChatFile.objects.get(id=file_id)
        # Check permissions
        if not self._user.has_perm("chat.access_file", file):
            raise PermissionDenied(_("You do not have permission to access this file."))
        # Enqueue the celery task
        file_path = file.saved_file.file.path
        if target_language == "fr":
            target_language == "fr-ca"
        try:
            translate_file(file_path, target_language, self._response_message_id)
        except:
            raise Exception(_("File translation failed."))
        return "File translated successfully. The user will see it appended to the response. No further action required."
