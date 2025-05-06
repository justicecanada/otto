import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
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

from chat.llm import OttoLLM
from otto.models import SecurityLabel, User
from otto.utils.common import display_cad_cost, set_costs

logger = get_logger(__name__)
llm = OttoLLM()

STATUS_CHOICES = [
    ("PENDING", "Not started"),
    ("INIT", "Starting..."),
    ("PROCESSING", "Processing..."),
    ("SUCCESS", "Success"),
    ("ERROR", "Error"),
    ("BLOCKED", "Stopped"),
]

PDF_EXTRACTION_CHOICES = [
    ("default", _("text only")),
    ("azure_read", _("OCR")),
    ("azure_layout", _("layout & OCR")),
]


def generate_uuid_hex():
    # We use the hex for compatibility with LlamaIndex table names
    # (Can't have dashes)
    return uuid.uuid4().hex


class LibraryManager(models.Manager):
    def get_default_library(self):
        try:
            return self.get_queryset().get(is_default_library=True)
        except Library.DoesNotExist:
            logger.error("Default 'Corporate' library not found")
            return None

    def reset_vector_store(self):
        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"

        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()

        metadata = reflection.Inspector.from_engine(engine)

        for table_name in metadata.get_table_names():
            session.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))

        session.commit()
        session.close()

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

    order = models.IntegerField(default=0)
    is_public = models.BooleanField(default=False)
    is_default_library = models.BooleanField(default=False)
    is_personal_library = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_personal_library", "-is_public", "order", "-created_at"]
        verbose_name_plural = "Libraries"

    def clean(self):
        self._validate_public_library()
        self._validate_default_library()
        self._validate_personal_library()
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

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def access(self):
        self.accessed_at = timezone.now()
        self.save()

    def __str__(self):
        return str(
            _("Chat uploads")
            if self.is_personal_library
            else (self.name or _("Untitled library"))
        )

    @transaction.atomic
    def delete(self, *args, **kwargs):
        self.reset(recreate=False)
        super().delete(*args, **kwargs)

    def process_all(self):
        for ds in self.data_sources.all():
            for document in ds.documents.all():
                document.process()

    def reset(self, recreate=True):
        db = settings.DATABASES["vector_db"]
        connection_string = f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"

        engine = create_engine(connection_string)
        Session = sessionmaker(bind=engine)
        session = Session()
        session.execute(text(f"DROP TABLE IF EXISTS data_{self.uuid_hex} CASCADE"))
        session.commit()
        session.close()
        if recreate:
            # This will create the vector store table
            llm.get_retriever(self.uuid_hex).retrieve("?")

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
            data_sources = self.data_sources.filter(
                chat__messages__isnull=False
            ).distinct()

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

    class Meta:
        ordering = ["order", "-created_at"]

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        from .tasks import delete_documents_from_vector_store

        delete_documents_from_vector_store.delay(
            [document.uuid_hex for document in self.documents.all()],
            self.library.uuid_hex,
        )
        super().delete(*args, **kwargs)

    def process_all(self):
        for document in self.documents.all():
            document.process()

    @property
    def label(self):
        if not self.library.is_personal_library:
            return str(self)
        chat_title = (
            self.chat.title if self.chat and self.chat.title else _("Untitled chat")
        )
        data_source_time = self.modified_at if not self.chat else self.chat.accessed_at
        return f"{chat_title} ({data_source_time.strftime('%y/%m/%d %I:%M %p')})"

    @property
    def short_label(self):
        if not self.library.is_personal_library:
            return str(self)
        chat_title = (
            self.chat.title if self.chat and self.chat.title else _("Untitled chat")
        )
        return chat_title


class Document(models.Model):
    """
    Result of adding a URL or uploading a file to chat or librarian modal.
    Corresponds to a document in the vector store.
    """

    class Meta:
        ordering = ["-created_at"]

    uuid_hex = models.CharField(
        default=generate_uuid_hex, editable=False, unique=True, max_length=32
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    status_details = models.TextField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=50, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    # Cost includes OpenAI embedding and (in some cases) Azure Document AI costs
    usd_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    # Document always associated with a single DataSource
    data_source = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="documents",
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
    # Filename stored here instead of in the File object since one file (hash)
    # may be uploaded under different filenames
    filename = models.CharField(max_length=500, null=True, blank=True)
    # File path as extracted from zip, email, etc. (e.g. "something.zip/inner-file.txt")
    file_path = models.TextField(null=True, blank=True)

    # Specific to PDF documents.
    # The extraction method *that was used* to extract text from the PDF
    pdf_extraction_method = models.CharField(
        max_length=40, null=True, blank=True, choices=PDF_EXTRACTION_CHOICES
    )

    def __str__(self):
        return self.name

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
    def celery_status_message(self):
        if self.celery_task_id:
            try:
                result = AsyncResult(self.celery_task_id)
                return result.info.get("status_text", "Processing...")
            except Exception as e:
                self.celery_task_id = None
                self.status = "ERROR"
                self.save()
            return "Error"
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

    def delete(self, *args, **kwargs):
        from .tasks import delete_documents_from_vector_store

        delete_documents_from_vector_store.delay(
            [self.uuid_hex], self.data_source.library.uuid_hex
        )
        super().delete(*args, **kwargs)

    def process(self, pdf_method="default", mock_embedding=False):
        from .tasks import process_document

        bind_contextvars(document_id=self.id)

        # Logic for updating the document embeddings, metadata, etc.
        if not (self.saved_file or self.url):
            self.status = "ERROR"
            self.save()
            return
        process_document.delay(self.id, get_language(), pdf_method, mock_embedding)
        self.celery_task_id = "tbd"
        self.status = "INIT"
        self.save()

    def stop(self):
        if self.celery_task_id:
            try:
                AsyncResult(self.celery_task_id).revoke(terminate=True)
            except Exception as e:
                logger.error(f"Failed to stop document processing task: {e}")
        self.celery_task_id = None
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
        if self.chat_files.exists() or self.documents.exists():
            logger.info(f"File {self.file.name} has associated objects; not deleting")
            return
        if self.file:
            self.file.delete(True)
        self.delete()


@receiver(post_delete, sender=DataSource)
def data_source_post_delete(sender, instance, **kwargs):
    try:
        # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
        Library.objects.filter(pk=instance.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Data source post delete error: {e}")


@receiver(post_save, sender=DataSource)
def data_source_post_save(sender, instance, **kwargs):
    try:
        # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
        Library.objects.filter(pk=instance.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Data source post save error: {e}")


@receiver(post_save, sender=Document)
def document_post_save(sender, instance, **kwargs):
    try:
        # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
        Library.objects.filter(pk=instance.data_source.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Document post save error: {e}")


@receiver(post_delete, sender=Document)
def document_post_delete(sender, instance, **kwargs):
    try:
        if instance.saved_file is not None:
            instance.saved_file.safe_delete()
        # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
        Library.objects.filter(pk=instance.data_source.library.pk).update(
            accessed_at=timezone.now()
        )
    except Exception as e:
        logger.error(f"Document post delete error: {e}")
