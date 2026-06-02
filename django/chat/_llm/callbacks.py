"""Token counting and callback handlers for LLM operations."""

from typing import Optional

from llama_index.core.callbacks.base_handler import BaseCallbackHandler
from llama_index.core.callbacks.schema import CBEventType, EventPayload
from llama_index.core.instrumentation.event_handlers import BaseEventHandler
from llama_index.core.instrumentation.events.embedding import EmbeddingEndEvent
from llama_index.core.instrumentation.events.llm import (
    LLMChatEndEvent,
    LLMChatStartEvent,
    LLMCompletionEndEvent,
)
from structlog import get_logger

logger = get_logger(__name__)


class ModelEventHandler(BaseEventHandler):
    @classmethod
    def class_name(cls) -> str:
        """Class name."""
        return "ModelEventHandler"

    def handle(self, event) -> None:
        """Logic for handling event."""
        if isinstance(event, LLMCompletionEndEvent):
            print(f"LLM Prompt length: {len(event.prompt)}")
            print(f"LLM Completion: {str(event.response.text)}")
        elif isinstance(event, LLMChatEndEvent):
            messages_str = "\n".join([str(x) for x in event.messages])
            print(f"LLM Input Messages length: {len(messages_str)}")
            print(f"LLM Response: {str(event.response.message)}")
        elif isinstance(event, LLMChatStartEvent):
            print(event.dict())
        elif isinstance(event, EmbeddingEndEvent):
            print(f"Embedding {len(event.chunks)} text chunks")


