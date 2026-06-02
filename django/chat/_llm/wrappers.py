"""OpenAI/LlamaIndex wrappers for Otto."""

from typing import Any, Optional, Sequence

from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    ChatResponseAsyncGen,
    LLMMetadata,
    MessageRole,
    ThinkingBlock,
)
from llama_index.llms.openai import OpenAI, OpenAIResponses
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningSummaryTextDoneEvent,
)


class OttoResponsesWrapper(OpenAIResponses):
    """Wrapper for OpenAI Responses API (reasoning models).

    Adds support for:
    - gpt-5 text options (e.g. verbosity)
    - incremental streaming of reasoning summary events
    - token counting from ResponseCompletedEvent
    """

    def __init__(
        self, *args, priority=None, token_counter=None, text_options=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        # priority is currently unused; kept for compatibility with existing call sites
        self._priority = priority
        self._token_counter = token_counter
        self._text_options = text_options

    def _get_model_kwargs(self, **kwargs):
        """Override to inject text options and remove unsupported params for Responses API.

        Note: Do not include Azure-specific params like 'priority' in the request body,
        as the OpenAI v1 GA API rejects unknown parameters.
        """
        model_kwargs = super()._get_model_kwargs(**kwargs)

        # Add text options (e.g., verbosity) for gpt-5 models
        if self._text_options:
            model_kwargs["text"] = self._text_options

        # Remove temperature/top_p - Responses API doesn't support them for reasoning models
        model_kwargs.pop("temperature", None)
        model_kwargs.pop("top_p", None)

        # Ensure extra_body exists but do not add unsupported fields
        model_kwargs.setdefault("extra_body", {})

        return model_kwargs

    def _build_reasoning_steps(
        self,
        reasoning_summaries: dict[int, str],
        complete_step_indices: set[int],
    ) -> list[dict]:
        """Build list of reasoning steps in order, marking complete status."""
        return [
            {"index": idx, "text": text, "complete": idx in complete_step_indices}
            for idx, text in sorted(reasoning_summaries.items())
        ]

    def _create_reasoning_response(
        self,
        event_type: str,
        reasoning_steps: list[dict],
        delta: str,
        raw_event,
        additional_kwargs: dict,
    ) -> ChatResponse:
        """Create a ChatResponse with reasoning steps as structured data."""
        return ChatResponse(
            message=ChatMessage(
                role=MessageRole.ASSISTANT,
                blocks=[
                    ThinkingBlock(
                        content="",  # Must be string, not list
                        additional_information={
                            "type": event_type,
                            "reasoning_steps": reasoning_steps,
                        },
                    )
                ],
            ),
            delta=delta,
            raw=raw_event,
            additional_kwargs=additional_kwargs,
        )

    async def _astream_chat(
        self, messages: Sequence[ChatMessage], **kwargs: Any
    ) -> ChatResponseAsyncGen:
        """
        Override to handle reasoning summary events incrementally.

        This allows us to stream reasoning summaries as they arrive, rather than
        waiting for the entire reasoning phase to complete.
        """
        from llama_index.llms.openai.utils import to_openai_message_dicts

        message_dicts = to_openai_message_dicts(
            messages,
            model=self.model,
            is_responses_api=True,
        )

        async def gen() -> ChatResponseAsyncGen:
            built_in_tool_calls = []
            additional_kwargs = {"built_in_tool_calls": []}
            current_tool_call: Optional[ResponseFunctionToolCall] = None
            local_previous_response_id = self._previous_response_id
            # Track reasoning summaries by summary_index (keeping them separate)
            reasoning_summaries: dict[int, str] = {}
            # Track which reasoning steps are complete (received done event)
            complete_step_indices: set[int] = set()

            response_stream = await self._aclient.responses.create(
                input=message_dicts,
                stream=True,
                **self._get_model_kwargs(**kwargs),
            )

            async for event in response_stream:
                # Handle reasoning summary delta events (incremental streaming)
                if isinstance(event, ResponseReasoningSummaryTextDeltaEvent):
                    summary_idx = event.summary_index
                    # Accumulate delta for this specific reasoning step
                    reasoning_summaries[summary_idx] = (
                        reasoning_summaries.get(summary_idx, "") + event.delta
                    )
                    reasoning_steps = self._build_reasoning_steps(
                        reasoning_summaries, complete_step_indices
                    )
                    yield self._create_reasoning_response(
                        "reasoning_summary_delta",
                        reasoning_steps,
                        event.delta,
                        event,
                        additional_kwargs,
                    )
                    continue

                # Handle reasoning summary done event
                elif isinstance(event, ResponseReasoningSummaryTextDoneEvent):
                    summary_idx = event.summary_index
                    # Set the final text for this reasoning step and mark as complete
                    reasoning_summaries[summary_idx] = event.text
                    complete_step_indices.add(summary_idx)
                    reasoning_steps = self._build_reasoning_steps(
                        reasoning_summaries, complete_step_indices
                    )
                    yield self._create_reasoning_response(
                        "reasoning_summary_done",
                        reasoning_steps,
                        "",
                        event,
                        additional_kwargs,
                    )
                    continue

                # For all other events, use the parent class's processing logic
                (
                    blocks,
                    built_in_tool_calls,
                    additional_kwargs,
                    current_tool_call,
                    local_previous_response_id,
                    delta,
                ) = OpenAIResponses.process_response_event(
                    event=event,
                    built_in_tool_calls=built_in_tool_calls,
                    additional_kwargs=additional_kwargs,
                    current_tool_call=current_tool_call,
                    track_previous_responses=self.track_previous_responses,
                    previous_response_id=local_previous_response_id,
                )

                if (
                    self.track_previous_responses
                    and local_previous_response_id != self._previous_response_id
                ):
                    self._previous_response_id = local_previous_response_id

                if built_in_tool_calls:
                    additional_kwargs["built_in_tool_calls"] = built_in_tool_calls

                # For ResponseCompletedEvent, extract usage info but don't yield the blocks
                # (they've already been streamed incrementally)
                if isinstance(event, ResponseCompletedEvent):
                    # Extract usage from the completed response
                    if self._token_counter and hasattr(event, "response"):
                        self._token_counter._extract_tokens_from_raw_response(
                            event.response
                        )
                    continue

                # Yield a ChatResponse with the current state
                yield ChatResponse(
                    message=ChatMessage(
                        role=MessageRole.ASSISTANT,
                        blocks=blocks,
                    ),
                    delta=delta,
                    raw=event,
                    additional_kwargs=additional_kwargs,
                )

        return gen()


class OttoCompletionsWrapper(OpenAI):
    """Wrapper for Chat Completions API.

    Ensures streaming responses include usage so token counting works reliably.
    """

    def __init__(self, *args, priority=None, **kwargs):
        super().__init__(*args, **kwargs)
        # priority is currently unused; kept for compatibility with existing call sites
        self._priority = priority

    def _prepare_kwargs(self, kwargs, streaming=False):
        """Inject stream options into kwargs.

        Do not include Azure-specific params like 'priority' in the request body,
        as the OpenAI v1 GA API rejects unknown parameters.
        """
        kwargs.setdefault("extra_body", {})
        if streaming:
            # Request usage information in the last chunk of streaming responses
            kwargs.setdefault("stream_options", {"include_usage": True})

    def complete(self, prompt, **kwargs):
        self._prepare_kwargs(kwargs)
        return super().complete(prompt, **kwargs)

    async def astream_complete(self, prompt, **kwargs):
        self._prepare_kwargs(kwargs, streaming=True)
        return await super().astream_complete(prompt, **kwargs)

    def chat(self, chat_history, **kwargs):
        self._prepare_kwargs(kwargs)
        return super().chat(chat_history, **kwargs)

    async def astream_chat(self, chat_history, **kwargs):
        self._prepare_kwargs(kwargs, streaming=True)
        return await super().astream_chat(chat_history, **kwargs)


class OttoAltCompletionsWrapper(OttoCompletionsWrapper):
    """Chat Completions wrapper for OpenAI-compatible non-OpenAI models.

    Models like Cohere and GPT-OSS are hosted on Azure AI Services with an
    OpenAI-compatible endpoint. LlamaIndex's OpenAI class validates model names
    against a hardcoded list, so we override the metadata property to skip that.
    """

    def __init__(self, *args, context_window: int = 128000, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_window = context_window

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=self._context_window,
            num_output=self.max_tokens or 4096,
            is_chat_model=True,
            is_function_calling_model=False,
            model_name=self.model,
        )
