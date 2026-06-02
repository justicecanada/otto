import json
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from structlog import get_logger

from otto.utils.common import display_cad_cost, set_costs

from librarian.models import DataSource, SavedFile
from librarian.utils.process_engine import sanitize_content_type

from chat_next._llm.models import DEFAULT_CHAT_MODEL_ID

logger = get_logger(__name__)

COMPACTION_INDICATOR_MIN_PREVIOUS_PERCENTAGE = 50
COMPACTION_INDICATOR_MIN_DROP_TOKENS = 20000


REASONING_EFFORT_CHOICES = [
    ("none", _("None")),
    ("minimal", _("Minimal")),
    ("low", _("Low")),
    ("medium", _("Medium")),
    ("high", _("High")),
    ("xhigh", _("Xhigh")),
]

VERBOSITY_CHOICES = [
    ("low", _("Low")),
    ("medium", _("Medium")),
    ("high", _("High")),
]

# Tool category IDs for local function tools
# Each category can be independently enabled/disabled in the chat options
TOOL_CATEGORY_QA_LIBRARIES = "local_qa_libraries"  # Q&A library tools
TOOL_CATEGORY_LEGISLATION = "local_legislation"  # Legacy hidden local laws search
TOOL_CATEGORY_LEGAL_RESEARCH = "local_legal_research"  # A2AJ-backed legal research
TOOL_CATEGORY_TRANSLATION = "local_translation"  # File translation
TOOL_CATEGORY_DOCUMENT_PROCESSING = (
    "local_document_processing"  # LLM document processing
)
TOOL_CATEGORY_TERMINOLOGY = "local_terminology"  # Terminology lookups
TOOL_CATEGORY_TRANSCRIPTION = "local_transcription"  # Audio/video transcription
TOOL_CATEGORY_URL_RETRIEVAL = "url_retriever"  # Allow-listed URL fetch
TOOL_CATEGORY_SKILLS = "local_skills"  # Skill instructions loader

# Display metadata for each tool category.
# Used in context picker (_views/context.py) and tool call display.
TOOL_DISPLAY = {
    TOOL_CATEGORY_QA_LIBRARIES: {
        "name": _("Libraries"),
        "description": _("Search accessible document libraries"),
        "icon": "collection",
    },
    TOOL_CATEGORY_LEGISLATION: {
        "name": _("Legislation Search"),
        "description": _("Search Canadian federal Acts and regulations"),
        "icon": "journal-bookmark",
    },
    TOOL_CATEGORY_LEGAL_RESEARCH: {
        "name": _("Legal Research"),
        "description": _(
            "Search Canadian case law (including provincial/territorial courts "
            "and tribunals), plus legislation and regulations available through "
            "A2AJ public legal data API."
        ),
        "icon": "journal-text",
    },
    TOOL_CATEGORY_TRANSLATION: {
        "name": _("Azure File Translation"),
        "description": _(
            "Translate files using Azure Translator, preserving formatting."
        ),
        "icon": "translate",
    },
    TOOL_CATEGORY_DOCUMENT_PROCESSING: {
        "name": _("Batch Processing"),
        "description": _("Run the same prompt over multiple documents."),
        "icon": "file-earmark-text",
    },
    TOOL_CATEGORY_TERMINOLOGY: {
        "name": _("GC Terminology"),
        "description": _("Search in TERMIUM Plus®"),
        "icon": "book",
    },
    TOOL_CATEGORY_TRANSCRIPTION: {
        "name": _("Audio/Video Transcription"),
        "description": _("Transcribe audio and video files into text."),
        "icon": "mic",
    },
    TOOL_CATEGORY_SKILLS: {
        "name": _("Skill Management"),
        "description": _(
            "Use skill-management tools (preset migration and skill creation/editing)."
        ),
        "icon": "lightbulb",
    },
    TOOL_CATEGORY_URL_RETRIEVAL: {
        "name": _("URL Retrieval"),
        "description": _("Fetch allow-listed webpages or ingest them into libraries"),
        "tooltip": _(
            "Fetch textual content or enqueue webpages (HTML/PDF/images) into chat files or other libraries."
        ),
        "icon": "globe2",
    },
    "code_interpreter": {
        "name": _("Code Interpreter"),
        "description": _("Run Python code for analysis and calculations."),
        "icon": "code-square",
    },
}

# Available tools for AI Assistant (OpenAI built-in tools and local function categories)
# These are passed to the Responses API 'tools' parameter
AVAILABLE_TOOLS = [
    (TOOL_CATEGORY_QA_LIBRARIES, _("Libraries")),
    ("code_interpreter", _("Code interpreter")),
    # (TOOL_CATEGORY_LEGISLATION, _("Legislation search")),
    # (TOOL_CATEGORY_TRANSLATION, _("File translation")),
    (TOOL_CATEGORY_DOCUMENT_PROCESSING, _("Batch document processing")),
    # (TOOL_CATEGORY_TRANSCRIPTION, _("Audio/video transcription")),
    (TOOL_CATEGORY_LEGAL_RESEARCH, _("Canadian legal research (Public)")),
    (TOOL_CATEGORY_TERMINOLOGY, _("TERMIUM Plus® (Public)")),
    (TOOL_CATEGORY_URL_RETRIEVAL, _("URL retrieval")),
]

