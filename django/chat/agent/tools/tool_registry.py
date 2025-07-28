"""
A central registry for agent tools.
"""

from django.utils.translation import gettext_lazy as _

from smolagents import VisitWebpageTool, WebSearchTool

from .chat_history_retriever import ChatHistoryTool
from .file_tools import FileReaderTool
from .law_retriever import LawRetrieverTool
from .summarize_tools import SummarizeFileTool
from .translate_tools import TranslateFileTool

# A dictionary of all available tools. The keys are used to store the enabled tools in
# the ChatOptions.agent_tools list. The values are dictionaries containing the tool's
# class, a user-friendly name, and any initialization parameters.
# The 'init_params' can contain placeholders for values that are only available at
# runtime, like 'chat_history'.
# The keys should match the name attribute of the class.
AVAILABLE_TOOLS = {
    "law_retriever": {
        "class": LawRetrieverTool,
        "name": _("Legislation search"),
        "init_params": {},
    },
    "summarize_file": {
        "class": SummarizeFileTool,
        "name": _("Summarize file"),
        "init_params": {"chat_id": None},
    },
    "chat_history": {
        "class": ChatHistoryTool,
        "name": _("Chat history"),
        "init_params": {"chat_id": None},
    },
    "file_reader": {
        "class": FileReaderTool,
        "name": _("Read file"),
        "init_params": {"user_id": None},
    },
    "translate_file": {
        "class": TranslateFileTool,
        "name": _("Translate file"),
        "init_params": {"user_id": None, "response_message_id": None},
    },
    "web_search": {
        "class": WebSearchTool,
        "name": _("Web search"),
        "init_params": {},
    },
    "visit_webpage": {
        "class": VisitWebpageTool,
        "name": _("Visit webpage"),
        "init_params": {},
    },
}
