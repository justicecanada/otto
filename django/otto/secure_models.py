import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from structlog import get_logger

logger = get_logger(__name__)


class AccessKey:
    def __init__(self, user=None, bypass=False):
        if not user and not bypass:
            logger.error("User or bypass must be provided.")
            raise ValueError("User or bypass must be provided.")

        self.user = user
        self.bypass = bypass


class AccessControl(models.Model):
    CAN_VIEW = "can_view"
    CAN_CHANGE = "can_change"
    CAN_DELETE = "can_delete"

    PERMISSION_CHOICES = [
        (CAN_VIEW, "Can view"),
        (CAN_CHANGE, "Can change"),
        (CAN_DELETE, "Can delete"),
    ]

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    can_view = models.BooleanField(default=False)
    can_change = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    reason = models.CharField(max_length=255, blank=True, null=True)
    modified_at = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="access_controls_modified_by",
    )

    class Meta:
        # Index the user and content object fields for faster lookups
        indexes = [models.Index(fields=["user", "object_id"])]
        unique_together = ("user", "object_id")

    def __str__(self):
        return f"{self.user.upn} - {self.content_type.model} Access"

    @transaction.atomic
    def save(self, *args, **kwargs):
        action = "C" if not self.pk else "U"
        super().save(*args, **kwargs)

        # Given self.content_type and object_id, get the content_object
        content_object = self.content_type.get_object_for_this_type(pk=self.object_id)

        AccessControlLog.objects.create(
            upn=self.user.upn,
            action=action,
            can_view=self.can_view,
            can_change=self.can_change,
            can_delete=self.can_delete,
            content_object=str(content_object),
            reason=self.reason,
            modified_by=self.modified_by.upn if self.modified_by else None,
            modified_at=timezone.now(),
        )

    @transaction.atomic
    def delete(self, reason, *args, **kwargs):
        # Log deletion before actual deletion
        AccessControlLog.objects.create(
            upn=self.user.upn,
            action="D",
            can_view=self.can_view,
            can_change=self.can_change,
            can_delete=self.can_delete,
            content_type=self.content_type,
            object_id=self.object_id,
            content_object=str(self.content_object),
            reason=reason,
            modified_by=self.modified_by.upn if self.modified_by else None,
            modified_at=timezone.now(),
        )

        super().delete(*args, **kwargs)

    @classmethod
    def valid_permissions(cls):
        return [cls.CAN_VIEW, cls.CAN_CHANGE, cls.CAN_DELETE]

    @classmethod
    @transaction.atomic
    def grant_permissions(
        cls,
        user,
        content_object,
        required_permissions,
        modified_by=None,
        reason=None,
    ):
        """
        Grant permissions
        AccessControl.grant_permissions(
            user,
            app_instance,
            required_permissions=['can_view', 'can_change', 'can_delete', 'can_add'],
            modified_by=current_user,
            reason="Granting permissions"
        )
        """

        # Ensure that at least one permission is specified
        if not required_permissions:
            logger.error("At least one permission should be granted.")
            raise ValueError("At least one permission should be granted.")

        # Validate that the provided permissions are valid
        valid_permissions = set(cls.valid_permissions())
        if not set(required_permissions).issubset(valid_permissions):
            logger.error("Invalid permissions specified.")
            raise ValueError("Invalid permissions specified.")

        access_control, created = cls.objects.get_or_create(
            user=user,
            content_type=ContentType.objects.get_for_model(content_object),
            object_id=content_object.pk,
            defaults={permission: True for permission in required_permissions},
            modified_by=modified_by,
            reason=reason,
        )

        if not created:
            # Update existing record with the new permissions
            for permission in valid_permissions:
                setattr(access_control, permission, permission in required_permissions)

            access_control.modified_by = modified_by
            access_control.reason = reason
            access_control.save()

        # Update the many-to-many relationship in the content_object
        content_object.access_controls.add(access_control)

        return

    @classmethod
    @transaction.atomic
    def revoke_permissions(
        cls,
        user,
        content_object,
        revoked_permissions=None,
        modified_by=None,
        reason=None,
    ):
        """
        Revoke permissions
        AccessControl.revoke_permissions(
            user,
            app_instance,
            revoked_permissions=['can_view', 'can_change', 'can_delete'],
            modified_by=current_user,
            reason="Revoking permissions"
        )
        """

        # If revoked_permissions is not provided, revoke all permissions
        if revoked_permissions is None:
            revoked_permissions = cls.valid_permissions()

        # Validate that the provided permissions are valid
        valid_permissions = set(cls.valid_permissions())
        if not set(revoked_permissions).issubset(valid_permissions):
            logger.error("Invalid permissions specified.")
            raise ValueError("Invalid permissions specified.")

        access_control = cls.objects.filter(
            user=user,
            content_type=ContentType.objects.get_for_model(content_object),
            object_id=content_object.id,
        ).first()

        if access_control:
            # Revoke the specified permissions
            for permission in revoked_permissions:
                setattr(access_control, permission, False)

                # Dynamically generate the permission string for the revoked permission
                permission_string = f"{content_object._meta.app_label}.{permission}_{content_object._meta.model_name}"

                # Remove the permission from the user
                user.user_permissions.remove(
                    Permission.objects.get(codename=permission_string)
                )

            # Set modified_by and reason if provided
            access_control.modified_by = modified_by
            access_control.reason = reason
            access_control.save()

            # Update the many-to-many relationship in the content_object
            content_object.access_controls.remove(access_control)

            # Check if all can_ fields are False, then delete the record
            if all(
                not getattr(access_control, permission)
                for permission in valid_permissions
            ):
                access_control.delete()

    @classmethod
    def check_permissions(cls, user, content_object, required_permissions):
        """
        Check permissions
        required_permissions = ['can_view', 'can_add']
        if AccessControl.check_permissions(user, app_instance, required_permissions):
            # User has the required permissions
            pass
        """

        access_control = cls.objects.filter(
            user=user,
            content_type=ContentType.objects.get_for_model(content_object),
            object_id=content_object.id,
        ).first()

        if access_control:
            return all(
                getattr(access_control, permission)
                for permission in required_permissions
            )

        return False


