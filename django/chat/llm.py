import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

from django.conf import settings
from django.utils.translation import gettext_lazy as _

import tiktoken
from llama_index.core import PromptTemplate, VectorStoreIndex
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.indices.prompt_helper import PromptHelper
from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.instrumentation.event_handlers import BaseEventHandler
from llama_index.core.instrumentation.events.embedding import EmbeddingEndEvent
from llama_index.core.instrumentation.events.llm import (
    LLMChatEndEvent,
    LLMChatStartEvent,
    LLMCompletionEndEvent,
)
from llama_index.core.llms import MockLLM
from llama_index.core.response_synthesizers import CompactAndRefine, TreeSummarize
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.schema import QueryBundle
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.vector_stores.postgres import PGVectorStore
from retrying import retry
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from structlog import get_logger

from otto.models import Cost

from .llm_models import get_model

logger = get_logger(__name__)

debug = settings.DEBUG

# Context variable for load testing - when set to True, forces use of Mock LLM and Mock Embedding
mock_llm_context: ContextVar[bool] = ContextVar("mock_llm_context", default=False)

# Lazy-initialized shared database engines (module-level for reuse across requests)
_pg_sync_engine = None
_pg_async_engine = None


def _get_connection_params():
    """Get current database connection params (respects test database names)."""
    return {
        "database": settings.DATABASES["vector_db"]["NAME"],
        "host": settings.DATABASES["vector_db"]["HOST"],
        "password": settings.DATABASES["vector_db"]["PASSWORD"],
        "user": settings.DATABASES["vector_db"]["USER"],
        "port": settings.DATABASES["vector_db"]["PORT"],
    }


def get_pg_engines():
    """Get or create shared PostgreSQL engines for vector store.
    Lazy initialization ensures test database names are used correctly.
    Returns tuple of (sync_engine, async_engine).
    """
    global _pg_sync_engine, _pg_async_engine

    if _pg_sync_engine is None or _pg_async_engine is None:
        connection_params = _get_connection_params()
        pg_sync_conn_string = f"postgresql+psycopg2://{connection_params['user']}:{connection_params['password']}@{connection_params['host']}:{connection_params['port']}/{connection_params['database']}"
        pg_async_conn_string = f"postgresql+asyncpg://{connection_params['user']}:{connection_params['password']}@{connection_params['host']}:{connection_params['port']}/{connection_params['database']}"

        _pg_sync_engine = create_engine(pg_sync_conn_string)
        _pg_async_engine = create_async_engine(pg_async_conn_string)

    return _pg_sync_engine, _pg_async_engine


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


if settings.DEBUG:
    root_dispatcher = get_dispatcher()
    root_dispatcher.add_event_handler(ModelEventHandler())


def chat_history_to_prompt(chat_history: list) -> str:
    """
    Convert a list of ChatMessage objects to a single prompt string.
    Each message will be formatted as: "<role>: <content>"
    """
    from llama_index.core.base.llms.types import ChatMessage

    lines = []
    for msg in chat_history:
        # If msg is a dict, convert to ChatMessage
        if not isinstance(msg, ChatMessage) and hasattr(ChatMessage, "model_validate"):
            msg = ChatMessage.model_validate(msg)
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)
        if role and content:
            lines.append(f"{role.value}: {content}")
        elif content:
            lines.append(str(content))
    return "\n".join(lines)


