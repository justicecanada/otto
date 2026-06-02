import importlib

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from otto.models import Group, User

from chat.models import Chat
from librarian.models import Library, LibraryUserRole

user_merge = importlib.import_module("otto.user_merge")


@pytest.fixture(autouse=True)
def _compat_chat_next_default_preset_id(monkeypatch):
    """Keep merge tests compatible after removing User.chat_next_default_preset."""
    if hasattr(User, "chat_next_default_preset_id"):
        return
    monkeypatch.setattr(
        User,
        "chat_next_default_preset_id",
        property(lambda self: None),
        raising=False,
    )


@pytest.mark.django_db
def test_find_by_upn_prefers_active_user_for_case_variants():
    active_user = User.objects.create_user(
        upn="case.user@example.com",
        email="case.user@example.com",
        first_name="Case",
        last_name="User",
    )
    inactive_user = User.objects.create_user(
        upn="Case.User@example.com",
        email="case.user+old@example.com",
        first_name="Case",
        last_name="User",
        is_active=False,
    )

    found = User.objects.find_by_upn("CASE.USER@example.com")

    assert found.pk == active_user.pk
    assert inactive_user.upn == "case.user@example.com"


@pytest.mark.django_db
def test_manage_users_upload_matches_upn_case_insensitively(client, all_apps_user):
    admin = all_apps_user("merge_admin")
    client.force_login(admin)

    existing_user = User.objects.create_user(
        upn="case.user@justice.gc.ca",
        email="case.user@justice.gc.ca",
        first_name="Case",
        last_name="User",
    )
    otto_admin_group = Group.objects.get(name="Otto admin")

    upload_file = SimpleUploadedFile(
        "users.csv",
        b"upn,roles,monthly_max,cost_groups\nCase.User@justice.gc.ca,Otto admin,100,\n",
        content_type="text/csv",
    )

    response = client.post(reverse("upload_users"), data={"csv_file": upload_file})

    assert response.status_code == 302
    assert User.objects.filter(upn__iexact="case.user@justice.gc.ca").count() == 1
    existing_user.refresh_from_db()
    assert existing_user.upn == "case.user@justice.gc.ca"
    assert otto_admin_group in existing_user.groups.all()


@pytest.mark.django_db
def test_manage_user_merge_requires_permission(client, basic_user, all_apps_user):
    unauthorized_user = basic_user("unauthorized_user", accept_terms=True)
    client.force_login(unauthorized_user)

    response = client.get(reverse("manage_user_merge"))

    assert response.status_code == 302

    admin = all_apps_user("authorized_admin")
    client.force_login(admin)
    response = client.get(reverse("manage_user_merge"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_manage_user_merge_view_executes_merge(client, all_apps_user):
    admin = all_apps_user("merge_admin_view")
    client.force_login(admin)
    target = all_apps_user("merge_target")
    source = all_apps_user("merge_source")

    preview_response = client.post(
        reverse("manage_user_merge"),
        data={
            "target_user": target.id,
            "source_users": [source.id],
            "action": "preview",
        },
    )

    assert preview_response.status_code == 200
    assert source.upn in preview_response.content.decode("utf-8")

    execute_response = client.post(
        reverse("manage_user_merge"),
        data={
            "target_user": target.id,
            "source_users": [source.id],
            "confirm_merge": "on",
            "action": "merge",
        },
    )

    assert execute_response.status_code == 200
    content = execute_response.content.decode("utf-8")
    source.refresh_from_db()
    assert source.is_active is False
    assert "Post-merge report" in content
    assert '<li class="mb-1">Roles/permissions/cost groups merged:' in content
    assert "\\nRoles/permissions/cost groups merged" not in content


@pytest.mark.django_db
def test_manage_user_merge_page_renders_field_specific_autocomplete_inputs(
    client, all_apps_user
):
    admin = all_apps_user("merge_admin_markup")
    client.force_login(admin)

    response = client.get(reverse("manage_user_merge"))
    content = response.content.decode("utf-8")

    assert response.status_code == 200
    assert 'name="source_users"' in content
    assert 'name="target_user"' in content
    assert content.index("Source users") < content.index("Target user")


@pytest.mark.django_db
def test_manage_user_merge_preview_with_widget_post_names(client, all_apps_user):
    admin = all_apps_user("merge_admin_widget_post")
    client.force_login(admin)
    target = all_apps_user("merge_target_widget_post")
    source = all_apps_user("merge_source_widget_post")

    response = client.post(
        reverse("manage_user_merge"),
        data={
            "target_user": str(target.id),
            "source_users": [str(source.id)],
            "action": "preview",
        },
    )
    content = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "This field is required" not in content
    assert source.upn in content


@pytest.mark.django_db
def test_merge_users_preserves_highest_library_role_and_deactivates_source(
    all_apps_user,
):
    target = all_apps_user("library_target")
    source = all_apps_user("library_source")

    library = Library(
        name="Shared library",
        description="Shared test library",
        created_by=source,
        is_personal_library=False,
    )
    library.save()
    LibraryUserRole.objects.create(user=target, library=library, role="viewer")
    source_role = LibraryUserRole.objects.create(
        user=source, library=library, role="admin"
    )

    result = user_merge.merge_users(target, [source], actor=target)

    target_role = LibraryUserRole.objects.get(user=target, library=library)
    assert target_role.role == "admin"
    assert not LibraryUserRole.objects.filter(pk=source_role.pk).exists()
    source.refresh_from_db()
    assert source.is_active is False
    assert result.report_sections
    assert any(
        "LibraryUserRole moved/upgraded/deleted" in line for line in result.report_lines
    )


@pytest.mark.django_db
def test_merge_users_rolls_back_on_error(all_apps_user, monkeypatch):
    target = all_apps_user("rollback_target")
    source = all_apps_user("rollback_source")
    chat = Chat.objects.create(user=source, title="Rollback chat")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(user_merge, "_transfer_preset_permissions", boom)

    with pytest.raises(RuntimeError, match="boom"):
        user_merge.merge_users(target, [source], actor=target)

    chat.refresh_from_db()
    source.refresh_from_db()
    assert chat.user_id == source.id
    assert source.is_active is True
