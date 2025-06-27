import uuid

from django.conf import settings
from django.utils.translation import gettext as _

import sqlalchemy
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
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.vector_stores.postgres import PGVectorStore
from retrying import retry
from structlog import get_logger

from otto.models import Cost

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


if settings.DEBUG:
    root_dispatcher = get_dispatcher()
    root_dispatcher.add_event_handler(ModelEventHandler())


llms = {
    "gpt-4.1-mini": {
        "description": _("GPT-4.1-mini (best value, 3x cost)"),
        "model": "gpt-4.1-mini",
        "max_tokens_in": 1047576,
        "max_tokens_out": 32768,
    },
    "gpt-4.1": {
        "description": _("GPT-4.1 (best quality, 12x cost)"),
        "model": "gpt-4.1",
        "max_tokens_in": 1047576,
        "max_tokens_out": 32768,
    },
    "o3-mini": {
        "description": _("o3-mini (adds reasoning, 7x cost)"),
        "model": "o3-mini",
        "max_tokens_in": 200000,
        "max_tokens_out": 100000,
    },
    "gpt-4o-mini": {
        "description": _("GPT-4o-mini (legacy model, 1x cost)"),
        "model": "gpt-4o-mini",
        "max_tokens_in": 128000,
        "max_tokens_out": 16384,
    },
    "gpt-4o": {
        "description": _("GPT-4o (legacy model, 15x cost)"),
        "model": "gpt-4o",
        "max_tokens_in": 128000,
        "max_tokens_out": 16384,
    },
}

CHAT_MODELS = [(k, v["description"]) for k, v in llms.items()]

NO_CHAT_MODELS = ["command-a"]


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
    ):
        if deployment not in llms:
            raise ValueError(f"Invalid deployment: {deployment}")
        self.deployment = deployment
        self.model = llms[deployment]["model"]
        self.temperature = temperature
        self._token_counter = TokenCountingHandler(
            tokenizer=tiktoken.get_encoding("o200k_base").encode
        )
        self._callback_manager = CallbackManager([self._token_counter])
        self.llm = self._get_llm()
        self.mock_embedding = mock_embedding
        self.embed_model = self._get_embed_model()
        self.max_input_tokens = llms[deployment]["max_tokens_in"]
        self.max_output_tokens = llms[deployment]["max_tokens_out"]

    # Convenience methods to interact with LLM
    # Each will return a complete response (not single tokens)
    async def chat_stream(self, chat_history: list):
        """
        Stream complete response (not single tokens) from list of chat history objects
        """
        if self.deployment in NO_CHAT_MODELS:
            prompt = chat_history_to_prompt(chat_history)
            async for chunk in self.stream(prompt):
                yield chunk
            return
        response_stream = await self.llm.astream_chat(chat_history)
        async for chunk in response_stream:
            yield chunk.message.content

    async def stream(self, prompt: str):
        response_stream = await self.llm.astream_complete(prompt)
        async for chunk in response_stream:
            yield chunk.text

    def complete(self, prompt: str, **kwargs):
        """
        Return complete response string from single prompt string (no streaming)
        """
        return self.llm.complete(prompt, **kwargs).text

    def chat_complete(self, chat_history: list):
        """
        Return complete response string from list of chat history objects (no streaming)
        """
        if self.deployment in NO_CHAT_MODELS:
            prompt = chat_history_to_prompt(chat_history)
            return self.complete(prompt)
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
                cost_type=f"{self.deployment}-in", count=self.input_token_count
            )
            usd_cost += c1.usd_cost
        if self.output_token_count > 0:
            c2 = Cost.objects.new(
                cost_type=f"{self.deployment}-out", count=self.output_token_count
            )
            usd_cost += c2.usd_cost
        if self.embed_token_count > 0 and not self.mock_embedding:
            c3 = Cost.objects.new(cost_type="embedding", count=self.embed_token_count)
            usd_cost += c3.usd_cost

        self._token_counter.reset_counts()
        return usd_cost

    # RAG-related getters for retriever (get sources only) and response synthesizer
    def get_retriever(
        self,
        vector_store_table: str,
        filters: MetadataFilters = None,
        top_k: int = 5,
        vector_weight: float = 0.6,
        hnsw: bool = False,
    ) -> QueryFusionRetriever:

        pg_idx = self.get_index(vector_store_table, hnsw=hnsw)

        vector_retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=max(top_k, 100),
            filters=filters,
            llm=self.llm,
            embed_model=self.embed_model,
        )
        text_retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=max(top_k, 100),
            filters=filters,
            llm=self.llm,
            embed_model=self.embed_model,
        )
        hybrid_retriever = QueryFusionRetriever(
            [vector_retriever, text_retriever],
            similarity_top_k=top_k,
            num_queries=1,  # set this to 1 to disable query generation
            mode="relative_score",
            use_async=False,
            retriever_weights=[vector_weight, 1 - vector_weight],
            llm=self.llm,
        )
        return hybrid_retriever

    def get_index(
        self, vector_store_table: str, hnsw: bool = False
    ) -> VectorStoreIndex:
        vector_store = OttoVectorStore.from_params(
            database=settings.DATABASES["vector_db"]["NAME"],
            host=settings.DATABASES["vector_db"]["HOST"],
            password=settings.DATABASES["vector_db"]["PASSWORD"],
            user=settings.DATABASES["vector_db"]["USER"],
            port=settings.DATABASES["vector_db"]["PORT"],
            table_name=vector_store_table,
            embed_dim=1536,  # openai embedding dimension
            hybrid_search=True,
            text_search_config="english",
            perform_setup=True,
            use_jsonb=True,
            hnsw_kwargs=(
                {"hnsw_ef_construction": 256, "hnsw_m": 32, "hnsw_ef_search": 256}
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

    def _get_llm(self) -> AzureOpenAI:
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            api_key=settings.AZURE_OPENAI_KEY,
            deployment_name=self.deployment,
            model=self.model,
            temperature=self.temperature,
            callback_manager=self._callback_manager,
        )

    def _get_embed_model(self) -> AzureOpenAIEmbedding | MockEmbedding:
        if self.mock_embedding:
            return MockEmbedding(1536)
        return AzureOpenAIEmbedding(
            model="text-embedding-3-large",
            deployment_name="text-embedding-3-large",
            dimensions=1536,
            embed_batch_size=16,
            api_key=settings.AZURE_OPENAI_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            callback_manager=self._callback_manager,
        )


class OttoVectorStore(PGVectorStore):
    # Override from LlamaIndex to add retrying and connection test
    @retry(
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
    )
    def _connect(self):
        from sqlalchemy import create_engine
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        self._engine = create_engine(
            self.connection_string, echo=self.debug, **self.create_engine_kwargs
        )
        self._session = sessionmaker(self._engine)

        self._async_engine = create_async_engine(
            self.async_connection_string, **self.create_engine_kwargs
        )
        self._async_session = sessionmaker(self._async_engine, class_=AsyncSession)  # type: ignore

        # Test the connection to ensure it's established
        with self._engine.connect() as connection:
            connection.execute(sqlalchemy.text("SELECT 1"))