# Whitelist of tool IDs currently exposed/allowed in chat_next options.
AVAILABLE_TOOL_IDS = {tool_id for tool_id, _ in AVAILABLE_TOOLS}


def sanitize_enabled_tools(enabled_tools: list[str] | None) -> list[str]:
    """Return enabled tool IDs filtered to currently-allowed choices.

    This protects runtime behavior from stale values stored in chat options
    (for example, categories that were once available but are now hidden).
    """
    if not enabled_tools:
        return []

    sanitized = []
    seen = set()
    for tool_id in enabled_tools:
        if tool_id in AVAILABLE_TOOL_IDS and tool_id not in seen:
            sanitized.append(tool_id)
            seen.add(tool_id)
    return sanitized


# All local tool category IDs (for filtering in the API client)
LOCAL_TOOL_CATEGORIES = {
    TOOL_CATEGORY_QA_LIBRARIES,
    TOOL_CATEGORY_LEGISLATION,
    TOOL_CATEGORY_LEGAL_RESEARCH,
    TOOL_CATEGORY_TRANSLATION,
    TOOL_CATEGORY_DOCUMENT_PROCESSING,
    TOOL_CATEGORY_TERMINOLOGY,
    TOOL_CATEGORY_TRANSCRIPTION,
    TOOL_CATEGORY_URL_RETRIEVAL,
    TOOL_CATEGORY_SKILLS,
}

# Local tools that support manual approval/auto-approve.
# Each entry maps a tool ID (used for auto-approve settings) to its UI label
# and the enabled-tools checkbox value it should attach to.
LOCAL_TOOLS_WITH_APPROVAL = {
    "list_canadian_legal_datasets": {
        "label": _("Canadian legal research (Public)"),
        "category": TOOL_CATEGORY_LEGAL_RESEARCH,
    },
    "search_canadian_case_law": {
        "label": _("Canadian legal research (Public)"),
        "category": TOOL_CATEGORY_LEGAL_RESEARCH,
    },
    "fetch_canadian_case_by_citation": {
        "label": _("Canadian legal research (Public)"),
        "category": TOOL_CATEGORY_LEGAL_RESEARCH,
    },
    "search_canadian_legislation": {
        "label": _("Canadian legal research (Public)"),
        "category": TOOL_CATEGORY_LEGAL_RESEARCH,
    },
    "fetch_canadian_legislation_by_citation": {
        "label": _("Canadian legal research (Public)"),
        "category": TOOL_CATEGORY_LEGAL_RESEARCH,
    },
    "termium_lookup": {
        "label": _("TERMIUM Plus® (Public)"),
        "category": TOOL_CATEGORY_TERMINOLOGY,
    },
}

# Default tools enabled for new chats
DEFAULT_ENABLED_TOOLS = [
    TOOL_CATEGORY_QA_LIBRARIES,
    "code_interpreter",
    TOOL_CATEGORY_LEGAL_RESEARCH,
    TOOL_CATEGORY_DOCUMENT_PROCESSING,
    TOOL_CATEGORY_TERMINOLOGY,
    TOOL_CATEGORY_URL_RETRIEVAL,
]


EXTERNAL_TOOL_APPROVAL_DECISION_PENDING = "pending"
EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED = "auto_approved"
EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED = "approved"
EXTERNAL_TOOL_APPROVAL_DECISION_DENIED = "denied"

EXTERNAL_TOOL_APPROVAL_DECISION_CHOICES = [
    (EXTERNAL_TOOL_APPROVAL_DECISION_PENDING, _("Pending review")),
    (EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED, _("Auto-approved")),
    (EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED, _("Manually approved")),
    (EXTERNAL_TOOL_APPROVAL_DECISION_DENIED, _("Denied")),
]

EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO = "no"
EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM = "llm"
EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS = "local_checks"
EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API = "azure_language_api"

EXTERNAL_TOOL_APPROVAL_PII_SOURCE_CHOICES = [
    (EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO, _("No")),
    (EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM, _("LLM")),
    (EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS, _("Local checks")),
    (EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API, _("Azure Language API")),
]


def get_default_enabled_skills_for_user(user):
    """Return default fixture-backed skills that should be auto-enabled.

    Default skills are system-provided (no owner) and shared with everyone.
    """
    return Skill.objects.get_accessible(user).filter(
        id__in=get_default_enabled_skills().values_list("id", flat=True)
    )


