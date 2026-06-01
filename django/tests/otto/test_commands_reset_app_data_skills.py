from django.core.management import call_command
from django.core.management.base import CommandError

import pytest
from chat_next.models import ChatSettings, Skill

from otto.models import SecurityLabel

from librarian.models import DataSource, Document, Library, LibraryUserRole


@pytest.mark.django_db
def test_reset_skills_preserves_existing_skills_files_content(all_apps_user):
    owner = all_apps_user()

    skills_library = Library.objects.create(
        name_en="Skills Files",
        name_fr="Fichiers de compétences",
        created_by=owner,
        is_public=True,
    )
    LibraryUserRole.objects.create(library=skills_library, user=owner, role="admin")

    custom_folder = DataSource.objects.create(
        library=skills_library,
        name_en="Custom Public Skills",
        name_fr="Compétences publiques personnalisées",
    )
    custom_doc = Document.objects.create(
        data_source=custom_folder,
        filename="custom-skill-notes.txt",
        status="SUCCESS",
        extracted_text="my custom skill file",
    )

    call_command("reset_app_data", "skills")

    refreshed_library = Library.objects.get(name_en="Skill files (Otto defaults)")

    # Library is now private/admin-managed.
    assert refreshed_library.is_public is False
    assert not Library.objects.filter(name_en="Skills Files").exists()

    # Existing user-created folder/documents must not be deleted.
    assert DataSource.objects.filter(
        id=custom_folder.id, library=refreshed_library
    ).exists()
    assert Document.objects.filter(id=custom_doc.id, data_source=custom_folder).exists()

    # Fixture-backed translation resources are still present for built-in skills.
    translation_folder = DataSource.objects.filter(
        library=refreshed_library,
        name_en="Translation",
    ).first()
    assert translation_folder is not None
    assert Document.objects.filter(
        data_source=translation_folder,
        filename="JUS_translation_glossary.txt",
    ).exists()

    otto_help_folder = DataSource.objects.filter(
        library=refreshed_library,
        name_en="Otto Help",
    ).first()
    assert otto_help_folder is not None
    assert Document.objects.filter(
        data_source=otto_help_folder,
        filename="Otto_terms_of_use_en.txt",
    ).exists()
    assert Document.objects.filter(
        data_source=otto_help_folder,
        filename="Otto_conditions_d_utilisation_fr.txt",
    ).exists()


@pytest.mark.django_db
def test_reset_skills_defaults_library_is_editable_by_all_admins(
    all_apps_user, basic_user
):
    admin_user = all_apps_user()
    non_admin_user = basic_user(accept_terms=True)

    call_command("reset_app_data", "skills")

    later_admin_user = all_apps_user(username="later_admin")
    skills_library = Library.objects.get(name_en="Skill files (Otto defaults)")

    assert LibraryUserRole.objects.filter(
        library=skills_library,
        user=admin_user,
        role="admin",
    ).exists()
    assert admin_user.has_perm("librarian.view_library", skills_library)
    assert admin_user.has_perm("librarian.edit_library", skills_library)
    assert not admin_user.has_perm("librarian.delete_library", skills_library)
    assert later_admin_user.has_perm("librarian.view_library", skills_library)
    assert later_admin_user.has_perm("librarian.edit_library", skills_library)
    assert later_admin_user.has_perm("librarian.manage_library_users", skills_library)
    assert not later_admin_user.has_perm("librarian.delete_library", skills_library)
    assert not LibraryUserRole.objects.filter(
        library=skills_library,
        user=later_admin_user,
    ).exists()
    assert not LibraryUserRole.objects.filter(
        library=skills_library,
        user=non_admin_user,
    ).exists()
    assert not non_admin_user.has_perm("librarian.view_library", skills_library)
    assert not non_admin_user.has_perm("librarian.edit_library", skills_library)
    assert not non_admin_user.has_perm("librarian.delete_library", skills_library)


@pytest.mark.django_db
def test_reset_skills_creates_fixture_backed_skills():
    Skill.objects.all().delete()

    call_command("reset_app_data", "skills")

    assert Skill.objects.filter(display_name_en="Translation").exists()
    assert Skill.objects.filter(display_name_en="Skill Creator").exists()
    assert Skill.objects.filter(display_name_en="Otto Help").exists()


@pytest.mark.django_db
def test_reset_skills_restores_fixture_skill_state(all_apps_user):
    stale_skill = Skill.objects.get(display_name_en="Translation")
    stale_skill.description = "Outdated"
    stale_skill.body = "Old body"
    stale_skill.sharing_option = "everyone"
    stale_skill.is_system = False
    stale_skill.save()

    call_command("reset_app_data", "skills")

    stale_skill.refresh_from_db()

    assert stale_skill.owner is None
    assert stale_skill.sharing_option == "everyone"
    assert stale_skill.is_system is True
    assert stale_skill.accessible_to.count() == 0
    assert stale_skill.editable_by.count() == 0
    assert stale_skill.display_name_en == "Translation"


@pytest.mark.django_db
def test_reset_skills_enables_default_skills_for_existing_chat_settings(all_apps_user):
    user = all_apps_user()
    user_settings = ChatSettings.objects.create(user=user)

    custom_skill = Skill.objects.create(
        display_name="Custom Skill",
        description="A custom skill",
        body="Custom body",
        owner=user,
    )
    user_settings.enabled_skills.add(custom_skill)

    call_command("reset_app_data", "skills")

    user_settings.refresh_from_db()

    assert user_settings.enabled_skills.filter(display_name="Custom Skill").exists()
    assert user_settings.enabled_skills.filter(display_name_en="Translation").exists()
    assert user_settings.enabled_skills.filter(display_name_en="Skill Creator").exists()


@pytest.mark.django_db
def test_reset_skills_resolves_otto_help_terms_documents():
    call_command("reset_app_data", "skills")

    otto_help = Skill.objects.get(display_name_en="Otto Help")

    assert {(hint["type"], hint["name"]) for hint in otto_help.context_hints} >= {
        ("document", "Otto_terms_of_use_en.txt"),
        ("document", "Otto_conditions_d_utilisation_fr.txt"),
    }


@pytest.mark.django_db
def test_reset_skills_sets_fixture_folder_classification_to_unclassified():
    call_command("reset_app_data", "skills")

    skills_library = Library.objects.get(name_en="Skill files (Otto defaults)")
    unclassified = SecurityLabel.objects.get(acronym_en="UC")

    assert (
        DataSource.objects.get(
            library=skills_library,
            name_en="Translation",
        ).security_label
        == unclassified
    )
    assert (
        DataSource.objects.get(
            library=skills_library,
            name_en="Otto Help",
        ).security_label
        == unclassified
    )


@pytest.mark.django_db
def test_reset_skills_raises_clear_error_when_security_label_is_missing(monkeypatch):
    def missing_security_label(*args, **kwargs):
        raise SecurityLabel.DoesNotExist()

    monkeypatch.setattr(SecurityLabel.objects, "get", missing_security_label)

    with pytest.raises(CommandError, match="Run reset_app_data security_labels first"):
        call_command("reset_app_data", "skills")
