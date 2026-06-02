from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def browser_test_personas() -> list[dict[str, object]]:
    return [
        {
            "slug": "first_admin",
            "label": _("First Admin"),
            "button_class": "btn-danger",
            "upn": settings.BROWSER_TEST_AUTH_UPN,
            "email": settings.BROWSER_TEST_AUTH_EMAIL,
            "oid": settings.BROWSER_TEST_AUTH_OID,
            "first_name": settings.BROWSER_TEST_AUTH_FIRST_NAME,
            "last_name": settings.BROWSER_TEST_AUTH_LAST_NAME,
            "group_names": [settings.OTTO_ADMIN_GROUP],
        },
        {
            "slug": "second_admin",
            "label": _("Second Admin"),
            "button_class": "btn-warning text-black",
            "upn": "browser.test.second.admin@example.com",
            "email": "browser.test.second.admin@example.com",
            "oid": "browser-test-second-admin-local",
            "first_name": "Second",
            "last_name": "Admin",
            "group_names": [settings.OTTO_ADMIN_GROUP],
        },
        {
            "slug": "basic_user",
            "label": _("Basic User"),
            "button_class": "btn-secondary",
            "upn": "browser.test.user@example.com",
            "email": "browser.test.user@example.com",
            "oid": "browser-test-basic-user-local",
            "first_name": "Basic",
            "last_name": "User",
            "group_names": [settings.OTTO_USER_GROUP],
        },
        {
            "slug": "public_bulk_uploader",
            "label": _("Public BulkUploader"),
            "button_class": "btn-primary",
            "upn": "browser.test.public.bulk.uploader@example.com",
            "email": "browser.test.public.bulk.uploader@example.com",
            "oid": "browser-test-public-bulk-uploader-local",
            "first_name": "Public",
            "last_name": "BulkUploader",
            "group_names": [
                settings.OTTO_USER_GROUP,
                settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP,
                settings.OTTO_BULK_UPLOADER_GROUP,
            ],
        },
        {
            "slug": "beta_tester",
            "label": _("Beta Tester"),
            "button_class": "btn-success",
            "upn": "browser.test.beta.tester@example.com",
            "email": "browser.test.beta.tester@example.com",
            "oid": "browser-test-beta-tester-local",
            "first_name": "Beta",
            "last_name": "Tester",
            "group_names": [settings.OTTO_USER_GROUP, settings.OTTO_BETA_TESTER_GROUP],
        },
    ]


def ensure_browser_test_user(persona: dict[str, object], using: str | None = None):
    user_model = get_user_model()
    user_manager = user_model.objects.db_manager(using)
    group_manager = Group.objects.db_manager(using)

    user = user_manager.find_by_upn(persona["upn"])

    if user is None:
        user = user_manager.create_user(
            upn=persona["upn"],
            email=persona["email"],
            oid=persona["oid"],
            first_name=persona["first_name"],
            last_name=persona["last_name"],
        )

    fields_to_update = []
    desired_fields = {
        "email": persona["email"],
        "oid": persona["oid"],
        "first_name": persona["first_name"],
        "last_name": persona["last_name"],
        "is_active": True,
        "accepted_terms_date": timezone.localdate(),
        "default_ai_assistant": "chat_next",
        "homepage_tour_completed": True,
        "ai_assistant_tour_completed": True,
        "chat_next_tour_completed": True,
        "laws_search_tour_completed": True,
    }

    for field_name, desired_value in desired_fields.items():
        if getattr(user, field_name) != desired_value:
            setattr(user, field_name, desired_value)
            fields_to_update.append(field_name)

    if fields_to_update:
        user.save(update_fields=fields_to_update)

    groups = []
    for group_name in persona["group_names"]:
        group, _ = group_manager.get_or_create(name=group_name)
        groups.append(group)
    user.groups.set(groups)

    return user


def seed_browser_test_users(using: str | None = None) -> list:
    if not settings.BROWSER_TEST_AUTH_ENABLED:
        return []

    return [
        ensure_browser_test_user(persona, using=using)
        for persona in browser_test_personas()
    ]