def get_default_enabled_skills():
    """Return fixture-backed default skills."""
    return Skill.objects.filter(
        owner__isnull=True,
        sharing_option="everyone",
    )


def sync_default_enabled_skills(*, add_missing_defaults_to_all=False):
    """Sync fixture-backed default skills into existing ChatSettings."""
    default_skills = list(get_default_enabled_skills())
    if not default_skills:
        return 0, 0

    updated_settings = 0
    added_skill_links = 0

    for chat_settings in ChatSettings.objects.prefetch_related("enabled_skills"):
        enabled_skill_ids = {skill.id for skill in chat_settings.enabled_skills.all()}
        if enabled_skill_ids and not add_missing_defaults_to_all:
            continue

        missing_default_skills = [
            skill for skill in default_skills if skill.id not in enabled_skill_ids
        ]
        if not missing_default_skills:
            continue

        chat_settings.enabled_skills.add(*missing_default_skills)
        updated_settings += 1
        added_skill_links += len(missing_default_skills)

    return updated_settings, added_skill_links


SHARING_OPTIONS = [
    ("private", _("Make private")),
    ("everyone", _("Share with everyone")),
    ("others", _("Share with others")),
]


def create_chat_data_source(user, chat):
    if not user.personal_library:
        user.create_personal_library()
    return DataSource.objects.create(
        name=f"Chat {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
        library=user.personal_library,
        chat_next=chat,
    )


class ChatSettingsManager(models.Manager):
    def get_or_create_for_user(self, user):
        """Get or create ChatSettings for a user."""
        from chat_next._llm.models import (
            DEFAULT_CHAT_MODEL_ID,
            is_chat_next_selectable_model,
        )

        chat_settings, created = self.get_or_create(user=user)

        # Always sanitize stored tool IDs in case hidden/legacy IDs exist.
        cleaned_tools = sanitize_enabled_tools(chat_settings.chat_enabled_tools)

        # For newly created settings, seed default enabled tools.
        if created and not cleaned_tools:
            cleaned_tools = list(DEFAULT_ENABLED_TOOLS)

        # For newly created settings or if display name is blank, populate from user's full_name.
        display_name_updated = False
        if not (chat_settings.user_display_name or "").strip():
            user_full_name = getattr(user, "full_name", "").strip()
            if user_full_name:
                chat_settings.user_display_name = user_full_name
                display_name_updated = True

        update_fields = []
        if cleaned_tools != (chat_settings.chat_enabled_tools or []):
            chat_settings.chat_enabled_tools = cleaned_tools
            update_fields.append("chat_enabled_tools")

        if not is_chat_next_selectable_model(chat_settings.chat_model):
            chat_settings.chat_model = DEFAULT_CHAT_MODEL_ID
            update_fields.append("chat_model")

        if display_name_updated:
            update_fields.append("user_display_name")

        if update_fields:
            chat_settings.save(update_fields=update_fields)

        # For newly created settings, auto-enable default fixture skills.
        if created and not chat_settings.enabled_skills.exists():
            default_skills = list(get_default_enabled_skills_for_user(user))
            if default_skills:
                chat_settings.enabled_skills.add(*default_skills)

        return chat_settings, created


class ChatSettings(models.Model):
    """
    User-level settings that apply to all chats. Replaces per-chat ChatOptions.
    """

    objects = ChatSettingsManager()

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_settings",
    )

    # Personalization
    user_display_name = models.CharField(
        max_length=255, blank=True, help_text="Display name (auto-populated from Entra)"
    )
    send_name_to_model = models.BooleanField(default=False)
    job_description = models.TextField(blank=True)
    global_instructions = models.TextField(blank=True)

    # Model settings
    chat_model = models.CharField(max_length=255, default=DEFAULT_CHAT_MODEL_ID)
    chat_temperature = models.FloatField(default=0.5)
    chat_reasoning_effort = models.CharField(
        max_length=10, default="medium", choices=REASONING_EFFORT_CHOICES
    )
    chat_verbosity = models.CharField(
        max_length=10, default="medium", choices=VERBOSITY_CHOICES
    )

    # Tool settings
    chat_enabled_tools = models.JSONField(default=list, blank=True)
    chat_auto_approve_tools = models.JSONField(default=list, blank=True)

    # Advanced settings
    CONTEXT_MANAGEMENT_CHOICES = [
        ("compact", _("Compact")),
        ("truncate", _("Truncate")),
        ("error", _("Show error")),
    ]
    chat_max_iterations = models.PositiveIntegerField(default=25)
    chat_context_management = models.CharField(
        max_length=10,
        default="compact",
        choices=CONTEXT_MANAGEMENT_CHOICES,
    )
    chat_system_prompt = models.TextField(blank=True)
    chat_include_images = models.BooleanField(default=False)
    chat_include_pdfs = models.BooleanField(default=False)

    # Skills
    enabled_skills = models.ManyToManyField(
        "Skill",
        blank=True,
        related_name="enabled_in_settings",
    )

    class Meta:
        verbose_name_plural = "Chat settings"

    def __str__(self):
        return f"ChatSettings for {self.user}"

    @property
    def chat_available_skills(self):
        """Backward compat alias for enabled_skills."""
        return self.enabled_skills

    def get_accessible_enabled_skills(self, user=None):
        """Return enabled skills the given user can currently access."""
        target_user = user or getattr(self, "user", None)
        if not target_user:
            return Skill.objects.none()
        return Skill.objects.get_accessible(target_user).filter(
            enabled_in_settings=self
        )


