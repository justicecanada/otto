import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from structlog import get_logger

from chat.prompts import (
    DEFAULT_CHAT_PROMPT,
    QA_POST_INSTRUCTIONS,
    QA_PRE_INSTRUCTIONS,
    QA_PROMPT_TEMPLATE,
    QA_SYSTEM_PROMPT,
)
from librarian.models import Library, SavedFile
from otto.models import SecurityLabel

logger = get_logger(__name__)

DEFAULT_MODE = "qa"


class ChatManager(models.Manager):
    def create(self, *args, **kwargs):
        if "mode" in kwargs:
            mode = kwargs.pop("mode")
        else:
            mode = DEFAULT_MODE
        kwargs["options"] = ChatOptions.objects.from_defaults(
            user=kwargs["user"], mode=mode
        )
        kwargs["security_label_id"] = SecurityLabel.default_security_label().id
        return super().create(*args, **kwargs)


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

    security_label = models.ForeignKey(
        SecurityLabel,
        on_delete=models.SET_NULL,
        null=True,
    )

    options = models.OneToOneField(
        "ChatOptions", on_delete=models.CASCADE, related_name="chat", null=True
    )

    def __str__(self):
        return f"Chat {self.id}: {self.title}"

    def access(self):
        self.accessed_at = timezone.now()
        self.save()


class ChatOptionsManager(models.Manager):
    def from_defaults(self, mode=None, user=None):
        """
        If a user default exists, copy that into a new ChatOptions object.
        If not, create a new object with some default settings manually.
        Set the mode and chat FK in the new object.
        """
        if user:
            user_default = (
                self.get_queryset().filter(user=user, user_default=True).first()
            )
        if user and user_default:
            new_options = user_default
            new_options.pk = None
            if mode:
                new_options.mode = mode
            new_options.save()
        else:
            # Default Otto settings
            default_library = Library.objects.get_default_library()
            new_options = self.create(
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
            new_options.save()
            if default_library:
                new_options.qa_data_sources.set(default_library.data_sources.all())

        return new_options


class ChatOptions(models.Model):
    """
    Options for a chat, e.g. the mode, custom prompts, etc.
    """

    objects = ChatOptionsManager()

    # Default case: ChatOptions object is associated with a particular chat.
    # (Does not show up in the list of option presets for a user.)

    # Second case: user options preset. One user can have many presets.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        related_name="chat_options",
    )
    preset_name = models.CharField(max_length=255, blank=True)
    # Third case (can overlap with 2nd case): User default options.
    user_default = models.BooleanField(default=False)

    mode = models.CharField(max_length=255, default=DEFAULT_MODE)

    # Chat-specific options
    chat_model = models.CharField(max_length=255, default="gpt-4o")
    chat_temperature = models.FloatField(default=0.1)
    chat_system_prompt = models.TextField(blank=True)

    # Summarize-specific options
    summarize_model = models.CharField(max_length=255, default="gpt-4o")
    summarize_style = models.CharField(max_length=255, default="short")
    summarize_language = models.CharField(max_length=255, default="en")
    summarize_prompt = models.TextField(blank=True)

    # Translate-specific options
    translate_language = models.CharField(max_length=255, default="fr")

    # Library QA-specific options
    qa_model = models.CharField(max_length=255, default="gpt-4o")
    qa_library = models.ForeignKey(
        "librarian.Library",
        on_delete=models.SET_NULL,
        null=True,
        related_name="qa_options",
    )
    qa_data_sources = models.ManyToManyField(
        "librarian.DataSource", related_name="qa_options"
    )
    qa_topk = models.IntegerField(default=5)
    qa_system_prompt = models.TextField(blank=True)
    qa_prompt_template = models.TextField(blank=True)
    qa_pre_instructions = models.TextField(blank=True)
    qa_post_instructions = models.TextField(blank=True)
    qa_source_order = models.CharField(max_length=20, default="score")
    qa_vector_ratio = models.FloatField(default=0.6)
    qa_answer_mode = models.CharField(max_length=20, default="combined")
    qa_prune = models.BooleanField(default=True)
    qa_rewrite = models.BooleanField(default=False)

    @property
    def qa_prompt_combined(self):
        from llama_index.core import ChatPromptTemplate
        from llama_index.core.llms import ChatMessage, MessageRole

        return ChatPromptTemplate(
            message_templates=[
                ChatMessage(
                    content=self.qa_system_prompt,
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
        if hasattr(self, "chat") and self.user:
            logger.error(
                "ChatOptions cannot be associated with both a chat AND a user.",
            )
            raise ValueError(
                "ChatOptions cannot be associated with both a chat AND a user."
            )

    def make_user_default(self):
        if self.user:
            self.user.chat_options.filter(user_default=True).update(user_default=False)
            self.user_default = True
            self.save()
        else:
            logger.error("User must be set to set user default.")
            raise ValueError("User must be set to set user default")

    def save(self, *args, **kwargs):
        self.clean()
        new = self.pk is None
        if not new:
            orig = ChatOptions.objects.get(pk=self.pk)
            if orig.qa_library != self.qa_library:
                logger.info("Chat library selection changed. Resetting data_sources.")
                self.qa_data_sources.set(self.qa_library.data_sources.all())
        super().save(*args, **kwargs)


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
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    pinned = models.BooleanField(default=False)
    # Flexible JSON field for mode-specific details such as translation target language
    details = models.JSONField(default=dict)
    mode = models.CharField(max_length=255, default="chat")
    parent = models.OneToOneField(
        "self", on_delete=models.CASCADE, null=True, related_name="child"
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
        return self.answersource_set.all().order_by("-node_score")

    @property
    def display_cost(self):
        approx_cost_cad = float(self.cost) * settings.USD_TO_CAD
        if approx_cost_cad < 0.005:
            return "< $0.01"
        return f"${approx_cost_cad:.2f}"

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
        # TODO: Extracting text from file may incur Azure Document AI costs.
        # Need to refactor extract_text to create Cost object with correct user and mode.
        # (Presently, this is only used in summarize mode, and user can be inferred...)
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
