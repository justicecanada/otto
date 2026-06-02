import json
import re
import uuid

from django.conf import settings
from django.db import connections, models
from django.db.models import BooleanField, Case, Q, Value, When
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from data_fetcher.util import get_request
from structlog import get_logger

from otto.models import User
from otto.utils.common import display_cad_cost, set_costs

from chat._llm.models import (
    DEFAULT_CHAT_MODEL_ID,
    DEFAULT_QA_MODEL_ID,
    DEFAULT_SUMMARIZE_MODEL_ID,
    DEFAULT_TRANSLATE_MODEL_ID,
    MODELS_BY_ID,
    get_model,
    get_updated_model_id,
)
from chat.prompts import current_time_prompt
from librarian.models import DataSource, Library, SavedFile
from librarian.utils.process_engine import sanitize_content_type

logger = get_logger(__name__)

DEFAULT_MODE = "chat"

MODE_CHOICES = [
    ("chat", _("Chat")),
    ("qa", _("Q&A")),
    ("summarize", _("Summarize")),
    ("translate", _("Translate")),
]

QA_SCOPE_CHOICES = [
    ("all", _("Entire library")),
    ("data_sources", _("Selected folders")),
    ("documents", _("Selected documents")),
]

QA_MODE_CHOICES = [
    ("rag", _("Top excerpts (RAG)")),
    ("summarize", _("Full documents")),
]

QA_PROCESS_MODE_CHOICES = [
    ("combined_docs", _("Combine")),
    ("per_doc", _("Separate")),
]
QA_SOURCE_ORDER_CHOICES = [
    ("score", _("Relevance score")),
    ("reading_order", _("Reading order")),
]

REASONING_EFFORT_CHOICES = [
    ("none", _("None (fastest, cheapest)")),
    ("minimal", _("Minimal (fastest, cheapest)")),
    ("low", _("Low (better instruction-following)")),
    ("medium", _("Medium (more deliberate reasoning)")),
    ("high", _("High (complex, multi-step reasoning)")),
    ("xhigh", _("Xhigh (deepest reasoning, slowest)")),
]

VERBOSITY_CHOICES = [
    ("low", _("Low (concise responses)")),
    ("medium", _("Medium (balanced detail)")),
    ("high", _("High (detailed responses)")),
]

TRANSLATE_MODEL_CHOICES = [
    ("azure", _("Azure Translator (best for files, 15x cost)")),
    ("gpt-5.4", _("GPT-5.4 (best quality, 2x cost)")),
    ("gpt-5.4-mini", _("GPT-5.4-mini (best value, 0.5x cost)")),
    ("gpt-5.4-nano", _("GPT-5.4-nano (high throughput, 0.1x cost)")),
    ("azure_custom", _("Azure Translator - JUS Custom")),
]


def create_chat_data_source(user, chat):
    library = user.personal_library
    if not library:
        user.create_personal_library()
        library = user.personal_library
    return DataSource.objects.create(
        name=f"Chat {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
        library=library,
        chat=chat,
    )


class ChatManager(models.Manager):
    def create(self, *args, **kwargs):
        if "mode" in kwargs:
            mode = kwargs.pop("mode")
        else:
            mode = DEFAULT_MODE
        kwargs["loaded_preset"] = None
        instance = super().create(*args, **kwargs)
        ChatOptions.objects.from_defaults(
            mode=mode,
            chat=instance,
        )
        create_chat_data_source(kwargs["user"], instance)
        return instance


class Chat(models.Model):
    """
    A sequence of messages between a user and a bot
    """

    objects = ChatManager()

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    title = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    # Last access time manually updated when chat is opened
    accessed_at = models.DateTimeField(auto_now_add=True)
    pinned = models.BooleanField(default=False, null=True)
    last_modification_date = models.DateTimeField(default=timezone.now)

    loaded_preset = models.ForeignKey("Preset", on_delete=models.SET_NULL, null=True)

    class Meta:
        indexes = [
            # Index for filtering user chats ordered by modification date
            models.Index(fields=["user", "-last_modification_date"]),
            # Index for title search
            models.Index(fields=["user", "title"]),
        ]

    def __str__(self):
        return f"Chat {self.id}: {self.title}"

    def delete(self, *args, **kwargs):
        if hasattr(self, "data_source") and self.data_source:
            self.data_source.delete()
        super().delete(*args, **kwargs)


