import uuid

from django.conf import settings
from django.db import models
from django.db.models import BooleanField, Q, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rules import is_group_member
from structlog import get_logger

from chat.prompts import (
    DEFAULT_CHAT_PROMPT,
    QA_POST_INSTRUCTIONS,
    QA_PRE_INSTRUCTIONS,
    QA_PROMPT_TEMPLATE,
    QA_SYSTEM_PROMPT,
    current_time_prompt,
)
from librarian.models import DataSource, Library, SavedFile
from otto.models import SecurityLabel, User
from otto.utils.common import display_cad_cost, set_costs

logger = get_logger(__name__)

DEFAULT_MODE = "chat"


def create_chat_data_source(user, chat):
    if not user.personal_library:
        user.create_personal_library()
    return DataSource.objects.create(
        name=f"Chat {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
        library=user.personal_library,
        chat=chat,
    )


class ChatManager(models.Manager):
    def create(self, *args, **kwargs):
        if "mode" in kwargs:
            mode = kwargs.pop("mode")
        else:
            mode = DEFAULT_MODE
        kwargs["security_label_id"] = SecurityLabel.default_security_label().id
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

    # AC-20: Allows for the classification of information
    security_label = models.ForeignKey(
        SecurityLabel,
        on_delete=models.SET_NULL,
        null=True,
    )

    def __str__(self):
        return f"Chat {self.id}: {self.title}"

    def access(self):
        self.accessed_at = timezone.now()
        self.save()


class ChatOptionsManager(models.Manager):
    def from_defaults(self, mode=None, chat=None):
        """
        If a user default exists, copy that into a new ChatOptions object.
        If not, create a new object with some default settings manually.
        Set the mode and chat FK in the new object.
        """
        if chat and chat.user.default_preset:
            new_options = chat.user.default_preset.options
            if new_options:
                new_options.pk = None
        else:
            # Default Otto settings
            default_library = Library.objects.get_default_library()
            new_options = self.create(
                chat_agent=False,
                qa_library=default_library,
                chat_system_prompt=_(DEFAULT_CHAT_PROMPT),
                chat_model=settings.DEFAULT_CHAT_MODEL,
                qa_model=settings.DEFAULT_CHAT_MODEL,
                summarize_model=settings.DEFAULT_CHAT_MODEL,
                qa_prompt_template=_(QA_PROMPT_TEMPLATE),
                qa_pre_instructions=_(QA_PRE_INSTRUCTIONS),
                qa_post_instructions=_(QA_POST_INSTRUCTIONS),
                qa_system_prompt=_(QA_SYSTEM_PROMPT),
            )
        if mode:
            new_options.mode = mode
        if chat:
            new_options.chat = chat
        new_options.save()

        return new_options


QA_SCOPE_CHOICES = [
    ("all", _("Entire library")),
    ("data_sources", _("Selected data sources")),
    ("documents", _("Selected documents")),
]

QA_MODE_CHOICES = [
    ("rag", _("Use top sources only (fast, cheap)")),
    ("summarize", _("Read entire documents (slow, expensive)")),
]

