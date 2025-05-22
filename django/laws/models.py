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

from chat.llm import OttoLLM

logger = get_logger(__name__)


class LawManager(models.Manager):

    def from_docs_and_nodes(
        self,
        document_en,
        nodes_en,
        document_fr,
        nodes_fr,
        sha_256_hash_en,
        sha_256_hash_fr,
        add_to_vector_store=True,
        force_update=False,
        llm=None,
    ):
        # Does this law already exist?
        existing_law = self.filter(node_id_en=document_en.doc_id)
        en_hash_changed = True
        fr_hash_changed = True
        if existing_law.exists():
            obj = existing_law.first()
            en_hash_changed = obj.sha_256_hash_en != sha_256_hash_en
            fr_hash_changed = obj.sha_256_hash_fr != sha_256_hash_fr
            logger.debug(f"en_hash_changed: {en_hash_changed}")
            logger.debug(f"fr_hash_changed: {fr_hash_changed}")
        else:
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
        obj.sha_256_hash_en = sha_256_hash_en
        obj.sha_256_hash_fr = sha_256_hash_fr

        obj.full_clean()
        obj.save()

        if add_to_vector_store:
            if llm is None:
                return
            idx = llm.get_index("laws_lois__", hnsw=True)
            nodes = []
            if existing_law.exists():
                # Remove the old content from the vector store
                if en_hash_changed or force_update:
                    idx.delete_ref_doc(obj.node_id_en, delete_from_docstore=True)
                if fr_hash_changed or force_update:
                    idx.delete_ref_doc(obj.node_id_fr, delete_from_docstore=True)
            if en_hash_changed or force_update:
                nodes.append(document_en)
                nodes.extend(nodes_en)
            if fr_hash_changed or force_update:
                nodes.append(document_fr)
                nodes.extend(nodes_fr)
            nodes = [n for n in nodes if n.text]
            batch_size = 16
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

    #### BILINGUAL FIELDS (through model translation)
    # Title concatenates short_title (or long_title, if no short_title) and ref_number
    title = models.TextField()
    short_title = models.TextField(null=True, blank=True)
    long_title = models.TextField(null=True, blank=True)
    # Enabling authority matches format of ref_number
    ref_number = models.CharField(max_length=255)  # e.g. "A-0.6" or "SOR-86-1026".
    enabling_authority = models.CharField(max_length=255, null=True, blank=True)
    # ID of the LlamaIndex node for the document
    node_id = models.CharField(max_length=255, unique=True)

    # Hashes for each language - so we can see if the content has changed
    sha_256_hash_en = models.CharField(max_length=64, null=True, blank=True)
    sha_256_hash_fr = models.CharField(max_length=64, null=True, blank=True)

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
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text(f"DROP TABLE IF EXISTS data_laws_lois__ CASCADE"))
        session.commit()
        session.close()
        cls.objects.all().delete()

    @classmethod
    def get_index(cls):
        idx = OttoLLM().get_index("laws_lois__", hnsw=True)
        return idx

    class Meta:
        indexes = [
            models.Index(fields=["title"]),
            models.Index(fields=["type"]),
        ]