class ChatOptionsManager(models.Manager):
    def from_defaults(self, mode=None, chat=None):
        """
        If a user default exists, copy that into a new ChatOptions object.
        If not, create a new object with some default settings manually.
        Set the mode and chat FK in the new object.

        Optimized to create the ChatOptions in a single INSERT with all field
        values from the source, instead of INSERT empty + UPDATE everything.
        M2M fields are set after creation (Django requires a saved object).
        """
        from django.contrib import messages as django_messages
        from django.forms.models import model_to_dict

        if chat and chat.user.default_preset:
            source_options = chat.user.default_preset.options
            preset_to_load = chat.user.default_preset
        else:
            default_preset = Preset.objects.get_global_default()
            source_options = default_preset.options
            preset_to_load = None

        # Check for deprecated models on the source (may update & save source)
        self.check_and_update_models(source_options)

        # Build all field values from source, excluding M2M (which need
        # a saved object) and identity fields. model_to_dict with M2M excluded
        # avoids querying M2M join tables.
        source_dict = model_to_dict(
            source_options,
            exclude=[
                "id",
                "chat",
                "qa_data_sources",
                "qa_documents",
                "qa_additional_documents",
                "qa_excluded_documents",
            ],
        )

        # model_to_dict returns FK fields by name with raw ID values.
        # Django's create() needs the _id suffix for raw IDs.
        fk_fields = {"qa_library", "translate_glossary"}
        create_kwargs = {"chat": chat}
        for key, value in source_dict.items():
            if key in fk_fields:
                create_kwargs[f"{key}_id"] = value
            else:
                create_kwargs[key] = value

        if mode:
            create_kwargs["mode"] = mode

        # Check qa_library permission before creating
        m2m_reset = False
        user = chat.user if chat else None
        if user:
            qa_library = source_options.qa_library
            if not qa_library or not user.has_perm(
                "librarian.view_library", qa_library
            ):
                request = get_request()
                if request:
                    django_messages.warning(
                        request,
                        _(
                            "QA library for settings preset not accessible. "
                            "It has been reset to your personal library."
                        ),
                    )
                if user.personal_library:
                    create_kwargs["qa_library"] = user.personal_library
                    create_kwargs.pop("qa_library_id", None)
                    create_kwargs["qa_scope"] = "all"
                    create_kwargs["qa_mode"] = "rag"
                    m2m_reset = True

        # Single INSERT with all field values (replaces empty INSERT + full UPDATE)
        new_options = self.create(**create_kwargs)

        # Copy M2M fields if library wasn't reset.
        # Use .add() instead of .set() since the object is new (avoids extra SELECT).
        if not m2m_reset:
            ds_ids = list(source_options.qa_data_sources.values_list("id", flat=True))
            doc_ids = list(source_options.qa_documents.values_list("id", flat=True))
            additional_doc_ids = list(
                source_options.qa_additional_documents.values_list("id", flat=True)
            )
            excluded_doc_ids = list(
                source_options.qa_excluded_documents.values_list("id", flat=True)
            )
            if ds_ids:
                new_options.qa_data_sources.add(*ds_ids)
            if doc_ids:
                new_options.qa_documents.add(*doc_ids)
            if additional_doc_ids:
                new_options.qa_additional_documents.add(*additional_doc_ids)
            if excluded_doc_ids:
                new_options.qa_excluded_documents.add(*excluded_doc_ids)

        # Update chat's loaded preset
        if preset_to_load:
            chat.loaded_preset = preset_to_load
            chat.save(update_fields=["loaded_preset"])

        return new_options

    def check_and_update_models(self, options):
        """
        Checks and updates deprecated or invalid model IDs in a ChatOptions instance.
        Returns a list of user-facing messages about the changes.
        """
        from django.contrib import messages

        update_messages = []
        changed = False

        model_fields = [
            (
                "chat_model",
                _("Selected chat model is deprecated. Upgrading from"),
            ),
            (
                "qa_model",
                _("Selected Q&A model is deprecated. Upgrading from"),
            ),
            (
                "summarize_model",
                _("Selected summarization model is deprecated. Upgrading from"),
            ),
        ]

        for field, msg_from in model_fields:
            old_model_id = getattr(options, field)
            new_model_id, was_updated = get_updated_model_id(old_model_id)
            if was_updated:
                setattr(options, field, new_model_id)
                old_model_obj = MODELS_BY_ID.get(old_model_id)
                old_desc = old_model_obj.description if old_model_obj else old_model_id
                new_desc = get_model(new_model_id).description
                update_messages.append(f"{msg_from} {old_desc} {_('to')} {new_desc}.")
                changed = True

        if changed:
            options.save()
            request = get_request()
            if request:
                for msg in update_messages:
                    messages.info(request, msg)