QA_SOURCE_ORDER_CHOICES = [
    ("score", _("Relevance score")),
    ("reading_order", _("Reading order")),
]


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

    # Chat-specific options
    chat_model = models.CharField(max_length=255, default="gpt-4o")
    chat_temperature = models.FloatField(default=0.1)
    chat_system_prompt = models.TextField(blank=True)
    chat_agent = models.BooleanField(default=True)

    # Summarize-specific options
    summarize_model = models.CharField(max_length=255, default="gpt-4o")
    summarize_style = models.CharField(max_length=255, default="short")
    summarize_language = models.CharField(max_length=255, default="en")
    summarize_prompt = models.TextField(blank=True)

    # Translate-specific options
    translate_language = models.CharField(max_length=255, default="fr")

    # QA-specific options
    qa_model = models.CharField(max_length=255, default="gpt-4o")
    qa_library = models.ForeignKey(
        "librarian.Library",
        on_delete=models.SET_NULL,
        null=True,
        related_name="qa_options",
    )
    qa_mode = models.CharField(max_length=20, default="rag", choices=QA_MODE_CHOICES)
    qa_scope = models.CharField(max_length=20, default="all", choices=QA_SCOPE_CHOICES)
    qa_data_sources = models.ManyToManyField(
        "librarian.DataSource", related_name="qa_options"
    )
    qa_documents = models.ManyToManyField(
        "librarian.Document", related_name="qa_options"
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
    qa_answer_mode = models.CharField(max_length=20, default="combined")
    qa_prune = models.BooleanField(default=True)
    qa_rewrite = models.BooleanField(default=False)
    qa_granularity = models.IntegerField(default=768)

    @property
    def qa_prompt_combined(self):
        from llama_index.core import ChatPromptTemplate
        from llama_index.core.llms import ChatMessage, MessageRole

        return ChatPromptTemplate(
            message_templates=[
                ChatMessage(
                    content=current_time_prompt() + self.qa_system_prompt,
                    role=MessageRole.SYSTEM,
                ),
                ChatMessage(
                    content=self.qa_prompt_template,
                    role=MessageRole.USER,
                ),
            ]
        ).partial_format(
            pre_instructions=self.qa_pre_instructions,
            post_instructions=self.qa_post_instructions,
        )

    def clean(self):
        if hasattr(self, "chat") and self.preset.first():
            logger.error(
                "ChatOptions cannot be associated with both a chat AND a user preset.",
            )
            raise ValueError(
                "ChatOptions cannot be associated with both a chat AND a user preset."
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
    def get_accessible_presets(self, user: User, language: str = None):
        ordering = ["-default", "-favourite"]
        if language:
            ordering.append(f"name_{language}")

        is_admin = is_group_member("Otto admin")(user)

        # admins will have access to all presets
        if is_admin:
            presets = self.filter(is_deleted=False)
        else:
            presets = self.filter(
                Q(owner=user) | Q(is_public=True) | Q(accessible_to=user),
                is_deleted=False,
            )
        return (
            presets.distinct()
            .annotate(
                favourite=Coalesce(
                    Q(favourited_by__in=[user]),
                    Value(False),
                    output_field=BooleanField(),
                ),
                default=Coalesce(
                    Q(default_for__in=[user]),
                    Value(False),
                    output_field=BooleanField(),
                ),
            )
            .order_by(*ordering)
        )


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
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_public = models.BooleanField(default=False)

    accessible_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="accessible_presets"
    )
    favourited_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="favourited_presets"
    )
    is_deleted = models.BooleanField(default=False)

    SHARING_OPTIONS = [
        ("private", _("Make Private")),
        ("everyone", _("Share with everyone")),
        ("others", _("Share with others")),
    ]
    sharing_option = models.CharField(
        max_length=10,
        choices=SHARING_OPTIONS,
        default="private",
    )

    @property
    def shared_with(self):
        if self.is_public:
            return "Shared with everyone"
        elif self.accessible_to.exists():
            return "Shared with others"
        return "Private"

    def toggle_favourite(self, user: User):
        """Sets the favourite flag for the preset.
        Returns True if the preset was added to the favourites, False if it was removed.
        Raises ValueError if user is None.
        """

        if user:
            try:
                self.favourited_by.get(pk=user.id)
                self.favourited_by.remove(user)
                return False
            except:
                self.favourited_by.add(user)
                return True
        else:
            logger.error("User must be set to set user default.")
            raise ValueError("User must be set to set user default")

    def delete_preset(self, user: User):
        # TODO: Preset refactor: Delete preset if no other presets are using it
        if self.owner != user:
            logger.error("User is not the owner of the preset.")
            raise ValueError("User is not the owner of the preset.")
        self.is_deleted = True
        self.save()

    def get_description(self, language: str):
        language = language.lower()
        if language == "en":
            return self.description_en if self.description_en else self.description_fr
        else:
            return self.description_fr if self.description_fr else self.description_en

    def set_as_default(self, user: User):
        if user:
            if user.default_preset == self:
                user.default_preset = None
            else:
                user.default_preset = self
            user.save()
            return user.default_preset
        else:
            logger.error("User must be set to set user default.")
            raise ValueError("User must be set to set user default")


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
    usd_cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    pinned = models.BooleanField(default=False)
    # Flexible JSON field for mode-specific details such as translation target language
    details = models.JSONField(default=dict)
    mode = models.CharField(max_length=255, default="chat")
    parent = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, related_name="child"
    )

    def __str__(self):
        return f"{'(BOT) ' if self.is_bot else ''}msg {self.id}: {self.text}"

    @property
    def num_files(self):
        return self.files.count()

    @property
    def sorted_files(self):
        return self.files.all().order_by("created_at")

    @property
    def sources(self):
        return self.answersource_set.all().order_by("id")

    @property
    def display_cost(self):
        return display_cad_cost(self.usd_cost)

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
                check=(Q(parent__isnull=False) & Q(is_bot=True))
                | Q(parent__isnull=True),
                name="check_parent_is_user_message",
            )
        ]


class AnswerSource(models.Model):
    """
    Node from a Document that was used to answer a question. Associated with Message.
    """

    message = models.ForeignKey("Message", on_delete=models.CASCADE)
    document = models.ForeignKey(
        "librarian.Document", on_delete=models.SET_NULL, null=True
    )
    node_text = models.TextField()
    node_score = models.FloatField(default=0.0)
    # Saved citation for cases where the source Document is deleted later
    saved_citation = models.TextField(blank=True)
    group_number = models.IntegerField(default=0)

    def __str__(self):
        document_citation = self.citation
        return f"{document_citation} ({self.node_score:.2f}):\n{self.node_text[:144]}"

    @property
    def html(self):
        from chat.utils import md

        return md.convert(self.node_text)

    @property
    def citation(self):
        return self.document.citation if self.document else self.saved_citation


class ChatFileManager(models.Manager):
    def create(self, *args, **kwargs):
        # If not provided, create the file object
        if not kwargs.get("saved_file"):
            file = SavedFile.objects.create(
                eof=kwargs.pop("eof", False),
                content_type=kwargs.pop("content_type", ""),
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
    filename = models.CharField(max_length=255)
    saved_file = models.ForeignKey(
        SavedFile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="chat_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    eof = models.BooleanField(default=False)
    # The text extracted from the file
    text = models.TextField(blank=True)

    def __str__(self):
        return f"File {self.id}: {self.filename}"

    def extract_text(self, fast=True):

        from librarian.utils.process_engine import (
            extract_markdown,
            get_process_engine_from_type,
        )

        if not self.saved_file:
            return

        process_engine = get_process_engine_from_type(self.saved_file.content_type)
        self.text, _ = extract_markdown(
            self.saved_file.file.read(), process_engine, fast=fast
        )
        self.save()


@receiver(post_delete, sender=ChatFile)
def delete_saved_file(sender, instance, **kwargs):
    # NOTE: If file was uploaded to chat in Q&A mode, this won't delete unless
    # document is also delete from librarian modal (or entire chat is deleted)
    try:
        instance.saved_file.safe_delete()
    except Exception as e:
        logger.error(f"Failed to delete saved file: {e}")
