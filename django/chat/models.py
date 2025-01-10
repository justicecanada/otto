import re
import uuid

from django.conf import settings
from django.db import connections, models
from django.db.models import BooleanField, Q, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from data_fetcher.util import get_request
from structlog import get_logger

from chat.prompts import current_time_prompt
from librarian.models import DataSource, Library, SavedFile
from librarian.utils.process_engine import guess_content_type
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

    loaded_preset = models.ForeignKey("Preset", on_delete=models.SET_NULL, null=True)

    # AC-20: Allows for the classification of information
    security_label = models.ForeignKey(
        SecurityLabel,
        on_delete=models.SET_NULL,
        null=True,
    )

    def __str__(self):
        return f"Chat {self.id}: {self.title}"

    def delete(self, *args, **kwargs):
        if hasattr(self, "data_source") and self.data_source:
            self.data_source.delete()
        super().delete(*args, **kwargs)


class ChatOptionsManager(models.Manager):
    def from_defaults(self, mode=None, chat=None):
        from chat.utils import copy_options

        """
        If a user default exists, copy that into a new ChatOptions object.
        If not, create a new object with some default settings manually.
        Set the mode and chat FK in the new object.
        """
        if chat and chat.user.default_preset:

            new_options = self.create()
            copy_options(chat.user.default_preset.options, new_options, chat.user)
        else:
            # get default preset
            default_preset = Preset.objects.get_global_default()

            # create a copy of the default preset options
            new_options = self.create()
            copy_options(default_preset.options, new_options, chat.user)

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
    ("summarize", _("Full documents, separate answers ($)")),
    ("summarize_combined", _("Full documents, combined answer ($)")),
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

    # Prompt is only saved/restored for presets
    prompt = models.TextField(blank=True, default="")

    # Chat-specific options
    chat_model = models.CharField(max_length=255, default="gpt-4o")
    chat_temperature = models.FloatField(default=0.1)
    chat_system_prompt = models.TextField(blank=True)
    chat_agent = models.BooleanField(default=False)

    # Summarize-specific options
    summarize_model = models.CharField(max_length=255, default="gpt-4o")
    summarize_style = models.CharField(max_length=255, default="short")
    summarize_language = models.CharField(max_length=255, default="en")
    summarize_instructions = models.TextField(blank=True)
    summarize_prompt = models.TextField(blank=True)
    summarize_gender_neutral = models.BooleanField(default=True)

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
    def get_global_default(self):
        # Check the language of the current request
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            return self.get(french_default=True)
        else:
            return self.get(english_default=True)

    def get_accessible_presets(self, user: User, language: str = None):
        ordering = ["-default", "-favourite"]
        if language:
            ordering.append(f"name_{language}")

        presets = self.filter(
            Q(owner=user) | Q(accessible_to=user) | Q(sharing_option="everyone"),
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

    def create_from_yaml(self, data):
        """
        Create Preset objects from a dictionary loaded from chat/fixtures/presets.yaml
        """
        from chat.utils import copy_options

        assert len(data) >= 2, "YAML file must contain at least two presets"
        created_options = {}
        for item_name, item in data.items():
            item["sharing_option"] = "everyone"
            options_dict = item.pop("options", None)
            # TODO: Consider allowing different libraries for default presets
            options_dict["qa_library"] = Library.objects.get_default_library()
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
    favourited_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="favourited_presets"
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

    def set_as_user_default(self, user: User):
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

    def __str__(self):
        return f"Preset {self.id}: {self.name_en}"


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
        Lookup the node text from the vector DB
        """
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
    filename = models.CharField(max_length=500)
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

    def extract_text(self, pdf_method="default"):

        from librarian.utils.process_engine import (
            extract_markdown,
            get_process_engine_from_type,
        )

        if not self.saved_file:
            return

        with self.saved_file.file.open("rb") as file:
            content = file.read()
            content_type = guess_content_type(
                content, self.saved_file.content_type, self.filename
            )
            process_engine = get_process_engine_from_type(content_type)
            self.text, _ = extract_markdown(
                content, process_engine, pdf_method=pdf_method
            )
            self.save()


@receiver(post_delete, sender=ChatFile)
def delete_saved_file(sender, instance, **kwargs):
    # NOTE: If file was uploaded to chat in Q&A mode, this won't delete unless
    # document is also deleted from librarian modal (or entire chat is deleted)
    try:
        instance.saved_file.safe_delete()
    except Exception as e:
        logger.error(f"Failed to delete saved file: {e}")
