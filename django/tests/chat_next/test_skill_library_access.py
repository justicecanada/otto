"""
Tests for skill-based library access control.

When a skill references documents, folders, or libraries in its context_hints,
users who can access that skill should also be able to access those resources
through the Q&A library tools (rag_search, get_document_text, etc.).
"""

import pytest
from chat_next._tools.qa_libraries import (
    _user_can_view_data_source,
    _user_can_view_document,
    _user_can_view_library,
)
from chat_next._utils.context_hints import (
    _resolve_lookup_key_context_hint,
    check_context_hints,
)
from chat_next.models import Skill

from librarian.models import DataSource, Document, Library, LibraryUserRole


@pytest.mark.django_db
class TestSkillBasedLibraryAccess:
    """Test that skill context_hints grant library access to skill users."""

    def _create_private_library(self, owner):
        """Create a private library owned by the given user."""
        library = Library.objects.create(
            name="Skill-Referenced Library",
            created_by=owner,
            is_public=False,
        )
        LibraryUserRole.objects.create(library=library, user=owner, role="admin")
        return library

    def _create_skill_with_library_hint(self, owner, library, sharing="everyone"):
        """Create a skill that references a library in its context_hints."""
        return Skill.objects.create(
            display_name="Test Skill",
            description="A test skill",
            body="Do something with the library.",
            owner=owner,
            sharing_option=sharing,
            context_hints=[
                {"type": "library", "id": library.id, "name": library.name},
            ],
        )

    def test_user_without_skill_cannot_view_private_library(self, all_apps_user):
        """A user with no skill access cannot view a private library."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        assert not _user_can_view_library(other_user, library)

    def test_user_with_skill_can_view_referenced_library(self, all_apps_user):
        """A user who can access a skill can view libraries it references."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        # Create a public skill referencing this library
        self._create_skill_with_library_hint(owner, library, sharing="everyone")

        # other_user should now be able to view the library via the skill
        assert _user_can_view_library(other_user, library)

    def test_private_skill_does_not_grant_access(self, all_apps_user):
        """A private skill does not grant access to non-owner users."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        self._create_skill_with_library_hint(owner, library, sharing="private")

        # other_user cannot access the private skill, so no library access
        assert not _user_can_view_library(other_user, library)

    def test_shared_skill_grants_access_to_shared_users(self, all_apps_user):
        """A skill shared with specific users grants them library access."""
        owner = all_apps_user()
        shared_user = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        skill = self._create_skill_with_library_hint(owner, library, sharing="others")
        skill.accessible_to.add(shared_user)

        # shared_user gets access via skill
        assert _user_can_view_library(shared_user, library)
        # other_user does NOT
        assert not _user_can_view_library(other_user, library)

    def test_document_hint_does_not_grant_library_access(self, all_apps_user):
        """A skill referencing a document does NOT grant access to the whole library."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)
        data_source = DataSource.objects.create(library=library, name="Folder")
        document = Document.objects.create(
            data_source=data_source,
            filename="test.txt",
            status="INDEXED",
        )

        Skill.objects.create(
            display_name="Doc Hint Skill",
            description="Skill referencing a document",
            body="Use the document.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "document", "id": document.id, "name": "test.txt"},
            ],
        )

        # Document hint should NOT grant access to the whole library
        assert not _user_can_view_library(other_user, library)
        # But SHOULD grant access to the specific document
        assert _user_can_view_document(other_user, document)

    def test_document_hint_does_not_grant_sibling_access(self, all_apps_user):
        """A skill referencing doc A does NOT grant access to doc B in the same library."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)
        data_source = DataSource.objects.create(library=library, name="Folder")
        doc_a = Document.objects.create(
            data_source=data_source, filename="a.txt", status="INDEXED"
        )
        doc_b = Document.objects.create(
            data_source=data_source, filename="b.txt", status="INDEXED"
        )

        Skill.objects.create(
            display_name="Single Doc Skill",
            description="References only doc A",
            body="Use doc A.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "document", "id": doc_a.id, "name": "a.txt"},
            ],
        )

        assert _user_can_view_document(other_user, doc_a)
        assert not _user_can_view_document(other_user, doc_b)

    def test_folder_hint_does_not_grant_library_access(self, all_apps_user):
        """A skill referencing a folder does NOT grant access to the whole library."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)
        data_source = DataSource.objects.create(library=library, name="Folder")

        Skill.objects.create(
            display_name="Folder Hint Skill",
            description="Skill referencing a folder",
            body="Use the folder.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "folder", "id": data_source.id, "name": "Folder"},
            ],
        )

        # Folder hint should NOT grant access to the whole library
        assert not _user_can_view_library(other_user, library)
        # But SHOULD grant access to the specific folder
        assert _user_can_view_data_source(other_user, data_source)

    def test_folder_hint_grants_document_access_within(self, all_apps_user):
        """A folder hint grants access to documents inside that folder."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)
        ds_hinted = DataSource.objects.create(library=library, name="Hinted")
        ds_other = DataSource.objects.create(library=library, name="Other")
        doc_in = Document.objects.create(
            data_source=ds_hinted, filename="in.txt", status="INDEXED"
        )
        doc_out = Document.objects.create(
            data_source=ds_other, filename="out.txt", status="INDEXED"
        )

        Skill.objects.create(
            display_name="Folder Scope Skill",
            description="Folder hint",
            body="body",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "folder", "id": ds_hinted.id, "name": "Hinted"},
            ],
        )

        # Document inside hinted folder is accessible (via can_view_data_source chain)
        assert _user_can_view_document(other_user, doc_in)
        # Document in another folder in the same library is NOT
        assert not _user_can_view_document(other_user, doc_out)

    def test_string_id_in_hint_works(self, all_apps_user):
        """context_hints with string IDs (from YAML fixtures) still work."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        Skill.objects.create(
            display_name="String ID Skill",
            description="Skill with string ID in hint",
            body="Use the library.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "library", "id": str(library.id), "name": library.name},
            ],
        )

        assert _user_can_view_library(other_user, library)

    def test_lookup_key_document_hint_grants_document_access(self, all_apps_user):
        """Fixture-style lookup_key hints should resolve to skill-library documents."""
        owner = all_apps_user()
        other_user = all_apps_user()
        resolved = _resolve_lookup_key_context_hint("document", "translation-glossary")
        assert resolved is not None
        document = Document.objects.get(id=resolved["id"])

        Skill.objects.create(
            display_name="Lookup Key Skill",
            description="Skill referencing fixture lookup key",
            body="Use the glossary.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {
                    "type": "document",
                    "lookup_key": "translation-glossary",
                    "name": "JUS Translation Glossary",
                },
            ],
        )

        assert _user_can_view_document(other_user, document)

    def test_removed_skill_does_not_grant_access(self, all_apps_user):
        """A deleted skill does not grant library access."""
        owner = all_apps_user()
        other_user = all_apps_user()
        library = self._create_private_library(owner)

        skill = Skill.objects.create(
            display_name="Deleted Skill",
            description="A deleted skill",
            body="Deleted.",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "library", "id": library.id, "name": library.name},
            ],
        )
        skill.delete()

        assert not _user_can_view_library(other_user, library)


