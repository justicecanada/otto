import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from celery.result import AsyncResult
from sqlalchemy import create_engine, text
from sqlalchemy.engine import reflection
from sqlalchemy.orm import sessionmaker
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import SecurityLabel, User
from otto.priorities import LOW
from otto.utils.common import display_cad_cost, set_costs

from chat.llm import OttoLLM
from librarian.cache import (
    clear_document_cache,
    clear_pending_embedding_chunks,
    get_celery_task_id,
    get_pending_embedding_chunks,
    set_celery_task_id,
)

logger = get_logger(__name__)
llm = OttoLLM()


def translate_status_text(status_text):
    """
    Translate status messages that were stored in English from Celery tasks.
    This allows status messages to be displayed in the user's language.
    """
    if not status_text:
        return status_text

    # Define translation mappings for known status messages
    translations = {
        "Fetching URL...": _("Fetching URL..."),
        "Reading file...": _("Reading file..."),
        "Converting .doc to .docx...": _("Converting .doc to .docx..."),
        "Extracting text...": _("Extracting text..."),
        "Submitting to Azure Document Intelligence...": _(
            "Submitting to Azure Document Intelligence..."
        ),
        "Waiting for Azure Document Intelligence...": _(
            "Waiting for Azure Document Intelligence..."
        ),
        "Parsing Azure response...": _("Parsing Azure response..."),
        "Processing...": _("Processing..."),
    }

    # Check for exact match
    if status_text in translations:
        return translations[status_text]

    # Check for "Adding to library..." patterns with progress
    if status_text.startswith("Adding to library..."):
        # Extract the progress part (e.g., "(10/100)" or "(10/100 - waiting)")
        base = _("Adding to library...")
        waiting_text = _("waiting")
        remainder = status_text[len("Adding to library...") :]
        if " - waiting)" in remainder:
            # Replace "waiting" with translated version
            parts = remainder.split(" - waiting)")
            if len(parts) == 2:
                return f"{base}{parts[0]} - {waiting_text}){parts[1]}"
        return f"{base}{remainder}"

    return status_text


STATUS_CHOICES = [
    ("PENDING", "Not started"),
    ("INIT", "Starting..."),
    ("PROCESSING", "Processing..."),
    ("TEXT_EXTRACTED", "Text extracted"),
    ("PAUSED", "Paused"),
    ("SUCCESS", "Success"),
    ("ERROR", "Error"),
    ("BLOCKED", "Stopped"),
]

PDF_EXTRACTION_CHOICES = [
    ("default", _("text only")),
    ("layout", _("text & layout")),
    ("azure_read", _("OCR")),
    ("azure_layout", _("OCR & layout")),
]


def generate_uuid_hex():
    # We use the hex for compatibility with LlamaIndex table names
    # (Can't have dashes)
    return uuid.uuid4().hex


class LibraryManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def including_deleted(self):
        return super().get_queryset()

    def get_default_library(self):
        try:
            return self.get_queryset().get(is_default_library=True)
        except Library.DoesNotExist:
            logger.error("Default 'Corporate' library not found")
            return None

    def reset_vector_store(self):
        """WARNING: This drops ALL vector store tables! Use with extreme caution."""
        logger.warning(
            "reset_vector_store called - this will drop ALL vector tables",
            table_count=self.count(),
        )

        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"

        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()

        metadata = reflection.Inspector.from_engine(engine)
        tables_dropped = []

        for table_name in metadata.get_table_names():
            session.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
            tables_dropped.append(table_name)

        session.commit()
        session.close()

        logger.warning(
            "Dropped all vector tables",
            tables_dropped=tables_dropped,
        )

    def create(self, *args, **kwargs):
        library = super().create(*args, **kwargs)
        library.reset()
        return library


