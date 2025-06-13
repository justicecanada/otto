import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import BooleanField, Q, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from data_fetcher.util import get_request
from structlog import get_logger

from chat.models import SHARING_OPTIONS
from librarian.models import SavedFile
from otto.models import User

logger = get_logger(__name__)


class TemplateManager(models.Manager):
    def get_accessible_templates(self, user: User, language: str = None):
        ordering = [
            "-sharing_option",
        ]

        templates = self.filter(
            Q(owner=user) | Q(accessible_to=user) | Q(sharing_option="everyone"),
        )
        return templates.distinct().order_by(*ordering)


class Template(models.Model):
    objects = TemplateManager()

    # Metadata
    name_en = models.CharField(max_length=255, blank=True)
    name_fr = models.CharField(max_length=255, blank=True)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )

    sharing_option = models.CharField(
        max_length=10,
        choices=SHARING_OPTIONS,
        default="private",
    )
    accessible_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="accessible_templates"
    )

    # Schema and example output generated from TemplateFields.
    # Use TextField instead of JSONField since we don't query the schema in the database.
    generated_schema = models.TextField(null=True)

    # Store the last test extraction result and timestamp
    last_example_source = models.ForeignKey(
        "Source", null=True, blank=True, on_delete=models.SET_NULL
    )
    fields_modified_timestamp = models.DateTimeField(null=True, blank=True)
    layout_modified_timestamp = models.DateTimeField(null=True, blank=True)
    last_test_fields_timestamp = models.DateTimeField(null=True, blank=True)
    last_test_layout_timestamp = models.DateTimeField(null=True, blank=True)

    # Template rendering
    layout_jinja = models.TextField(null=True, blank=True)
    docx_template = models.ForeignKey(SavedFile, on_delete=models.SET_NULL, null=True)

    @property
    def shared_with(self):
        if self.sharing_option == "everyone":
            return _("Shared with everyone")
        elif self.sharing_option == "others":
            return _("Shared with others")
        return _("Private")

    @property
    def description_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            description = self.description_fr or self.description_en
        else:
            description = self.description_en or self.description_fr
        return description or _("No description available")

    def __str__(self):
        return f"Template {self.id}: {self.name_en}"

    @property
    def name_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            return self.name_fr or self.name_en
        else:
            return self.name_en or self.name_fr

    @property
    def example_session(self):
        return self.sessions.filter(is_example_session=True).first()

    @property
    def example_sources(self):
        session = self.example_session
        if session:
            return session.sources.filter(is_example_template=False)
        return Source.objects.none()

    @property
    def example_source(self):
        """
        Returns the first example source for the template, if it exists.
        """
        session = self.example_session
        if session:
            return session.sources.filter(is_example_template=False).first()
        return None

    @property
    def example_template(self):
        session = self.example_session
        if session:
            return session.sources.filter(is_example_template=True).first()
        return None


class SourceStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    EXTRACTING_TEXT = "extracting_text", _("Extracting text")
    EXTRACTING_FIELDS = "extracting_fields", _("Extracting fields")
    FILLING_TEMPLATE = "filling_template", _("Filling template")
    COMPLETED = "completed", _("Completed")
    ERROR = "error", _("Error")


class Source(models.Model):
    text = models.TextField(null=True)
    status = models.CharField(
        max_length=50,
        choices=SourceStatus.choices,
        default=SourceStatus.PENDING,
    )
    extracted_json = models.JSONField(null=True)  # JSON from LLM
    template_result = models.TextField(null=True)  # Result from template rendering
    session = models.ForeignKey(
        "TemplateSession",
        on_delete=models.CASCADE,
        related_name="sources",
        null=True,
        blank=True,
    )
    url = models.URLField(
        max_length=2000,
        null=True,
        blank=True,
    )
    filename = models.CharField(
        max_length=2000,
        null=True,
        blank=True,
    )
    saved_file = models.ForeignKey(
        SavedFile,
        on_delete=models.SET_NULL,
        related_name="template_sources",
        null=True,
        blank=True,
    )
    is_example_template = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def get_status_display(self):
        """
        Return the human-readable label for the status field using SourceStatus choices.
        """
        return dict(SourceStatus.choices).get(self.status, self.status) or _(
            "Unknown status"
        )

    @property
    def name(self):
        return self.filename or self.url or _("Unnamed source")


