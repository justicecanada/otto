import importlib

import pytest

from otto.models import User
from otto.utils.entra import (
    EntraUser,
    set_entra_status_for_inactive_users,
    update_or_create_users,
)


@pytest.mark.django_db
def test_update_or_create_users_sets_entra_profile_fields():
    update_or_create_users(
        [
            EntraUser(
                id="oid-active-1",
                upn="entra.active@justice.gc.ca",
                email="entra.active@justice.gc.ca",
                display_name="Active, Entra",
                first_name="Entra",
                last_name="Active",
                status=User.EntraStatus.ACTIVE,
                job_title="Technical Advisor",
                preferred_language="Français",
            )
        ]
    )

    user = User.objects.get(upn="entra.active@justice.gc.ca")
    assert user.is_active is True
    assert user.entra_status == User.EntraStatus.ACTIVE
    assert user.job_title == "Technical Advisor"
    assert user.preferred_language == "Français"


def test_entra_module_import_does_not_require_graph_settings(settings):
    settings.ENTRA_AUTHORITY = None
    settings.ENTRA_CLIENT_ID = None
    settings.ENTRA_CLIENT_SECRET = None

    import otto.utils.entra as entra_module

    reloaded_module = importlib.reload(entra_module)

    assert callable(reloaded_module.update_or_create_users)


@pytest.mark.django_db
def test_set_entra_status_for_inactive_users_marks_disabled_deleted_and_unknown():
    disabled_user = User.objects.create_user(
        upn="disabled.user@justice.gc.ca",
        email="disabled.user@justice.gc.ca",
        first_name="Disabled",
        last_name="User",
    )
    deleted_user = User.objects.create_user(
        upn="deleted.user@justice.gc.ca",
        email="deleted.user@justice.gc.ca",
        first_name="Deleted",
        last_name="User",
        oid="deleted-oid-1",
    )
    unknown_user = User.objects.create_user(
        upn="unknown.user@justice.gc.ca",
        email="unknown.user@justice.gc.ca",
        first_name="Unknown",
        last_name="User",
    )

    set_entra_status_for_inactive_users(
        active_users=[],
        disabled_users=[
            EntraUser(
                id="disabled-oid-1",
                upn="disabled.user@justice.gc.ca",
                email="disabled.user@justice.gc.ca",
                display_name="Disabled, User",
                first_name="Disabled",
                last_name="User",
                status=User.EntraStatus.DISABLED,
                job_title="Platform Analyst",
                preferred_language="English",
            )
        ],
        deleted_users=[
            EntraUser(
                id="deleted-oid-1",
                upn="deleted.user@justice.gc.ca",
                email="deleted.user@justice.gc.ca",
                display_name="Deleted, User",
                first_name="Deleted",
                last_name="User",
                status=User.EntraStatus.DELETED,
            )
        ],
    )

    disabled_user.refresh_from_db()
    deleted_user.refresh_from_db()
    unknown_user.refresh_from_db()

    assert disabled_user.is_active is False
    assert disabled_user.entra_status == User.EntraStatus.DISABLED
    assert disabled_user.job_title == "Platform Analyst"
    assert disabled_user.preferred_language == "English"

    assert deleted_user.is_active is False
    assert deleted_user.entra_status == User.EntraStatus.DELETED

    assert unknown_user.is_active is False
    assert unknown_user.entra_status == User.EntraStatus.UNKNOWN
