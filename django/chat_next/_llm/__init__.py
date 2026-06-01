"""
LLM module for Otto chat application.

This module provides:
- Direct OpenAI Responses API client (ResponsesAPIClient) - primary interface
- Helper functions for building conversation input from database
- Model configuration and selection
"""

# Import constants directly to avoid circular imports
from .code_interpreter import (
    download_code_interpreter_images,
    download_container_files,
    download_sandbox_files,
    extract_code_interpreter_outputs,
    extract_container_id,
    extract_file_citations,
    extract_unique_container_ids,
    replace_sandbox_urls,
)
from .constants import (
    CODE_INTERPRETER_SUPPORTED_EXTENSIONS,
    CONTEXT_STUFFING_EXTENSIONS,
    TOOL_COST_TYPES,
    is_code_interpreter_supported,
)
from .models import (
    ALL_MODELS,
    COMPACTION_MIN_TOKENS,
    COMPACTION_THRESHOLD_TOKENS_BY_MODEL_ID,
    DEFAULT_CHAT_MODEL_ID,
    DEFAULT_LAWS_MODEL_ID,
    DEFAULT_QA_MODEL_ID,
    DEFAULT_SUMMARIZE_MODEL_ID,
    DEFAULT_TRANSLATE_MODEL_ID,
    LLM,
    MODELS_BY_ID,
    ModelProvider,
    get_chat_model_choices,
    get_compaction_threshold_percentage,
    get_compaction_threshold_tokens,
    get_context_usage_display,
    get_grouped_chat_model_choices,
    get_model,
    get_updated_model_id,
    should_compact_context,
)
from .openai_responses import (
    COMPACTION_PROCESSING_STEP,
    ResponsesAPIClient,
    StreamChunk,
    TokenUsage,
    ToolCall,
    _sanitize_function_call_output_for_storage,  # noqa
    bot_message_from_db,
    build_conversation_input,
    build_file_upload_input,
    build_system_prompt,
    check_tool_cost_warning,
    create_compaction_costs,
    create_tool_costs,
    estimate_tool_call_costs,
    estimate_tool_continuation_usage,
    extract_output_items,
    get_chat_loaded_skill_state,
    get_context_management_mode,
    merge_loaded_skill_states,
    persist_chat_loaded_skill_state,
    process_file_upload,
    restore_chat_loaded_skill_state,
    should_proactively_compact_tool_continuation,
    stream_chat_for_htmx,
    upload_chat_files_to_openai,
    user_message,
    user_message_from_db,
)

__all__ = [
    # Primary Responses API interface
    "ResponsesAPIClient",
    "StreamChunk",
    "TokenUsage",
    "ToolCall",
    "build_conversation_input",
    "build_system_prompt",
    "stream_chat_for_htmx",
    "COMPACTION_PROCESSING_STEP",
    "get_context_management_mode",
    "user_message",
    "user_message_from_db",
    "bot_message_from_db",
    "extract_output_items",
    "create_compaction_costs",
    "create_tool_costs",
    "estimate_tool_continuation_usage",
    "estimate_tool_call_costs",
    "check_tool_cost_warning",
    "should_proactively_compact_tool_continuation",
    "get_chat_loaded_skill_state",
    "merge_loaded_skill_states",
    "persist_chat_loaded_skill_state",
    "restore_chat_loaded_skill_state",
    # File upload handling
    "upload_chat_files_to_openai",
    "build_file_upload_input",
    "process_file_upload",
    # Code Interpreter utilities
    "extract_file_citations",
    "extract_code_interpreter_outputs",
    "extract_container_id",
    "extract_unique_container_ids",
    "download_container_files",
    "download_sandbox_files",
    "replace_sandbox_urls",
    "download_code_interpreter_images",
    # Constants
    "CODE_INTERPRETER_SUPPORTED_EXTENSIONS",
    "CONTEXT_STUFFING_EXTENSIONS",
    "TOOL_COST_TYPES",
    "is_code_interpreter_supported",
    # Model configuration
    "LLM",
    "ModelProvider",
    "ALL_MODELS",
    "MODELS_BY_ID",
    "get_model",
    "get_chat_model_choices",
    "get_grouped_chat_model_choices",
    "get_updated_model_id",
    "DEFAULT_CHAT_MODEL_ID",
    "DEFAULT_QA_MODEL_ID",
    "DEFAULT_SUMMARIZE_MODEL_ID",
    "DEFAULT_TRANSLATE_MODEL_ID",
    "DEFAULT_LAWS_MODEL_ID",
    # Compaction support
    "COMPACTION_THRESHOLD_TOKENS_BY_MODEL_ID",
    "COMPACTION_MIN_TOKENS",
    "get_compaction_threshold_tokens",
    "get_compaction_threshold_percentage",
    "should_compact_context",
    "get_context_usage_display",
]
