from django.conf import settings

import tiktoken
from llama_index.core import PromptTemplate, VectorStoreIndex
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.response_synthesizers import CompactAndRefine, TreeSummarize
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI
from llama_index.vector_stores.postgres import PGVectorStore

from otto.models import Cost


class OttoLLM:
    """
    Wrapper around LlamaIndex to assist with cost tracking and reduce boilerplate.
    "model" must match the name of the LLM deployment in Azure.
    """

    _deployment_to_model_mapping = {
        "gpt-4o": "gpt-4o",
        "gpt-4": "gpt-4-1106-preview",
        "gpt-35": "gpt-35-turbo-0125",
    }
    _deployment_to_max_input_tokens_mapping = {
        "gpt-4o": 128000,
        "gpt-4": 128000,
        "gpt-35": 16385,
    }

    def __init__(
        self, deployment: str = settings.DEFAULT_CHAT_MODEL, temperature: float = 0.1
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
        self.embed_model = self._get_embed_model()
        # self._service_context = ServiceContext.from_defaults(
        #     llm=self.llm,
        #     embed_model=self.embed_model,
        #     callback_manager=self._callback_manager,
        # )
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
        if self.input_token_count > 0:
            Cost.objects.new(
                cost_type=f"{self.deployment}-in", count=self.input_token_count
            )
        if self.output_token_count > 0:
            Cost.objects.new(
                cost_type=f"{self.deployment}-out", count=self.output_token_count
            )
        if self.embed_token_count > 0:
            Cost.objects.new(cost_type="embedding", count=self.embed_token_count)

    # RAG-related getters for retriever (get sources only) and response synthesizer
    def get_retriever(
        self,
        vector_store_table: str,
        filters: MetadataFilters = None,
        top_k: int = 5,
        vector_weight: float = 0.6,
    ) -> QueryFusionRetriever:

        pg_idx = self.get_index(vector_store_table)

        pg_idx._callback_manager = self._callback_manager

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

    def get_index(self, vector_store_table: str) -> VectorStoreIndex:
        vector_store = PGVectorStore.from_params(
            database=settings.DATABASES["vector_db"]["NAME"],
            host=settings.DATABASES["vector_db"]["HOST"],
            password=settings.DATABASES["vector_db"]["PASSWORD"],
            port=5432,
            user=settings.DATABASES["vector_db"]["USER"],
            table_name=vector_store_table,
            embed_dim=1536,  # openai embedding dimension
            hybrid_search=True,
            text_search_config="english",
            perform_setup=True,
        )
        idx = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            llm=self.llm,
            embed_model=self.embed_model,
            callback_manager=self._callback_manager,
            show_progress=False,
        )
        return idx

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
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_VERSION,
            api_key=settings.AZURE_OPENAI_KEY,
            deployment_name=self.deployment,
            model=self.model,
            temperature=self.temperature,
            callback_manager=self._callback_manager,
        )

    def _get_embed_model(self) -> AzureOpenAIEmbedding:
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