class OttoLLM:
    """
    Wrapper around LlamaIndex to assist with cost tracking and reduce boilerplate.
    "model" must match the name of the LLM deployment in Azure.
    """

    def __init__(
        self,
        deployment: str = settings.DEFAULT_CHAT_MODEL,
        temperature: float = 0.1,
        mock_embedding: bool = False,
        reasoning_effort: str = "minimal",
        embedding_deployment: str = "text-embedding-3-large-questions",
    ):
        # Check if mock_llm is enabled via contextvar (for load testing)
        self.use_mock_llm = mock_llm_context.get(False)

        # "minimal" reasoning effort only valid for gpt-5 models
        if reasoning_effort == "minimal" and not deployment.startswith("gpt-5"):
            reasoning_effort = "low"

        self.llm_config = get_model(deployment)
        if not self.llm_config:
            raise ValueError(f"Invalid deployment: {deployment}")

        self.deployment = self.llm_config.deployment_name
        self.model = self.llm_config.deployment_name
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self._token_counter = TokenCountingHandler(
            tokenizer=tiktoken.get_encoding("o200k_base").encode
        )
        self._callback_manager = CallbackManager([self._token_counter])
        self.llm = self._get_llm()
        # If mock_llm context is set, always use mock embedding regardless of mock_embedding parameter
        self.mock_embedding = mock_embedding or self.use_mock_llm
        self.embedding_deployment = embedding_deployment
        self.embed_model = self._get_embed_model()
        self.max_input_tokens = self.llm_config.max_tokens_in
        self.max_output_tokens = self.llm_config.max_tokens_out

    # Convenience methods to interact with LLM
    # Each will return a complete response (not single tokens)
    async def chat_stream(self, chat_history: list):
        """
        Stream complete response (not single tokens) from list of chat history objects
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
                context_window=self.max_input_tokens,
                num_output=min(self.max_output_tokens, 16384),
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
            yield _(
                "An error occurred while summarizing the text."
            ) + f" _({_('Error ID:')} {error_id})_"

    # Token counting / cost tracking
    @property
    def input_token_count(self):
        return self._token_counter.prompt_llm_token_count

    @property
    def output_token_count(self):
        return self._token_counter.completion_llm_token_count

    @property
    def embed_token_count(self):
        return self._token_counter.total_embedding_token_count

    def create_costs(self) -> None:
        """
        Create Otto Cost objects for the given user and feature.
        """
        usd_cost = 0
        if self.input_token_count > 0:
            c1 = Cost.objects.new(
                cost_type=f"{self.llm_config.model_id}-in",
                count=self.input_token_count,
            )
            usd_cost += c1.usd_cost
        if self.output_token_count > 0:
            c2 = Cost.objects.new(
                cost_type=f"{self.llm_config.model_id}-out",
                count=self.output_token_count,
            )
            usd_cost += c2.usd_cost
        if self.embed_token_count > 0 and not self.mock_embedding:
            c3 = Cost.objects.new(cost_type="embedding", count=self.embed_token_count)
            usd_cost += c3.usd_cost

        self._token_counter.reset_counts()
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
            callback_manager=self._callback_manager,
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

    def _get_llm(self) -> AzureOpenAI | MockLLM:
        if self.use_mock_llm:
            # Use small max_tokens for efficient load testing - generates short responses instead of echoing prompts
            return MockLLM(max_tokens=50)
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            api_key=settings.AZURE_OPENAI_KEY,
            deployment_name=self.deployment,
            model=self.model,
            temperature=self.temperature,
            callback_manager=self._callback_manager,
            reasoning_effort=self.reasoning_effort,
        )

    def _get_embed_model(self) -> AzureOpenAIEmbedding | MockEmbedding:
        if self.mock_embedding:
            return MockEmbedding(1536)
        return AzureOpenAIEmbedding(
            model="text-embedding-3-large",
            deployment_name=self.embedding_deployment,
            dimensions=1536,
            embed_batch_size=16,
            api_key=settings.AZURE_OPENAI_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            callback_manager=self._callback_manager,
        )


class OttoVectorStore(PGVectorStore):
    # Override from LlamaIndex to reuse shared engines across all OttoVectorStore instances
    @retry(
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
    )
    def _connect(
        self,
    ):  # Use shared engines to avoid creating new connections on every RAG request
        pg_sync_engine, pg_async_engine = get_pg_engines()

        self._engine = pg_sync_engine
        self._session = sessionmaker(self._engine)

        self._async_engine = pg_async_engine
        self._async_session = sessionmaker(self._async_engine, class_=AsyncSession)  # type: ignore


class OttoFusionRetriever(QueryFusionRetriever):
    """Threaded variant of QueryFusionRetriever to safely parallelize multi-retriever
    fusion without invoking nested event loops.
    Differences from upstream:
    - Ignores parent run_async_tasks pathway (which can trigger nested event loop errors)
      and instead uses a ThreadPoolExecutor when parallel=True.
    - Keeps num_queries=1 in current usage (no query generation overhead), but retains
      fusion logic for score weighting.
    - If parallel=False, falls back to parent's synchronous behavior.
    """

    def __init__(
        self,
        retrievers,
        llm=None,
        query_gen_prompt=None,
        mode="relative_score",
        similarity_top_k=5,
        num_queries=1,
        parallel: bool = True,
        max_workers: int | None = None,
        retriever_weights=None,
        **kwargs,
    ):
        # Force use_async False in parent; we manage our own concurrency
        super().__init__(
            retrievers,
            llm=llm,
            query_gen_prompt=query_gen_prompt,
            mode=mode,
            similarity_top_k=similarity_top_k,
            num_queries=num_queries,
            use_async=False,
            retriever_weights=retriever_weights,
            **kwargs,
        )
        self._parallel = parallel
        self._max_workers = max_workers

    def _run_threaded_queries(self, queries):
        """Run underlying retriever.retrieve concurrently via threads."""
        results = {}
        # Build all (query, retriever) pairs
        tasks = []
        for query in queries:
            for i, retriever in enumerate(self._retrievers):
                tasks.append((query, i, retriever))
        max_workers = self._max_workers or min(32, len(tasks) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(retriever.retrieve, query): (query.query_str, idx)
                for query, idx, retriever in tasks
            }
            for future in future_map:
                key = future_map[future]
                results[key] = future.result()
        return results

    def _retrieve(self, query_bundle: QueryBundle):  # type: ignore[override]
        queries = [query_bundle]
        if self.num_queries > 1:
            # Preserve compatibility if num_queries > 1 in future usage
            queries.extend(self._get_queries(query_bundle.query_str))

        if self._parallel and len(self._retrievers) > 1:
            results = self._run_threaded_queries(queries)
        else:
            # Fallback to simple sync iteration
            results = {}
            for query in queries:
                for i, retriever in enumerate(self._retrievers):
                    results[(query.query_str, i)] = retriever.retrieve(query)

        if self.mode == "reciprocal_rerank":
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == "relative_score":
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == "dist_based_score":
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == "simple":
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")

    async def _aretrieve(self, query_bundle: QueryBundle):  # type: ignore[override]
        """Provide a true async path by delegating to underlying async retrievers if available.
        If any underlying retriever lacks 'aretrieve', we fall back to thread pool to
        avoid blocking the loop.
        """
        queries = [query_bundle]
        if self.num_queries > 1:
            queries.extend(self._get_queries(query_bundle.query_str))

        # Check capability
        can_async = all(hasattr(r, "aretrieve") for r in self._retrievers)
        if can_async:
            import asyncio

            tasks = []
            task_keys = []
            for query in queries:
                for i, retriever in enumerate(self._retrievers):
                    tasks.append(retriever.aretrieve(query))
                    task_keys.append((query.query_str, i))
            task_results = await asyncio.gather(*tasks)
            results = {k: v for k, v in zip(task_keys, task_results)}
        else:
            # Fall back to threaded sync retrieval to preserve non-blocking behavior
            results = self._run_threaded_queries(queries)

        if self.mode == "reciprocal_rerank":
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == "relative_score":
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == "dist_based_score":
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == "simple":
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")
