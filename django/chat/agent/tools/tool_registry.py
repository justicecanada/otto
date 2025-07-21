"""
A central registry for agent tools.
"""

from django.utils.translation import gettext_lazy as _

from smolagents import VisitWebpageTool, WebSearchTool

from .chat_history_retriever import ChatHistoryTool
from .law_retriever import LawRetrieverTool

# A dictionary of all available tools. The keys are used to store the enabled tools in
# the ChatOptions.agent_tools list. The values are dictionaries containing the tool's
# class, a user-friendly name, and any initialization parameters.
# The 'init_params' can contain placeholders for values that are only available at
# runtime, like 'chat_history'.
AVAILABLE_TOOLS = {
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
    "law_retriever": {
        "class": LawRetrieverTool,
        "name": _("Law retriever"),
        "init_params": {},
    },
    "chat_history": {
        "class": ChatHistoryTool,
        "name": _("Chat history"),
        "init_params": {"chat_history": None},  # Placeholder for runtime value
    },
}
