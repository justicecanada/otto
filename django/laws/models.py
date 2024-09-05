import time

from django.conf import settings
from django.db import models

import tiktoken
from llama_index.core import VectorStoreIndex
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger
from tqdm import tqdm

logger = get_logger(__name__)

token_counter = TokenCountingHandler(
    tokenizer=tiktoken.encoding_for_model("gpt-4").encode
)


def connect_to_vector_store(vector_store_table: str) -> VectorStoreIndex:
    # Same as in Librarian utils, but with token counter added
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
        embed_batch_size=128,
        api_key=settings.AZURE_OPENAI_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_VERSION,
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

    # Remove the old content from the vector store
    idx = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        llm=llm,
        embed_model=embed_model,
        callback_manager=CallbackManager([token_counter]),
        show_progress=False,
    )

    return idx


class LawManager(models.Manager):

    def from_doc_and_nodes(self, document, nodes, add_to_vector_store=True):
        obj = self.model()
        obj.title = document.metadata["display_metadata"]
        obj.short_title = document.metadata.get("short_title")
        obj.long_title = document.metadata.get("long_title")
        obj.ref_number = (
            document.metadata["consolidated_number"]
            or document.metadata["instrument_number"]
            or document.metadata["bill_number"]
        )
        obj.law_id = document.metadata["id"]
        obj.lang = document.metadata["lang"]
        obj.type = document.metadata["type"]
        obj.last_amended_date = document.metadata.get("last_amended_date")
        obj.current_date = document.metadata.get("current_date")
        obj.enabling_authority = document.metadata.get("enabling_authority")
        obj.node_id = document.doc_id

        obj.full_clean()
        obj.save()

        if add_to_vector_store:
            nodes = [document] + nodes
            idx = connect_to_vector_store("laws_lois__")
            batch_size = 128
            logger.debug(
                f"Embedding & inserting nodes into vector store (batch size={batch_size} nodes)..."
            )
            for i in tqdm(range(0, len(nodes), batch_size)):
                # Exponential backoff retry
                for j in range(4, 12):
                    try:
                        idx.insert_nodes(nodes[i : i + batch_size])
                        break
                    except Exception as e:
                        logger.error(f"Error inserting nodes: {e}")
                        logger.error(f"Retrying in {2**j} seconds...")
                        time.sleep(2**j)
        return obj


class Law(models.Model):
    """
    Act or regulation. Mirrors LlamaIndex vector store representation.
    """

    # Title concatenates short_title, long_title and ref_number
    title = models.TextField()
    short_title = models.TextField(null=True, blank=True)
    long_title = models.TextField(null=True, blank=True)

    # e.g. "A-0.6" or "SOR-86-1026".
    # Enabling authority or external references use these.
    ref_number = models.CharField(max_length=255)

    # Language-agnostic ID of the law
    law_id = models.CharField(max_length=255)

    lang = models.CharField(max_length=10, default="eng")
    type = models.CharField(max_length=255, default="act")
    last_amended_date = models.DateField(null=True)
    current_date = models.DateField(null=True)

    # enabling_authority = models.ForeignKey("self", on_delete=models.PROTECT, null=True)
    enabling_authority = models.CharField(max_length=255, null=True, blank=True)

    # ID of the LlamaIndex node for the document (language-specific)
    node_id = models.CharField(max_length=255, unique=True)

    objects = LawManager()

    @classmethod
    def reset(cls):
        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:5432/{db['NAME']}"
        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text(f"DROP TABLE IF EXISTS data_laws_lois__ CASCADE"))
        session.commit()
        session.close()
        cls.objects.all().delete()

    @classmethod
    def get_index(cls):
        idx = connect_to_vector_store("laws_lois__")
        return idx
