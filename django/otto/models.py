import datetime
import ipaddress
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    Group,
    PermissionsMixin,
)
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from structlog import get_logger

from otto.utils.common import cad_cost, display_cad_cost
from otto.utils.request_cache import quiet_cache_within_request as cache_within_request

logger = get_logger(__name__)


class CustomUserManager(BaseUserManager):
    @staticmethod
    def normalize_upn(upn):
        if upn is None:
            return upn
        return upn.strip().lower()

    def find_by_upn(self, upn, include_inactive=True):
        normalized_upn = self.normalize_upn(upn)
        if not normalized_upn:
            return None

        queryset = self.get_queryset().filter(upn__iexact=normalized_upn)
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        return queryset.order_by("-is_active", "id").first()

    def get_by_natural_key(self, username):
        user = self.find_by_upn(username, include_inactive=True)
        if user is None:
            raise self.model.DoesNotExist(
                f"{self.model._meta.object_name} matching query does not exist."
            )
        return user

    def create_user(self, upn, password=None, **extra_fields):
        extra_fields.setdefault(
            "entra_status",
            self.model.EntraStatus.ACTIVE
            if extra_fields.get("is_active", True)
            else self.model.EntraStatus.UNKNOWN,
        )
        user = self.model(upn=self.normalize_upn(upn), **extra_fields)
        user.save()
        # Create personal library
        user.create_personal_library()
        return user

    def create_superuser(self, upn, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(upn, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class EntraStatus(models.TextChoices):
        ACTIVE = "active", _("Active")
        DISABLED = "disabled", _("Disabled")
        DELETED = "deleted", _("Deleted")
        UNKNOWN = "unknown", _("Unknown")

    AI_ASSISTANT_CHOICES = [
        ("chat", "chat"),
        ("chat_next", "chat_next"),
    ]

    objects = CustomUserManager()
    upn = models.CharField(max_length=255)
    email = models.EmailField()
    oid = models.CharField(max_length=255, null=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    entra_status = models.CharField(
        max_length=20,
        choices=EntraStatus,
        default=EntraStatus.UNKNOWN,
    )
    job_title = models.CharField(max_length=255, blank=True, default="")
    preferred_language = models.CharField(max_length=80, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    accepted_terms_date = models.DateField(null=True)
    default_preset = models.ForeignKey(
        "chat.Preset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for",
    )
    default_ai_assistant = models.CharField(
        max_length=20,
        choices=AI_ASSISTANT_CHOICES,
        default="chat",
    )
    monthly_max = models.IntegerField(default=settings.DEFAULT_MONTHLY_MAX)
    monthly_bonus = models.IntegerField(default=0)  # Resets each month to 0
    homepage_tour_completed = models.BooleanField(default=False)
    ai_assistant_tour_completed = models.BooleanField(default=False)
    laws_search_tour_completed = models.BooleanField(default=False)
    chat_next_tour_completed = models.BooleanField(default=False)

    USERNAME_FIELD = "upn"
    REQUIRED_FIELDS = []

    @cache_within_request
    def _check_is_admin(self):
        """Check if user is member of Otto admin group - cached per request"""
        return self.groups.filter(name=settings.OTTO_ADMIN_GROUP).exists()

    @cache_within_request
    def _check_is_operations_admin(self):
        """Check if user is member of Operations admin or Otto admin group - cached per request"""
        return self.groups.filter(
            name__in=[settings.OTTO_OPERATIONS_ADMIN_GROUP, settings.OTTO_ADMIN_GROUP]
        ).exists()

    @cache_within_request
    def _check_is_bulk_uploader(self):
        """Check if user is member of Bulk uploader group - cached per request"""
        return self.groups.filter(name__in=[settings.OTTO_BULK_UPLOADER_GROUP]).exists()

    @property
    def is_admin(self):
        return self._check_is_admin()

    @property
    def is_operations_admin(self):
        return self._check_is_operations_admin()

    @property
    def is_bulk_uploader(self):
        return self._check_is_bulk_uploader()

    @cache_within_request
    def _check_is_data_steward(self):
        """Backward-compatible alias for _check_is_bulk_uploader."""
        return self._check_is_bulk_uploader()

    @property
    def is_data_steward(self):
        """Backward-compatible alias for is_bulk_uploader."""
        return self.is_bulk_uploader

    @property
    def is_public_sharing_admin(self):
        # Check if user is a Public sharing admin
        return self.groups.filter(
            name__in=[settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP]
        ).exists()

    @property
    def accepted_terms(self):
        return self.accepted_terms_date is not None

    @property
    def default_ai_assistant_route(self):
        if self.default_ai_assistant == "chat_next" and self.has_perm(
            "otto.can_access_chat_next"
        ):
            return "chat_next:new_chat"
        return "chat:new_chat"

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
        return f"{cad_cost(Cost.objects.get_user_cost(self)):.2f}"

    @property
    def this_month_max(self):
        return self.monthly_max + self.monthly_bonus

    @property
    def is_over_budget(self):
        # Only personal costs (not cost_group costs) count against personal budget
        return (
            cad_cost(Cost.objects.get_user_cost_this_month(self)) >= self.this_month_max
        )

    def __str__(self):
        return f"{self.lastname_firstname} ({self.email})"

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("upn"),
                condition=Q(is_active=True),
                name="otto_user_active_upn_ci_unique",
            )
        ]

    def save(self, *args, **kwargs):
        if self.upn:
            self.upn = User.objects.normalize_upn(self.upn)
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def make_otto_admin(self):
        self.groups.add(Group.objects.get(name=settings.OTTO_ADMIN_GROUP))

    # When user is deleted, their personal library should be also
    def delete(self, *args, **kwargs):
        from librarian.models import Library

        Library.objects.filter(created_by=self, is_personal_library=True).delete()
        Library.objects.filter(created_by=self, is_skill_library=True).delete()
        super().delete(*args, **kwargs)

    @property
    def personal_library(self):
        from librarian.models import Library

        @cache_within_request
        def _get_personal_library(user_id):
            return Library.objects.filter(
                created_by_id=user_id, is_personal_library=True
            ).first()

        return _get_personal_library(self.id)

    @property
    def skill_library(self):
        from librarian.models import Library

        @cache_within_request
        def _get_skill_library(user_id):
            return Library.objects.filter(
                created_by_id=user_id, is_skill_library=True
            ).first()

        return _get_skill_library(self.id)

    def create_personal_library(self):
        from librarian.models import Library, LibraryUserRole

        new_personal_library = Library.objects.create(
            name_en=self.full_name,
            name_fr=self.full_name,
            created_by=self,
            is_personal_library=True,
            description_en=f"Personal library for {self.upn}. Files uploaded to chats will be saved here.",
            description_fr=f"Bibliothèque personnels pour {self.upn}. Les fichiers téléchargés dans les chats seront enregistrés ici.",
        )
        LibraryUserRole.objects.create(
            user=self,
            library=new_personal_library,
            role="admin",
        )
        return new_personal_library

    def create_skill_library(self):
        from librarian.models import Library, LibraryUserRole

        new_skill_library = Library.objects.create(
            name_en=self.full_name,
            name_fr=self.full_name,
            created_by=self,
            is_skill_library=True,
            description_en=f"Skill files for {self.upn}. Files uploaded to skills will be saved here.",
            description_fr=f"Fichiers de compétences pour {self.upn}. Les fichiers téléchargés dans les compétences seront enregistrés ici.",
        )
        LibraryUserRole.objects.create(
            user=self,
            library=new_skill_library,
            role="admin",
        )
        return new_skill_library

    def get_active_cost_group(self, request):
        """
        Get the active cost group for cost tracking from session.
        Returns None if no cost group is selected.
        """
        if hasattr(request, "session") and "selected_cost_group_id" in request.session:
            cost_group_id = request.session["selected_cost_group_id"]
            try:
                return CostGroup.objects.get(id=cost_group_id)
            except CostGroup.DoesNotExist:
                # Clear invalid session data
                del request.session["selected_cost_group_id"]
        return None

    def set_active_cost_group(self, request, cost_group):
        """Set temporary cost group for cost tracking in session"""
        if cost_group:
            request.session["selected_cost_group_id"] = cost_group.id
        elif "selected_cost_group_id" in request.session:
            del request.session["selected_cost_group_id"]

    def clear_active_cost_group(self, request):
        """Clear temporary cost group selection"""
        if "selected_cost_group_id" in request.session:
            del request.session["selected_cost_group_id"]


class UserOptions(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    language = models.CharField(max_length=50, default="en")
    # Hide the settings sidebar by default
    chat_settings_width = models.IntegerField(default=0)

    def __str__(self):
        return f"Options for {self.user.upn}"


class Visitor(models.Model):
    user = models.OneToOneField(
        User, null=False, related_name="visitor", on_delete=models.CASCADE
    )
    session_key = models.CharField(null=False, max_length=40)


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional fields; can be used for progress bars, links, adding an icon, etc.
    heading = models.CharField(max_length=255, blank=True, null=True)
    progress = models.IntegerField(blank=True, null=True)  # 0-100, or None if n/a
    link = models.CharField(max_length=255, blank=True, null=True)
    category = models.CharField(max_length=50, blank=True, null=True)
    level = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return f"{self.heading} - {self.text[:50]}"


class FeedbackManager(models.Manager):
    def get_feedback_stats(self):
        from django.db.models import Count

        total_feedback_count = self.all().count()
        negative_chat_comment = self.filter(
            models.Q(chat_message__feedback=-1)
            | models.Q(chat_message_next__feedback=-1)
        ).count()
        resolved_feedback_count = self.filter(status="resolved").count()
        new_feedback_count = self.filter(status="new").count()
        in_progress_feedback_count = self.filter(status="in_progress").count()
        deferred_feedback_count = self.filter(status="deferred").count()
        closed_feedback_count = self.filter(status="closed").count()
        most_active = (
            self.values("app")
            .annotate(feedback_count=Count("id"))
            .order_by("-feedback_count")
            .first()
        )
        return {
            "total": total_feedback_count,
            "negative": negative_chat_comment,
            "resolved": resolved_feedback_count,
            "most_active": most_active,
            "new": new_feedback_count,
            "in_progress": in_progress_feedback_count,
            "deferred": deferred_feedback_count,
            "closed": closed_feedback_count,
        }


class Feedback(models.Model):
    FEEDBACK_TYPE_CHOICES = [
        ("feedback", _("Feedback")),
        ("bug", _("Bug")),
        ("question", _("Question")),
        ("feature_request", _("Feature request")),
        ("other", _("Other")),
    ]

    FEEDBACK_STATUS_CHOICES = [
        ("new", _("New")),
        ("in_progress", _("In progress")),
        ("deferred", _("Deferred")),
        ("resolved", _("Resolved")),
        ("closed", _("Closed")),
    ]

    PRIOTITY_CHOICES = [
        ("low", _("Low")),
        ("medium", _("Medium")),
        ("high", _("High")),
    ]

    feedback_type = models.CharField(
        max_length=50,
        choices=FEEDBACK_TYPE_CHOICES,
        blank=False,
        default="feedback",
    )
    status = models.CharField(
        max_length=16, choices=FEEDBACK_STATUS_CHOICES, blank=False, default="new"
    )
    priority = models.CharField(
        max_length=16, choices=PRIOTITY_CHOICES, blank=False, default="low"
    )
    app = models.TextField(max_length=200, blank=False)
    otto_version = models.CharField(max_length=50, null=False)
    feedback_message = models.TextField(blank=False)
    url_context = models.CharField(max_length=2048, blank=True)
    chat_message = models.ForeignKey(
        "chat.Message", null=True, on_delete=models.SET_NULL, related_name="message"
    )
    chat_message_next = models.ForeignKey(
        "chat_next.Message",
        null=True,
        on_delete=models.SET_NULL,
        related_name="feedback_set",
    )
    admin_notes = models.TextField(blank=True)
    # Snapshot of the preset/options at the time the feedback was created
    loaded_preset = models.ForeignKey(
        "chat.Preset",
        null=True,
        on_delete=models.SET_NULL,
        related_name="feedbacks",
    )
    preset_snapshot = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="feedback",
    )
    modified_on = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="modified_feedback",
    )

    objects = FeedbackManager()

    class Meta:
        indexes = [
            # Index for filtering and ordering feedback by status and date
            models.Index(fields=["status", "-created_at"]),
            # Index for filtering by feedback type
            models.Index(fields=["feedback_type", "-created_at"]),
            # Index for filtering by app
            models.Index(fields=["app", "-created_at"]),
        ]


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
        from chat_next.models import Message as MessageNext
        from structlog.contextvars import get_contextvars

        from chat.models import Message
        from librarian.models import Document

        cost_type = CostType.objects.get(short_name=cost_type)

        # The rest of the fields are optional & stored in structlog request context
        request_context = get_contextvars()
        message_id = request_context.get("message_id")
        message_next_id = request_context.get("message_next_id")
        document_id = request_context.get("document_id")
        law_id = request_context.get("law_id")
        feature = request_context.get("feature")
        request_id = request_context.get("request_id")
        # Truncate request_id if too long to prevent DB errors (max_length=100)
        if request_id and len(request_id) > 100:
            request_id = request_id[:100]
        user_id = request_context.get("user_id")
        cost_group_id = request_context.get("cost_group_id")

        # Determine user and cost_group from IDs in context
        user = None
        cost_group = None

        # Laws loading always uses Otto admin cost group, but can track the user who initiated it
        if feature == "laws_load":
            # Always use Otto administration cost group for laws loading
            cost_group = CostGroup.objects.filter(cost_group_id="otto-admin").first()
            # But still track the user who initiated it (if any)
            if user_id:
                user = User.objects.filter(id=user_id).first()
        else:
            if user_id:
                user = User.objects.filter(id=user_id).first()
            if cost_group_id:
                cost_group = CostGroup.objects.filter(id=cost_group_id).first()

        cost_object = self.create(
            cost_type=cost_type,
            count=count,
            usd_cost=(count * cost_type.unit_cost) / cost_type.unit_quantity,
            # Optional fields from request context
            feature=feature,
            request_id=request_id,
            user=user,
            cost_group=cost_group,
            # Set FK IDs directly to avoid unnecessary object fetches
            message_id=message_id,
            message_next_id=message_next_id,
            document_id=document_id,
            law_id=law_id,
        )

        logger.info(
            "cost_object_created",
            cost_id=cost_object.id,
            cost_type_short_name=cost_type.short_name,
            count=count,
            usd_cost=float(cost_object.usd_cost),
            feature=cost_object.feature,
            request_id=cost_object.request_id,
            user_id=cost_object.user_id,
            cost_group_id=cost_object.cost_group_id,
            message_id=cost_object.message_id,
            message_next_id=cost_object.message_next_id,
            document_id=cost_object.document_id,
            law_id=cost_object.law_id,
            context_message_id=message_id,
            context_message_next_id=message_next_id,
            context_feature=feature,
            context_user_id=user_id,
            context_cost_group_id=cost_group_id,
        )

        # Recalculate document and message costs, if applicable
        if document_id:
            try:
                Document.objects.get(id=document_id).calculate_costs()
            except Document.DoesNotExist:
                pass
        if message_id:
            try:
                Message.objects.get(id=message_id).calculate_costs()
            except Message.DoesNotExist:
                pass
        if message_next_id:
            try:
                MessageNext.objects.get(id=message_next_id).calculate_costs()
            except MessageNext.DoesNotExist:
                pass

        return cost_object

    def _sum_cost(self, **filters):
        """Helper: aggregate usd_cost with DB-level SUM instead of Python sum."""
        return self.filter(**filters).aggregate(total=Sum("usd_cost"))["total"] or 0

    def get_user_cost(self, user):
        # Total cost for a user
        return self._sum_cost(user=user)

    def get_user_cost_by_type(self, user, cost_type):
        # Total cost for a user by cost type
        return self._sum_cost(user=user, cost_type=cost_type)

    def get_user_cost_by_feature(self, user, feature):
        # Total cost for a user by feature
        return self._sum_cost(user=user, feature=feature)

    def get_user_cost_today(self, user):
        """Total cost for a user today (excludes cost group costs)"""
        return self._sum_cost(
            user=user,
            cost_group__isnull=True,
            date_incurred=datetime.date.today(),
        )

    def get_user_cost_this_month(self, user):
        """Total cost for a user this month to date (starting 1st of the month)
        Excludes costs tracked to cost groups.
        """
        month_start_date = datetime.date.today().replace(day=1)
        return self._sum_cost(
            user=user,
            cost_group__isnull=True,
            date_incurred__gte=month_start_date,
            date_incurred__lte=datetime.date.today(),
        )

    def get_total_cost(self):
        # Total cost for all users
        return self.aggregate(total=Sum("usd_cost"))["total"] or 0

    def get_total_cost_by_type(self, cost_type):
        # Total cost for all users by cost type
        return self._sum_cost(cost_type=cost_type)

    def get_total_cost_by_feature(self, feature):
        # Total cost for all users by feature
        return self._sum_cost(feature=feature)

    def get_cost_group_cost(self, cost_group):
        # Total cost for a cost group
        return self._sum_cost(cost_group=cost_group)

    def get_cost_group_cost_today(self, cost_group):
        """Total cost for a cost group today"""
        return self._sum_cost(
            cost_group=cost_group,
            date_incurred=datetime.date.today(),
        )

    def get_cost_group_cost_this_month(self, cost_group):
        """Total cost for a cost group this month to date (starting 1st of the month)"""
        month_start_date = datetime.date.today().replace(day=1)
        return self._sum_cost(
            cost_group=cost_group,
            date_incurred__gte=month_start_date,
            date_incurred__lte=datetime.date.today(),
        )

    def get_total_number_of_queries_legislation_search(self):
        return self.filter(feature="laws_query").count()

    def get_user_number_of_queries_legislation_search(self, user):
        return self.filter(feature="laws_query", user=user).count()

    def get_total_number_files_uploaded_text_extractor(self):
        return self.filter(feature="text_extractor").count()

    def get_total_number_of_chat_messages_sent(self):
        return (
            self.filter(
                feature__in=["chat", "chat_next", "qa", "translate", "summarize"]
            )
            .filter(cost_type__name__contains="input")
            .count()
        )

    def get_total_number_of_chat_tokens(self):
        from django.db.models import F

        return (
            self.filter(
                feature__in=["chat", "chat_next", "qa", "translate", "summarize"],
                cost_type__name__contains="input",
            ).aggregate(total=Sum(F("count") * F("cost_type__unit_quantity")))["total"]
            or 0
        )

    def get_total_number_of_librarian_embedding_cost_objects(self):
        return (
            self.filter(feature="librarian")
            .filter(cost_type__short_name__contains="embedding")
            .count()
        )

    def get_total_number_of_librarian_embedding_tokens(self):
        from django.db.models import F

        return (
            self.filter(
                feature="librarian",
                cost_type__short_name__contains="embedding",
            ).aggregate(total=Sum(F("count") * F("cost_type__unit_quantity")))["total"]
            or 0
        )


FEATURE_CHOICES = [
    ("librarian", _("Librarian")),
    ("qa", _("Q&A")),
    ("chat", _("Chat")),
    ("chat_next", _("AI Assistant (preview)")),
    ("translate", _("Translate")),
    ("summarize", _("Summarize")),
    ("template_wizard", _("Template Wizard")),
    ("laws_query", _("Legislation Search")),
    ("laws_load", _("Legislation loading")),
    ("text_extractor", _("Text Extractor")),
    ("load_test", _("Load test")),
]

CHAT_TYPE_CHOICES = [
    ("chat", _("Chat")),
    ("chat_next", _("AI Assistant (preview)")),
    ("qa", _("Q&A")),
    ("summarize", _("Summarize")),
    ("translate", _("Translate")),
]

CHAT_FEATURES = ["chat", "chat_next", "qa", "summarize", "translate"]

COUNT_TYPE_CHOICES = [
    ("chat_messages", _("Chat messages")),
    ("input_tokens", _("Input tokens")),
    ("output_tokens", _("Output tokens")),
    ("embedding_tokens", _("Embedding tokens")),
    ("files_created", _("Files created")),
    ("laws_query", _("Legislation queries")),
]


class Cost(models.Model):
    """Tracks costs in US dollars for API calls"""

    # Required
    cost_type = models.ForeignKey(CostType, on_delete=models.PROTECT, null=True)
    count = models.IntegerField(default=1)
    cost_group = models.ForeignKey("CostGroup", on_delete=models.PROTECT, null=True)

    # Automatically added/calculated
    date_incurred = models.DateField(auto_now_add=True)
    usd_cost = models.DecimalField(max_digits=12, decimal_places=6)

    # Optional, for aggregation and reporting
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
    feature = models.CharField(
        max_length=50, null=True, blank=True, choices=FEATURE_CHOICES
    )

    # Optional, for debugging purposes
    request_id = models.CharField(max_length=100, null=True, blank=True)
    message = models.ForeignKey("chat.Message", on_delete=models.SET_NULL, null=True)
    message_next = models.ForeignKey(
        "chat_next.Message", on_delete=models.SET_NULL, null=True
    )
    document = models.ForeignKey(
        "librarian.Document", on_delete=models.SET_NULL, null=True
    )
    law = models.ForeignKey("laws.Law", on_delete=models.SET_NULL, null=True)

    objects = CostManager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "date_incurred"]),
            models.Index(fields=["user", "cost_group", "date_incurred"]),
            models.Index(fields=["cost_group", "date_incurred"]),
            models.Index(fields=["feature", "date_incurred"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else _("Otto")
        return f"{user_str} - {self.feature} - {self.cost_type.name} - {display_cad_cost(self.usd_cost)}"


class CostGroup(models.Model):
    """Optional cost group to which users can be assigned for cost tracking purposes."""

    cost_group_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    lex_file_number = models.CharField(max_length=50, null=True, blank=True)
    date_created = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    monthly_max = models.IntegerField(
        default=300,
        help_text="Monthly budget limit in CAD for this cost group",
    )
    # Users who can switch to this cost group (in addition to admins)
    users = models.ManyToManyField(
        User,
        related_name="available_cost_groups",
        blank=True,
        help_text="Users who can select this cost group for cost tracking",
    )

    def __str__(self):
        return self.name

    @property
    def total_cost(self):
        return display_cad_cost(Cost.objects.get_cost_group_cost(self))

    @property
    def is_over_budget(self):
        """Check if cost group has exceeded its monthly budget"""
        return (
            cad_cost(Cost.objects.get_cost_group_cost_this_month(self))
            >= self.monthly_max
        )

    @classmethod
    def get_available_cost_groups(cls, user):
        """
        Get cost groups available for the user to switch to.
        - Admins can see all active cost groups
        - Other users can only see cost groups they're explicitly added to
        """
        if user.is_admin:
            return cls.objects.filter(active=True).order_by("name")
        return user.available_cost_groups.filter(active=True).order_by("name")


TEAM_ROLE_CHOICES = [
    ("admin", _("Admin")),
    ("member", _("Member")),
]


class Team(models.Model):
    """A named group of users for sharing resources (libraries, presets, skills)."""

    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_teams",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="otto_team_name_ci_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def admins(self):
        return User.objects.filter(
            team_memberships__team=self, team_memberships__role="admin"
        )

    @property
    def members(self):
        return User.objects.filter(
            team_memberships__team=self, team_memberships__role="member"
        )

    @property
    def all_users(self):
        return User.objects.filter(team_memberships__team=self)


class TeamMembership(models.Model):
    """Junction table linking users to teams with a role."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="team_memberships"
    )
    role = models.CharField(max_length=10, choices=TEAM_ROLE_CHOICES, default="member")

    class Meta:
        unique_together = ["team", "user"]

    def __str__(self):
        return f"{self.user} in {self.team}: {self.role}"


class ApiClient(models.Model):
    TOKEN_PREFIX = "otto_api"

    name = models.CharField(max_length=255, unique=True)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_api_clients",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_api_clients",
    )
    is_active = models.BooleanField(default=True)
    secret_hash = models.CharField(max_length=255, blank=True, default="")
    secret_last_rotated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "name"]),
            models.Index(fields=["owner", "is_active"]),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def parse_token(cls, token: str):
        prefix = f"{cls.TOKEN_PREFIX}_"
        if not token or not token.startswith(prefix) or "." not in token:
            return None

        public_hex, secret = token.removeprefix(prefix).split(".", 1)
        try:
            public_id = uuid.UUID(hex=public_hex)
        except ValueError:
            return None

        if not secret:
            return None

        return public_id, secret

    def issue_token(self) -> str:
        secret = secrets.token_urlsafe(32)
        self.secret_hash = make_password(secret)
        self.secret_last_rotated_at = timezone.now()
        self.save(
            update_fields=["secret_hash", "secret_last_rotated_at", "modified_at"]
        )
        return f"{self.TOKEN_PREFIX}_{self.public_id.hex}.{secret}"

    def check_secret(self, secret: str) -> bool:
        return bool(
            self.secret_hash and secret and check_password(secret, self.secret_hash)
        )

    def has_scope(self, scope: str) -> bool:
        return self.scope_assignments.filter(scope=scope).exists()

    def is_ip_allowed(self, ip_address: str | None) -> bool:
        configured_ranges = list(self.allowed_ip_ranges.values_list("cidr", flat=True))
        if not configured_ranges:
            return True
        if not ip_address:
            return False

        try:
            candidate = ipaddress.ip_address(ip_address)
        except ValueError:
            return False

        return any(
            candidate in ipaddress.ip_network(cidr, strict=False)
            for cidr in configured_ranges
        )

    def mark_used(self, ip_address: str | None = None) -> None:
        self.last_used_at = timezone.now()
        self.last_used_ip = (ip_address or "").strip()
        self.save(update_fields=["last_used_at", "last_used_ip", "modified_at"])


class ApiClientScope(models.Model):
    client = models.ForeignKey(
        ApiClient,
        on_delete=models.CASCADE,
        related_name="scope_assignments",
    )
    scope = models.CharField(max_length=100)

    class Meta:
        ordering = ["scope"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "scope"],
                name="otto_api_client_scope_unique",
            )
        ]

    def __str__(self):
        return f"{self.client.name}: {self.scope}"


class ApiClientAllowedIP(models.Model):
    client = models.ForeignKey(
        ApiClient,
        on_delete=models.CASCADE,
        related_name="allowed_ip_ranges",
    )
    cidr = models.CharField(max_length=64)

    class Meta:
        ordering = ["cidr"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "cidr"],
                name="otto_api_client_allowed_ip_unique",
            )
        ]

    def __str__(self):
        return f"{self.client.name}: {self.cidr}"

    def clean(self):
        super().clean()
        try:
            self.cidr = str(
                ipaddress.ip_network((self.cidr or "").strip(), strict=False)
            )
        except ValueError as exc:
            raise ValidationError(
                {"cidr": _("Enter a valid IP address or CIDR range.")}
            ) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ApiClientAuditEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "created", _("Created")
        UPDATED = "updated", _("Updated")
        SECRET_ROTATED = "secret_rotated", _("Secret rotated")

    client = models.ForeignKey(
        ApiClient,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="api_client_audit_events",
    )
    event_type = models.CharField(
        max_length=32, choices=EventType, default=EventType.UPDATED
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["client", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.client.name}: {self.get_event_type_display()}"


class OttoStatusManager(models.Manager):
    @cache_within_request
    def singleton(self):
        return self.get_or_create(pk=1)[0]


EXTERNAL_TOOL_REVIEW_AZURE_PII_CATEGORY_CHOICES = [
    ("Address", _("Address")),
    ("Age", _("Age")),
    ("DateTime", _("Date/time")),
    ("Email", _("Email")),
    ("IPAddress", _("IP address")),
    ("Location", _("Location")),
    ("NumericIdentifier", _("Numeric identifier")),
    ("Organization", _("Organization")),
    ("Person", _("Person")),
    ("PersonType", _("Person type")),
    ("PhoneNumber", _("Phone number")),
    ("URL", _("URL")),
]


def default_external_tool_review_flagged_azure_pii_categories():
    return [
        "Address",
        "Email",
        "IPAddress",
        "NumericIdentifier",
        "Organization",
        "Person",
        "PhoneNumber",
    ]


class OttoStatus(models.Model):
    """Misc. information, e.g. when updates occurred. Use as singleton."""

    objects = OttoStatusManager()
    laws_last_refreshed = models.DateTimeField(null=True, blank=True)
    exchange_rate = models.FloatField(null=False, blank=False, default=1.38)
    terms_last_updated = models.DateTimeField(default=datetime.datetime.now)
    # Upload limits (values are in megabytes). A value of NULL means "no limit".
    # These are admin-adjustable via the admin interface and used by the UI/JS.
    normal_chat_max_mb = models.IntegerField(
        null=True,
        blank=True,
        default=25,
        help_text="Max chat upload size (MB) for normal users",
    )
    normal_librarian_max_mb = models.IntegerField(
        null=True,
        blank=True,
        default=50,
        help_text="Max librarian upload size (MB) for normal users",
    )
    bulk_uploader_chat_max_mb = models.IntegerField(
        null=True,
        blank=True,
        default=50,
        help_text="Max chat upload size (MB) for Bulk uploaders",
    )
    bulk_uploader_librarian_max_mb = models.IntegerField(
        null=True,
        blank=True,
        default=500,
        help_text="Max librarian upload size (MB) for Bulk uploaders",
    )
    librarian_auto_embed_max_chunks = models.IntegerField(
        null=True,
        blank=True,
        default=512,
        help_text=(
            "Pause librarian documents for manual approval when extracted chunk count "
            "exceeds this value. NULL disables the pause threshold."
        ),
    )
    external_tool_review_flagged_azure_pii_categories = models.JSONField(
        default=default_external_tool_review_flagged_azure_pii_categories,
        blank=True,
        help_text=(
            "Azure Language PII categories that should count as approval-warning "
            "signals for external-tool requests."
        ),
    )
    external_tool_review_flag_local_pii = models.BooleanField(
        default=True,
        help_text=(
            "Treat local regex matches like email addresses, phone numbers, and "
            "social insurance numbers as approval-warning signals."
        ),
    )
    external_tool_review_flag_credentials_or_secrets = models.BooleanField(
        default=True,
        help_text=(
            "Treat credential-like text such as API keys, tokens, passwords, and "
            "connection strings as approval-warning signals."
        ),
    )
    external_tool_review_flag_large_payloads = models.BooleanField(
        default=True,
        help_text=(
            "Treat unusually large outbound text payloads as approval-warning signals."
        ),
    )
    external_tool_review_flag_privileged_or_classified = models.BooleanField(
        default=False,
        help_text=(
            "Treat phrase-only matches like ‘classified’, ‘cabinet confidence’, or "
            "‘solicitor-client’ as approval-warning signals."
        ),
    )

    def chat_max_bytes_for(self, user):
        """Return max chat upload size in bytes for the given user (or None for no limit)."""
        if user.is_admin:
            return None
        if user.is_bulk_uploader:
            return (
                None
                if self.bulk_uploader_chat_max_mb is None
                else int(self.bulk_uploader_chat_max_mb * 1000000)
            )
        return (
            None
            if self.normal_chat_max_mb is None
            else int(self.normal_chat_max_mb * 1000000)
        )

    def librarian_max_bytes_for(self, user):
        """Return max librarian upload size in bytes for the given user (or None for no limit)."""
        if user.is_admin:
            return None
        if user.is_bulk_uploader:
            return (
                None
                if self.bulk_uploader_librarian_max_mb is None
                else int(self.bulk_uploader_librarian_max_mb * 1000000)
            )
        return (
            None
            if self.normal_librarian_max_mb is None
            else int(self.normal_librarian_max_mb * 1000000)
        )

    @property
    def steward_chat_max_mb(self):
        """Backward-compatible alias for bulk_uploader_chat_max_mb."""
        return self.bulk_uploader_chat_max_mb

    @steward_chat_max_mb.setter
    def steward_chat_max_mb(self, value):
        self.bulk_uploader_chat_max_mb = value

    @property
    def steward_librarian_max_mb(self):
        """Backward-compatible alias for bulk_uploader_librarian_max_mb."""
        return self.bulk_uploader_librarian_max_mb

    @steward_librarian_max_mb.setter
    def steward_librarian_max_mb(self, value):
        self.bulk_uploader_librarian_max_mb = value

    def should_pause_librarian_embedding(self, chunk_count: int) -> bool:
        """Return True when librarian auto-embedding should pause for manual approval."""
        threshold = self.librarian_auto_embed_max_chunks
        return threshold is not None and chunk_count > threshold


class BlockedURL(models.Model):
    url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.url
