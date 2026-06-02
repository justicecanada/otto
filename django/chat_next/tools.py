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

# Re-export core infrastructure
# Import all sub-modules to trigger tool registration
from chat_next._tools import (  # noqa: F401
    document_processing,
    legal_research,
    preset_migration,
    qa_libraries,
    skills,
    terminology,
    transcription,
    translation,
    url_retrieval,
)
from chat_next._tools.base import (  # noqa: F401
    TOOL_REGISTRY,
    OttoTool,
    ToolContext,
    ToolRegistry,
    build_function_call_output,
    execute_tool_call,
)
from chat_next._tools.document_processing import (  # noqa: F401
    plan_document_chunks,
    prompt_document_chunks,
    prompt_document_ranges,
    prompt_documents,
)
from chat_next._tools.legal_research import (  # noqa: F401
    fetch_canadian_case_by_citation,
    fetch_canadian_legislation_by_citation,
    list_canadian_legal_datasets,
    search_canadian_case_law,
    search_canadian_legislation,
    search_laws,
)

# Re-export tool functions for backward compatibility
from chat_next._tools.qa_libraries import (  # noqa: F401
    find_in_document,
    get_document_text,
    list_documents,
    list_folders,
    list_libraries,
    load_library_files,
    rag_search,
    view_library_files,
)
from chat_next._tools.terminology import termium_lookup  # noqa: F401
from chat_next._tools.transcription import transcribe_files  # noqa: F401
from chat_next._tools.translation import translate_files  # noqa: F401

# Re-export helper utilities
from chat_next._tools.utils import (  # noqa: F401
    get_enabled_local_categories,
    get_local_tool_names,
    has_local_tools_enabled,
    is_local_tool_category,
)