class AccessControlLog(models.Model):
    ACTION_CHOICES = [
        ("C", _("Create")),
        ("U", _("Update")),
        ("D", _("Delete")),
    ]
    upn = models.CharField(max_length=255)
    action = models.CharField(max_length=1, choices=ACTION_CHOICES)
    can_view = models.BooleanField(default=False)
    can_change = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    content_object = models.CharField(max_length=255)
    reason = models.CharField(max_length=255, blank=True, null=True)
    modified_by = models.CharField(max_length=255, blank=True, null=True)
    modified_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        timestamp = self.modified_at.strftime("%Y-%m-%d %H:%M:%S")
        return f"{timestamp} - {self.get_action_display()}: {self.upn} - {self.content_object}"


class SecureManager(models.Manager):

    def _apply_row_level_security(self, access_key: AccessKey, query: Q):
        if not access_key.bypass:
            query &= Q(
                access_controls__user=access_key.user,
                access_controls__content_type=ContentType.objects.get_for_model(
                    self.model
                ),
                access_controls__object_id=models.F("id"),
                access_controls__can_view=True,
            )
        return query

    def all(self, access_key: AccessKey, **kwargs):
        query = self._apply_row_level_security(access_key, Q(**kwargs))
        return super().filter(query)

    def get(self, access_key: AccessKey, **kwargs):
        query = self._apply_row_level_security(access_key, Q(**kwargs))
        return super().get(query)

    def filter(self, access_key: AccessKey, **kwargs):
        query = self._apply_row_level_security(access_key, Q(**kwargs))
        return super().filter(query)

    def create(self, access_key: AccessKey, **kwargs):
        if not access_key.bypass:

            permission = Permission.objects.get(
                content_type=ContentType.objects.get_for_model(self.model),
                codename=f"add_{self.model._meta.model_name}",
            )

            if permission not in access_key.user.user_permissions.all():
                raise PermissionError(
                    "You do not have permission to create this object."
                )

        kwargs["id"] = uuid.uuid4()
        instance = self.model(**kwargs)
        instance.save(AccessKey(bypass=True))
        if not access_key.bypass:
            instance.grant_ownership_to(
                access_key,
                modified_by=access_key.user,
                reason="Owner of the object.",
            )
        return instance


