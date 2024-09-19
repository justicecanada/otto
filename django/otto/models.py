import datetime
import uuid

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    Group,
    PermissionsMixin,
)
from django.db import models
from django.utils.translation import gettext_lazy as _

from otto.utils.common import display_cad_cost


class CustomUserManager(BaseUserManager):
    def create_user(self, upn, password=None, **extra_fields):
        user = self.model(upn=upn, **extra_fields)
        user.save()
        return user

    def create_superuser(self, upn, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(upn, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    # Always store lowercase email addresses. This is done when user is created.
    upn = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    oid = models.CharField(max_length=255, null=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    accepted_terms_date = models.DateField(null=True)
    pilot = models.ForeignKey("Pilot", on_delete=models.SET_NULL, null=True, blank=True)
    default_preset = models.ForeignKey(
        "chat.Preset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for",
    )

    objects = CustomUserManager()

    USERNAME_FIELD = "upn"
    REQUIRED_FIELDS = []

    @property
    def accepted_terms(self):
        return self.accepted_terms_date is not None

    @property
    def lastname_firstname(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def username(self):
        return self.email.split("@")[0]

    @property
    def num_messages(self):
        from chat.models import Chat, Message

        chats = Chat.objects.filter(user=self)
        return Message.objects.filter(chat__in=chats, is_bot=False).count()

    @property
    def roles(self):
        return self.groups.all()

    @property
    def total_cost(self):
        return display_cad_cost(Cost.objects.get_user_cost(self))

    def __str__(self):
        return f"{self.lastname_firstname} ({self.email})"

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
        ]

    def make_otto_admin(self):
        self.groups.add(Group.objects.get(name="Otto admin"))


class UserOptions(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    language = models.CharField(max_length=50, default="en")
    # Hide the settings sidebar by default
    chat_settings_width = models.IntegerField(default=0)

    def __str__(self):
        return f"Options for {self.user.upn}"


class AppManager(models.Manager):

    def create_from_yaml(self, app_data):
        if "user_group" in app_data["fields"]:
            # TODO: Handle user_group translations (may require extending Group model)
            user_group_name = app_data["fields"]["user_group"]
            app_data["fields"]["user_group"], _created = Group.objects.get_or_create(
                name=user_group_name
            )

        features = app_data["fields"].pop("features")
        app = super().create(**app_data["fields"])
        app.save()

        for feature_data in features:
            feature = Feature.objects.create(
                app=app,
                name=feature_data["fields"]["name_en"],
                description=feature_data["fields"]["description_en"],
                url=feature_data["fields"]["url"],
                category=feature_data["fields"]["category"],
                classification=feature_data["fields"].get("classification", ""),
                short_name=feature_data["fields"].get("short_name", None),
            )

            # Set French translations for features if available
            if "name_fr" in feature_data["fields"]:
                feature.name_fr = feature_data["fields"]["name_fr"]
            if "description_fr" in feature_data["fields"]:
                feature.description_fr = feature_data["fields"]["description_fr"]

            feature.save()

        return app

    def visible_to_user(self, user):
        return [app for app in self.all() if user.has_perm("otto.view_app", app)]

    def accessible_to_user(self, user):
        return [app for app in self.all() if user.has_perm("otto.access_app", app)]


class App(models.Model):
    # UUID for legacy reasons; hard to migrate back to int, so leaving it as-is.
    id = models.UUIDField(primary_key=True, editable=False, default=uuid.uuid4)
    objects = AppManager()
    handle = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=200)
    prod_ready = models.BooleanField(default=False)
    visible_to_all = models.BooleanField(default=True)
    user_group = models.ForeignKey(
        "auth.Group", on_delete=models.SET_NULL, blank=True, null=True
    )

    def __str__(self):
        return self.name


class Feature(models.Model):

    CATEGORY_CHOICES = [
        ("ai_assistant", _("AI assistant")),
        ("monitoring", _("Monitoring")),
        ("reporting", _("Reporting")),
        ("other", _("Other")),
    ]

    app = models.ForeignKey(App, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=50, unique=True, null=True)
    description = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    classification = models.CharField(max_length=50, blank=True, null=True)
    url = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class UsageTerm(models.Model):
    term_text = models.CharField(max_length=2000)

    def __str__(self):
        return self.term_text


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    text = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional fields; can be used for progress bars, links, adding an icon, etc.
    heading = models.CharField(max_length=255, blank=True, null=True)
    progress = models.IntegerField(blank=True, null=True)  # 0-100, or None if n/a
    link = models.CharField(max_length=255, blank=True, null=True)
    category = models.CharField(max_length=50, blank=True, null=True)
    level = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return f"{self.heading} - {self.text[:50]}"


class Feedback(models.Model):
    FEEDBACK_TYPE_CHOICES = [
        ("feedback", _("Feedback")),
        ("issue", _("Issue")),
    ]

    feedback_type = models.CharField(
        max_length=50,
        choices=FEEDBACK_TYPE_CHOICES,
        blank=False,
        default=_("Please select an option"),
    )
    app = models.TextField(max_length=200, blank=False)
    otto_version = models.CharField(max_length=12, null=False)
    feedback_message = models.TextField(blank=False)
    chat_message = models.ForeignKey(
        "chat.Message", null=True, on_delete=models.SET_NULL, related_name="message"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.DO_NOTHING
    )


class SecurityLabel(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField()
    acronym = models.CharField(max_length=10, unique=True)

    def __str__(self):
        return self.name

    @classmethod
    def default_security_label(cls):
        return cls.objects.get(acronym_en="UC")

    @classmethod
    def maximum_of(cls, acronyms):
        security_label = cls.objects.filter(acronym__in=acronyms).order_by("pk").last()
        if not security_label:
            security_label = SecurityLabel.default_security_label()
        return security_label


class CostType(models.Model):
    name = models.CharField(max_length=100)
    short_name = models.CharField(max_length=50, unique=True, null=True)
    description = models.TextField()
    # e.g. Token
    unit_name = models.CharField(max_length=50, default="units")
    # e.g. 0.00015 ($ USD)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=6, default=1)
    # e.g. 1000
    unit_quantity = models.IntegerField(default=1)

    @property
    def cost_per_unit(self):
        return self.unit_cost / self.unit_quantity

    def __str__(self):
        return self.name


class CostManager(models.Manager):

    def new(self, cost_type: str, count: int) -> "Cost":
        from structlog.contextvars import get_contextvars

        from chat.models import Message
        from librarian.models import Document

        cost_type = CostType.objects.get(short_name=cost_type)

        # The rest of the fields are optional & stored in structlog request context
        request_context = get_contextvars()
        message_id = request_context.get("message_id")
        document_id = request_context.get("document_id")
        user_id = request_context.get("user_id")

        cost_object = self.create(
            cost_type=cost_type,
            count=count,
            usd_cost=(count * cost_type.unit_cost) / cost_type.unit_quantity,
            # Optional fields from request context
            feature=request_context.get("feature"),
            request_id=request_context.get("request_id"),
            user=User.objects.get(id=user_id) if user_id else None,
            message=Message.objects.get(id=message_id) if message_id else None,
            document=Document.objects.get(id=document_id) if document_id else None,
        )

        # Recalculate document and message costs, if applicable
        if cost_object.document:
            cost_object.document.calculate_costs()
        if cost_object.message:
            cost_object.message.calculate_costs()

        return cost_object

    def get_user_cost(self, user):
        # Total cost for a user
        return sum(cost.usd_cost for cost in self.filter(user=user))

    def get_user_cost_by_type(self, user, cost_type):
        # Total cost for a user by cost type
        return sum(
            cost.usd_cost for cost in self.filter(user=user, cost_type=cost_type)
        )

    def get_user_cost_by_feature(self, user, feature):
        # Total cost for a user by feature
        return sum(cost.usd_cost for cost in self.filter(user=user, feature=feature))

    def get_total_cost(self):
        # Total cost for all users
        return sum(cost.usd_cost for cost in self.all())

    def get_total_cost_by_type(self, cost_type):
        # Total cost for all users by cost type
        return sum(cost.usd_cost for cost in self.filter(cost_type=cost_type))

    def get_total_cost_by_feature(self, feature):
        # Total cost for all users by feature
        return sum(cost.usd_cost for cost in self.filter(feature=feature))

    def get_user_cost_today(self, user):
        # Total cost for a user today
        return sum(
            cost.usd_cost
            for cost in self.filter(
                user=user, date_incurred__date=datetime.date.today()
            )
        )

    def get_pilot_cost(self, pilot):
        # Total cost for a pilot
        return sum(cost.usd_cost for cost in self.filter(user__pilot=pilot))


FEATURE_CHOICES = [
    ("librarian", _("Librarian")),
    ("qa", _("Q&A")),
    ("chat", _("Chat")),
    ("translate", _("Translate")),
    ("summarize", _("Summarize")),
    ("template_wizard", _("Template wizard")),
    ("laws_query", _("Legislation search")),
    ("laws_load", _("Legislation loading")),
    ("case_prep", _("Case prep assistant")),
    ("text_extractor", _("Text extractor")),
]


class Cost(models.Model):
    """Tracks costs in US dollars for API calls"""

    # Required
    cost_type = models.ForeignKey(CostType, on_delete=models.CASCADE)
    count = models.IntegerField(default=1)

    # Automatically added/calculated
    date_incurred = models.DateField(auto_now_add=True)
    usd_cost = models.DecimalField(max_digits=12, decimal_places=6)

    # Optional, for aggregation and reporting
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    feature = models.CharField(
        max_length=50, null=True, blank=True, choices=FEATURE_CHOICES
    )

    # Optional, for debugging purposes
    request_id = models.CharField(max_length=50, null=True, blank=True)
    message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, null=True, blank=True
    )
    document = models.ForeignKey(
        "librarian.Document", on_delete=models.CASCADE, null=True, blank=True
    )

    objects = CostManager()

    def __str__(self):
        user_str = self.user.username if self.user else _("Otto")
        return f"{user_str} - {self.feature} - {self.cost_type.name} - {display_cad_cost(self.usd_cost)}"


class Pilot(models.Model):
    """For pilot governance. Pilot users will have a FK to this model."""

    pilot_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    service_unit = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)

    def __str__(self):
        return self.name

    @property
    def user_count(self):
        return User.objects.filter(pilot=self).count()

    @property
    def total_cost(self):
        return display_cad_cost(Cost.objects.get_pilot_cost(self))