class ChatOptions(models.Model):
    """
    Options for a chat, e.g. the mode, custom prompts, etc.
    """

    objects = ChatOptionsManager()

    chat = models.OneToOneField(
        "Chat",
        on_delete=models.CASCADE,  # This will delete ChatOptions when Chat is deleted
        null=True,
        related_name="options",
    )

    mode = models.CharField(max_length=255, default=DEFAULT_MODE)

    # Prompt is only saved/restored for presets
    prompt = models.TextField(blank=True, default="")

    # Chat-specific options
    chat_model = models.CharField(max_length=255, default=DEFAULT_CHAT_MODEL_ID)
    chat_temperature = models.FloatField(default=0.5)
    chat_reasoning_effort = models.CharField(
        max_length=10, default="none", choices=REASONING_EFFORT_CHOICES
    )
    chat_verbosity = models.CharField(
        max_length=10, default="medium", choices=VERBOSITY_CHOICES
    )
    chat_system_prompt = models.TextField(blank=True)
    chat_include_images = models.BooleanField(default=False)
    chat_include_pdfs = models.BooleanField(default=False)

    # Summarize-specific options
    summarize_model = models.CharField(
        max_length=255, default=DEFAULT_SUMMARIZE_MODEL_ID
    )
    summarize_reasoning_effort = models.CharField(
        max_length=10, default="minimal", choices=REASONING_EFFORT_CHOICES
    )
    summarize_verbosity = models.CharField(
        max_length=10, default="medium", choices=VERBOSITY_CHOICES
    )
    summarize_prompt = models.TextField(blank=True)

    # Translate-specific options
    translate_language = models.CharField(max_length=255, default="fr")
    translate_model = models.CharField(
        max_length=20, default=DEFAULT_TRANSLATE_MODEL_ID
    )
    translate_glossary = models.ForeignKey(
        "librarian.SavedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="glossary_options",
    )
    # Filename stored here instead of in the SavedFile object since one file (hash)
    # may be uploaded under different filenames by different users
    translate_glossary_filename = models.CharField(
        max_length=500, null=True, blank=True
    )
    translate_prompt = models.TextField(blank=True)

    # QA-specific options
    qa_model = models.CharField(max_length=255, default=DEFAULT_QA_MODEL_ID)
    qa_reasoning_effort = models.CharField(
        max_length=10, default="minimal", choices=REASONING_EFFORT_CHOICES
    )
    qa_verbosity = models.CharField(
        max_length=10, default="medium", choices=VERBOSITY_CHOICES
    )
    qa_library = models.ForeignKey(
        "librarian.Library",
        on_delete=models.SET_NULL,
        null=True,
        related_name="qa_options",
    )
    qa_mode = models.CharField(max_length=20, default="rag", choices=QA_MODE_CHOICES)
    qa_process_mode = models.CharField(
        max_length=20, default="combined_docs", choices=QA_PROCESS_MODE_CHOICES
    )
    qa_scope = models.CharField(max_length=20, default="all", choices=QA_SCOPE_CHOICES)
    qa_data_sources = models.ManyToManyField(
        "librarian.DataSource", related_name="qa_options"
    )
    qa_documents = models.ManyToManyField(
        "librarian.Document", related_name="qa_options"
    )
    # Optional document-level filters when qa_scope="data_sources":
    # - additional documents to include from outside selected folders
    # - documents to exclude from selected folders
    qa_additional_documents = models.ManyToManyField(
        "librarian.Document", related_name="qa_additional_options", blank=True
    )
    qa_excluded_documents = models.ManyToManyField(
        "librarian.Document", related_name="qa_excluded_options", blank=True
    )
    qa_topk = models.IntegerField(default=5)
    qa_system_prompt = models.TextField(blank=True)
    qa_prompt_template = models.TextField(blank=True)
    qa_pre_instructions = models.TextField(blank=True)
    qa_post_instructions = models.TextField(blank=True)
    qa_source_order = models.CharField(
        max_length=20, default="score", choices=QA_SOURCE_ORDER_CHOICES
    )
    qa_vector_ratio = models.FloatField(default=0.6)
    qa_granular_toggle = models.BooleanField(default=False)
    qa_granularity = models.IntegerField(default=768)
    # Don't want to affect existing presets during migration, so default is False
    qa_history = models.BooleanField(default=False)

    @property
    def qa_prompt_combined(self):
        from llama_index.core import ChatPromptTemplate
        from llama_index.core.llms import ChatMessage, MessageRole

        # Template is hardcoded here rather than stored in database,
        # as it's a code-level concern without GUI editing capability
        qa_user_template = """
<context>
  {context_str}
</context>
<mode_info>
  {mode_metadata}
</mode_info>
<instructions>
  {pre_instructions}
</instructions>
<query>
  {query_str}
</query>
{post_instructions}
---
ANSWER:"""

        return ChatPromptTemplate(
            message_templates=[
                ChatMessage(
                    content=current_time_prompt() + self.qa_system_prompt,
                    role=MessageRole.SYSTEM,
                ),
                ChatMessage(
                    content=qa_user_template,
                    role=MessageRole.USER,
                ),
            ]
        ).partial_format(
            pre_instructions=self.qa_pre_instructions,
            post_instructions=self.qa_post_instructions,
        )

    def make_user_default(self):
        if self.user:
            self.user.chat_options.filter(user_default=True).update(user_default=False)
            self.user_default = True
            self.save()
        else:
            logger.error("User must be set to set user default.")
            raise ValueError("User must be set to set user default")