class SecureModel(models.Model):
    # Primary key needs to be UUID for secure model to minimize the risk of ID guessing
    id = models.UUIDField(primary_key=True, editable=False)
    objects = SecureManager()
    access_controls = models.ManyToManyField(AccessControl)

    class Meta:
        abstract = True

    def _check_permissions(self, access_key: AccessKey, permission: "str"):
        if not access_key or access_key.bypass:
            return

        if not AccessControl.check_permissions(access_key.user, self, [permission]):
            raise PermissionError(
                f"You do not have permission to {permission.lower()} this object."
            )

    def save(self, access_key: AccessKey, **kwargs):
        self._check_permissions(access_key, AccessControl.CAN_CHANGE)
        super().save(**kwargs)

    def delete(self, access_key: AccessKey, **kwargs):
        self._check_permissions(access_key, AccessControl.CAN_DELETE)
        super().delete(**kwargs)

    def _grant_permissions(self, user, permission, modified_by=None, reason=None):
        AccessControl.grant_permissions(
            user,
            self,
            required_permissions=[permission],
            modified_by=modified_by,
            reason=reason,
        )

    def _revoke_permissions(self, user, permission, modified_by=None, reason=None):
        AccessControl.revoke_permissions(
            user,
            self,
            revoked_permissions=[permission],
            modified_by=modified_by,
            reason=reason,
        )

    def grant_ownership_to(self, access_key: AccessKey, modified_by=None, reason=None):
        valid_permissions = AccessControl.valid_permissions()
        AccessControl.grant_permissions(
            access_key.user,
            self,
            required_permissions=valid_permissions,
            modified_by=modified_by,
            reason=reason,
        )

    def grant_view_to(self, access_key: AccessKey, modified_by=None, reason=None):
        self._grant_permissions(
            access_key.user, AccessControl.CAN_VIEW, modified_by, reason
        )

    @classmethod
    def grant_create_to(cls, access_key: AccessKey):
        user = access_key.user
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(cls),
            codename=f"add_{cls._meta.model_name}",
        )
        user.user_permissions.add(permission)
        user.refresh_from_db()

    def grant_change_to(self, access_key: AccessKey, modified_by=None, reason=None):
        self._grant_permissions(
            access_key.user, AccessControl.CAN_CHANGE, modified_by, reason
        )

    def grant_delete_to(self, access_key: AccessKey, modified_by=None, reason=None):
        self._grant_permissions(
            access_key.user, AccessControl.CAN_DELETE, modified_by, reason
        )

    def revoke_view_from(self, access_key: AccessKey, modified_by=None, reason=None):
        self._revoke_permissions(
            access_key.user, AccessControl.CAN_VIEW, modified_by, reason
        )

    @classmethod
    def revoke_create_from(cls, access_key: AccessKey):
        user = access_key.user
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(cls),
            codename=f"add_{cls._meta.model_name}",
        )
        user.user_permissions.remove(permission)
        user.refresh_from_db()

    def revoke_change_from(self, access_key: AccessKey, modified_by=None, reason=None):
        self._revoke_permissions(
            access_key.user, AccessControl.CAN_CHANGE, modified_by, reason
        )

    def revoke_delete_from(self, access_key: AccessKey, modified_by=None, reason=None):
        self._revoke_permissions(
            access_key.user, AccessControl.CAN_DELETE, modified_by, reason
        )

    @classmethod
    def can_be_created_by(cls, access_key: AccessKey):
        user = access_key.user
        permission = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(cls),
            codename=f"add_{cls._meta.model_name}",
        )
        return permission in user.user_permissions.all()

    def can_be_viewed_by(self, access_key: AccessKey):
        return AccessControl.check_permissions(
            access_key.user, self, [AccessControl.CAN_VIEW]
        )

    def can_be_changed_by(self, access_key: AccessKey):
        return AccessControl.check_permissions(
            access_key.user, self, [AccessControl.CAN_CHANGE]
        )

    def can_be_deleted_by(self, access_key: AccessKey):
        return AccessControl.check_permissions(
            access_key.user, self, [AccessControl.CAN_DELETE]
        )


