"""
Local function calling tools for chat_next.

This module provides a clean, extensible architecture for registering and
executing local function tools that the AI can call. Each tool is defined
as an OttoTool instance containing:
- JSON schema definition for OpenAI API
- Python implementation function
- Metadata (name, description, permissions check)

Function calling flow (from OpenAI docs):
1. Request to model with tools defined
2. Model returns function_call items with arguments
3. Execute the function locally with those arguments
4. Send function_call_output back to model
5. Model uses results to generate final response

Usage:
    from chat_next.tools import TOOL_REGISTRY, execute_tool_call

    # Get tool configs for API request
    tools_config = TOOL_REGISTRY.get_tools_config(user, chat)

    # After receiving function_call from model:
    result = await execute_tool_call(
        tool_name="list_libraries",
        arguments={"query": "search term"},
        user=request.user,
        chat=chat,
    )
"""

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from django.utils.translation import gettext_lazy as _

from asgiref.sync import sync_to_async
from structlog import get_logger

from chat_next.models import TOOL_CATEGORY_QA_LIBRARIES, TOOL_CATEGORY_SKILLS

logger = get_logger(__name__)


LOCAL_SKILLS_ALWAYS_EXPOSED_TOOL_NAMES = {"load_skill_instructions"}

# Some tools carry behavior-critical routing/transparency guidance in their
# manifest text. Preserve those descriptions verbatim even while most tool
# manifests stay aggressively compact for prompt-size reasons.
FULL_FIDELITY_TOOL_SCHEMA_NAMES = {"get_document_text"}


PRETTY_TOOL_DISPLAY_NAMES = {
    "list_canadian_legal_datasets": _("List Canadian legal datasets"),
    "retrieve_url_content": _("Retrieve URL content"),
    "translate_files": _("Translate files"),
    "transcribe_files": _("Transcribe files"),
    "termium_lookup": _("Look up TERMIUM Plus"),
    "load_skill_instructions": _("Load skill instructions"),
    "list_libraries": _("List libraries"),
    "rag_search": _("RAG search"),
    "list_folders": _("List folders"),
    "list_documents": _("List documents"),
    "get_document_text": _("Get document text"),
    "find_in_document": _("Find in document"),
    "load_library_files": _("Load library files"),
    "view_library_files": _("View library files"),
    "list_presets": _("List presets"),
    "read_preset": _("Read preset"),
    "create_skill": _("Create skill"),
    "edit_skill": _("Edit skill"),
    "create_skill_from_preset": _("Create skill from preset"),
    "search_laws": _("Search laws"),
    "search_canadian_case_law": _("Search Canadian case law"),
    "fetch_canadian_case_by_citation": _("Fetch Canadian case by citation"),
    "search_canadian_legislation": _("Search Canadian legislation"),
    "fetch_canadian_legislation_by_citation": _(
        "Fetch Canadian legislation by citation"
    ),
    "prompt_documents": _("Process documents in batch"),
    "prompt_document_chunks": _("Process document chunks"),
    "prompt_document_ranges": _("Process document ranges"),
    "plan_document_chunks": _("Plan document chunks"),
}


def _compact_description(text: str | None, max_len: int | None) -> str:
    """Normalize whitespace and keep a compact leading summary."""
    value = " ".join(str(text or "").split())
    if not value:
        return ""

    if max_len is None:
        return value

    for sep in (". ", "; ", "\n"):
        if sep in value:
            candidate = value.split(sep, 1)[0].strip()
            if len(candidate) >= max_len:
                value = candidate
                break

    # if len(value) <= max_len:
    return value
    # return value[: max_len - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _compact_schema_descriptions(schema, max_len: int | None = 80):
    """Recursively shorten verbose JSON-schema descriptions for API payloads."""
    if isinstance(schema, dict):
        compacted = {}
        for key, value in schema.items():
            if key == "description":
                compacted[key] = _compact_description(value, max_len)
            else:
                compacted[key] = _compact_schema_descriptions(value, max_len=max_len)
        return compacted
    if isinstance(schema, list):
        return [_compact_schema_descriptions(item, max_len=max_len) for item in schema]
    return schema


def get_tool_display_name(tool_name: str) -> str:
    """Return the preferred human-readable display name for a tool."""
    if not tool_name:
        return ""
    return (
        PRETTY_TOOL_DISPLAY_NAMES.get(tool_name)
        or tool_name.replace("_", " ").capitalize()
    )


