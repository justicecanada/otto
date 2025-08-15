import uuid

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
from llama_index.core.response_synthesizers import CompactAndRefine, TreeSummarize
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai import OpenAI
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
OLLAMA_URL = "http://host.docker.internal:11434"


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
        reasoning_effort: str = "medium",
    ):
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
        self.mock_embedding = mock_embedding
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

        # Prepend system prompt prefix if it exists
        if self.llm_config.system_prompt_prefix:
            for message in chat_history:
                if message.role == "system":
                    message.content = (
                        f"{self.llm_config.system_prompt_prefix}\n{message.content}"
                    )
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
        hybrid_retriever = QueryFusionRetriever(
            [vector_retriever, text_retriever],
            similarity_top_k=top_k,
            num_queries=1,  # set this to 1 to disable query generation
            mode="relative_score",
            use_async=True,
            retriever_weights=[vector_weight, 1 - vector_weight],
            llm=self.llm,
        )
        return hybrid_retriever

    def get_index(
        self, vector_store_table: str, hnsw: bool = False, skip_setup: bool = False
    ) -> VectorStoreIndex:

        # Cache connection parameters to avoid repeated lookups
        connection_params = {
            "database": settings.DATABASES["vector_db"]["NAME"],
            "host": settings.DATABASES["vector_db"]["HOST"],
            "password": settings.DATABASES["vector_db"]["PASSWORD"],
            "user": settings.DATABASES["vector_db"]["USER"],
            "port": settings.DATABASES["vector_db"]["PORT"],
        }

        vector_store = OttoVectorStore.from_params(
            **connection_params,
            table_name=vector_store_table,
            embed_dim=1024,  # snowflake-arctic-embed2:latest dimension
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

    def _get_llm(self) -> Ollama:
        return Ollama(
            model="gpt-oss:20b",
            request_timeout=120,
            base_url=OLLAMA_URL,
        )

    def _get_embed_model(self) -> AzureOpenAIEmbedding | MockEmbedding:
        if self.mock_embedding:
            return MockEmbedding(1024)
        return OllamaEmbedding(
            model_name="snowflake-arctic-embed2:latest",
            base_url=OLLAMA_URL,
            embed_batch_size=16,
            callback_manager=self._callback_manager,
        )


class OttoVectorStore(PGVectorStore):
    # Override from LlamaIndex to add retrying, connection test, and correct pooling
    @retry(
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
    )
    def _connect(self):

        # Pooling for sync engine only
        sync_engine_kwargs = {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            **self.create_engine_kwargs,
        }

        self._engine = create_engine(
            self.connection_string, echo=self.debug, **sync_engine_kwargs
        )
        self._session = sessionmaker(self._engine)

        # Async engine: only pass async-appropriate kwargs
        async_engine_kwargs = dict(self.create_engine_kwargs)  # copy to avoid mutation

        # Add connect_args for asyncpg/pgbouncer compatibility
        async_engine_kwargs.setdefault("connect_args", {})
        async_engine_kwargs["connect_args"]["statement_cache_size"] = 0

        self._async_engine = create_async_engine(
            self.async_connection_string, echo=self.debug, **async_engine_kwargs
        )
        self._async_session = sessionmaker(self._async_engine, class_=AsyncSession)  # type: ignore

        # Optionally test the sync connection
        # with self._engine.connect() as connection:
        #     connection.execute(sqlalchemy.text("SELECT 1"))