class PresetManager(models.Manager):
    def get_global_default(self):
        # Prefetch options + qa_library to avoid extra queries during copy
        qs = self.select_related("options", "options__qa_library")
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            return qs.get(french_default=True)
        else:
            return qs.get(english_default=True)

    def get_accessible_presets(self, user: User, language: str = None):
        ordering = [
            "-default",
            "-english_default",
            "-french_default",
            "-sharing_option",
            "-options__mode",
        ]

        default_preset_id = getattr(user, "default_preset_id", None)

        presets = self.filter(
            Q(owner=user)
            | Q(accessible_to=user)
            | Q(editable_by=user)
            | Q(sharing_option="everyone"),
            is_deleted=False,
        )
        return (
            presets.distinct()
            .select_related("options", "owner", "options__qa_library")
            .prefetch_related("editable_by")
            .annotate(
                default=Case(
                    When(id=default_preset_id, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            )
            .order_by(*ordering)
        )

    def create_from_yaml(self, data):
        """
        Create Preset objects from a dictionary loaded from chat/fixtures/presets.yaml
        """
        import os

        from django.conf import settings

        from chat.utils import copy_options

        assert len(data) >= 2, "YAML file must contain at least two presets"
        created_options = {}
        for item_name, item in data.items():
            item["sharing_option"] = "everyone"
            options_dict = item.pop("options", None)
            # TODO: Consider allowing different libraries for default presets
            options_dict["qa_library"] = Library.objects.get_default_library()

            # Handle translate_glossary CSV file path
            if (
                "translate_glossary" in options_dict
                and options_dict["translate_glossary"]
            ):
                glossary_path = options_dict["translate_glossary"]
                if isinstance(glossary_path, str) and glossary_path.endswith(".csv"):
                    # Convert relative path to absolute path
                    full_path = os.path.join(
                        settings.BASE_DIR, "chat", "fixtures", glossary_path
                    )
                    if os.path.exists(full_path):
                        # Create SavedFile object from CSV file
                        from django.core.files import File

                        from librarian.models import SavedFile
                        from librarian.utils.process_engine import generate_hash

                        with open(full_path, "rb") as csv_file:
                            file_hash = generate_hash(csv_file)
                            csv_file.seek(0)  # Reset file pointer after hashing

                            # Check if SavedFile already exists with this hash
                            saved_file = SavedFile.objects.filter(
                                sha256_hash=file_hash
                            ).first()
                            if not saved_file:
                                saved_file = SavedFile.objects.create(
                                    file=File(csv_file, name=glossary_path),
                                    sha256_hash=file_hash,
                                    content_type="text/csv",
                                )

                        # Replace string path with SavedFile object and set filename
                        options_dict["translate_glossary"] = saved_file
                        options_dict["translate_glossary_filename"] = glossary_path
                    else:
                        # File doesn't exist, remove the field
                        options_dict.pop("translate_glossary")

            based_on = item.pop("based_on", None)
            # Prevent creation of multiple default presets
            if self.filter(english_default=True).exists():
                options_dict["english_default"] = False
            if self.filter(french_default=True).exists():
                options_dict["french_default"] = False
            # Case 1: Completely new options, not based on another
            if not based_on:
                # Create the ChatOptions object
                options_object = ChatOptions.objects.create(**options_dict)
            # Case 2: Based on a previously created options object
            if based_on:
                options_object = ChatOptions.objects.create()
                copy_options(created_options.get(based_on), options_object, None)
                for key, value in options_dict.items():
                    setattr(options_object, key, value)
                options_object.save()
            # Keep track of the options object for future "based_on" references
            created_options[item_name] = options_object
            # Create the Preset object with FK to ChatOptions object
            item["options"] = options_object
            self.create(**item)


SHARING_OPTIONS = [
    ("private", _("Make private")),
    ("everyone", _("Share with everyone")),
    ("others", _("Share with others")),
]


class Preset(models.Model):
    """
    A preset of options for a chat
    """

    objects = PresetManager()

    name_en = models.CharField(max_length=255, blank=True)
    name_fr = models.CharField(max_length=255, blank=True)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)
    options = models.ForeignKey(
        ChatOptions, on_delete=models.CASCADE, related_name="preset"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    accessible_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="accessible_presets"
    )
    editable_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="editable_presets", blank=True
    )
    accessible_to_teams = models.ManyToManyField(
        "otto.Team", related_name="accessible_presets", blank=True
    )
    editable_by_teams = models.ManyToManyField(
        "otto.Team", related_name="editable_presets", blank=True
    )
    is_deleted = models.BooleanField(default=False)

    sharing_option = models.CharField(
        max_length=10,
        choices=SHARING_OPTIONS,
        default="private",
    )
    english_default = models.BooleanField(default=False)
    french_default = models.BooleanField(default=False)

    @property
    def shared_with(self):
        if self.sharing_option == "everyone":
            return _("Shared with everyone")
        elif self.sharing_option == "others":
            return _("Shared with others")
        return _("Private")

    @property
    def global_default(self):
        return self.english_default or self.french_default

    def delete_preset(self, user: User):
        # TODO: Preset refactor: Delete preset if no other presets are using it
        if self.owner != user:
            logger.error("User is not the owner of the preset.")
            raise ValueError("User is not the owner of the preset.")
        self.is_deleted = True
        self.save()

    @property
    def description_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            description = self.description_fr or self.description_en
        else:
            description = self.description_en or self.description_fr
        return description or _("No description available")

    def __str__(self):
        return f"Preset {self.id}: {self.name_en}"

    @property
    def name_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            return self.name_fr or self.name_en
        else:
            return self.name_en or self.name_fr


