from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger

from chat.llm import OttoLLM
from librarian.tasks import delete_documents_from_vector_store

logger = get_logger(__name__)


class LawManager(models.Manager):
    def create_or_update_from_documents(
        self,
        law_status,
        document_en,
        document_fr,
    ):
        """
        Create or update a Law object from document metadata.
        Does NOT handle vector store embedding - that's done in insert_law_chunks task.
        """
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

        # NOTE: Don't set sha_256_hash fields yet - only after successful vector store operations
        obj.eng_law_id = law_status.eng_law_id

        obj.full_clean()
        obj.save()

        # Update law_status to link to the law object
        if not law_status.law:
            law_status.law = obj
            law_status.save()

        return obj

    def purge(self, keep_ids):
        """
        Delete any Law objects where law.eng_law_id is not in law_ids
        """
        if not keep_ids:
            return

        to_delete = self.exclude(eng_law_id__in=set(keep_ids))

        # Create LawLoadingStatus objects for purged laws and collect node_ids for cleanup
        node_ids_to_delete = []
        for law in to_delete:
            LawLoadingStatus.objects.create(
                law=None,
                eng_law_id=law.eng_law_id,
                status="deleted",
                finished_at=timezone.now(),
                details="Existing law not present in the list of laws to load",
            )
            # Collect node IDs for vector store cleanup
            if law.node_id_en:
                node_ids_to_delete.append(law.node_id_en)
            if law.node_id_fr:
                node_ids_to_delete.append(law.node_id_fr)

        purged_count = int(to_delete.count())
        if purged_count > 0:
            logger.info(f"Purging {purged_count} Law objects not in keep_ids")
            # Call delete manually on each law since we override delete method
            for law in to_delete:
                law.delete()
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
    eng_law_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)

    objects = LawManager()

    @property
    def type_label(self):
        return _("Regulation") if self.type == "regulation" else _("Act")

    def __str__(self):
        return self.title

    def delete(self, *args, **kwargs):
        """
        Delete the law and clean up associated vector store entries.
        """
        # Use the existing librarian task to clean up vector store
        # Pass node_ids as document_uuids and use laws_lois__ as library_uuid
        try:
            delete_documents_from_vector_store(
                [self.node_id_en, self.node_id_fr], "laws_lois__"
            )
        except Exception as e:
            logger.error(
                f"Error deleting nodes from vector store for law {self.eng_law_id}: {e}"
            )

        super().delete(*args, **kwargs)

    @classmethod
    def reset(cls):
        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text("DROP TABLE IF EXISTS data_laws_lois__ CASCADE"))
        session.commit()
        session.close()
        cls.objects.all().delete()
        # Recreate the table by calling llm get_index
        cls.get_index()

    @classmethod
    def get_index(cls):
        idx = OttoLLM().get_index("laws_lois__", hnsw=False)
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

    STATUS_CHOICES = [
        ("cancelled", {"en": "Cancelled", "fr": "Annulé"}),
        ("started", {"en": "Started", "fr": "Commencé"}),
        ("downloading", {"en": "Downloading", "fr": "Téléchargement"}),
        ("resetting", {"en": "Resetting", "fr": "Réinitialisation"}),
        ("purging", {"en": "Purging", "fr": "Purge"}),
        ("checking_existing", {"en": "Checking Existing", "fr": "Vérification"}),
        (
            "generating_hashes",
            {"en": "Generating Hashes", "fr": "Génération de hachages"},
        ),
        ("loading_laws", {"en": "Loading Laws", "fr": "Chargement des lois"}),
        (
            "rebuilding_indexes",
            {"en": "Rebuilding Indexes", "fr": "Reconstructions des index"},
        ),
        ("finished", {"en": "Finished", "fr": "Terminé"}),
        ("cancelled", {"en": "Cancelled", "fr": "Annulé"}),
        ("not_started", {"en": "Not started", "fr": "Non commencé"}),
        ("error", {"en": "Error", "fr": "Erreur"}),
    ]
    status = models.CharField(
        max_length=50,
        default="not_started",
        blank=True,
        choices=[(k, v["en"]) for k, v in STATUS_CHOICES],
    )

    @property
    def status_label(self):
        from django.utils.translation import get_language

        lang = get_language()
        for k, v in self.STATUS_CHOICES:
            if k == self.status:
                if lang and lang.startswith("fr"):
                    return v["fr"]
                return v["en"]
        return self.status

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    options = models.JSONField(default=dict, blank=True)

    def cancel(self):
        """
        Cancel the job by setting status to 'cancelled' and updating finished_at.
        Also marks all pending/in-progress LawLoadingStatus entries as cancelled
        and revokes any spawned Celery tasks to prevent queue churn.
        """
        if self.celery_task_id:
            from celery import current_app

            # Cancel the Celery task if it exists
            task = current_app.AsyncResult(self.celery_task_id)
            if task:
                try:
                    task.revoke(terminate=True)
                except Exception as e:
                    logger.error(
                        f"Error cancelling Celery task {self.celery_task_id}: {e}"
                    )

        # Bulk-revoke spawned child tasks so workers skip them immediately
        spawned_ids = (self.options or {}).get("spawned_task_ids", [])
        if spawned_ids:
            from celery import current_app

            try:
                current_app.control.revoke(spawned_ids)
                logger.info(f"Revoked {len(spawned_ids)} spawned tasks from queue")
            except Exception as e:
                logger.error(f"Error revoking spawned tasks: {e}")

        # Update job status
        self.status = "cancelled"
        self.finished_at = timezone.now()
        self.error_message = "Job was cancelled by user."
        self.save()

        # Mark all pending/in-progress law loading statuses as cancelled
        from django.db.models import Q

        pending_statuses = LawLoadingStatus.objects.filter(
            Q(finished_at__isnull=True)
            | Q(
                status__in=[
                    "parsing_xml",
                    "embedding_nodes",
                    "pending_new",
                    "pending_update",
                ]
            )
        )

        cancelled_count = pending_statuses.update(
            status="cancelled",
            finished_at=timezone.now(),
            error_message="Job was cancelled by user.",
        )

        if cancelled_count > 0:
            logger.info(f"Marked {cancelled_count} law loading statuses as cancelled")

    @property
    def options_str(self):
        """
        Return a string representation of the options JSON field.
        """
        if not self.options:
            return "No options provided"
        internal_option_keys = {"spawned_task_ids"}
        visible_options = {
            k: v for k, v in self.options.items() if k not in internal_option_keys
        }
        if not visible_options:
            return "No options provided"
        return ", ".join(f"{k}: {v}" for k, v in visible_options.items())


