from django.conf import settings

import sqlalchemy
import tiktoken
from llama_index.core import PromptTemplate, VectorStoreIndex
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.response_synthesizers import CompactAndRefine, TreeSummarize
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.vector_stores.postgres import PGVectorStore
from retrying import retry

from otto.models import Cost


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


class OttoLLM:
    """
    Wrapper around LlamaIndex to assist with cost tracking and reduce boilerplate.
    "model" must match the name of the LLM deployment in Azure.
    """

    _deployment_to_model_mapping = {
        "gpt-4o-mini": "gpt-4o-mini",
        "gpt-4o": "gpt-4o",
        "gpt-4o-2024-11-20": "gpt-4o",
    }
    _deployment_to_max_input_tokens_mapping = {
        "gpt-4o-mini": 128000,
        "gpt-4o": 128000,
        "gpt-4o-2024-11-20": 128000,
    }

    def __init__(
        self,
        deployment: str = settings.DEFAULT_CHAT_MODEL,
        temperature: float = 0.1,
        mock_embedding: bool = False,
    ):
        if deployment not in self._deployment_to_model_mapping:
            raise ValueError(f"Invalid deployment: {deployment}")
        self.deployment = deployment
        self.model = self._deployment_to_model_mapping[deployment]
        self.temperature = temperature
        self._token_counter = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model(self.model).encode
        )
        self._callback_manager = CallbackManager([self._token_counter])
        self.llm = self._get_llm()
        self.mock_embedding = mock_embedding
        self.embed_model = self._get_embed_model()
        self.max_input_tokens = self._deployment_to_max_input_tokens_mapping[deployment]

    # Convenience methods to interact with LLM
    # Each will return a complete response (not single tokens)
    async def chat_stream(self, chat_history: list):
        """
        Stream complete response (not single tokens) from list of chat history objects
        """
        response_stream = await self.llm.astream_chat(chat_history)
        async for chunk in response_stream:
            yield chunk.message.content

    async def stream(self, prompt: str):
        """
        Stream complete response (not single tokens) from single prompt string
        """
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
        return self.llm.chat(chat_history).message.content

    async def tree_summarize(
        self,
        context: str,
        query: str = "summarize the text",
        template: PromptTemplate = None,
    ):
        """
        Stream complete response (not single tokens) from context string and query.
        Optional: summary template (must include "{context_str}" and "{query_str}".)
        """
        response = await self._get_tree_summarizer(
            summary_template=template
        ).aget_response(query, [context])
        response_text = ""
        async for chunk in response:
            response_text += chunk
            yield response_text

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
        self, summary_template: PromptTemplate = None
    ) -> TreeSummarize:
        return TreeSummarize(
            llm=self.llm,
            callback_manager=self._callback_manager,
            prompt_helper=None,
            summary_template=summary_template,
            output_cls=None,
            streaming=True,
            use_async=True,
            verbose=True,
        )

    def _get_llm(self) -> AzureOpenAI:
        endpoint = (
            settings.AZURE_OPENAI_CANADA_CENTRAL_ENDPOINT
            if self.deployment == "gpt-4o-2024-11-20"
            else settings.AZURE_OPENAI_ENDPOINT
        )
        api_key = (
            settings.AZURE_OPENAI_CANADA_CENTRAL_KEY
            if self.deployment == "gpt-4o-2024-11-20"
            else settings.AZURE_OPENAI_KEY
        )
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_version=settings.AZURE_OPENAI_VERSION,
            api_key=api_key,
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
