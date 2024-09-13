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


def connect_to_vector_store(
    vector_store_table: str, mock_embedding: bool = False
) -> VectorStoreIndex:
    # Same as in Librarian utils, but with token counter added
    from llama_index.core.embeddings import MockEmbedding
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

    if mock_embedding:
        embed_model = MockEmbedding(1536)
    else:
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

    def from_docs_and_nodes(
        self,
        document_en,
        nodes_en,
        document_fr,
        nodes_fr,
        add_to_vector_store=True,
        mock_embedding=False,
    ):
        obj = self.model()

        # Document-level metadata (English)
        obj.short_title_en = document_en.metadata.get("short_title")
        obj.long_title_en = document_en.metadata.get("long_title")
        obj.ref_number_en = (
            document_en.metadata["consolidated_number"]
            or document_en.metadata["instrument_number"]
            or document_en.metadata["bill_number"]
        )
        obj.title_en = (
            f"{obj.short_title_en or obj.long_title_en} ({obj.ref_number_en})"
        )
        obj.enabling_authority_en = document_en.metadata.get("enabling_authority")
        obj.node_id_en = document_en.doc_id
        # Document-level metadata (French)
        obj.short_title_fr = document_fr.metadata.get("short_title")
        obj.long_title_fr = document_fr.metadata.get("long_title")
        obj.ref_number_fr = (
            document_fr.metadata["consolidated_number"]
            or document_fr.metadata["instrument_number"]
            or document_fr.metadata["bill_number"]
        )
        obj.title_fr = (
            f"{obj.short_title_fr or obj.long_title_fr} ({obj.ref_number_fr})"
        )
        obj.enabling_authority_fr = document_fr.metadata.get("enabling_authority")
        obj.node_id_fr = document_fr.doc_id

        # Language-agnostic metadata
        obj.type = document_en.metadata["type"]
        obj.last_amended_date = document_en.metadata.get("last_amended_date", None)
        obj.current_date = document_en.metadata.get("current_date", None)
        obj.in_force_start_date = document_en.metadata.get("in_force_start_date", None)

        obj.full_clean()
        obj.save()

        if add_to_vector_store:
            nodes = [document_en, document_fr] + nodes_en + nodes_fr
            idx = connect_to_vector_store("laws_lois__", mock_embedding)
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
    Translated model so that the list of laws can be displayed in the user's language.
    """

    #### BILINGUAL FIELDS
    # Title concatenates short_title (or long_title, if no short_title) and ref_number
    title = models.TextField()
    short_title = models.TextField(null=True, blank=True)
    long_title = models.TextField(null=True, blank=True)
    # Enabling authority matches format of ref_number
    ref_number = models.CharField(max_length=255)  # e.g. "A-0.6" or "SOR-86-1026".
    enabling_authority = models.CharField(max_length=255, null=True, blank=True)
    # ID of the LlamaIndex node for the document
    node_id = models.CharField(max_length=255, unique=True)

    #### SHARED BETWEEN LANGUAGES
    type = models.CharField(max_length=255, default="act")  # "act" or "regulation"
    last_amended_date = models.DateField(null=True, blank=True)
    current_date = models.DateField(null=True, blank=True)
    in_force_start_date = models.DateField(null=True, blank=True)

    objects = LawManager()

    def __str__(self):
        return self.title

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