@receiver(post_delete, sender=Source)
def delete_saved_file(sender, instance, **kwargs):
    try:
        instance.saved_file.safe_delete()
    except Exception as e:
        logger.error(f"Failed to delete saved file: {e}")


class TemplateSessionStatus(models.TextChoices):
    SELECT_SOURCES = "select_sources", _("Selecting")
    FILL_TEMPLATE = "fill_template", _("Processing")
    ERROR = "error", _("Error")
    COMPLETED = "completed", _("Completed")


class TemplateSession(models.Model):
    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name="sessions"
    )
    is_example_session = models.BooleanField(
        default=False,
        help_text=_("Contains examples for use during template creation"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="template_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_accessed = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=50,
        choices=TemplateSessionStatus.choices,
        default=TemplateSessionStatus.SELECT_SOURCES,
    )

    def __str__(self):
        return f"Session for {self.template.name_auto} by {self.user.username} at {self.created_at}"

    def get_status_display(self):
        """
        Return the human-readable label for the status field using TemplateSessionStatus choices.
        """
        return dict(TemplateSessionStatus.choices).get(self.status, self.status) or _(
            "Unknown status"
        )

    class Meta:
        ordering = ["id"]


@receiver(post_save, sender=Template)
def create_example_session(sender, instance, created, **kwargs):
    if created:
        # Create an example session if it doesn't exist
        session = instance.sessions.filter(is_example_session=True).first()
        if not session:
            session = TemplateSession.objects.create(
                template=instance,
                is_example_session=True,
                user=instance.owner if instance.owner else None,
            )


class FieldType(models.TextChoices):
    # Types supported by OpenAI structured outputs
    STR = "str", _("Text")
    FLOAT = "float", _("Decimal")
    INT = "int", _("Integer")
    BOOL = "bool", _("Yes/No")
    OBJECT = "object", _("Group of fields")


class StringFormat(models.TextChoices):
    # String formats that can be enforced by OpenAI structured outputs
    NONE = "none", _("Free text")
    EMAIL = "email", _("Email")
    DATE = "date", _("Date")
    TIME = "time", _("Time")
    DATE_TIME = "date-time", _("Date and Time")
    DURATION = "duration", _("Duration")


class TemplateField(models.Model):
    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name="fields", null=True
    )
    field_name = models.CharField(max_length=255)
    field_type = models.CharField(
        max_length=50, choices=FieldType.choices, default=FieldType.STR
    )
    string_format = models.CharField(
        max_length=50, choices=StringFormat.choices, default=StringFormat.NONE
    )
    required = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    list = models.BooleanField(default=False)
    parent_field = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="child_fields",
        null=True,
        blank=True,
    )
    slug = models.CharField(
        max_length=64,
        help_text=_(
            "Unique identifier for use in templates (letters, numbers, underscores only)."
        ),
        null=False,
        blank=False,
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.field_name} ({self.get_field_type_display()}{' list' if self.list else ''})"

    def clean(self):
        # Only allow letters, numbers, underscores
        if not re.match(r"^\w+$", self.slug or ""):
            raise ValidationError(
                {"slug": _("Slug must contain only letters, numbers, and underscores.")}
            )
        # Uniqueness logic
        if self.parent_field:
            siblings = TemplateField.objects.filter(
                template=self.template, parent_field=self.parent_field
            ).exclude(pk=self.pk)
            if siblings.filter(slug=self.slug).exists():
                raise ValidationError(
                    {"slug": _("Slug must be unique among sibling fields.")}
                )
        else:
            top_level = TemplateField.objects.filter(
                template=self.template, parent_field__isnull=True
            ).exclude(pk=self.pk)
            if top_level.filter(slug=self.slug).exists():
                raise ValidationError(
                    {"slug": _("Slug must be unique among top-level fields.")}
                )

    def save(self, *args, **kwargs):
        if not self.slug:
            # Auto-generate slug from field_name
            base_slug = slugify(self.field_name).replace("-", "_")
            # Ensure only valid chars
            base_slug = re.sub(r"[^\w]", "", base_slug)
            self.slug = base_slug or "field"
        self.full_clean()
        super().save(*args, **kwargs)

    def get_full_slug(self):
        if self.parent_field:
            return f"{self.parent_field.get_full_slug()}__{self.slug}"
        return self.slug
