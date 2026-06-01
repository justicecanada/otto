"""Core OttoLLM wrapper class."""

import uuid
from contextvars import ContextVar

from django.conf import settings
from django.utils.translation import gettext_lazy as _

import tiktoken
from llama_index.core import PromptTemplate, VectorStoreIndex
from llama_index.core.callbacks import CallbackManager
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.indices.prompt_helper import PromptHelper
from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.llms import MockLLM
from llama_index.core.response_synthesizers import CompactAndRefine, TreeSummarize
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.vector_stores.types import MetadataFilters
from structlog import get_logger

from otto import priorities

from .callbacks import ModelEventHandler, OttoTokenCountingHandler
from .models import ModelProvider, get_model, normalize_reasoning_effort
from .retrievers import OttoFusionRetriever
from .utils import chat_history_to_prompt
from .vector_store import OttoVectorStore, _get_connection_params
from .wrappers import (
    OttoAltCompletionsWrapper,
    OttoCompletionsWrapper,
    OttoResponsesWrapper,
)

logger = get_logger(__name__)

debug = settings.DEBUG

# Context variable for load testing - when set to True, forces use of Mock LLM and Mock Embedding
mock_llm_context: ContextVar[bool] = ContextVar("mock_llm_context", default=False)


if settings.DEBUG:
    root_dispatcher = get_dispatcher()
    root_dispatcher.add_event_handler(ModelEventHandler())