class OttoTokenCountingHandler(BaseCallbackHandler):
    """
    Custom token counting handler that extracts actual token usage from raw API responses.
    This provides more accurate cost tracking by capturing:

    For LLM calls:
    - Actual prompt tokens (may differ from tiktoken estimates)
    - Actual completion tokens (may differ from tiktoken estimates)
    - Reasoning tokens (for reasoning models like o3, o4, gpt-5)
    - Cached tokens (prompt tokens that were cached, reducing cost)

    For Embedding calls:
    - Actual embedding input tokens from API response when available
    - Falls back to tiktoken estimation only when raw response unavailable
    """

    def __init__(
        self, tokenizer=None, event_starts_to_ignore=None, event_ends_to_ignore=None
    ):
        super().__init__(event_starts_to_ignore or [], event_ends_to_ignore or [])
        self.tokenizer = tokenizer

        # Actual token counts from API responses
        self.prompt_llm_token_count = 0
        self.completion_llm_token_count = 0
        self.reasoning_token_count = 0
        self.cached_token_count = 0

        # Embedding token counts
        self.total_embedding_token_count = 0
        self.last_embedding_token_count = 0

    def on_event_start(
        self,
        event_type: CBEventType,
        payload: dict | None = None,
        event_id: str = "",
        parent_id: str = "",
        **kwargs,
    ) -> str:
        """Run when an event starts."""
        return event_id

    def on_event_end(
        self,
        event_type: CBEventType,
        payload: dict | None = None,
        event_id: str = "",
        **kwargs,
    ) -> None:
        """Extract token counts from event payload."""
        if payload is None:
            return

        # Handle LLM events (chat/completion callbacks)
        if event_type == CBEventType.LLM:
            # Try different payload keys - LlamaIndex uses different keys for different call types
            response = payload.get(EventPayload.RESPONSE) or payload.get(
                EventPayload.COMPLETION
            )

            if response and hasattr(response, "raw") and response.raw:
                self._extract_tokens_from_raw_response(response.raw)

        # Handle embedding events
        elif event_type == CBEventType.EMBEDDING:
            # Try to extract token count from raw API response first
            token_count = self._extract_embedding_tokens_from_payload(payload)

            # Fall back to tiktoken estimation only if raw response unavailable
            if token_count is None:
                chunks = payload.get(EventPayload.CHUNKS)
                if chunks and self.tokenizer:
                    token_count = sum(len(self.tokenizer(chunk)) for chunk in chunks)

            if token_count is not None:
                self.last_embedding_token_count = token_count
                self.total_embedding_token_count += token_count

    def _get_val(self, obj, key, default=0):
        """Helper to safely get value from object or dict."""
        if hasattr(obj, key):
            return getattr(obj, key) or default
        elif isinstance(obj, dict):
            return obj.get(key, default)
        return default

    def _extract_tokens_from_raw_response(self, raw_response):
        """
        Extract token usage information from the raw OpenAI/Azure OpenAI response.

        The raw response structure for both Chat Completions and Responses API includes:
        - usage.prompt_tokens: Total prompt tokens (also called input_tokens in Responses API)
        - usage.completion_tokens: Output tokens (also called output_tokens in Responses API)
        - usage.total_tokens: Sum of prompt + completion
        - usage.prompt_tokens_details.cached_tokens: Cached prompt tokens (if any)
        - usage.completion_tokens_details.reasoning_tokens: Reasoning tokens (for reasoning models)
        - usage.output_tokens_details.reasoning_tokens: Reasoning tokens (Responses API format)

        This handles both API formats automatically.
        """
        try:
            usage = self._get_val(raw_response, "usage", None)

            if not usage:
                logger.debug("No usage information found in raw response")
                return

            # Extract basic token counts (works for both Chat Completions and Responses API)
            # Responses API uses input_tokens/output_tokens, but also provides prompt_tokens/completion_tokens for compatibility
            self.prompt_llm_token_count += self._get_val(
                usage, "prompt_tokens"
            ) or self._get_val(usage, "input_tokens")
            self.completion_llm_token_count += self._get_val(
                usage, "completion_tokens"
            ) or self._get_val(usage, "output_tokens")

            # Extract cached tokens (reduces cost for cached prompts)
            cached_tokens = 0
            details = self._get_val(
                usage, "prompt_tokens_details", None
            ) or self._get_val(usage, "input_tokens_details", None)
            if details:
                cached_tokens = self._get_val(details, "cached_tokens")
            self.cached_token_count += cached_tokens

            # Extract reasoning tokens (additional visibility for reasoning models)
            # Check both completion_tokens_details (Chat Completions) and output_tokens_details (Responses API)
            reasoning_tokens = 0
            details = self._get_val(
                usage, "completion_tokens_details", None
            ) or self._get_val(usage, "output_tokens_details", None)
            if details:
                reasoning_tokens = self._get_val(details, "reasoning_tokens")
            self.reasoning_token_count += reasoning_tokens

            logger.debug(
                "Token usage extracted from raw response",
                prompt_tokens=self.prompt_llm_token_count,
                completion_tokens=self.completion_llm_token_count,
                reasoning_tokens=self.reasoning_token_count,
                cached_tokens=self.cached_token_count,
            )

        except Exception as e:
            logger.warning(f"Failed to extract token usage from raw response: {e}")

    def _extract_embedding_tokens_from_payload(self, payload: dict) -> Optional[int]:
        """Extract token count from embedding API response if available.

        OpenAI/Azure embedding responses include usage information:
        - usage.prompt_tokens: Number of tokens in the input
        - usage.total_tokens: Same as prompt_tokens for embeddings

        Returns the token count if available, None otherwise.
        """
        try:
            # Check if there's additional kwargs that might contain raw response
            # LlamaIndex may store this differently than LLM responses
            for key in ["response", "raw", "result"]:
                if key in payload:
                    response = payload[key]
                    usage = self._get_val(response, "usage", None)

                    if usage:
                        # Extract prompt_tokens (for embeddings, this is the input token count)
                        tokens = self._get_val(usage, "prompt_tokens", None)
                        if tokens is not None:
                            logger.debug(
                                "Embedding token count extracted from raw response",
                                tokens=tokens,
                            )
                            return tokens

            return None
        except Exception as e:
            logger.debug(f"Could not extract embedding tokens from payload: {e}")
            return None

    def reset_counts(self) -> None:
        """Reset all token counters."""
        self.prompt_llm_token_count = 0
        self.completion_llm_token_count = 0
        self.reasoning_token_count = 0
        self.cached_token_count = 0
        self.total_embedding_token_count = 0
        self.last_embedding_token_count = 0

    def start_trace(self, trace_id: str | None = None) -> None:
        """Run when an overall trace is launched."""
        pass

    def end_trace(
        self,
        trace_id: str | None = None,
        trace_map: dict | None = None,
    ) -> None:
        """Run when an overall trace is exited."""
        pass