# ============================================================================
# Tool Definition Classes
# ============================================================================


@dataclass
class OttoTool:
    """
    Represents a local function tool that can be called by the AI.

    Attributes:
        name: Unique identifier for the tool (e.g., "list_libraries")
        description: Human-readable description for the AI
        parameters: JSON Schema defining the function's input arguments
        execute: Async function that implements the tool logic
        category: Tool category for UI grouping (e.g., TOOL_CATEGORY_QA_LIBRARIES)
        requires_user: Whether the tool requires user context
        requires_chat: Whether the tool requires chat context
        permission_check: Optional function to check if user can use this tool
    """

    name: str
    description: str
    parameters: dict
    execute: Callable  # async def execute(arguments: dict, context: ToolContext) -> Any
    category: str = (
        TOOL_CATEGORY_QA_LIBRARIES  # Default to Q&A libraries for backward compat
    )
    requires_user: bool = True
    requires_chat: bool = False
    permission_check: Optional[Callable] = None  # (user, chat) -> bool
    strict: bool = True  # Enable strict mode for structured outputs
    requires_approval: bool = False  # Require manual approval before execution
    approval_label: Optional[str] = None  # Optional display label for approval UI
    allow_auto_approve: bool = False  # Allow "auto-approve" option in UI
    approval_policy: Any = None  # Optional query-aware approval policy
    is_external_tool: bool = False  # Sends data outside Otto
    external_service_name: Optional[str] = None  # Human-readable external service
    estimate_cost: Optional[Callable] = None  # (arguments: dict) -> str | None

    @property
    def display_name(self) -> str:
        """Backward-compatible human-readable label for approval/UI flows.

        Older call sites still expect ``display_name``. The current tool metadata
        stores that label in ``approval_label`` when a friendlier UI label is
        needed, otherwise the tool name is the best fallback.
        """

        return self.approval_label or get_tool_display_name(self.name)

    def to_api_schema(self) -> dict:
        """Convert to OpenAI API tool definition format."""
        if self.name in FULL_FIDELITY_TOOL_SCHEMA_NAMES:
            description = _compact_description(self.description, None)
            parameters = _compact_schema_descriptions(
                copy.deepcopy(self.parameters),
                max_len=None,
            )
        else:
            description = _compact_description(self.description, 150)
            parameters = _compact_schema_descriptions(copy.deepcopy(self.parameters))

        return {
            "type": "function",
            "name": self.name,
            "description": description,
            "parameters": parameters,
            "strict": self.strict,
        }

    def can_use(self, user, chat=None) -> bool:
        """Check if the user/chat can use this tool."""
        if self.permission_check:
            return self.permission_check(user, chat)
        return True


@dataclass
class ToolContext:
    """
    Context passed to tool execution functions.

    Provides access to user, chat, and other relevant data
    without requiring each tool to accept many parameters.
    """

    user: Any  # User model instance
    chat: Any = None  # Chat model instance (optional)
    extra: dict = field(default_factory=dict)  # Additional context if needed