class ChatManager(models.Manager):
    def create(self, *args, **kwargs):
        instance = super().create(*args, **kwargs)
        # Ensure user has ChatSettings
        ChatSettings.objects.get_or_create_for_user(kwargs["user"])
        create_chat_data_source(kwargs["user"], instance)
        return instance


class Chat(models.Model):
    """
    A sequence of messages between a user and a bot
    """

    objects = ChatManager()

    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    title = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_next_set"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Last access time manually updated when chat is opened
    accessed_at = models.DateTimeField(auto_now_add=True)
    pinned = models.BooleanField(default=False, null=True)
    last_modification_date = models.DateTimeField(default=timezone.now)

    # Persist the latest active code interpreter container for this chat
    # so we can attempt to reuse it across turns (reduces per-session fees
    # and preserves sandbox files between messages, subject to 20 min idle expiry).
    code_interpreter_container_id = models.CharField(
        max_length=255, blank=True, default=""
    )
    # Persist a compacted prefix of the conversation so we can rebuild context
    # without resurrecting the full pre-compaction history when response chaining
    # breaks or when we proactively compact between user messages.
    compacted_input_items = models.JSONField(default=list, blank=True)
    loaded_skill_state = models.JSONField(default=dict, blank=True)
    compacted_through_message = models.ForeignKey(
        "Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    @property
    def settings(self):
        """Get user-level chat settings."""
        try:
            return self.user.chat_settings
        except ChatSettings.DoesNotExist:
            return ChatSettings.objects.get_or_create_for_user(self.user)[0]

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
        super().delete(*args, **kwargs)

    def clear_compacted_context(self, *, save: bool = True):
        self.compacted_input_items = []
        self.compacted_through_message = None
        if save:
            self.save(
                update_fields=["compacted_input_items", "compacted_through_message"]
            )


@receiver(pre_delete, sender=Chat)
def chat_pre_delete(sender, instance, **kwargs):
    """
    Pre-delete handler for Chat to collect OpenAI responses for cleanup.

    When a chat is deleted:
    1. Collect all response IDs from bot messages
    2. Queue async deletion of these OpenAI responses

    Note: OpenAI file deletion is handled by SavedFile.safe_delete() which is
    called when each ChatFile is deleted. This ensures files are only deleted
    when no other ChatFiles or Documents reference the same SavedFile.

    The actual deletion happens asynchronously so the UI response is fast.
    """
    try:
        from chat_next.tasks import delete_openai_responses_batch

        # Collect all response IDs from bot messages in this chat
        response_ids = list(
            Message.objects.filter(chat=instance, is_bot=True)
            .exclude(response_id="")
            .values_list("response_id", flat=True)
        )

        # Queue async deletion (fire-and-forget)
        if response_ids:
            delete_openai_responses_batch.delay(response_ids)
            logger.info(
                "Queued deletion of OpenAI responses for chat",
                chat_id=str(instance.id),
                response_count=len(response_ids),
            )

    except Exception as e:
        # Don't block chat deletion if OpenAI cleanup fails
        # The nightly cleanup task will catch any dangling resources
        logger.error(
            "Failed to queue OpenAI response cleanup for chat deletion",
            chat_id=str(instance.id),
            error=str(e),
        )


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
    # Flexible JSON field for mode-specific details such as translation target language
    details = models.JSONField(default=dict)
    parent = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, related_name="child"
    )
    seconds_elapsed = models.FloatField(default=0.0)

    # Store raw response.output from OpenAI Responses API for conversation state management
    # For bot messages: contains assistant message items + encrypted reasoning (if applicable)
    # For user messages: contains user input items (text, images, files)
    # Used to reconstruct conversation history without relying on OpenAI's storage
    response_output = models.JSONField(default=list, blank=True)

    # Store the OpenAI response ID (e.g., "resp_67cb61fa3a448190bcf2c42d96f0d1a8")
    # Used with previous_response_id for chaining responses to optimize input caching.
    # Responses are stored for 30 days on Azure's side when store=True.
    # If this is set, we use previous_response_id instead of rebuilding input items.
    response_id = models.CharField(max_length=255, blank=True, default="")

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
        Return message files in a stable, hierarchy-aware order without triggering
        extra queries when the relation has been prefetched.

        Nested container children (ZIP/MSG/EML extracts) should sort by their full
        display path so siblings and descendants render in the same order users see
        in the underlying archive/email structure.
        """

        def sort_key(chat_file):
            display_path = ""
            try:
                display_path = (chat_file.display_path or "").casefold()
            except Exception:
                display_path = ""

            created_at = getattr(chat_file, "created_at", None)
            filename = (getattr(chat_file, "filename", "") or "").casefold()
            file_id = getattr(chat_file, "id", 0) or 0
            return (display_path, created_at, filename, file_id)

        # If files were prefetched, use the in-memory cache and sort in Python
        cache = getattr(self, "_prefetched_objects_cache", {}) or {}
        if "files" in cache:
            files = list(cache["files"])
            files.sort(key=sort_key)
            return files
        # Otherwise, fall back to DB ordering
        return sorted(self.files.all(), key=sort_key)

    @property
    def display_cost(self):
        return display_cad_cost(self.usd_cost)

    def get_context_usage_display_for_usage(self, usage: dict | None = None):
        """Return context usage display info for a specific usage payload."""
        if not self.is_bot:
            return None

        transient_usage = getattr(self, "_transient_usage", None)
        usage = usage or transient_usage or ((self.details or {}).get("usage"))
        if not usage:
            return None

        from chat_next._llm.models import (
            get_compaction_threshold_tokens,
            get_context_usage_display,
        )

        model_overrides = (self.details or {}).get("model_overrides") or {}
        model_id = model_overrides.get("chat_model") or (
            self.chat.settings.chat_model if hasattr(self.chat, "settings") else None
        )
        if not model_id:
            return None

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cached_tokens = usage.get("cached_tokens", 0)
        reasoning_tokens = usage.get("reasoning_tokens", 0)

        if input_tokens == 0 and output_tokens == 0:
            return None

        return get_context_usage_display(
            input_tokens,
            output_tokens,
            model_id,
            cached_tokens,
            reasoning_tokens,
            show_token_breakdown=bool(getattr(self.chat.user, "is_admin", False)),
            near_limit_threshold_tokens=(
                get_compaction_threshold_tokens(model_id)
                if getattr(self.chat.settings, "chat_context_management", "compact")
                == "compact"
                else None
            ),
            near_limit_threshold_pct=(
                None
                if getattr(self.chat.settings, "chat_context_management", "compact")
                == "compact"
                else 100
            ),
        )

    def get_context_total_tokens(self, usage: dict | None = None):
        """Return total context tokens for a usage payload, if available."""
        context_usage = self.get_context_usage_display_for_usage(usage)
        if not context_usage:
            return None
        return context_usage.get("total_tokens")

    def get_compaction_signal_total_tokens(self, usage: dict | None = None):
        """Return a conservative token total for inferring compaction.

        For the compaction badge heuristic, ignore reasoning-only output tokens so
        large reasoning swings do not look like compaction.
        """
        if not self.is_bot:
            return None

        transient_usage = getattr(self, "_transient_usage", None)
        usage = usage or transient_usage or ((self.details or {}).get("usage"))
        if not usage:
            return None

        input_tokens = max(int(usage.get("input_tokens", 0) or 0), 0)
        output_tokens = max(int(usage.get("output_tokens", 0) or 0), 0)
        reasoning_tokens = max(int(usage.get("reasoning_tokens", 0) or 0), 0)

        if input_tokens == 0 and output_tokens == 0:
            return None

        non_reasoning_output_tokens = max(0, output_tokens - reasoning_tokens)
        return input_tokens + non_reasoning_output_tokens

    @property
    def has_compaction_processing_step(self) -> bool:
        details = self.details or {}

        raw_processing_steps = details.get("raw_processing_steps") or []
        for step in raw_processing_steps:
            if (
                isinstance(step, dict)
                and step.get("type") == "tool_call"
                and step.get("tool_type") == "compaction"
            ):
                return True

        processing_steps = details.get("processing_steps") or []
        compacted_title = str(_("Compacted conversation"))
        for step in processing_steps:
            if not isinstance(step, dict):
                continue
            if step.get("tool_type") == "compaction":
                return True
            if step.get("title") == compacted_title:
                return True

        return False

    @property
    def was_compacted_during_response(self) -> bool:
        if not self.is_bot:
            return False

        if self.has_compaction_processing_step:
            return True

        current_context_usage = self.context_usage
        current_total = self.get_compaction_signal_total_tokens()
        if not current_context_usage or current_total is None:
            return False

        previous_total = getattr(self, "_previous_bot_total_tokens", None)
        previous_percentage = getattr(self, "_previous_bot_context_percentage", None)
        if previous_total is None:
            previous_bot_message = (
                Message.objects.filter(chat=self.chat, is_bot=True)
                .filter(
                    Q(date_created__lt=self.date_created)
                    | (Q(date_created=self.date_created) & Q(id__lt=self.id))
                )
                .order_by("-date_created", "-id")
                .first()
            )
            previous_total = (
                previous_bot_message.get_compaction_signal_total_tokens()
                if previous_bot_message
                else None
            )
            previous_context_usage = (
                previous_bot_message.context_usage if previous_bot_message else None
            )
            previous_percentage = (
                previous_context_usage.get("percentage")
                if previous_context_usage
                else None
            )

        return (
            previous_total is not None
            and previous_percentage is not None
            and previous_percentage > COMPACTION_INDICATOR_MIN_PREVIOUS_PERCENTAGE
            and (previous_total - current_total) >= COMPACTION_INDICATOR_MIN_DROP_TOKENS
        )

    @property
    def processing_steps(self):
        if not self.details:
            return []
        return self.details.get("processing_steps") or []

    @property
    def has_reasoning_processing_steps(self):
        from chat_next.utils import is_reasoning_display_step

        return any(is_reasoning_display_step(step) for step in self.processing_steps)

    @property
    def reasoning_steps_json(self):
        """Return JSON-encoded processing steps from details if available."""
        from chat_next.utils import get_display_processing_steps

        if not self.details:
            return None

        processing_steps = get_display_processing_steps(self, language=get_language())
        if self.details.get("processing_steps"):
            all_events = (self.details.get("query_info") or []) + processing_steps
        else:
            all_events = processing_steps

        if all_events:
            return json.dumps(all_events, ensure_ascii=False)
        return None

    @property
    def context_usage(self):
        """Return context usage display info if available in message details.

        Returns a dict with keys:
        - 'total_tokens': Total tokens used
        - 'max_tokens': Maximum context tokens for the model
        - 'percentage': Usage percentage (0-100)
        - 'display_text': Human-readable text like "148K/272K"
        - 'near_limit': True if approaching compaction threshold
        """
        transient_context_usage = getattr(self, "_transient_context_usage", None)
        if transient_context_usage is not None:
            return transient_context_usage
        return self.get_context_usage_display_for_usage()

    def calculate_costs(self):
        set_costs(self)

    def get_toggled_feedback(self, feeback_value):
        if feeback_value not in [-1, 1]:
            logger.error("Feedback must be either 1 or -1")
            raise ValueError("Feedback must be either 1 or -1")

        if self.feedback == feeback_value:
            return 0
        return feeback_value

    class Meta:
        constraints = [
            # Only bot messages can have a parent
            models.CheckConstraint(
                condition=(Q(parent__isnull=False) & Q(is_bot=True))
                | Q(parent__isnull=True),
                name="chat_next_check_parent_is_user_message",
            )
        ]
        indexes = [
            # Index for searching messages by chat and date
            models.Index(fields=["chat", "date_created"]),
            # Index for filtering messages by chat and checking if text contains search term
            models.Index(fields=["chat", "is_bot"]),
        ]
        ordering = ["id"]


class ExternalToolApprovalLog(models.Model):
    """Audit log for external tool approvals and auto-approvals."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_next_external_tool_approval_logs",
    )
    message = models.ForeignKey(
        "Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="external_tool_approval_logs",
    )
    message_id_snapshot = models.BigIntegerField(null=True, blank=True, editable=False)
    tool_call_id = models.CharField(max_length=255)
    approval_request_id = models.CharField(max_length=255, blank=True, default="")
    tool_name = models.CharField(max_length=255)
    tool_label = models.CharField(max_length=255, blank=True)
    external_service_name = models.CharField(max_length=255, blank=True)
    query = models.TextField(blank=True)
    tool_arguments = models.JSONField(default=dict, blank=True)
    decision = models.CharField(
        max_length=20,
        choices=EXTERNAL_TOOL_APPROVAL_DECISION_CHOICES,
        default=EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
    )
    approval_source = models.CharField(max_length=64, blank=True)
    pii_flagged = models.BooleanField(default=False)
    pii_flag_source = models.CharField(
        max_length=32,
        choices=EXTERNAL_TOOL_APPROVAL_PII_SOURCE_CHOICES,
        default=EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO,
    )
    pii_entity_categories = models.JSONField(default=list, blank=True)
    displayed_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["message_id_snapshot", "tool_call_id"],
                name="chat_next_external_tool_approval_log_per_snapshot_call",
            )
        ]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["decision", "created_at"]),
            models.Index(fields=["tool_name", "created_at"]),
            models.Index(fields=["message_id_snapshot"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["pii_flagged", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        normalized_pii_entity_categories = []
        for category in self.pii_entity_categories or []:
            category_text = str(category).strip()
            if category_text and category_text not in normalized_pii_entity_categories:
                normalized_pii_entity_categories.append(category_text)
        self.pii_entity_categories = sorted(normalized_pii_entity_categories)

        if self.message_id and not self.message_id_snapshot:
            self.message_id_snapshot = self.message_id
        if not self.pii_flagged:
            self.pii_flag_source = EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO
            self.pii_entity_categories = []
        elif not self.pii_flag_source or (
            self.pii_flag_source == EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO
        ):
            self.pii_flag_source = EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tool_name} ({self.get_decision_display()})"

    @property
    def message_reference_id(self):
        return self.message_id_snapshot or self.message_id

    @property
    def review_latency_seconds(self):
        if not self.displayed_at or not self.decided_at:
            return None
        return max((self.decided_at - self.displayed_at).total_seconds(), 0.0)

    @property
    def approval_source_display(self):
        source_labels = {
            "manual": _("Manual review"),
            "query_policy": _("Query policy"),
            "user_allowlist": _("User allowlist"),
            "cache": _("Cache"),
        }
        return source_labels.get(self.approval_source, self.approval_source or "")

    @property
    def pii_flag_source_display(self):
        if not self.pii_flagged:
            return _("No")

        source_labels = {
            EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM: _("LLM"),
            EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS: _("Local checks"),
            EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API: _(
                "Azure Language API"
            ),
        }
        return source_labels.get(self.pii_flag_source, _("LLM"))

    @property
    def pii_entity_categories_display(self):
        return ", ".join(self.pii_entity_categories or [])


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
        related_name="chat_next_files",
    )
    document = models.ForeignKey(
        "librarian.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_next_files",
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
            # safe_delete() checks if any other ChatFiles/Documents reference this SavedFile
            # and only deletes it (including the OpenAI file) if there are no other references
            instance.saved_file.safe_delete()
        if instance.document_id:
            from librarian.models import Document

            document = Document.objects.filter(id=instance.document_id).first()
            if document:
                data_source = document.data_source
                if data_source is None or getattr(data_source, "chat_next_id", None):
                    document.delete()
    except Exception as e:
        logger.error(f"Failed to delete chat file dependencies: {e}")


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
        compacted_through_id = instance.chat.compacted_through_message_id
        if instance.chat.compacted_input_items and (
            compacted_through_id is None or instance.id <= compacted_through_id
        ):
            instance.chat.clear_compacted_context()

        # Delete documents uploaded in this message (including nested files)
        # Handle many-to-many associations: remove link; delete only if no other links and no data_source context
        from librarian.models import Document

        from chat_next.tasks import delete_openai_responses_batch

        # Remove M2M associations and delete orphaned documents (no messages)
        # Use chat_next_messages for the new chat_next app
        for document in Document.objects.filter(chat_next_messages__id=instance.id):
            document.chat_next_messages.remove(instance)
            # If this document is no longer linked to any messages, delete it when
            # it's not a librarian-only doc OR it belongs to a chat_next data source.
            if not document.chat_next_messages.exists():
                ds = document.data_source
                if ds is None or getattr(ds, "chat_next_id", None):
                    document.delete()

        # Handle OpenAI response cleanup when any message is deleted
        # When a message is deleted (user or bot), we need to:
        # 1. Delete OpenAI responses for this message (if bot) and all subsequent bot messages
        # 2. Clear response_id on all subsequent bot messages so the next request rebuilds full history
        #
        # This is necessary because responses chain via previous_response_id - if a message
        # in the middle is deleted, subsequent responses become invalid/orphaned.

        # Find all subsequent bot messages (after this message's creation time)
        subsequent_bot_messages = Message.objects.filter(
            chat=instance.chat,
            is_bot=True,
            date_created__gte=instance.date_created,
        )
        if instance.is_bot:
            subsequent_bot_messages = subsequent_bot_messages.exclude(pk=instance.pk)

        # Collect response_ids for deletion from OpenAI
        response_ids_to_delete = list(
            subsequent_bot_messages.exclude(response_id="").values_list(
                "response_id", flat=True
            )
        )
        # Add the current message's response_id if it's a bot message
        if instance.is_bot and instance.response_id:
            response_ids_to_delete.insert(0, instance.response_id)

        # Clear response_id on all subsequent bot messages
        # This ensures the next request will rebuild full history instead of using stale previous_response_id
        cleared_count = subsequent_bot_messages.exclude(response_id="").update(
            response_id=""
        )
        if cleared_count > 0:
            logger.info(
                "Cleared response_id on subsequent messages after deletion",
                deleted_message_id=instance.id,
                deleted_message_is_bot=instance.is_bot,
                cleared_count=cleared_count,
            )

        # Queue async deletion of OpenAI responses
        if response_ids_to_delete:
            delete_openai_responses_batch.delay(response_ids_to_delete)
            logger.info(
                "Queued deletion of OpenAI responses for message and subsequent",
                message_id=instance.id,
                response_count=len(response_ids_to_delete),
            )

    except Exception as e:
        logger.exception(f"Message pre delete error: {e}")


class SkillTag(models.Model):
    """Bilingual tag for categorising skills. Translated via modeltranslation."""

    name = models.CharField(max_length=64, unique=True)
    embedding = models.JSONField(
        null=True,
        blank=True,
        help_text="Precomputed embedding vector for similarity-based tag suggestions.",
    )

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self.name_en:
            self.name_en = self.name_en.lower()
        if self.name_fr:
            self.name_fr = self.name_fr.lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or ""


class SkillManager(models.Manager):
    def get_accessible(self, user):
        """Return skills the user can see: own, shared with them, or public."""
        user_id = getattr(user, "id", None)
        model = self.model

        accessible_to_user_exists = model.accessible_to.through.objects.filter(
            skill_id=OuterRef("pk"),
            user_id=user_id,
        )
        editable_by_user_exists = model.editable_by.through.objects.filter(
            skill_id=OuterRef("pk"),
            user_id=user_id,
        )
        accessible_to_team_exists = model.accessible_to_teams.through.objects.filter(
            skill_id=OuterRef("pk"),
            team__memberships__user_id=user_id,
        )
        editable_by_team_exists = model.editable_by_teams.through.objects.filter(
            skill_id=OuterRef("pk"),
            team__memberships__user_id=user_id,
        )

        return (
            self.annotate(
                _accessible_to_user=Exists(accessible_to_user_exists),
                _editable_by_user=Exists(editable_by_user_exists),
                _accessible_to_team=Exists(accessible_to_team_exists),
                _editable_by_team=Exists(editable_by_team_exists),
            )
            .filter(
                Q(owner_id=user_id)
                | Q(sharing_option="everyone")
                | Q(_accessible_to_user=True)
                | Q(_editable_by_user=True)
                | Q(_accessible_to_team=True)
                | Q(_editable_by_team=True)
            )
            .select_related("owner")
            .prefetch_related("skill_tags")
        )

    def create_from_yaml(self, data):
        """Create or restore fixture-backed public Skill objects from skills.yaml.

        Unlike user-created skills, fixture-backed skills should not require a
        separate slug field. We keep the YAML shape aligned with legacy presets
        and match built-ins by their public/system identity plus bilingual
        display names.
        """
        for _fixture_key, skill_data in data.items():
            defaults = {
                "display_name": "",
                "description": "",
                "short_description": "",
                "body": "",
                "required_tools": [],
                "context_hints": [],
                "tags": [],
                "owner": None,
                "sharing_option": "everyone",
                "is_system": True,
                "is_featured": False,
            }
            defaults.update(skill_data)

            identity_q = Q(
                owner__isnull=True,
                display_name_en=defaults.get("display_name_en", "") or "",
                display_name_fr=defaults.get("display_name_fr", "") or "",
            )
            skill = self.filter(identity_q).first()

            if skill is None:
                skill = self.create(**defaults)
            else:
                for field, value in defaults.items():
                    setattr(skill, field, value)
                skill.save()

            skill.accessible_to.clear()
            skill.editable_by.clear()


class Skill(models.Model):
    """
    A skill definition: structured instructions + tool config + context hints.
    """

    objects = SkillManager()

    # Bilingual metadata (modeltranslation will add _en/_fr variants)
    display_name = models.CharField(max_length=255)
    description = models.TextField(
        help_text="Trigger text: what does this skill do and when to use it.",
    )
    short_description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional short description for card display.",
    )

    # SKILL.md body (instructions loaded on demand)
    body = models.TextField(
        help_text="Markdown instructions. Aim for under 500 lines / 5000 chars.",
    )

    # Configuration
    required_tools = models.JSONField(
        default=list,
        blank=True,
        help_text='Tool categories to auto-enable, e.g. ["local_terminology"]',
    )
    context_hints = models.JSONField(
        default=list,
        blank=True,
        help_text='Libraries/docs to hint, e.g. [{"type":"library","id":"123","name":"..."}]',
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Legacy free-form tags (deprecated, use skill_tags M2M).",
    )
    skill_tags = models.ManyToManyField(
        SkillTag,
        blank=True,
        related_name="skills",
    )

    # Ownership & sharing
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_chat_next_skills",
    )
    accessible_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="accessible_chat_next_skills",
    )
    editable_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="editable_chat_next_skills",
    )
    accessible_to_teams = models.ManyToManyField(
        "otto.Team",
        blank=True,
        related_name="accessible_chat_next_skills",
    )
    editable_by_teams = models.ManyToManyField(
        "otto.Team",
        blank=True,
        related_name="editable_chat_next_skills",
    )
    sharing_option = models.CharField(
        max_length=10,
        choices=SHARING_OPTIONS,
        default="private",
    )

    # Flags
    is_system = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)

    # Timestamps
    load_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.display_name or _("Untitled skill")
