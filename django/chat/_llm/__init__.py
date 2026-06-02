"""
LLM module for Otto chat application.

This module provides:
- Core LLM wrapper (OttoLLM)
- Token counting and callbacks
- OpenAI/LlamaIndex wrappers
- Vector store integration
- Custom retrievers
"""

from .callbacks import ModelEventHandler, OttoTokenCountingHandler
from .core import OttoLLM, mock_llm_context
from .models import (
    ALL_MODELS,
    DEFAULT_CHAT_MODEL_ID,
    DEFAULT_LAWS_MODEL_ID,
    DEFAULT_QA_MODEL_ID,
    DEFAULT_SUMMARIZE_MODEL_ID,
    DEFAULT_TRANSLATE_MODEL_ID,
    LLM,
    MODELS_BY_ID,
    ModelProvider,
    get_chat_model_choices,
    get_grouped_chat_model_choices,
    get_model,
    get_updated_model_id,
)
from .retrievers import OttoFusionRetriever
from .utils import (
    _extract_status_and_retry_after,
    chat_history_to_prompt,
    retry_with_backoff,
)
from .vector_store import OttoVectorStore, get_pg_engines

__all__ = [
    "OttoLLM",
    "mock_llm_context",
    "OttoTokenCountingHandler",
    "ModelEventHandler",
    "OttoVectorStore",
    "get_pg_engines",
    "OttoFusionRetriever",
    "retry_with_backoff",
    "chat_history_to_prompt",
    "_extract_status_and_retry_after",
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
]