class ToolRegistry:
    """
    Registry of available local function tools.

    Manages tool registration and provides methods to:
    - Get tool configurations for API requests
    - Execute tool calls by name
    - Filter tools by user permissions and enabled categories
    """

    def __init__(self):
        self._tools: dict[str, OttoTool] = {}

    def register(self, tool: OttoTool) -> None:
        """Register a tool in the registry."""
        if tool.name in self._tools:
            logger.warning("Tool already registered, overwriting", tool_name=tool.name)
        self._tools[tool.name] = tool
        logger.debug("Tool registered", tool_name=tool.name, category=tool.category)

    def get(self, name: str) -> Optional[OttoTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_available_tools(
        self,
        user,
        chat=None,
        enabled_categories: list[str] = None,
        unlocked_local_skill_tools: bool = False,
    ) -> list[OttoTool]:
        """Get all tools available to the given user/chat.

        Args:
            user: The user making the request
            chat: The chat context (optional)
            enabled_categories: List of enabled category IDs. If None, all categories enabled.
        """
        tools = []
        for tool in self._tools.values():
            # Check permission
            if not tool.can_use(user, chat):
                continue
            # local_skills tools stay hidden until explicitly unlocked by the
            # skill loader, except for the loader itself.
            if (
                tool.category == TOOL_CATEGORY_SKILLS
                and not unlocked_local_skill_tools
                and tool.name not in LOCAL_SKILLS_ALWAYS_EXPOSED_TOOL_NAMES
            ):
                continue
            # Check category filter
            if (
                enabled_categories is not None
                and tool.category not in enabled_categories
            ):
                continue
            tools.append(tool)
        return tools

    def get_tools_config(
        self,
        user,
        chat=None,
        enabled_categories: list[str] = None,
        unlocked_local_skill_tools: bool = False,
    ) -> list[dict]:
        """
        Get tool configurations for the OpenAI API request.

        Returns list of tool definitions in API format, filtered by permissions
        and enabled categories.

        Args:
            user: The user making the request
            chat: The chat context (optional)
            enabled_categories: List of enabled category IDs. If None, all categories enabled.
        """
        available = self.get_available_tools(
            user,
            chat,
            enabled_categories,
            unlocked_local_skill_tools=unlocked_local_skill_tools,
        )

        def _priority(tool: OttoTool) -> tuple[int, str]:
            # Ensure skill instructions loader is always first when present.
            if tool.name == "load_skill_instructions":
                return (0, tool.name)
            # Keep other local_skills tools ahead of non-skill tools.
            if tool.category == TOOL_CATEGORY_SKILLS:
                return (1, tool.name)
            return (2, tool.name)

        available = sorted(available, key=_priority)
        return [tool.to_api_schema() for tool in available]

    def list_tool_names(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_categories(self) -> set[str]:
        """Get all unique categories from registered tools."""
        return {tool.category for tool in self._tools.values()}


# Global registry instance
TOOL_REGISTRY = ToolRegistry()


# ============================================================================
# Tool Execution
# ============================================================================


async def execute_tool_call(
    tool_name: str,
    arguments: dict | str,
    user,
    chat=None,
    extra_context: dict = None,
) -> dict:
    """
    Execute a tool call and return the result.

    Args:
        tool_name: Name of the tool to execute
        arguments: Arguments from the model (dict or JSON string)
        user: User making the request
        chat: Chat context (optional)
        extra_context: Additional context to pass to the tool

    Returns:
        Dict with 'success' (bool) and either 'result' or 'error'
    """
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        logger.warning("Unknown tool requested", tool_name=tool_name)
        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}",
        }

    # Check permissions
    can_use_tool = await sync_to_async(tool.can_use)(user, chat)
    if not can_use_tool:
        logger.warning(
            "User not permitted to use tool",
            tool_name=tool_name,
            user_id=user.id if user else None,
        )
        return {
            "success": False,
            "error": "You don't have permission to use this tool.",
        }

    # Parse arguments if string
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse tool arguments", error=str(e))
            return {
                "success": False,
                "error": f"Invalid arguments: {e}",
            }

    # Build context
    context = ToolContext(
        user=user,
        chat=chat,
        extra=extra_context or {},
    )

    try:
        result = await tool.execute(arguments, context)
        return {
            "success": True,
            "result": result,
        }
    except Exception as e:
        logger.exception("Tool execution failed", tool_name=tool_name, error=str(e))
        return {
            "success": False,
            "error": str(e),
        }


def build_function_call_output(call_id: str, output: Any) -> dict:
    """
    Build a function_call_output item for the API.

    Args:
        call_id: The call_id from the function_call item
        output: The result to send back. Can be:
            - A string (sent as-is)
            - A dict/list (JSON-encoded)
            - A dict with "_vision_output" key containing vision items to include
              directly in the output array (for view_library_files etc.)

    Returns:
        Dict in function_call_output format. The output field will be either:
        - A JSON string (standard case)
        - An array of content items including input_image/input_file (vision case)
    """
    # Check if this is a vision output with embedded content items
    if isinstance(output, dict) and "_vision_output" in output:
        vision_items = output.pop("_vision_output", [])
        # Build array output: text result + vision items
        output_array = [{"type": "input_text", "text": json.dumps(output, default=str)}]
        output_array.extend(vision_items)
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output_array,
        }

    # Standard case: JSON string output
    if isinstance(output, str):
        output_str = output
    else:
        output_str = json.dumps(output, default=str)

    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": output_str,
    }
