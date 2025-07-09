import time

from django.conf import settings
from django.db import models
from django.utils import timezone

from data_fetcher import cache_within_request
from llama_index.core.schema import MediaResource
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger

from chat.llm import OttoLLM

logger = get_logger(__name__)


class LawManager(models.Manager):

    def from_docs_and_nodes(
        self,
        law_status,
        document_en,
        nodes_en,
        document_fr,
        nodes_fr,
        add_to_vector_store=True,
        force_update=False,
        llm=None,
        progress_callback=None,
        current_task_id=None,
    ):
        from laws.tasks import is_cancelled

        # Updating an existing law?
        if law_status.law:
            obj = law_status.law
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
        obj.sha_256_hash_en = law_status.sha_256_hash_en
        obj.sha_256_hash_fr = law_status.sha_256_hash_fr
        obj.eng_law_id = law_status.eng_law_id

        obj.full_clean()
        obj.save()

        if add_to_vector_store:
            if llm is None:
                return
            idx = llm.get_index("laws_lois__", hnsw=True)
            nodes = []
            if law_status.law:
                # Remove the old content from the vector store
                idx.delete_ref_doc(obj.node_id_en, delete_from_docstore=True)
                idx.delete_ref_doc(obj.node_id_fr, delete_from_docstore=True)
            # Always add the document and chunk nodes for embedding
            nodes.append(document_en)
            nodes.extend(nodes_en)
            nodes.append(document_fr)
            nodes.extend(nodes_fr)
            batch_size = 16
            logger.debug(
                f"Embedding & inserting nodes into vector store (batch size={batch_size} nodes)..."
            )
            for node in nodes:
                if not node.text.strip():
                    node.text_resource = MediaResource(text=node.doc_id)

            original_details = law_status.details or ""
            total_batches = (len(nodes) + batch_size - 1) // batch_size
            for i in range(0, len(nodes), batch_size):
                if is_cancelled(current_task_id):
                    logger.info("Law loading job cancelled by user.")
                    law_status.status = "cancelled"
                    law_status.finished_at = timezone.now()
                    law_status.error_message = "Job was cancelled by user."
                    law_status.save()
                    idx.delete_ref_doc(obj.node_id_en, delete_from_docstore=True)
                    idx.delete_ref_doc(obj.node_id_fr, delete_from_docstore=True)
                    obj.delete()
                    return None
                batch_num = (i // batch_size) + 1
                logger.debug(f"Processing embedding batch {batch_num}/{total_batches}")
                law_status.details = (
                    f"{original_details} (embedding batch {batch_num}/{total_batches})"
                )
                law_status.save()
                for j in range(2, 12):
                    try:
                        idx.insert_nodes(nodes[i : i + batch_size])
                        break
                    except Exception as e:
                        logger.error(f"Error inserting nodes: {e}")
                        logger.error(f"Retrying in {2**j} seconds...")
                        time.sleep(2**j)
        return obj

    def purge(self, keep_ids):
        """
        Delete any Law objects where law.eng_law_id is not in law_ids
        """
        if not keep_ids:
            return

        to_delete = self.exclude(eng_law_id__in=set(keep_ids))
        # Create LawLoadingStatus objects for purged laws
        for law in to_delete:
            LawLoadingStatus.objects.create(
                law=None,
                eng_law_id=law.eng_law_id,
                status="deleted",
                finished_at=timezone.now(),
                details="Existing law not present in the list of laws to load",
            )
        purged_count = int(to_delete.count())
        if purged_count > 0:
            logger.info(f"Purging {purged_count} Law objects not in keep_ids")
            to_delete.delete()
        else:
            logger.info("No Law objects to purge")


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

    # To correlate with the XML file name
    eng_law_id = models.CharField(max_length=50, null=True, blank=True)

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
            models.Index(fields=["node_id"]),
        ]


class JobStatusManager(models.Manager):
    def singleton(self):
        return self.get_or_create(pk=1)[0]


class JobStatus(models.Model):
    objects = JobStatusManager()

    status = models.CharField(max_length=50, default="not_started", blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)

    def cancel(self):
        """
        Cancel the job by setting status to 'cancelled' and updating finished_at.
        """
        if self.celery_task_id:
            from celery import current_app

            # Cancel the Celery task if it exists
            task = current_app.AsyncResult(self.celery_task_id)
            if task:
                try:
                    task.revoke()
                except Exception as e:
                    logger.error(
                        f"Error cancelling Celery task {self.celery_task_id}: {e}"
                    )

        # Update job status
        self.status = "cancelled"
        self.finished_at = timezone.now()
        self.error_message = "Job was cancelled by user."
        self.save()


class LawLoadingStatus(models.Model):
    law = models.OneToOneField(
        Law,
        on_delete=models.SET_NULL,
        related_name="loading_status",
        null=True,
        blank=True,
    )
    eng_law_id = models.CharField(max_length=50, null=True, blank=True)
    STATUS_CHOICES = [
        ("pending_new", "Pending (New)"),
        ("pending_update", "Pending (Update)"),
        ("parsing_xml", "Parsing XML"),
        ("embedding_nodes", "Embedding Nodes"),
        ("finished_new", "Finished (New)"),
        ("finished_update", "Finished (Update)"),
        ("finished_nochange", "Finished (No Change)"),
        ("error", "Error"),
        ("deleted", "Deleted"),
        ("empty", "Empty"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(
        max_length=50, default="pending_new", choices=STATUS_CHOICES
    )
    details = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    cost = models.FloatField(default=0.0)
    sha_256_hash_en = models.CharField(max_length=64, null=True, blank=True)
    sha_256_hash_fr = models.CharField(max_length=64, null=True, blank=True)