class Message(models.Model):
    """
    A single message within a chat, which may be from a user or a bot
    """

    chat = models.ForeignKey("Chat", on_delete=models.CASCADE, related_name="messages")
    text = models.TextField()
    date_created = models.DateTimeField(auto_now_add=True)
    # 0: user didn't click either like or dislike
    # 1: user clicked like
    # -1: user clicked dislike
    feedback = models.IntegerField(default=0)
    feedback_comment = models.TextField(blank=True)
    is_bot = models.BooleanField(default=False)
    bot_name = models.CharField(max_length=255, blank=True)
    usd_cost = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    pinned = models.BooleanField(default=False)
    # Flexible JSON field for mode-specific details such as translation target language
    details = models.JSONField(default=dict)
    mode = models.CharField(max_length=255, default="chat")
    parent = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, related_name="child"
    )
    claims_list = models.JSONField(default=list, blank=True)
    seconds_elapsed = models.FloatField(default=0.0)

    def __str__(self):
        return f"{'(BOT) ' if self.is_bot else ''}msg {self.id}: {self.text}"

    @property
    def num_files(self):
        # Use annotated value if available (from queryset), otherwise count
        if hasattr(self, "num_files_count"):
            return self.num_files_count
        return self.files.count()

    @property
    def sorted_files(self):
        """
        Return message files ordered by created_at without triggering extra queries
        when the relation has been prefetched.
        """
        # If files were prefetched, use the in-memory cache and sort in Python
        cache = getattr(self, "_prefetched_objects_cache", {}) or {}
        if "files" in cache:
            files = list(
                cache["files"]
            )  # already includes saved_file via prefetch if requested
            try:
                files.sort(key=lambda f: f.created_at)
            except Exception:
                # Fallback to original order if created_at is unavailable
                pass
            return files
        # Otherwise, fall back to DB ordering
        return self.files.all().order_by("created_at")

    @property
    def sources(self):
        return self.answersource_set.all().order_by("id")

    @property
    def has_sources(self):
        # Use prefetched cache if available to avoid an extra EXISTS query
        cache = getattr(self, "_prefetched_objects_cache", {}) or {}
        if "answersource_set" in cache:
            return len(cache["answersource_set"]) > 0
        return self.answersource_set.exists()

    @property
    def display_cost(self):
        return display_cad_cost(self.usd_cost)

    @property
    def reasoning_steps_json(self):
        """Return JSON-encoded events (query_info + reasoning_steps) from details if available."""
        if not self.details:
            return None
        # Combine query_info and reasoning_steps for display in widget
        all_events = (self.details.get("query_info") or []) + (
            self.details.get("reasoning_steps") or []
        )
        if all_events:
            return json.dumps(all_events, ensure_ascii=False)
        return None

    def calculate_costs(self):
        set_costs(self)

    def get_toggled_feedback(self, feeback_value):
        if feeback_value not in [-1, 1]:
            logger.error("Feedback must be either 1 or -1")
            raise ValueError("Feedback must be either 1 or -1")

        if self.feedback == feeback_value:
            return 0
        return feeback_value

    def update_claims_list(self):
        """
        Updates the claims_list field with all claims found in response.
        """
        from .utils import extract_claims_from_llm

        # Extract claims from the LLM response
        self.claims_list = extract_claims_from_llm(self.text)
        self.save(update_fields=["claims_list"])

    class Meta:
        constraints = [
            # Only bot messages can have a parent
            models.CheckConstraint(
                condition=(Q(parent__isnull=False) & Q(is_bot=True))
                | Q(parent__isnull=True),
                name="check_parent_is_user_message",
            )
        ]
        indexes = [
            # Index for searching messages by chat and date
            models.Index(fields=["chat", "date_created"]),
            # Index for filtering messages by chat and checking if text contains search term
            models.Index(fields=["chat", "is_bot"]),
        ]
        ordering = ["id"]