class Library(models.Model):
    # Same as vector store table name
    uuid_hex = models.CharField(
        default=generate_uuid_hex, editable=False, unique=True, max_length=32
    )
    objects = LibraryManager()

    # Named libraries will show in a list; unnamed libraries are bound to a chat
    name = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    modified_at = models.DateTimeField(auto_now=True)
    # Last access time manually updated when library is queried through Library Q&A
    accessed_at = models.DateTimeField(auto_now_add=True)
    # Set when user deletes a library; hard deletion happens via nightly cleanup command
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    order = models.IntegerField(default=0)
    is_public = models.BooleanField(default=False)
    is_default_library = models.BooleanField(default=False)
    is_personal_library = models.BooleanField(default=False)
    is_skill_library = models.BooleanField(default=False)

    # HNSW index management
    hnsw_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="None=automatic, True=force on, False=force off",
    )
    HNSW_STATUS_CHOICES = [
        ("none", "No index"),
        ("pending", "Build pending"),
        ("building", "Building index"),
        ("ready", "Index ready"),
        ("deleting", "Deleting index"),
        ("error", "Build failed"),
    ]
    hnsw_status = models.CharField(
        max_length=20, choices=HNSW_STATUS_CHOICES, default="none"
    )
    hnsw_task_id = models.CharField(max_length=255, null=True, blank=True)
    total_chunks = models.IntegerField(default=0)

    class Meta:
        ordering = [
            "-is_personal_library",
            "-is_skill_library",
            "-is_public",
            "order",
            "-created_at",
        ]
        verbose_name_plural = "Libraries"
        indexes = [
            models.Index(
                fields=[
                    "-is_personal_library",
                    "-is_skill_library",
                    "-is_public",
                    "order",
                    "-created_at",
                ]
            ),
        ]

    def clean(self):
        self._validate_public_library()
        self._validate_default_library()
        self._validate_personal_library()
        self._validate_skill_library()
        super().clean()

    def _validate_public_library(self):
        if not self.is_public:
            return
        if not self.name:
            raise ValidationError("Public libraries must have a name")
        if (
            Library.objects.filter(is_public=True, name=self.name)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError("A public library with this name already exists")
        if self.is_personal_library:
            raise ValidationError("Personal libraries cannot be public libraries")

    def _validate_default_library(self):
        if not self.is_default_library:
            return
        elif (
            Library.objects.filter(is_default_library=True).exclude(pk=self.pk).exists()
        ):
            raise ValidationError("There can be only one default library")
        elif self.is_personal_library:
            raise ValidationError("Personal libraries cannot be default libraries")
        elif not self.is_public:
            raise ValidationError("Default libraries must be public libraries")

    def _validate_personal_library(self):
        if not self.is_personal_library:
            return
        if (
            Library.objects.filter(is_personal_library=True, created_by=self.created_by)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError(
                "There can be only one personal library for each user"
            )

    def _validate_skill_library(self):
        if not self.is_skill_library:
            return
        if self.is_public:
            raise ValidationError("Skill libraries cannot be public libraries")
        if self.is_personal_library:
            raise ValidationError("Skill libraries cannot also be personal libraries")
        if (
            Library.objects.filter(is_skill_library=True, created_by=self.created_by)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError("There can be only one skill library for each user")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def access(self):
        self.accessed_at = timezone.now()
        self.save()

    def __str__(self):
        if self.is_personal_library:
            return str(_("Chat files"))
        if self.is_skill_library:
            return str(_("Skill files"))
        return str(self.name or _("Untitled library"))

    def delete(self, *args, **kwargs):
        """Soft delete: mark the library as deleted. Hard deletion happens nightly."""
        for data_source in self.data_sources.all():
            data_source.stop_all_document_tasks()
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    @transaction.atomic
    def hard_delete(self):
        """Actually delete the library, vector table, and all related data."""
        # Stop tasks again in case any are still running when nightly cleanup runs.
        for data_source in self.data_sources.all():
            data_source.stop_all_document_tasks()
        self.reset(recreate=False, force=True)
        super().delete()

    def process_all(self):
        for ds in self.data_sources.all():
            for document in ds.documents.all():
                document.process()

    def reset(self, recreate=True, force=False):
        """Reset the vector store table for this library.

        WARNING: This drops the entire vector table, including all embeddings!
        Should only be called for new/empty libraries or during deletion.

        Args:
            recreate: If True, recreate the empty table after dropping
            force: If True, skip safety check for existing documents (use with caution!)
        """
        # Safety check: prevent accidental data loss
        if not force and self.pk:
            doc_count = Document.objects.filter(data_source__library=self).count()
            if doc_count > 0:
                logger.error(
                    "Attempted to reset library with existing documents - blocked",
                    library_id=self.id,
                    library_name=self.name,
                    document_count=doc_count,
                )
                raise ValueError(
                    f"Cannot reset library '{self.name}' - it has {doc_count} documents. "
                    "Delete documents first or use force=True to override (data will be lost)."
                )

        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"

        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text(f"DROP TABLE IF EXISTS data_{self.uuid_hex} CASCADE"))
        session.commit()
        session.close()

        logger.info(
            "Dropped vector table",
            library_id=self.id if self.pk else None,
            library_name=self.name if hasattr(self, "name") else None,
            table_name=f"data_{self.uuid_hex}",
            recreate=recreate,
        )

        if recreate:
            # This will create the vector store table - use get_index with skip_setup=False
            llm.get_index(self.uuid_hex, skip_setup=False)
        # Reset HNSW status after dropping table
        self.hnsw_status = "none"
        self.hnsw_task_id = None
        self.total_chunks = 0
        self.save(update_fields=["hnsw_status", "hnsw_task_id", "total_chunks"])

    def update_total_chunks(self):
        """Recalculate and cache total chunks from all documents in this library."""
        from django.db.models import Sum

        total = (
            Document.objects.filter(
                data_source__library=self, is_container=False
            ).aggregate(total=Sum("num_chunks"))["total"]
            or 0
        )
        self.total_chunks = total
        self.save(update_fields=["total_chunks"])
        return total

    def should_use_hnsw(self):
        """Determine if HNSW index should be used for this library.

        Returns True if:
        - hnsw_enabled is explicitly True, OR
        - hnsw_enabled is None (auto) AND total_chunks >= HNSW_THRESHOLD
        """
        if self.hnsw_enabled is not None:
            return self.hnsw_enabled  # Manual override
        return self.total_chunks >= settings.HNSW_THRESHOLD

    def use_hnsw_for_query(self):
        """Determine if HNSW index should be used for queries right now.

        Returns True only if:
        - The library should use HNSW (based on settings/threshold), AND
        - The HNSW index actually exists and is ready to use

        This is the method to use when deciding whether to pass hnsw=True to retriever methods.
        """
        return self.should_use_hnsw() and self.hnsw_status == "ready"

    def check_and_build_hnsw(self):
        """Check if HNSW should be built and trigger build if needed.

        Called after document additions to check if threshold was crossed.
        Only builds if status is 'none' and should_use_hnsw() is True.
        """
        if self.should_use_hnsw() and self.hnsw_status == "none":
            from librarian.tasks import build_hnsw_index

            self.hnsw_status = "pending"
            self.save(update_fields=["hnsw_status"])
            result = build_hnsw_index.delay(self.uuid_hex)
            self.hnsw_task_id = result.id
            self.save(update_fields=["hnsw_task_id"])
            logger.info(
                "HNSW build triggered",
                library_id=self.id,
                total_chunks=self.total_chunks,
                task_id=result.id,
            )

    @property
    def sorted_data_sources(self):
        return self.data_sources.all()

    @property
    def security_label(self):
        return SecurityLabel.maximum_of(
            self.data_sources.values_list("security_label__acronym", flat=True)
        )

    @property
    def admins(self):
        return self.user_roles.filter(role="admin").values_list("user", flat=True)

    @property
    def contributors(self):
        return self.user_roles.filter(role="contributor").values_list("user", flat=True)

    @property
    def viewers(self):
        return self.user_roles.filter(role="viewer").values_list("user", flat=True)

    @property
    def folders(self):
        if self.is_personal_library:
            # Personal libraries contain per-chat folders for both legacy chat and chat_next.
            # Only include chats that have at least one message, to avoid clutter from empty chats.
            # Order by most recently accessed chat first.
            from django.db.models.functions import Coalesce

            data_sources = (
                self.data_sources.filter(
                    Q(chat__messages__isnull=False)
                    | Q(chat_next__messages__isnull=False)
                )
                .distinct()
                .annotate(
                    _chat_accessed=Coalesce(
                        "chat__accessed_at", "chat_next__accessed_at", "modified_at"
                    )
                )
                .order_by("-_chat_accessed")
            )

        else:
            data_sources = self.data_sources.all()

        return data_sources.prefetch_related("security_label")


# AC-20: Allows for fine-grained control over who can access and manage information sources
class LibraryUserRole(models.Model):
    """
    Represents a user's role in a library.
    """

    # AC-21: Allows for the assignment of different roles to users
    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("contributor", "Contributor"),
        ("viewer", "Viewer"),
    ]

    library = models.ForeignKey(
        Library, on_delete=models.CASCADE, related_name="user_roles"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="library_roles"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    class Meta:
        unique_together = ["library", "user"]

    def __str__(self):
        return f"{self.user} in {self.library}: {self.role}"


class LibraryTeamRole(models.Model):
    """Represents a team's role in a library."""

    ROLE_CHOICES = LibraryUserRole.ROLE_CHOICES

    library = models.ForeignKey(
        Library, on_delete=models.CASCADE, related_name="team_roles"
    )
    team = models.ForeignKey(
        "otto.Team", on_delete=models.CASCADE, related_name="library_roles"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    class Meta:
        unique_together = ["library", "team"]

    def __str__(self):
        return f"{self.team} in {self.library}: {self.role}"


class DataSourceManager(models.Manager):
    def create(self, *args, **kwargs):
        # Set the security label default to "UC" (unclassified)

        # We do this instead of setting a default value on 'security_label' because
        # this is a reference to an instance of the SecurityLabel model which
        # may not exist at migration time
        kwargs["security_label_id"] = SecurityLabel.default_security_label().id
        return super().create(*args, **kwargs)


class DataSource(models.Model):
    """
    Represents sub-library "collection" of documents.
    """

    # UUID is used for filtering in the vector store
    uuid_hex = models.CharField(
        default=generate_uuid_hex, editable=False, unique=True, max_length=32
    )
    objects = DataSourceManager()
    name = models.CharField(max_length=255)
    library = models.ForeignKey(
        Library, on_delete=models.CASCADE, related_name="data_sources"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    order = models.IntegerField(default=0)

    # AC-21: Allow users to categorize sensitive information
    security_label = models.ForeignKey(
        SecurityLabel,
        on_delete=models.SET_NULL,
        null=True,
    )

    chat = models.OneToOneField(
        "chat.Chat",
        on_delete=models.CASCADE,  # This will delete DataSource when Chat is deleted
        related_name="data_source",
        null=True,
    )

    chat_next = models.OneToOneField(
        "chat_next.Chat",
        on_delete=models.CASCADE,  # This will delete DataSource when Chat is deleted
        related_name="data_source",
        null=True,
    )

    skill = models.OneToOneField(
        "chat_next.Skill",
        on_delete=models.CASCADE,  # This will delete DataSource when Skill is deleted
        related_name="data_source",
        null=True,
    )

    class Meta:
        ordering = ["order", "-created_at"]
        indexes = [
            models.Index(fields=["order", "-created_at"]),
        ]

    def __str__(self):
        return self.name

    def stop_all_document_tasks(self) -> list[str]:
        """Utility to stop/revoke celery tasks for all documents in this data source. Returns list of UUIDs of all documents in data source."""
        uuids = []

        document_qs = self.documents.all().only("uuid_hex")
        for doc in document_qs:
            doc.stop_document_task()
            uuids.append(doc.uuid_hex)

        return uuids

    def delete(self, *args, **kwargs):
        from .tasks import delete_documents_from_vector_store

        uuids = self.stop_all_document_tasks()
        if uuids:
            delete_documents_from_vector_store.delay(uuids, self.library.uuid_hex)
        super().delete(*args, **kwargs)

    def process_all(self):
        for document in self.documents.all():
            document.process()

    @property
    def chat_obj(self):
        """Return the associated chat instance (legacy chat or chat_next), if any."""
        return self.chat or self.chat_next

    @property
    def is_chat_folder(self) -> bool:
        return bool(self.chat_obj)

    @property
    def is_skill_folder(self) -> bool:
        return bool(self.skill_id)

    @property
    def chat_title(self) -> str:
        """Human-friendly chat title for personal-library chat folders."""
        chat = self.chat_obj
        if chat and getattr(chat, "title", ""):
            return chat.title
        return str(_("Untitled chat"))

    @property
    def chat_accessed_at(self):
        """Timestamp used for displaying personal-library chat folders."""
        chat = self.chat_obj
        # Fall back to the data source modified time for non-chat folders.
        return getattr(chat, "accessed_at", None) or self.modified_at

    @property
    def label(self):
        if not self.library.is_personal_library:
            return str(self)
        chat_title = self.chat_title
        data_source_time = self.chat_accessed_at
        return f"{chat_title} ({data_source_time.strftime('%y/%m/%d %I:%M %p')})"

    @property
    def short_label(self):
        if not self.library.is_personal_library:
            return str(self)
        return self.chat_title


class Document(models.Model):
    """
    Result of adding a URL or uploading a file to chat or librarian modal.
    Corresponds to a document in the vector store.
    """

    PROVENANCE_UNKNOWN = "unknown"
    PROVENANCE_USER_UPLOAD = "user_upload"
    PROVENANCE_URL_RETRIEVAL = "url_retrieval"
    PROVENANCE_GENERATED_OUTPUT = "generated_output"
    PROVENANCE_CHOICES = [
        (PROVENANCE_UNKNOWN, "Unknown"),
        (PROVENANCE_USER_UPLOAD, "User upload"),
        (PROVENANCE_URL_RETRIEVAL, "URL retrieval"),
        (PROVENANCE_GENERATED_OUTPUT, "Generated output"),
    ]

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Index for ordering by creation date across all documents
            models.Index(fields=["-created_at"]),
            # Index for filtering documents by data_source and status
            models.Index(fields=["data_source", "status"]),
            # Index for filtering non-container documents
            models.Index(fields=["data_source", "is_container"]),
            # Index for ordering by creation date within a data_source
            models.Index(fields=["data_source", "-created_at"]),
            # Index for sorting documents by extracted_modified_at or created_at
            models.Index(
                fields=["data_source", "-extracted_modified_at", "-created_at"]
            ),
            # Index for sorting documents by filename
            models.Index(fields=["data_source", "filename"]),
            # Index for sorting documents by num_chunks
            models.Index(fields=["data_source", "num_chunks"]),
        ]

    uuid_hex = models.CharField(
        default=generate_uuid_hex, editable=False, unique=True, max_length=32
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    status_details = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    # Cost includes OpenAI embedding and (in some cases) Document Intelligence OCR costs
    usd_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # Document associated with a single DataSource if Q&A upload, otherwise None
    data_source = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
    )

    # Flag for container documents (ZIP files, etc.) that should not be added to vector DB
    # These documents exist to provide metadata but their content shouldn't be in RAG results
    is_container = models.BooleanField(default=False)

    # All messages this document is attached to (supports dedup within a data source)
    # For legacy chat app
    messages = models.ManyToManyField(
        "chat.Message",
        related_name="attached_documents",
        blank=True,
    )

    # For new chat_next app (parallel development)
    chat_next_messages = models.ManyToManyField(
        "chat_next.Message",
        related_name="attached_documents",
        blank=True,
    )

    # Extracted title may come from HTML <title>, PDF metadata, etc.
    extracted_title = models.CharField(max_length=500, null=True, blank=True)

    # Last modified time of the document as extracted from the source metadata, etc.
    extracted_modified_at = models.DateTimeField(null=True, blank=True)

    # Generated title and description from LLM
    generated_title = models.CharField(max_length=500, null=True, blank=True)
    generated_description = models.TextField(null=True, blank=True)

    # User-provided citation; has precedence over extracted_title etc.
    manual_title = models.CharField(max_length=500, null=True, blank=True)

    # Not necessary to store permanently in this model; saved in vector DB chunks
    extracted_text = models.TextField(null=True, blank=True)
    num_chunks = models.IntegerField(null=True, blank=True)

    # Specific to URL-based documents
    url = models.URLField(null=True, blank=True)
    selector = models.CharField(max_length=255, null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    url_content_type = models.CharField(max_length=255, null=True, blank=True)

    # Specific to file-based documents
    saved_file = models.ForeignKey(
        "SavedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )
    original_saved_file = models.ForeignKey(
        "SavedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="original_documents",
    )
    # Filename stored here instead of in the File object since one file (hash)
    # may be uploaded under different filenames
    filename = models.CharField(max_length=500, null=True, blank=True)
    original_filename = models.CharField(max_length=500, null=True, blank=True)
    provenance = models.CharField(
        max_length=32,
        choices=PROVENANCE_CHOICES,
        default=PROVENANCE_UNKNOWN,
        db_index=True,
    )
    # File path as extracted from zip, email, etc. (e.g. "something.zip/inner-file.txt")
    file_path = models.TextField(null=True, blank=True)

    # For documents extracted from containers (ZIP, MSG, EML), reference to parent document
    parent_document = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="child_documents",
    )

    # Specific to PDF documents.
    # The extraction method *that was used* to extract text from the PDF
    pdf_extraction_method = models.CharField(
        max_length=40, null=True, blank=True, choices=PDF_EXTRACTION_CHOICES
    )

    def __str__(self):
        return self.name

    def _infer_original_file_fields_from_saved_file(self):
        if self.original_saved_file_id or not self.saved_file_id:
            return

        derivative = (
            self.saved_file.source_derivatives.select_related("source_saved_file")
            .order_by("-created_at")
            .first()
        )
        if derivative:
            self.original_saved_file = derivative.source_saved_file
            if not self.original_filename:
                self.original_filename = (
                    derivative.derivation_params.get("source_filename")
                    if derivative.derivation_params
                    else None
                ) or self.filename
            return

        self.original_saved_file = self.saved_file
        if not self.original_filename and self.filename:
            self.original_filename = self.filename

    def save(self, *args, **kwargs):
        self._infer_original_file_fields_from_saved_file()
        super().save(*args, **kwargs)

    @property
    def title(self):
        return self.manual_title or self.extracted_title or self.generated_title or None

    @property
    def name(self):
        return self.title or self.filename or self.url or "Untitled document"

    @property
    def pdf_method(self):
        method = self.pdf_extraction_method
        return dict(PDF_EXTRACTION_CHOICES).get(method, method)

    @property
    def processed_saved_file(self):
        if self.saved_file_id and self.original_saved_file_id:
            if self.saved_file_id != self.original_saved_file_id:
                return self.saved_file
        return None

    @property
    def has_processed_file(self):
        return self.processed_saved_file is not None

    @property
    def celery_task_id(self):
        return get_celery_task_id(self.id)

    @property
    def celery_status_message(self):
        task_id = get_celery_task_id(self.id)
        if not task_id:
            return None
        try:
            result = AsyncResult(task_id)
            info = getattr(result, "info", None)
            # Celery may return None or a non-dict for info during handoffs; be defensive
            if isinstance(info, dict):
                status_text = info.get("status_text")
                if status_text:
                    return translate_status_text(status_text)
                return _("Processing...")
            # Fall back to default spinner text in templates
            return None
        except Exception:
            # Do not mutate document status from a read-only property.
            # Transient backend issues or task handoffs can raise here; let tasks set ERROR.
            return None

    @property
    def href(self):
        return render_to_string(
            "librarian/components/document_href.html", {"document": self}
        )

    @property
    def href_button(self):
        return render_to_string(
            "librarian/components/document_href.html",
            {"document": self, "button": True},
        )

    @property
    def truncated_text(self):
        if self.extracted_text:
            truncated_text = self.extracted_text[:500]
            if len(self.extracted_text) > 500:
                truncated_text += "..."
            return truncated_text
        return ""

    @property
    def display_cost(self):
        return display_cad_cost(self.usd_cost)

    @property
    def content_type(self):
        if self.saved_file:
            return self.saved_file.content_type
        else:
            return self.url_content_type

    @property
    def file_exists(self):
        """Check if the underlying file exists on storage."""
        try:
            if self.saved_file and self.saved_file.file:
                return self.saved_file.file.storage.exists(self.saved_file.file.name)
        except Exception:
            pass
        return False

    @property
    def original_file_exists(self):
        """Check if the original/source file exists on storage."""
        try:
            if self.original_saved_file and self.original_saved_file.file:
                return self.original_saved_file.file.storage.exists(
                    self.original_saved_file.file.name
                )
        except Exception:
            pass
        return False

    @property
    def file_size(self):
        try:
            if self.saved_file and self.saved_file.file:
                return self.saved_file.file.size
        except (FileNotFoundError, OSError):
            pass
        return None

    def stop_document_task(self) -> None:
        """
        Stop/revoke a currently running celery task associated with the given document.
        """
        task_id = get_celery_task_id(self.id)
        if task_id:
            try:
                from otto.celery import app

                app.control.revoke(task_id=task_id, terminate=True)
                set_celery_task_id(self.id, None)
                logger.info(f"Revoked celery task {task_id} for document {self.id}")
            except Exception as e:
                logger.error(f"Error revoking celery task {task_id}: {e}")

    def delete(self, *args, **kwargs):
        from .tasks import delete_documents_from_vector_store

        self.stop_document_task()
        if self.data_source:
            delete_documents_from_vector_store.delay(
                [self.uuid_hex], self.data_source.library.uuid_hex
            )
        super().delete(*args, **kwargs)

    def process(
        self,
        pdf_method="default",
        mock_embedding=False,
        priority=LOW,
        finalization_priority=None,
        refresh_from_url=False,
    ):
        from structlog.contextvars import get_contextvars

        from .tasks import process_document

        bind_contextvars(document_id=self.id)

        # Logic for updating the document embeddings, metadata, etc.
        if not (self.saved_file or self.url):
            self.status = "ERROR"
            self.save()
            return
        # Set status to PROCESSING and enqueue heavy task directly, capturing task id
        self.status = "PROCESSING"
        self.status_details = None
        self.save(update_fields=["status", "status_details"])

        # Capture user_id and cost_group_id from current context to pass to Celery task
        request_context = get_contextvars()
        user_id = request_context.get("user_id")
        cost_group_id = request_context.get("cost_group_id")

        res = process_document.apply_async(
            kwargs={
                "document_id": self.id,
                "language": get_language(),
                "pdf_method": pdf_method,
                "mock_embedding": mock_embedding,
                "priority": priority,
                "finalization_priority": finalization_priority,
                "user_id": user_id,
                "cost_group_id": cost_group_id,
                "refresh_from_url": bool(refresh_from_url),
            },
            priority=priority,
        )
        set_celery_task_id(self.id, res.id)

    def start_manual_embedding(self, mock_embedding=False, priority=LOW):
        from librarian.tasks import finalize_document_light
        from librarian.utils.process_engine import (
            get_process_engine_from_type,
            split_markdown_into_chunks,
        )

        if not self.extracted_text:
            self.status = "ERROR"
            self.status_details = _(
                "No extracted text is available yet. Re-process this document before embedding it."
            )
            self.save(update_fields=["status", "status_details"])
            return None

        chunks = get_pending_embedding_chunks(self.id)
        if chunks is None:
            chunks = split_markdown_into_chunks(
                self.extracted_text,
                process_engine=get_process_engine_from_type(self.content_type or ""),
                pdf_method=self.pdf_extraction_method,
            )

        clear_pending_embedding_chunks(self.id)
        self.status = "TEXT_EXTRACTED"
        self.status_details = None
        self.save(update_fields=["status", "status_details"])

        res = finalize_document_light.apply_async(
            kwargs={
                "document_id": self.id,
                "chunks": chunks,
                "mock_embedding": mock_embedding,
            },
            priority=priority,
        )
        set_celery_task_id(self.id, res.id)
        return res

    def stop(self):
        task_id = get_celery_task_id(self.id)
        if task_id:
            try:
                AsyncResult(task_id).revoke(terminate=True)
            except Exception as e:
                logger.error(f"Failed to stop document processing task: {e}")
        set_celery_task_id(self.id, None)
        self.status = "BLOCKED"
        self.save()

    def calculate_costs(self):
        set_costs(self)


class SavedFile(models.Model):
    """
    Represents a file uploaded by the user.
    This object is referenced by 0..* Document or ChatFile objects.
    """

    sha256_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    file = models.FileField(upload_to="files/%Y/%m/%d/", max_length=500)
    content_type = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    eof = models.BooleanField(default=True)
    openai_file_id = models.CharField(blank=True, max_length=255, null=True)

    def __str__(self):
        return self.file.name

    def generate_hash(self):
        from librarian.utils.process_engine import generate_hash

        if self.file:
            with self.file.open("rb") as f:
                self.sha256_hash = generate_hash(f)
                self.save()
        return self.sha256_hash

    def safe_delete(self):
        if (
            self.chat_files.exists()
            or self.chat_next_files.exists()
            or self.documents.exists()
            or self.original_documents.exists()
            or self.glossary_options.exists()
            or self.derived_files.exists()
            or self.source_derivatives.exists()
        ):
            logger.info(f"File {self.file.name} has associated objects; not deleting")
            return False
        # Delete from OpenAI Files API if uploaded there
        if self.openai_file_id:
            from chat_next.tasks import delete_openai_file_async

            delete_openai_file_async.delay(self.openai_file_id)
        if self.file:
            self.file.delete(True)
        self.delete()
        return True


class SavedFileDerivative(models.Model):
    """Metadata describing a derived SavedFile generated from a source SavedFile."""

    source_saved_file = models.ForeignKey(
        SavedFile,
        on_delete=models.CASCADE,
        related_name="derived_files",
    )
    derived_saved_file = models.ForeignKey(
        SavedFile,
        on_delete=models.CASCADE,
        related_name="source_derivatives",
    )
    derivation_type = models.CharField(max_length=64, db_index=True)
    derivation_version = models.CharField(max_length=32, default="1")
    derivation_params = models.JSONField(default=dict, blank=True)
    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_saved_file", "derivation_type"]),
            models.Index(fields=["derived_saved_file"]),
        ]

    def __str__(self):
        return (
            f"{self.derivation_type}:{self.source_saved_file_id}"
            f"->{self.derived_saved_file_id}"
        )


@receiver(post_delete, sender=DataSource)
def data_source_post_delete(sender, instance, **kwargs):
    try:
        # Update accessed_at so the library's retention window resets.
        Library.objects.filter(pk=instance.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Data source post delete error: {e}")


@receiver(pre_delete, sender=DataSource)
def data_source_pre_delete_preserve_skill_references(sender, instance, **kwargs):
    """Preserve chat-backed skill references before DataSource documents cascade-delete."""
    if not getattr(instance, "chat_next_id", None):
        return

    try:
        from chat_next.chat_deletion import preserve_skill_referenced_chat_files

        preserve_skill_referenced_chat_files(instance)
    except Exception as e:
        logger.exception(
            "Failed to preserve skill-referenced files before data source deletion",
            data_source_id=instance.id,
            chat_next_id=instance.chat_next_id,
            error=str(e),
        )


@receiver(post_save, sender=DataSource)
def data_source_post_save(sender, instance, **kwargs):
    try:
        # Update accessed_at so the library's retention window resets.
        Library.objects.filter(pk=instance.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Data source post save error: {e}")


@receiver(post_save, sender=Document)
def document_post_save(sender, instance, **kwargs):
    try:
        if instance.data_source:
            # Update accessed_at so the library's retention window resets.
            Library.objects.filter(pk=instance.data_source.library.pk).update(
                accessed_at=timezone.now()
            )
    except Exception as e:
        logger.error(f"Document post save error: {e}")


@receiver(post_delete, sender=Document)
def document_post_delete(sender, instance, **kwargs):
    try:
        clear_document_cache(instance.id)
        files_to_release = []
        if instance.saved_file is not None:
            files_to_release.append(instance.saved_file)
        if (
            instance.original_saved_file is not None
            and instance.original_saved_file_id != instance.saved_file_id
        ):
            files_to_release.append(instance.original_saved_file)

        for saved_file in files_to_release:
            saved_file.safe_delete()
        if DataSource.objects.filter(id=instance.data_source_id):
            # Update accessed_at so the library's retention window resets.
            Library.objects.filter(pk=instance.data_source.library.pk).update(
                accessed_at=timezone.now()
            )
    except Exception as e:
        logger.error(f"Document post delete error: {e}")