class SecureRelatedManager(models.Manager):
    def _apply_row_level_security(self, access_key: AccessKey, queryset, permission):
        if not access_key.bypass:
            # Collect parent IDs that the user has permission to access
            parent_ids = set()
            parents = []
            for obj in queryset:
                parents = obj.get_permission_parents()
                parent_ids.update(
                    parent.id
                    for parent in parents
                    if AccessControl.check_permissions(
                        access_key.user, parent, [permission]
                    )
                )

            # Dynamically determine the related field names and apply the filter
            parent_field_names = [
                field.name
                for field in queryset.model._meta.fields
                if isinstance(field, models.ForeignKey)
                and field.related_model in [parent.__class__ for parent in parents]
            ]

            # Apply the filter for each parent field name
            filter_conditions = {
                f"{field_name}__id__in": parent_ids for field_name in parent_field_names
            }
            queryset = queryset.filter(**filter_conditions)

        return queryset

    def all(self, access_key: AccessKey, **kwargs):
        queryset = super().all(**kwargs)
        return self._apply_row_level_security(
            access_key, queryset, AccessControl.CAN_VIEW
        )

    def get(self, access_key: AccessKey, **kwargs):
        queryset = super().filter(**kwargs)
        queryset = self._apply_row_level_security(
            access_key, queryset, AccessControl.CAN_VIEW
        )
        try:
            return queryset.get()
        except queryset.model.DoesNotExist:
            raise PermissionDenied("You do not have permission to view this object.")

    def filter(self, access_key: AccessKey, **kwargs):
        queryset = super().filter(**kwargs)
        return self._apply_row_level_security(
            access_key, queryset, AccessControl.CAN_VIEW
        )

    def create(self, access_key: AccessKey, **kwargs):

        instance = self.model(**kwargs)

        if not access_key.bypass:

            # Check if the user's access key has permission to the parent objects
            for parent in instance.get_permission_parents():
                if not AccessControl.check_permissions(
                    access_key.user, parent, [AccessControl.CAN_CHANGE]
                ):
                    raise PermissionDenied(
                        "You do not have permission to create this object."
                    )

        instance.save(AccessKey(bypass=True))
        return instance


class SecureRelatedModel(models.Model):
    objects = SecureRelatedManager()

    class Meta:
        abstract = True

    def get_permission_parents(self):
        """
        Method to be overridden by subclasses to return the parent objects
        against which permissions should be checked.
        """
        logger.error("Subclasses must implement get_permission_parents method.")
        raise NotImplementedError(
            "Subclasses must implement get_permission_parents method."
        )

    def check_related_permissions(self, access_key, permission):
        if access_key.bypass:
            return

        for parent in self.get_permission_parents():
            if not AccessControl.check_permissions(
                access_key.user, parent, [permission]
            ):
                logger.error(
                    f"Invalid permission ({permission.lower()}) to object for {access_key.user}."
                )
                raise PermissionDenied(
                    f"You do not have permission to {permission.lower()} this object."
                )

    def save(self, access_key: AccessKey, *args, **kwargs):
        self.check_related_permissions(access_key, AccessControl.CAN_CHANGE)
        super().save(*args, **kwargs)

    def delete(self, access_key: AccessKey, *args, **kwargs):
        self.check_related_permissions(access_key, AccessControl.CAN_DELETE)
        super().delete(*args, **kwargs)