class AnswerSourceManager(models.Manager):
    def create(self, *args, **kwargs):
        # Extract page numbers using regex
        source_text = kwargs.pop("node_text", "")
        page_numbers = re.findall(r"<page_(\d+)>", source_text)
        page_numbers = list(map(int, page_numbers))  # Convert to integers
        if page_numbers:
            kwargs["min_page"] = min(page_numbers)
            kwargs["max_page"] = max(page_numbers)
        # Create the object but don't save
        instance = self.model(*args, **kwargs)
        # Save the citation in case the source Document is deleted later
        instance.saved_citation = instance.citation
        instance.save()
        return instance


class AnswerSource(models.Model):
    """
    Node from a Document that was used to answer a question. Associated with Message.
    """

    objects = AnswerSourceManager()
    message = models.ForeignKey("Message", on_delete=models.CASCADE)
    document = models.ForeignKey(
        "librarian.Document", on_delete=models.SET_NULL, null=True
    )
    node_id = models.CharField(max_length=255, blank=True)
    node_score = models.FloatField(default=0.0)
    # Saved citation for cases where the source Document is deleted later
    saved_citation = models.TextField(blank=True)
    group_number = models.IntegerField(default=0)

    min_page = models.IntegerField(null=True)
    max_page = models.IntegerField(null=True)
    processed_text = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.citation} ({self.node_score:.2f})"

    @property
    def html(self):
        from chat.utils import md

        return md.convert(self.node_text)

    @property
    def citation(self):
        return render_to_string(
            "chat/components/source_citation.html",
            {"document": self.document, "source": self},
        )

    @property
    def node_text(self):
        """
        Lookup the node text from the vector DB (if not already stored here)
        """
        if self.processed_text:
            return self.processed_text

        if self.document:
            table_id = self.document.data_source.library.uuid_hex
            with connections["vector_db"].cursor() as cursor:
                cursor.execute(
                    f"SELECT text FROM data_{table_id} WHERE node_id = '{self.node_id}'"
                )
                row = cursor.fetchone()
                if row:
                    return row[0]
        return _("Source not available (document deleted or modified since message)")