class LawLoadingStatus(models.Model):
    @property
    def details_label(self):
        from django.utils.translation import get_language

        lang = get_language()
        details_map = {
            "NULL hashes - assuming needs update": "Hachages NULL - mise à jour nécessaire",
            "No changes detected": "Aucun changement détecté",
            "No changes detected - forced update": "Aucun changement détecté - mise à jour forcée",
            "Changes detected - update": "Changements détectés - mise à jour",
            "Existing law deleted due to now being empty": "La loi existante a été supprimée car elle est maintenant vide",
            "Law updated successfully": "Loi mise à jour avec succès",
            "New law added successfully": "Nouvelle loi ajoutée avec succès",
            "Existing law not present in the list of laws to load": "La loi existante n'est pas présente dans la liste des lois à charger",
            "New law": "Nouvelle loi",
        }

        if lang and lang.startswith("fr"):
            import re

            if self.details in details_map:
                return details_map[self.details]

            emebedding_re = re.compile(r"^(.*) \(embedding batch (\d+)/(\d+)\)$")
            match = emebedding_re.match(self.details or "")
            if match:
                base, batch_num, total_batches = match.groups()
                base_fr = details_map.get(base, base)
                return f"{base_fr} (intégration {batch_num}/{total_batches})"
        return self.details

    law = models.OneToOneField(
        Law,
        on_delete=models.SET_NULL,
        related_name="loading_status",
        null=True,
        blank=True,
    )
    eng_law_id = models.CharField(max_length=50, null=True, blank=True)
    STATUS_CHOICES = [
        ("pending_new", {"en": "Pending (New)", "fr": "En attente (Nouveau)"}),
        (
            "pending_update",
            {"en": "Pending (Update)", "fr": "En attente (Mise à jour)"},
        ),
        ("parsing_xml", {"en": "Parsing XML", "fr": "Analyse XML"}),
        ("embedding_nodes", {"en": "Embedding Nodes", "fr": "Intégration des noeuds"}),
        ("finished_new", {"en": "Finished (New)", "fr": "Terminé (Nouveau)"}),
        ("finished_update", {"en": "Finished (Update)", "fr": "Terminé (Mise à jour)"}),
        (
            "finished_nochange",
            {"en": "Finished (No Change)", "fr": "Terminé (Aucun changement)"},
        ),
        ("error", {"en": "Error", "fr": "Erreur"}),
        ("deleted", {"en": "Deleted", "fr": "Supprimé"}),
        ("empty", {"en": "Empty", "fr": "Vide"}),
        ("cancelled", {"en": "Cancelled", "fr": "Annulé"}),
    ]
    status = models.CharField(
        max_length=50,
        default="pending_new",
        choices=[(k, v["en"]) for k, v in STATUS_CHOICES],
    )

    @property
    def status_label(self):
        from django.utils.translation import get_language

        lang = get_language()
        for k, v in self.STATUS_CHOICES:
            if k == self.status:
                if lang and lang.startswith("fr"):
                    return v["fr"]
                return v["en"]
        return self.status

    details = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    cost = models.FloatField(default=0.0)
    sha_256_hash_en = models.CharField(max_length=64, null=True, blank=True)
    sha_256_hash_fr = models.CharField(max_length=64, null=True, blank=True)
