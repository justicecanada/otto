from django.conf import settings

from llama_index.core import ServiceContext, VectorStoreIndex


def connect_to_vector_store(vector_store_table: str) -> VectorStoreIndex:
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
    from llama_index.llms.azure_openai import AzureOpenAI
    from llama_index.vector_stores.postgres import PGVectorStore

    llm = AzureOpenAI(
        model=settings.DEFAULT_CHAT_MODEL,  # TODO: Rethink how to pass this in. Maybe a global variable? Or dynamic based on the library?
        deployment_name=settings.DEFAULT_CHAT_MODEL,  # TODO: Revisit whether unfiltered is still needed or if an alternative can be used.
        api_key=settings.AZURE_OPENAI_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_VERSION,
    )

    embed_model = AzureOpenAIEmbedding(
        model="text-embedding-3-large",
        deployment_name="text-embedding-3-large",
        dimensions=1536,
        embed_batch_size=16,
        api_key=settings.AZURE_OPENAI_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_VERSION,
    )

    service_context = ServiceContext.from_defaults(
        llm=llm,
        embed_model=embed_model,
    )

    # Get the vector store for the library
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
        service_context=service_context,
        show_progress=False,
    )

    return idx