class ChatFileManager(models.Manager):
    def create(self, *args, **kwargs):
        # If not provided, create the file object
        if not kwargs.get("saved_file"):
            file = SavedFile.objects.create(
                eof=kwargs.pop("eof", False),
                content_type=sanitize_content_type(kwargs.pop("content_type", "")),
            )
            kwargs["saved_file"] = file
        return super().create(*args, **kwargs)


class ChatFile(models.Model):
    """
    A file within a chat. These are displayed in a message.
    Can be a user-uploaded file or a system-returned file (e.g. translation result)
    """

    objects = ChatFileManager()
    message = models.ForeignKey(
        "Message", on_delete=models.CASCADE, related_name="files"
    )
    filename = models.CharField(max_length=500)
    saved_file = models.ForeignKey(
        SavedFile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="chat_files",
    )
    document = models.ForeignKey(
        "librarian.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    eof = models.BooleanField(default=False)

    def __str__(self):
        return f"File {self.id}: {self.filename}"

    @property
    def text(self):
        if self.document:
            return self.document.extracted_text
        return ""

    @property
    def display_path(self):
        """
        Prefer the Document's file_path for nested files extracted from archives
        """
        try:
            # file_path is only set for nested files (e.g., "archive.zip/file.txt")
            if self.document and getattr(self.document, "file_path", None):
                return self.document.file_path
        except Exception:
            pass
        return self.filename


@receiver(post_delete, sender=ChatFile)
def delete_chat_file_dependencies(sender, instance, **kwargs):
    # NOTE: If file was uploaded to chat in Q&A mode, this won't delete unless
    # document is also deleted from librarian modal (or entire chat is deleted)
    try:
        if instance.saved_file:
            instance.saved_file.safe_delete()
        if instance.document:
            instance.document.delete()
    except Exception as e:
        logger.error(f"Failed to delete chat file dependencies: {e}")


@receiver(post_delete, sender=ChatOptions)
def delete_glossary_saved_file(sender, instance, **kwargs):
    """Delete SavedFile when ChatOptions is deleted, if no other references exist"""
    try:
        if instance.translate_glossary:
            instance.translate_glossary.safe_delete()
    except Exception as e:
        logger.error(f"Failed to delete glossary saved file: {e}")


@receiver(post_save, sender=Message)
def message_post_save(sender, instance, **kwargs):
    try:
        # Access Chat object to update last_modification_date
        Chat.objects.filter(pk=instance.chat.pk).update(
            last_modification_date=timezone.now()
        )
    except Exception as e:
        logger.exception(f"Message post save error: {e}")


@receiver(pre_delete, sender=Message)
def message_pre_delete(sender, instance, **kwargs):
    try:
        # Delete documents uploaded in this message (including nested files)
        # Handle many-to-many associations: remove link; delete only if no other links and no data_source context
        from librarian.models import Document

        # Remove M2M associations and delete orphaned documents (no messages)
        for document in Document.objects.filter(messages=instance):
            document.messages.remove(instance)
            # If this document is no longer linked to any messages, delete it when
            # it's not a librarian-only doc OR it belongs to a chat data source.
            if not document.messages.exists():
                ds = document.data_source
                if ds is None or getattr(ds, "chat_id", None):
                    document.delete()

        # Backwards compatibility: fall back to saved_file lookup for older
        # messages where documents may not have been linked.
        if instance.mode == "qa":
            chat_files = ChatFile.objects.filter(message=instance)
            for chat_file in chat_files:
                if chat_file.saved_file:
                    document = Document.objects.filter(
                        saved_file=chat_file.saved_file
                    ).first()
                    # Only delete if the document has no remaining message links
                    if document and not document.messages.exists():
                        ds = document.data_source
                        if ds is None or getattr(ds, "chat_id", None):
                            document.delete()
    except Exception as e:
        logger.exception(f"Message pre delete error: {e}")