class OttoLLM:
    """
    Wrapper around LlamaIndex to assist with cost tracking and reduce boilerplate.
    "model" must match the name of the LLM deployment in Azure.
    """

    def __init__(
        self,
        deployment: str = None,
        temperature: float = 0.5,
        mock_embedding: bool = False,
        reasoning_effort: str = "minimal",
        verbosity: str = None,
        priority: int = None,
    ):
        # Use default values from settings (resolved at runtime to avoid circular import)
        if deployment is None:
            deployment = settings.DEFAULT_CHAT_MODEL
        if priority is None:
            priority = settings.DEFAULT_LLM_PRIORITY

        # Check if mock_llm is enabled via contextvar (for load testing)
        self.use_mock_llm = mock_llm_context.get(False)

        reasoning_effort = normalize_reasoning_effort(deployment, reasoning_effort)

        self.llm_config = get_model(deployment)
        if not self.llm_config:
            raise ValueError(f"Invalid deployment: {deployment}")

        self.deployment = self.llm_config.deployment_name
        self.model = self.llm_config.deployment_name
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        # Verbosity is only used for gpt-5 models
        self.verbosity = verbosity if deployment.startswith("gpt-5") else None

        # Set timeout based on reasoning effort for reasoning models
        # Reasoning models need more time, especially at high reasoning effort
        if self.llm_config.reasoning:
            if reasoning_effort == "xhigh":
                self.timeout = 420.0  # 7 minutes for extra-high reasoning
            elif reasoning_effort == "high":
                self.timeout = 300.0  # 5 minutes for high reasoning
            elif reasoning_effort == "medium":
                self.timeout = 180.0  # 3 minutes for medium reasoning
            else:  # low or minimal
                self.timeout = 120.0  # 2 minutes for low/minimal reasoning
        else:
            self.timeout = 90.0  # Default 90 seconds for non-reasoning models

        # Use OttoTokenCountingHandler for accurate token counting from raw API responses
        # o200k_base tokenizer is used as fallback for embedding token estimation
        self._token_counter = OttoTokenCountingHandler(
            tokenizer=tiktoken.get_encoding("o200k_base").encode
        )
        self._callback_manager = CallbackManager([self._token_counter])

        self._embed_token_counter = OttoTokenCountingHandler(
            tokenizer=tiktoken.get_encoding("cl100k_base").encode
        )
        self._embed_callback_manager = CallbackManager([self._embed_token_counter])

        self.priority = priority
        self.max_input_tokens = self.llm_config.max_tokens_in
        self.max_output_tokens = self.llm_config.max_tokens_out
        self.llm = self._get_llm()
        # If mock_llm context is set, always use mock embedding regardless of mock_embedding parameter
        self.mock_embedding = mock_embedding or self.use_mock_llm
        self.embed_model = self._get_embed_model()

    # Convenience methods to interact with LLM
    # Each will return a complete response (not single tokens)
    async def chat_stream(self, chat_history: list):
        """
        Stream complete response from list of chat history objects.

        For reasoning models using Responses API, yields dictionaries with:
        - 'text': accumulated text content
        - 'thinking': thinking/reasoning content (if available)
        - 'raw_chunk': the raw chunk object for token counting

        For regular models, yields text strings directly.
        """
        if not self.llm_config.supports_chat_history:
            prompt = chat_history_to_prompt(chat_history)
            async for chunk in self.stream(prompt):
                yield chunk
            return

        # Prepend/append system prompt prefix/suffix if they exist to the first system message
        if self.llm_config.system_prompt_prefix or self.llm_config.system_prompt_suffix:
            for message in chat_history:
                if message.role == "system":
                    message.content = f"{self.llm_config.system_prompt_prefix}{message.content}{self.llm_config.system_prompt_suffix}"
                    break

        response_stream = await self.llm.astream_chat(chat_history)

        # Import here to avoid circular dependency issues
        from llama_index.core.base.llms.types import TextBlock, ThinkingBlock

        # For Responses API (reasoning models), handle blocks
        if self.llm_config.reasoning and isinstance(self.llm, OttoResponsesWrapper):
            accumulated_text = ""
            reasoning_steps = []  # List of reasoning step dicts
            chunk_count = 0

            async for chunk in response_stream:
                chunk_count += 1
                previous_steps_count = len(reasoning_steps)

                # Extract text and thinking blocks from the response
                for block in chunk.message.blocks:
                    if isinstance(block, TextBlock):
                        accumulated_text += block.text
                    elif isinstance(block, ThinkingBlock):
                        # Extract reasoning_steps from additional_information
                        if (
                            block.additional_information
                            and "reasoning_steps" in block.additional_information
                        ):
                            reasoning_steps = block.additional_information[
                                "reasoning_steps"
                            ]

                # Log if reasoning steps are streaming incrementally
                if reasoning_steps and len(reasoning_steps) != previous_steps_count:
                    logger.debug(
                        "Reasoning steps updated",
                        previous_count=previous_steps_count,
                        new_count=len(reasoning_steps),
                        chunk_number=chunk_count,
                    )

                # Yield structured response with text and reasoning steps
                # Include chunk_count so we can detect reasoning phase (chunks but no text yet)
                yield {
                    "text": accumulated_text,
                    "reasoning_steps": reasoning_steps,  # List of step dicts
                    "raw_chunk": chunk,
                    "is_reasoning": chunk_count > 0 and not accumulated_text,
                }
        else:
            # For regular Chat Completions API, yield text directly
            async for chunk in response_stream:
                yield chunk.message.content

    async def stream(self, prompt: str):
        response_stream = await self.llm.astream_complete(prompt)
        async for chunk in response_stream:
            yield chunk.text

    def complete(self, prompt: str):
        """
        Return complete response string from single prompt string (no streaming)
        """
        return self.llm.complete(prompt).text

    async def acomplete(self, prompt: str):
        """Async version of complete().

        Uses the underlying LLM's async completion interface to avoid running a sync
        wrapper inside an existing event loop (which triggers the nested async error
        in LlamaIndex). Returns the response text string.
        """
        response = await self.llm.acomplete(prompt)
        return response.text

    def chat_complete(self, chat_history: list):
        """
        Return complete response string from list of chat history objects (no streaming)
        """
        if not self.llm_config.supports_chat_history:
            prompt = chat_history_to_prompt(chat_history)
            return self.complete(prompt)

        # Prepend system prompt prefix if it exists
        if self.llm_config.system_prompt_prefix:
            for message in chat_history:
                if message.role == "system":
                    message.content = (
                        f"{self.llm_config.system_prompt_prefix}\n{message.content}"
                    )
                    break

        return self.llm.chat(chat_history).message.content

    async def achat_complete(self, chat_history: list):
        """Async version of chat_complete().

        Mirrors chat_complete but uses async LLM methods. Falls back to acomplete
        when the deployment does not support native chat history.
        """
        if not self.llm_config.supports_chat_history:
            prompt = chat_history_to_prompt(chat_history)
            return await self.acomplete(prompt)

        # Prepend system prompt prefix if it exists
        if self.llm_config.system_prompt_prefix:
            for message in chat_history:
                if message.role == "system":
                    message.content = (
                        f"{self.llm_config.system_prompt_prefix}\n{message.content}"
                    )
                    break
        response = await self.llm.achat(chat_history)
        return response.message.content

    async def tree_summarize(
        self,
        context: str,
        query: str = "summarize the text",
        template: PromptTemplate = None,
        chunk_size_limit: int | None = None,
        chunk_overlap_ratio: float = 0.1,
    ):
        """
        Stream complete response (not single tokens) from context string and query.
        Optional: summary template (must include "{context_str}" and "{query_str}".)
        """
        try:
            custom_prompt_helper = PromptHelper(
                context_window=self.max_input_tokens + self.max_output_tokens,
                num_output=self.max_output_tokens,
                chunk_size_limit=chunk_size_limit,
                chunk_overlap_ratio=chunk_overlap_ratio,
            )
            response = await self._get_tree_summarizer(
                prompt_helper=custom_prompt_helper, summary_template=template
            ).aget_response(query, [context])
            response_text = ""
            async for chunk in response:
                response_text += chunk
                yield response_text
        except Exception as e:
            error_id = str(uuid.uuid4())[:7]
            logger.exception(f"Error in tree_summarize: {e}", error_id=error_id)
            # Translatable string extracted for xgettext compatibility
            error_id_label = _("Error ID:")
            yield (
                _("An error occurred while summarizing the text.")
                + f" _({error_id_label} {error_id})_"
            )

    # Token counting / cost tracking
    @property
    def input_token_count(self):
        return self._token_counter.prompt_llm_token_count

    @property
    def output_token_count(self):
        return self._token_counter.completion_llm_token_count

    @property
    def reasoning_token_count(self):
        return self._token_counter.reasoning_token_count

    @property
    def cached_token_count(self):
        return self._token_counter.cached_token_count

    @property
    def embed_token_count(self):
        return self._embed_token_counter.total_embedding_token_count

    def create_costs(self) -> None:
        """
        Create Otto Cost objects for the given user and feature.

        For reasoning models, reasoning tokens are already included in completion_tokens.
        Cached tokens are charged at 10% of the normal input token rate (handled by separate cost type).
        """
        # Import here to avoid circular import (otto.settings imports from chat._llm.models)
        from otto.models import Cost

        usd_cost = 0

        # Regular input tokens (not from cache)
        regular_input_tokens = self.input_token_count - self.cached_token_count
        if regular_input_tokens > 0:
            c1 = Cost.objects.new(
                cost_type=f"{self.llm_config.model_id}-in",
                count=regular_input_tokens,
            )
            usd_cost += c1.usd_cost

        # Cached input tokens (charged at 10% of regular rate)
        if self.cached_token_count > 0:
            c_cached = Cost.objects.new(
                cost_type=f"{self.llm_config.model_id}-in-cached",
                count=self.cached_token_count,
            )
            usd_cost += c_cached.usd_cost

        # Output tokens (standard completion tokens)
        if self.output_token_count > 0:
            c2 = Cost.objects.new(
                cost_type=f"{self.llm_config.model_id}-out",
                count=self.output_token_count,
            )
            usd_cost += c2.usd_cost

        # Reasoning tokens (billed at output rate for reasoning models)
        # These are separate from regular completion tokens
        if self.reasoning_token_count > 0:
            # Reasoning tokens are already included in completion_tokens for billing purposes
            # But we track them separately for visibility if needed
            pass

        # Embedding tokens
        if self.embed_token_count > 0 and not self.mock_embedding:
            c4 = Cost.objects.new(cost_type="embedding", count=self.embed_token_count)
            usd_cost += c4.usd_cost

        # Log token usage for debugging
        if self.cached_token_count > 0 or self.reasoning_token_count > 0:
            logger.info(
                "Token usage details",
                model=self.llm_config.model_id,
                input_tokens=self.input_token_count,
                cached_tokens=self.cached_token_count,
                regular_input_tokens=regular_input_tokens,
                output_tokens=self.output_token_count,
                reasoning_tokens=self.reasoning_token_count,
            )

        self._token_counter.reset_counts()
        self._embed_token_counter.reset_counts()
        return usd_cost

    def get_fast_vector_retriever(
        self,
        vector_store_table: str,
        filters: MetadataFilters = None,
        top_k: int = 5,
        hnsw: bool = False,
    ):
        pg_idx = self.get_index(vector_store_table, hnsw=hnsw, skip_setup=True)

        return pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=top_k,
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 512} if hnsw else {},
        )

    def get_fast_text_retriever(
        self,
        vector_store_table: str,
        filters: MetadataFilters = None,
        top_k: int = 5,
    ):
        pg_idx = self.get_index(vector_store_table, hnsw=False, skip_setup=True)

        text_retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=top_k,
            filters=filters,
        )

        # Disable embedding to make it text-only
        text_retriever._vector_store.is_embedding_query = False

        return text_retriever

    def get_retriever(
        self,
        vector_store_table: str,
        filters: MetadataFilters = None,
        top_k: int = 5,
        vector_weight: float = 0.6,
        hnsw: bool = False,
    ) -> BaseRetriever:
        """Return a hybrid (vector + sparse) retriever.

        Always uses OttoFusionRetriever (thread-parallel fusion) when vector_weight is
        between 0 and 1. This yields low latency without risking nested event loop
        issues that arose with the upstream QueryFusionRetriever(use_async=True).

        Rationale for always-on fusion:
        - Simplicity of API (no feature flags to forget)
        - Deterministic performance characteristics
        - Thread pool concurrency is sufficient for typical 2-retriever hybrid
            (vector + sparse) scenario; avoids complexity of orchestrating coroutines
            within potentially async calling contexts (e.g. streaming generators).
        - If future scaling requires more retrievers or true async across process
            boundaries, we can reintroduce an async path with aretrieve() calls.
        """
        if vector_weight == 0:
            # If vector_weight is 0, use text-only retriever
            text_retriever = self.get_fast_text_retriever(
                vector_store_table, filters, top_k
            )
            return text_retriever
        elif vector_weight == 1:
            # If vector_weight is 1, use vector-only retriever
            vector_retriever = self.get_fast_vector_retriever(
                vector_store_table, filters, top_k, hnsw
            )
            return vector_retriever
        # Otherwise, use hybrid retriever
        text_retriever = self.get_fast_text_retriever(
            vector_store_table, filters, max(top_k * 2, 100)
        )
        vector_retriever = self.get_fast_vector_retriever(
            vector_store_table, filters, max(top_k * 2, 100), hnsw
        )
        # Always use OttoFusionRetriever (parallel hybrid). Simplicity > flag complexity.
        # If future need arises to disable fusion or parallelism, add a separate lightweight flag.
        hybrid_retriever = OttoFusionRetriever(
            [vector_retriever, text_retriever],
            similarity_top_k=top_k,
            num_queries=1,
            mode="relative_score",
            retriever_weights=[vector_weight, 1 - vector_weight],
            llm=self.llm,
            parallel=True,
        )
        return hybrid_retriever

    def get_index(
        self, vector_store_table: str, hnsw: bool = False, skip_setup: bool = False
    ) -> VectorStoreIndex:
        vector_store = OttoVectorStore.from_params(
            **_get_connection_params(),
            table_name=vector_store_table,
            embed_dim=1536,  # openai embedding dimension
            hybrid_search=True,
            text_search_config="english",
            perform_setup=not skip_setup,
            use_jsonb=True,
            debug=debug,
            hnsw_kwargs=(
                {"hnsw_ef_construction": 256, "hnsw_m": 16, "hnsw_ef_search": 512}
                if hnsw
                else None
            ),
        )

        idx = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            llm=self.llm,
            embed_model=self.embed_model,
            callback_manager=self._embed_callback_manager,
            show_progress=False,
        )
        return idx

    def temp_index_from_nodes(self, nodes: list) -> VectorStoreIndex:
        return VectorStoreIndex(embed_model=self.embed_model, nodes=nodes)

    def get_response_synthesizer(
        self,
        qa_prompt_template="{context}\n{query}",
    ):
        # Due to bug in LlamaIndex, passing service_context alone doesn't count tokens!
        # This is why we pass llm and callback_manager separately.

        return CompactAndRefine(
            streaming=True,
            llm=self.llm,
            callback_manager=self._callback_manager,
            text_qa_template=qa_prompt_template,
        )

    # Private helpers
    def _get_tree_summarizer(
        self,
        prompt_helper: PromptHelper = None,
        summary_template: PromptTemplate = None,
    ) -> TreeSummarize:
        return TreeSummarize(
            llm=self.llm,
            callback_manager=self._callback_manager,
            prompt_helper=prompt_helper,
            summary_template=summary_template,
            output_cls=None,
            streaming=True,
            use_async=True,
            verbose=True,
        )

    def _get_api_base(self, force_preview: bool = False) -> tuple[str, str | None]:
        """
        Return the Azure OpenAI base URL and api_version.

        For the GA v1 API, the base must include "/openai/v1" and api_version is
        not required. For older Azure versions, we keep the legacy base
        ("/openai") and pass the configured api_version.

        Args:
            force_preview: If True, forces use of preview API version instead of v1.
                          Required for models like Cohere and GPT-OSS that don't
                          support the v1 GA API.
        """

        azure_base = getattr(settings, "AZURE_AI_SERVICES_ENDPOINT", "").rstrip("/")
        if not azure_base:
            raise ValueError(
                "No LLM endpoint configured (missing AZURE_AI_SERVICES_ENDPOINT)"
            )

        configured_version = getattr(settings, "AZURE_AI_SERVICES_VERSION", None)

        # Some models (Cohere, GPT-OSS) need OpenAI-compatible v1 endpoint, not Azure versioned API
        if force_preview:
            # Use OpenAI-compatible endpoint (no api_version parameter)
            return f"{azure_base}/openai/v1", None

        if configured_version and configured_version.lower() == "v1":
            return f"{azure_base}/openai/v1", None

        return f"{azure_base}/openai", configured_version

    def _get_llm(
        self,
    ) -> (
        OttoCompletionsWrapper
        | OttoResponsesWrapper
        | OttoAltCompletionsWrapper
        | MockLLM
    ):
        if self.use_mock_llm:
            # Use small max_tokens for efficient load testing - generates short responses instead of echoing prompts
            return MockLLM(max_tokens=50)

        # Check if this model requires preview API (Cohere, GPT-OSS don't support v1)
        is_alternative_model = (
            self.llm_config.provider == ModelProvider.COHERE
            or self.model.startswith("gpt-oss-")
        )

        # Use Responses API for reasoning models to get reasoning summaries
        if self.llm_config.reasoning:
            # Map reasoning_effort to the Responses API format and request reasoning summaries
            # summary options: "auto", "concise", "detailed" (gpt-5 series don't support "concise")
            reasoning_options = {
                "effort": self.reasoning_effort,
                "summary": "auto",  # Enable reasoning summaries
            }
            logger.info(
                "Using Responses API with reasoning summaries",
                effort=self.reasoning_effort,
                verbosity=self.verbosity,
            )

            # Build text options for gpt-5 models (includes verbosity)
            text_options = {}
            if self.verbosity:
                text_options["verbosity"] = self.verbosity

            # Note: Responses API doesn't accept temperature parameter
            # Reasoning models always use temperature=1.0 internally
            api_base, api_version = self._get_api_base(
                force_preview=is_alternative_model
            )

            return OttoResponsesWrapper(
                api_base=api_base,
                api_version=api_version,
                api_key=settings.AZURE_AI_SERVICES_KEY,
                model=self.model,
                callback_manager=self._callback_manager,
                reasoning_options=reasoning_options,
                text_options=text_options if text_options else None,
                priority=self.priority,
                token_counter=self._token_counter,
                timeout=self.timeout,
                is_chat_model=True,
                default_headers={"HTTP-Referer": "otto", "X-Title": "Otto"},
            )

        # Use standard Chat Completions API for non-reasoning models
        api_base, api_version = self._get_api_base(force_preview=is_alternative_model)

        # Use OttoAltCompletionsWrapper for models not in LlamaIndex's hardcoded list
        # (Cohere, GPT-OSS are hosted via OpenAI-compatible API but fail LlamaIndex validation)
        llm_class = (
            OttoAltCompletionsWrapper
            if is_alternative_model
            else OttoCompletionsWrapper
        )

        return llm_class(
            api_base=api_base,
            api_version=api_version,
            api_key=settings.AZURE_AI_SERVICES_KEY,
            model=self.model,
            temperature=self.temperature,
            callback_manager=self._callback_manager,
            reasoning_effort=self.reasoning_effort,
            priority=self.priority,
            timeout=self.timeout,
            is_chat_model=True,
            default_headers={"HTTP-Referer": "otto", "X-Title": "Otto"},
        )

    def _get_embed_model(self):
        if self.mock_embedding:
            return MockEmbedding(1536)

        if self.priority == priorities.HIGH:
            deployment_name = "text-embedding-3-large-high-priority"
        else:
            deployment_name = "text-embedding-3-large"

        # LlamaIndex validates `model` against OpenAIEmbeddingModelType enum.
        # Our Azure deployment alias (e.g. *-high-priority) is not part of that enum.
        # Pass a valid canonical model for validation and override `model_name`
        # so requests still target the intended Azure deployment.
        canonical_model = "text-embedding-3-large"

        api_base, api_version = self._get_api_base()

        from llama_index.embeddings.openai import OpenAIEmbedding

        return OpenAIEmbedding(
            model=canonical_model,
            model_name=deployment_name,
            dimensions=1536,
            embed_batch_size=settings.EMBEDDING_BATCH_SIZE,
            api_key=settings.AZURE_AI_SERVICES_KEY,
            api_base=api_base,
            api_version=api_version,
            callback_manager=self._embed_callback_manager,
            max_retries=0,
            timeout=200,
        )
