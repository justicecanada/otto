import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from data_fetcher.util import get_request

from chat.models import SHARING_OPTIONS


class LayoutType(models.TextChoices):
    # Jinja: Use Django's Jinja2 template engine to render the template.
    # Template must include the field slugs exactly like {{ field_slug }}.
    # Natively HTML; can optionally use Jinja syntax for conditional display, loops, etc.
    JINJA_RENDERING = "jinja_rendering", _("Jinja HTML Rendering")
    # LLM: Use a language model to output a complete document.
    # Template can be semi-structured with placeholders for fields, instructions, etc.
    # but they do not need to perfectly conform to the field slugs etc.
    LLM_GENERATION = "llm_generation", _("Markdown with LLM generation")
    # Markdown: Just substitute the template fields verbatim into the markdown text.
    # Requires the field slugs to be included in the markdown text like {{ field_slug }}.
    MARKDOWN_SUBSTITUTION = "markdown_substitution", _("Markdown substitution")
    # Word: Use a Word document template with placeholders for fields.
    # Requires the field slugs to be included in the Word document like {{ field_slug }}.
    WORD_TEMPLATE = "word_template", _("Word template substitution")


class TemplateManager(models.Manager):
    pass


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
    example_json_output = models.TextField(null=True)

    # Store the last test extraction result and timestamp
    last_test_fields_result = models.TextField(null=True, blank=True)
    last_test_fields_timestamp = models.DateTimeField(null=True, blank=True)
    # Store the last layout rendering result, type, and timestamp
    last_test_layout_result = models.TextField(null=True, blank=True)
    last_test_layout_type = models.CharField(max_length=100, null=True, blank=True)
    last_test_layout_timestamp = models.DateTimeField(null=True, blank=True)

    # Template rendering
    layout_type = models.CharField(
        max_length=50,
        choices=LayoutType.choices,
        default=LayoutType.JINJA_RENDERING,
    )
    layout_jinja = models.TextField(null=True, blank=True)
    layout_markdown = models.TextField(null=True, blank=True)

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


class Source(models.Model):
    template = models.OneToOneField(
        Template, on_delete=models.CASCADE, related_name="example_source", null=True
    )
    text = models.TextField()


@receiver(post_save, sender=Template)
def create_example_source(sender, instance, created, **kwargs):
    if created:
        Source.objects.create(template=instance, text="")


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
