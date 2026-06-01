from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command

import pytest

User = get_user_model()


FIXTURE_GROUPS = [
    {"model": "auth.Group", "fields": {"name": "Otto admin"}},
    {
        "model": "auth.Group",
        "fields": {"name": "Bulk uploader"},
        "old_names": ["Data steward"],
    },
    {"model": "auth.Group", "fields": {"name": "Operations admin"}},
    {
        "model": "auth.Group",
        "fields": {"name": "Public sharing admin"},
        "old_names": ["JUS data steward", "Public library admin"],
    },
    {"model": "auth.Group", "fields": {"name": "Otto user"}},
    {"model": "auth.Group", "fields": {"name": "Beta tester"}},
]


def _patch_fixture(groups_data):
    """Return a context manager that patches yaml.safe_load to return groups_data."""
    import yaml

    yaml.safe_load

    def side_effect(stream):
        # Only intercept the groups.yaml read inside reset_groups.
        # Detect by checking if it looks like a fixture list vs other YAML.
        return groups_data

    return patch(
        "otto.management.commands.reset_app_data.yaml.safe_load",
        side_effect=side_effect,
    )


@pytest.mark.django_db
def test_reset_groups_is_soft_by_default():
    extra_group_name = "Do Not Delete Group"
    Group.objects.create(name=extra_group_name)

    call_command("reset_app_data", "groups")

    # Default mode should be soft: custom groups are preserved.
    assert Group.objects.filter(name=extra_group_name).exists()


@pytest.mark.django_db
def test_soft_sync_creates_missing_groups():
    """Groups that don't exist at all are created."""
    Group.objects.all().delete()

    with _patch_fixture(FIXTURE_GROUPS):
        call_command("reset_app_data", "groups")

    for entry in FIXTURE_GROUPS:
        assert Group.objects.filter(name=entry["fields"]["name"]).exists()


@pytest.mark.django_db
def test_soft_sync_renames_data_steward_to_bulk_uploader_preserving_members():
    Group.objects.all().delete()
    old = Group.objects.create(name="Data steward")
    user = User.objects.create_user("bulk-uploader-user")
    old.user_set.add(user)
    old_pk = old.pk

    with _patch_fixture(FIXTURE_GROUPS):
        call_command("reset_app_data", "groups")

    old.refresh_from_db()
    assert old.pk == old_pk
    assert old.name == "Bulk uploader"
    assert user in old.user_set.all()


@pytest.mark.django_db
def test_soft_sync_renames_by_old_names():
    """Local dev case: PKs don't match but old_names finds the group to rename."""
    Group.objects.all().delete()
    # Group exists with auto-incremented PK (not 101).
    old = Group.objects.create(name="JUS data steward")
    user = User.objects.create_user("rename-oldname-user")
    old.user_set.add(user)
    old_pk = old.pk

    fixture = [
        {
            "model": "auth.Group",
            "pk": 101,
            "fields": {"name": "Public sharing admin"},
            "old_names": ["JUS data steward"],
        },
    ]
    with _patch_fixture(fixture):
        call_command("reset_app_data", "groups")

    old.refresh_from_db()
    assert old.pk == old_pk  # same row, not recreated
    assert old.name == "Public sharing admin"
    assert user in old.user_set.all()


@pytest.mark.django_db
def test_soft_sync_merges_members_from_old_names():
    """When target name already exists AND old-named groups linger, members are merged."""
    Group.objects.all().delete()
    new_group = Group.objects.create(name="Public sharing admin")
    old_group = Group.objects.create(name="JUS data steward")
    lib_group = Group.objects.create(name="Public library admin")

    user_a = User.objects.create_user("user-a")
    user_b = User.objects.create_user("user-b")
    old_group.user_set.add(user_a)
    lib_group.user_set.add(user_b)

    fixture = [
        {
            "model": "auth.Group",
            "fields": {"name": "Public sharing admin"},
            "old_names": ["JUS data steward", "Public library admin"],
        },
    ]
    with _patch_fixture(fixture):
        call_command("reset_app_data", "groups")

    # Members from both old groups should now be in the target group.
    members = set(new_group.user_set.values_list("pk", flat=True))
    assert user_a.pk in members
    assert user_b.pk in members
    # Old-named groups are retired (deleted) after member migration.
    assert not Group.objects.filter(name="JUS data steward").exists()
    assert not Group.objects.filter(name="Public library admin").exists()


@pytest.mark.django_db
def test_soft_sync_merges_multiple_old_name_groups_during_rename():
    """When renaming via old_names, members from other old-named groups are also merged."""
    Group.objects.all().delete()
    jus_group = Group.objects.create(name="JUS data steward")
    lib_group = Group.objects.create(name="Public library admin")

    user_a = User.objects.create_user("merge-a")
    user_b = User.objects.create_user("merge-b")
    jus_group.user_set.add(user_a)
    lib_group.user_set.add(user_b)

    fixture = [
        {
            "model": "auth.Group",
            "fields": {"name": "Public sharing admin"},
            "old_names": ["JUS data steward", "Public library admin"],
        },
    ]
    with _patch_fixture(fixture):
        call_command("reset_app_data", "groups")

    # First old_names entry gets renamed; second gets merged then deleted.
    renamed = Group.objects.get(name="Public sharing admin")
    members = set(renamed.user_set.values_list("pk", flat=True))
    assert user_a.pk in members
    assert user_b.pk in members
    # Both old names should be gone.
    assert not Group.objects.filter(name="JUS data steward").exists()
    assert not Group.objects.filter(name="Public library admin").exists()


@pytest.mark.django_db
def test_soft_sync_does_not_delete_non_fixture_groups():
    """Soft mode does not delete groups that aren't in old_names."""
    Group.objects.create(name="Custom group")

    fixture = [
        {"model": "auth.Group", "fields": {"name": "Otto admin"}},
    ]
    with _patch_fixture(fixture):
        call_command("reset_app_data", "groups")

    assert Group.objects.filter(name="Custom group").exists()