@pytest.mark.django_db
class TestSkillReferenceWarnings:
    """Test the get_skills_referencing_item utility."""

    def test_finds_skills_referencing_library(self, all_apps_user):
        from otto.rules import get_skills_referencing_item

        owner = all_apps_user()
        library = Library.objects.create(
            name="Ref Library", created_by=owner, is_public=False
        )

        skill = Skill.objects.create(
            display_name="Referencing Skill",
            description="References a library",
            body="body",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "library", "id": library.id, "name": "Ref Library"},
            ],
        )

        result = get_skills_referencing_item("library", library.id)
        assert result.filter(id=skill.id).exists()

    def test_finds_skills_referencing_document(self, all_apps_user):
        from otto.rules import get_skills_referencing_item

        owner = all_apps_user()
        library = Library.objects.create(
            name="Ref Library 2", created_by=owner, is_public=False
        )
        ds = DataSource.objects.create(library=library, name="Folder")
        doc = Document.objects.create(
            data_source=ds, filename="test.txt", status="INDEXED"
        )

        skill = Skill.objects.create(
            display_name="Doc Referencing Skill",
            description="References a document",
            body="body",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "document", "id": doc.id, "name": "test.txt"},
            ],
        )

        result = get_skills_referencing_item("document", doc.id)
        assert result.filter(id=skill.id).exists()

    def test_does_not_find_removed_skills(self, all_apps_user):
        from otto.rules import get_skills_referencing_item

        owner = all_apps_user()

        skill = Skill.objects.create(
            display_name="Deleted Ref Skill",
            description="Deleted skill",
            body="body",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "library", "id": 99999, "name": "Fake"},
            ],
        )
        skill.delete()

        result = get_skills_referencing_item("library", 99999)
        assert not result.exists()

    def test_does_not_find_unrelated_skills(self, all_apps_user):
        from otto.rules import get_skills_referencing_item

        owner = all_apps_user()

        Skill.objects.create(
            display_name="Unrelated Skill",
            description="No ref",
            body="body",
            owner=owner,
            sharing_option="everyone",
            context_hints=[
                {"type": "library", "id": 11111, "name": "Other"},
            ],
        )

        result = get_skills_referencing_item("library", 22222)
        assert not result.exists()


@pytest.mark.django_db
def test_check_context_hints_marks_missing_items_as_broken(all_apps_user):
    owner = all_apps_user()
    library = Library.objects.create(
        name="Context Hint Library", created_by=owner, is_public=False
    )
    data_source = DataSource.objects.create(library=library, name="Folder")
    document = Document.objects.create(
        data_source=data_source, filename="test.txt", status="INDEXED"
    )
    referenced_skill = Skill.objects.create(
        display_name="Referenced skill",
        description="Exists",
        body="Prompt",
        owner=owner,
    )

    statuses = check_context_hints(
        [
            {"type": "library", "id": library.id, "name": library.name},
            {"type": "folder", "id": data_source.id, "name": data_source.name},
            {"type": "document", "id": document.id, "name": document.filename},
            {
                "type": "skill",
                "id": referenced_skill.id,
                "name": referenced_skill.display_name,
            },
            {"type": "tool", "id": "local_qa_libraries", "name": "Q&A Libraries"},
            {"type": "folder", "id": 999999, "name": "Missing folder"},
        ]
    )

    assert statuses[f"library:{library.id}"]["broken"] is False
    assert statuses[f"library:{library.id}"]["is_public"] is False
    assert statuses[f"folder:{data_source.id}"]["broken"] is False
    assert statuses[f"folder:{data_source.id}"]["is_public"] is False
    assert statuses[f"document:{document.id}"]["broken"] is False
    assert statuses[f"skill:{referenced_skill.id}"]["broken"] is False
    assert statuses["tool:local_qa_libraries"]["broken"] is False
    assert statuses["folder:999999"]["broken"] is True
    assert "Broken link" in statuses["folder:999999"]["message"]


@pytest.mark.django_db
def test_check_context_hints_marks_public_library_folders_as_public(all_apps_user):
    owner = all_apps_user()
    public_library = Library.objects.create(
        name="Public Context Hint Library",
        created_by=owner,
        is_public=True,
    )
    public_folder = DataSource.objects.create(library=public_library, name="Folder")

    statuses = check_context_hints(
        [
            {"type": "library", "id": public_library.id, "name": public_library.name},
            {"type": "folder", "id": public_folder.id, "name": public_folder.name},
        ]
    )

    assert statuses[f"library:{public_library.id}"]["broken"] is False
    assert statuses[f"library:{public_library.id}"]["is_public"] is True
    assert statuses[f"folder:{public_folder.id}"]["broken"] is False
    assert statuses[f"folder:{public_folder.id}"]["is_public"] is True
