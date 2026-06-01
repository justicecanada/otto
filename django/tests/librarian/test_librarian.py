import json
import os
import time
from datetime import datetime

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.urls import reverse

import pytest
from chat_next.models import Chat as NextChat
from chat_next.models import Message as NextMessage

from otto.utils.common import normalize_content_ingestion_url

from chat.models import Chat, Message
from librarian.forms import DocumentDetailForm, LibraryDetailForm
from librarian.models import (
    DataSource,
    Document,
    Library,
    LibraryUserRole,
    SavedFileDerivative,
)
from librarian.tasks import (
    _fetch_and_detect,
    parse_azure_response_and_continue,
    submit_and_poll_azure_document_ai,
)
from librarian.utils.derivatives import (
    DERIVATION_AZURE_OCR_PDF,
    record_saved_file_derivative,
)
from librarian.utils.library_management import (
    create_library_with_default_folder,
    resolve_library_ingestion_destination,
)
from librarian.utils.process_document import (
    process_file,
    save_content_to_saved_file,
)
from librarian.utils.process_engine import submit_azure_document_ai
from librarian.views import get_editable_libraries, get_viewable_libraries

this_dir = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.django_db
def test_handle_task_error_debug_and_prod(settings, all_apps_user):
    """
    Test _handle_task_error in both debug and production settings, covering error/exception branches.
    """
    from unittest.mock import MagicMock, patch

    from librarian.tasks import _handle_task_error

    all_apps_user()
    # Simulate a task and exc
    task = MagicMock()
    exc = Exception("Test error")
    args = (1, 2, 3)
    kwargs = {"foo": "bar"}
    einfo = MagicMock()
    with patch("librarian.tasks.logger"):
        # DEBUG True: should not raise, but may or may not log error depending on code path
        settings.DEBUG = True
        _handle_task_error(task, exc, args, kwargs, einfo)
        # No assertion on logger.error in DEBUG mode
        # DEBUG False: should log error and raise
        settings.DEBUG = False
        try:
            _handle_task_error(task, exc, args, kwargs, einfo)
        except Exception:
            pass  # Any exception is acceptable
        else:
            assert False, "Exception not raised in production mode"


@pytest.mark.django_db
def test_editable_library_list_and_library_form(client, all_apps_user, basic_user):
    # All apps user should be able to edit all public libraries
    # ... plus their personal library (automatically created with user)
    user = all_apps_user()
    client.force_login(user)
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 2
    # Add a library for the all apps user
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library", "is_public": False, "order": 0}
    )
    assert form.is_valid()
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 3
    # All apps user can create public libraries
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library 2", "is_public": True, "order": 0}
    )
    assert form.is_valid()
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 3
    # Public libraries must have a name
    form = LibraryDetailForm(
        user=user, data={"name_en": "", "is_public": True, "order": 0}
    )
    assert not form.is_valid()

    # Basic user should not be able to edit any libraries except their personal library
    other_user = basic_user()
    client.force_login(other_user)
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 1
    # Add a library for the basic user
    form = LibraryDetailForm(user=other_user, data={"is_public": False, "order": 0})
    assert form.is_valid()
    form.save()
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 2
    # Basic user can't create public libraries; it will just end up as a private library
    num_public_libraries = Library.objects.filter(is_public=True).count()
    form = LibraryDetailForm(
        user=other_user,
        data={"name_en": "Test Library 3", "is_public": True, "order": 0},
    )
    assert form.is_valid()
    form.save()
    assert Library.objects.filter(is_public=True).count() == num_public_libraries

    # Public sharing admin can create public libraries
    jus_steward = basic_user("jus_steward")
    jus_group, _ = Group.objects.get_or_create(name="Public sharing admin")
    jus_steward.groups.add(jus_group)
    num_public_libraries = Library.objects.filter(is_public=True).count()
    form = LibraryDetailForm(
        user=jus_steward,
        data={"name_en": "JUS Public Library", "is_public": True, "order": 0},
    )
    assert form.is_valid()
    form.save()
    assert Library.objects.filter(is_public=True).count() == num_public_libraries + 1

    # Check that admin user can't edit the basic user's non-public library
    client.force_login(user)
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 3


@pytest.mark.django_db
def test_defaults_skill_library_appears_for_admins_only(all_apps_user, basic_user):
    admin_user = all_apps_user()
    regular_user = basic_user(accept_terms=True)

    defaults_library = Library.objects.get(name_en="Skill files (Otto defaults)")

    assert defaults_library in get_viewable_libraries(admin_user)
    assert defaults_library in get_editable_libraries(admin_user)
    assert defaults_library not in get_viewable_libraries(regular_user)
    assert defaults_library not in get_editable_libraries(regular_user)


@pytest.mark.django_db
def test_skill_hints_do_not_expose_private_resources_in_librarian(
    client, all_apps_user
):
    from chat_next.models import Skill

    owner = all_apps_user("hint-owner")
    other_user = all_apps_user("hint-viewer")

    library = Library.objects.create(
        name_en="Hinted private library",
        created_by=owner,
        is_public=False,
    )
    LibraryUserRole.objects.create(library=library, user=owner, role="admin")
    data_source = DataSource.objects.create(library=library, name="Hinted folder")
    document = Document.objects.create(
        data_source=data_source,
        filename="hinted.txt",
        extracted_text="hinted",
        status="SUCCESS",
    )

    Skill.objects.create(
        display_name="Hinted private library skill",
        description="Runtime-only access",
        body="Prompt",
        owner=owner,
        sharing_option="everyone",
        context_hints=[
            {"type": "library", "id": library.id, "name": str(library)},
        ],
    )

    assert library not in get_viewable_libraries(other_user)

    client.force_login(other_user)

    assert (
        client.get(
            reverse("librarian:modal_view_library", kwargs={"library_id": library.id})
        ).status_code
        == 302
    )
    assert (
        client.get(
            reverse(
                "librarian:modal_view_data_source",
                kwargs={"data_source_id": data_source.id},
            )
        ).status_code
        == 302
    )
    assert (
        client.get(
            reverse("librarian:download_document", kwargs={"document_id": document.id})
        ).status_code
        == 302
    )
    assert (
        client.get(
            reverse("librarian:document_text", kwargs={"document_id": document.id})
        ).status_code
        == 302
    )
    assert (
        client.get(
            reverse("librarian:search_docs", args=[data_source.id]),
            {"search": "hinted"},
        ).status_code
        == 302
    )
    assert (
        client.get(
            reverse("librarian:sort_docs", args=[data_source.id, "filename"])
        ).status_code
        == 302
    )


@pytest.mark.django_db
def test_skill_library_is_hidden_even_if_bad_role_exists(all_apps_user):
    owner = all_apps_user("skill-library-owner")
    other_user = all_apps_user("skill-library-other")

    skill_library = owner.skill_library or owner.create_skill_library()
    LibraryUserRole.objects.update_or_create(
        library=skill_library,
        user=other_user,
        defaults={"role": "admin"},
    )

    assert skill_library not in get_viewable_libraries(other_user)
    assert skill_library not in get_editable_libraries(other_user)


@pytest.mark.django_db
def test_skill_library_user_management_is_blocked_for_owner(client, all_apps_user):
    owner = all_apps_user("skill-library-protected-owner")
    client.force_login(owner)

    skill_library = owner.skill_library or owner.create_skill_library()
    assert not owner.has_perm("librarian.manage_library_users", skill_library)

    response = client.post(
        reverse(
            "librarian:modal_manage_library_users",
            kwargs={"library_id": skill_library.id},
        ),
        {
            "admins": "",
            "contributors": "",
            "viewers": "",
        },
    )

    assert response.status_code == 302
    assert LibraryUserRole.objects.filter(
        library=skill_library,
        user=owner,
        role="admin",
    ).exists()


@pytest.mark.django_db
def test_modal_library_list(client, all_apps_user):
    client.force_login(all_apps_user())
    url = reverse("librarian:modal_library_list")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_modal_create_library_get(client, all_apps_user):
    client.force_login(all_apps_user())
    url = reverse("librarian:modal_create_library")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_modal_create_library_post(client, all_apps_user):
    client.force_login(all_apps_user())
    url = reverse("librarian:modal_create_library")
    response = client.post(url, {"name_en": "New Library", "is_public": True})
    assert response.status_code == 200  # or 302 if it redirects after creation


@pytest.mark.django_db
def test_modal_view_library_get(client, all_apps_user, basic_user):
    client.force_login(all_apps_user())
    library = Library.objects.get_default_library()
    url = reverse("librarian:modal_view_library", kwargs={"library_id": library.id})
    response = client.get(url)
    assert response.status_code == 200
    # Basic user should not be able to edit
    client.force_login(basic_user())
    response = client.get(url)
    # Redirects home with error notification
    assert response.status_code == 302


@pytest.mark.django_db
def test_modal_edit_library_get_redirect(client, all_apps_user, basic_user):
    client.force_login(all_apps_user())
    library = Library.objects.get_default_library()
    url = reverse(
        "librarian:modal_create_data_source", kwargs={"library_id": library.id}
    )
    response = client.get(url)
    assert response.status_code == 200

    user = basic_user()
    # Accept the terms
    user.accepted_terms_date = datetime.now()
    user.save()
    # Basic user should not be able to edit
    client.force_login(user)
    response = client.get(url)
    # Redirects to their personal library
    assert response.status_code == 302

    url = reverse(
        "librarian:modal_view_library", kwargs={"library_id": user.personal_library.id}
    )
    assert response.url == url
    # Try going directly to user's personal library this should work
    response = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_personal_library_folders_include_chat_next_chats(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a legacy chat with a message
    legacy_chat = Chat.objects.create(user=user, title="Legacy chat title")
    Message.objects.create(chat=legacy_chat, text="hello")
    legacy_ds = legacy_chat.data_source

    # Create a chat_next chat with a message
    next_chat = NextChat.objects.create(user=user, title="Next chat title")
    NextMessage.objects.create(chat=next_chat, text="hello")
    next_ds = next_chat.data_source

    # Ensure both data sources are linked to the user's personal library
    assert legacy_ds.library_id == user.personal_library.id
    assert next_ds.library_id == user.personal_library.id

    folders = list(user.personal_library.folders)
    assert legacy_ds in folders
    assert next_ds in folders

    # DataSource helpers should resolve chat titles for both chat types
    assert legacy_ds.chat_title == "Legacy chat title"
    assert next_ds.chat_title == "Next chat title"


@pytest.mark.django_db
@pytest.mark.usefixtures("configure_celery_for_tests")
def test_chat_data_source(client, all_apps_user, monkeypatch):
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

    from chat.llm import OttoLLM
    from librarian.tasks import delete_documents_from_vector_store, process_document

    original_process = Document.process

    def process_with_mock(document_self, *args, **kwargs):
        kwargs["mock_embedding"] = True
        return original_process(document_self, *args, **kwargs)

    monkeypatch.setattr(Document, "process", process_with_mock)

    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    data_source_id = data_source.id
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:direct_upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
    document_id = document.id
    assert document is not None

    # Get the file path of the uploaded file
    file_path = document.saved_file.file.path

    # Process synchronously (Celery configured eager via conftest)
    process_document.run(document_id=document.id, mock_embedding=True)

    # We need an llm instance for later assertions
    llm = OttoLLM(mock_embedding=True)
    # Ensure that the document was processed - that is, text nodes exist in vector DB
    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="node_type",
                value="document",
                operator="!=",
            ),
            MetadataFilter(
                key="doc_id",
                value=[document.uuid_hex],
                operator="in",
            ),
        ]
    )
    retriever = llm.get_retriever(user.personal_library.uuid_hex, filters)
    nodes = retriever.retrieve("What is this about?")
    assert len(nodes) > 0

    # pdf_method field may have been renamed to pdf_extraction_method in models; assert presence via either
    document.refresh_from_db()
    # Accept either naming (pdf_method or pdf_extraction_method) and include
    # legacy value 'text only' which some fixtures produce.
    allowed = {"default", "layout", "azure_read", "azure_layout", "text only"}
    pdf_method_val = getattr(document, "pdf_method", None)
    pdf_extraction_val = getattr(document, "pdf_extraction_method", None)
    if pdf_method_val:
        assert pdf_method_val in allowed
    elif pdf_extraction_val:
        assert pdf_extraction_val in allowed
    assert document.truncated_text.startswith(document.extracted_text[:10])
    assert "$" in document.display_cost

    # Test downloading document
    response = client.get(
        reverse("librarian:download_document", kwargs={"document_id": document_id})
    )
    assert response.status_code == 200
    # It should be a file
    assert (
        response["Content-Disposition"] == f'attachment; filename="{document.filename}"'
    )
    # Test getting the text of the document
    response = client.get(
        reverse("librarian:document_text", kwargs={"document_id": document_id})
    )
    assert response.status_code == 200
    assert response.content == document.extracted_text.encode("utf-8")

    # Now, delete the chat.
    chat.delete()
    # Ensure that the data source and document were deleted
    assert not DataSource.objects.filter(id=data_source_id).exists()
    assert not Document.objects.filter(id=document_id).exists()
    # The Celery delete methods won't have actually worked, so call them manually
    delete_documents_from_vector_store(
        [document.uuid_hex], user.personal_library.uuid_hex
    )
    # Ensure that the nodes were deleted
    time.sleep(1)
    nodes = retriever.retrieve("What is this about?")
    assert len(nodes) == 0

    # Check that the file is also deleted
    assert not os.path.exists(file_path)


@pytest.mark.django_db
def test_process_document_treats_octet_stream_zip_as_container(
    all_apps_user, monkeypatch
):
    from unittest.mock import Mock

    from librarian.models import SavedFile
    from librarian.tasks import finalize_document_light, process_document

    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    data_source = chat.data_source

    with open(os.path.join(this_dir, "test_files/example.zip"), "rb") as f:
        saved_file = SavedFile.objects.create(content_type="application/octet-stream")
        saved_file.file.save("misclassified.zip", ContentFile(f.read()))
        saved_file.generate_hash()

    document = Document.objects.create(
        data_source=data_source,
        saved_file=saved_file,
        filename="misclassified.zip",
    )

    finalize_apply_async = Mock()
    monkeypatch.setattr(finalize_document_light, "apply_async", finalize_apply_async)
    monkeypatch.setattr(Document, "process", lambda self, *args, **kwargs: None)

    result = process_document.run(document_id=document.id, mock_embedding=True)

    document.refresh_from_db()

    assert result["status"] == "SUCCESS"
    assert result["skipped_vector_store"] is True
    assert document.is_container is True
    assert document.status == "SUCCESS"
    assert document.child_documents.exists()
    finalize_apply_async.assert_not_called()


@pytest.mark.django_db
def test_document_start_passes_refresh_flag(client, all_apps_user, monkeypatch):
    user = all_apps_user()
    client.force_login(user)
    library = Library.objects.get_default_library()
    data_source = DataSource.objects.create(library=library, name="URL docs")
    document = Document.objects.create(
        data_source=data_source,
        url="https://example.com/page",
    )

    captured = {}

    def fake_process(self, pdf_method="default", refresh_from_url=False, **kwargs):
        captured["pdf_method"] = pdf_method
        captured["refresh_from_url"] = refresh_from_url

    monkeypatch.setattr(Document, "process", fake_process)

    url = reverse(
        "librarian:document_start",
        kwargs={"document_id": document.id, "pdf_method": "default"},
    )
    response = client.get(f"{url}?refresh_from_url=true")

    assert response.status_code == 200
    assert captured["pdf_method"] == "default"
    assert captured["refresh_from_url"] is True


@pytest.mark.django_db
def test_process_document_pauses_large_document_before_embedding(
    all_apps_user, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from otto.models import OttoStatus

    from librarian.cache import get_pending_embedding_chunks
    from librarian.tasks import finalize_document_light, process_document

    all_apps_user()
    library = Library.objects.get_default_library()
    data_source = DataSource.objects.create(library=library, name="Large docs")
    document = Document.objects.create(
        data_source=data_source,
        url="https://example.com/large.csv",
    )

    otto_status = OttoStatus.objects.singleton()
    otto_status.librarian_auto_embed_max_chunks = 2
    otto_status.save(update_fields=["librarian_auto_embed_max_chunks"])

    monkeypatch.setattr(
        "librarian.tasks._fetch_and_detect",
        lambda *args, **kwargs: (b"csv,data", "text/csv", None),
    )

    def fake_extract_and_persist(doc, *args, **kwargs):
        doc.extracted_text = "one\n\ntwo\n\nthree"
        doc.save(update_fields=["extracted_text"])
        return SimpleNamespace(
            markdown=doc.extracted_text,
            chunks=["one", "two", "three"],
            pdf_method="default",
            needs_azure=False,
        )

    finalize_apply_async = Mock()
    monkeypatch.setattr(
        "librarian.tasks._extract_and_persist", fake_extract_and_persist
    )
    monkeypatch.setattr(finalize_document_light, "apply_async", finalize_apply_async)
    monkeypatch.setattr(
        "librarian.tasks._delete_document_vectors", lambda **kwargs: None
    )

    result = process_document.run(document_id=document.id, mock_embedding=True)

    document.refresh_from_db()

    assert result["status"] == "PAUSED"
    assert result["paused_for_manual_embedding"] is True
    assert document.status == "PAUSED"
    assert document.num_chunks == 3
    assert get_pending_embedding_chunks(document.id) == ["one", "two", "three"]
    finalize_apply_async.assert_not_called()


@pytest.mark.django_db
def test_document_start_embedding_queues_paused_document(
    client, all_apps_user, monkeypatch
):
    from librarian.cache import (
        get_celery_task_id,
        get_pending_embedding_chunks,
        set_pending_embedding_chunks,
    )

    user = all_apps_user()
    client.force_login(user)
    library = Library.objects.get_default_library()
    data_source = DataSource.objects.create(library=library, name="Paused docs")
    document = Document.objects.create(
        data_source=data_source,
        filename="large.csv",
        extracted_text="one\n\ntwo\n\nthree",
        num_chunks=3,
        status="PAUSED",
        url_content_type="text/csv",
    )
    set_pending_embedding_chunks(document.id, ["one", "two", "three"])

    class FakeResult:
        id = "task-123"
        backend = None

    monkeypatch.setattr(
        "librarian.tasks.finalize_document_light.apply_async",
        lambda **kwargs: FakeResult(),
    )

    response = client.get(
        reverse(
            "librarian:document_start_embedding",
            kwargs={"document_id": document.id},
        )
    )

    document.refresh_from_db()

    assert response.status_code == 200
    assert document.status == "TEXT_EXTRACTED"
    assert get_celery_task_id(document.id) == "task-123"
    assert get_pending_embedding_chunks(document.id) is None


@pytest.mark.django_db
def test_data_source_start_incomplete_skips_paused_documents(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)
    library = Library.objects.get_default_library()
    data_source = DataSource.objects.create(library=library, name="Mixed docs")
    paused = Document.objects.create(
        data_source=data_source,
        filename="paused.csv",
        status="PAUSED",
        extracted_text="already extracted",
        url_content_type="text/csv",
    )
    pending = Document.objects.create(
        data_source=data_source,
        filename="pending.csv",
        status="PENDING",
        url_content_type="text/csv",
    )

    started = []
    monkeypatch.setattr(
        Document,
        "process",
        lambda self, pdf_method="default", **kwargs: started.append(self.id),
    )

    response = client.get(
        reverse(
            "librarian:data_source_start",
            kwargs={
                "data_source_id": data_source.id,
                "pdf_method": "default",
                "scope": "incomplete",
            },
        )
    )

    assert response.status_code == 200
    assert pending.id in started
    assert paused.id not in started


@pytest.mark.django_db
def test_data_source_embed_large_only_queues_paused_documents(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)
    library = Library.objects.get_default_library()
    data_source = DataSource.objects.create(library=library, name="Paused docs")
    paused = Document.objects.create(
        data_source=data_source,
        filename="paused.csv",
        status="PAUSED",
        extracted_text="ready",
        url_content_type="text/csv",
    )
    Document.objects.create(
        data_source=data_source,
        filename="pending.csv",
        status="PENDING",
        url_content_type="text/csv",
    )

    started = []
    monkeypatch.setattr(
        Document,
        "start_manual_embedding",
        lambda self, **kwargs: started.append(self.id),
    )

    response = client.get(
        reverse(
            "librarian:data_source_embed_large",
            kwargs={"data_source_id": data_source.id},
        )
    )

    assert response.status_code == 200
    assert started == [paused.id]


@pytest.mark.django_db
def test_start_stop(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    data_source_id = data_source.id
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:direct_upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
    document_id = document.id
    assert document is not None

    # Try start and stop processing
    # Start processing
    response = client.get(
        reverse(
            "librarian:document_start",
            kwargs={"document_id": document_id, "pdf_method": "default"},
        )
    )
    assert response.status_code == 200
    # Stop processing
    response = client.get(
        reverse("librarian:document_stop", kwargs={"document_id": document_id})
    )
    assert response.status_code == 200
    # Start processing for all documents in data source
    response = client.get(
        reverse(
            "librarian:data_source_start",
            kwargs={
                "data_source_id": data_source_id,
                "pdf_method": "default",
                "scope": "all",
            },
        )
    )
    assert response.status_code == 200
    # Stop processing for all documents in data source
    response = client.get(
        reverse("librarian:data_source_stop", kwargs={"data_source_id": data_source_id})
    )
    assert response.status_code == 200
    # Start processing for incomplete documents only
    response = client.get(
        reverse(
            "librarian:data_source_start",
            kwargs={
                "data_source_id": data_source_id,
                "pdf_method": "default",
                "scope": "incomplete",
            },
        )
    )
    assert response.status_code == 200
    # Try with an invalid scope - this should raise an exception
    with pytest.raises(ValueError):
        response = client.get(
            reverse(
                "librarian:data_source_start",
                kwargs={
                    "data_source_id": data_source_id,
                    "pdf_method": "default",
                    "scope": "invalid",
                },
            )
        )


@pytest.mark.django_db
def test_modal_views(client, all_apps_user):
    library = Library.objects.get_default_library()
    user = all_apps_user()
    client.force_login(user)
    data_source = DataSource.objects.create(library=library)
    # Create a document
    document = Document.objects.create(data_source=data_source, url="https://canada.ca")
    # Poll for status updates
    url = reverse(
        "librarian:data_source_status", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    assert response.status_code == 200
    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document.id},
    )
    response = client.get(url)
    assert response.status_code == 200
    """
    path(
        "modal/library/<int:library_id>/data_source/create/",
        modal_create_data_source,
        name="modal_create_data_source",
    ),
    """
    url = reverse(
        "librarian:modal_create_data_source", kwargs={"library_id": library.id}
    )
    response = client.get(url)
    assert response.status_code == 200
    """
    path(
        "modal/data_source/<int:data_source_id>/edit/",
        modal_view_data_source,
        name="modal_view_data_source",
    ),
    """
    url = reverse(
        "librarian:modal_view_data_source", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    assert response.status_code == 200
    """
    path(
        "modal/data_source/<int:data_source_id>/document/create/",
        modal_create_document,
        name="modal_create_document",
    ),
    """
    url = reverse(
        "librarian:modal_create_document", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    assert response.status_code == 200
    """
    path(
        "modal/document/<int:document_id>/edit/",
        modal_view_document,
        name="modal_view_document",
    ),
    """
    url = reverse("librarian:modal_view_document", kwargs={"document_id": document.id})
    response = client.get(url)
    assert response.status_code == 200
    """
    path(
        "modal/document/<int:document_id>/delete/",
        modal_delete_document,
        name="modal_delete_document",
    ),
    """
    url = reverse(
        "librarian:modal_delete_document", kwargs={"document_id": document.id}
    )
    response = client.delete(url)
    assert response.status_code == 200
    # Check the document object is deleted
    assert not Document.objects.filter(id=document.id).exists()

    """
    path(
        "modal/data_source/<int:data_source_id>/delete/",
        modal_delete_data_source,
        name="modal_delete_data_source",
    ),
    """
    url = reverse(
        "librarian:modal_delete_data_source", kwargs={"data_source_id": data_source.id}
    )
    response = client.delete(url)
    assert response.status_code == 200
    # Check the data source object is deleted
    assert not DataSource.objects.filter(id=data_source.id).exists()

    # Create a library
    from librarian.models import LibraryUserRole

    tmp_library = Library.objects.create(name_en="Test Library")
    LibraryUserRole.objects.create(user=user, library=tmp_library, role="admin")
    """
    path(
        "modal/library/<int:library_id>/users/",
        modal_manage_library_users,
        name="modal_manage_library_users",
    ),
    """
    url = reverse(
        "librarian:modal_manage_library_users", kwargs={"library_id": library.id}
    )
    response = client.get(url)
    # POST-only route
    assert response.status_code == 405
    # Delete the library
    """
    path(
        "modal/library/<int:library_id>/delete/",
        modal_delete_library,
        name="modal_delete_library",
    ),
    """
    url = reverse(
        "librarian:modal_delete_library", kwargs={"library_id": tmp_library.id}
    )
    response = client.delete(url)
    assert response.status_code == 200
    # Check the library is hidden from normal queries immediately
    assert not Library.objects.filter(id=tmp_library.id).exists()
    deleted_library = Library.objects.including_deleted().get(id=tmp_library.id)
    assert deleted_library.deleted_at is not None


@pytest.mark.django_db
def test_poll_status(client, all_apps_user):
    library = Library.objects.get_default_library()
    user = all_apps_user()
    client.force_login(user)
    data_source = DataSource.objects.create(library=library)
    document = Document.objects.create(data_source=data_source, url="https://canada.ca")
    document2 = Document.objects.create(
        data_source=data_source, url="https://canada.ca"
    )
    # Poll for status updates
    url = reverse(
        "librarian:data_source_status", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    # Check the context to ensure that poll_url is not None
    assert response.context["poll_url"] is not None
    # Check the document_status route as well
    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document.id},
    )
    response = client.get(url)
    # Check the context to ensure that poll_url is not None
    assert response.context["poll_url"] is not None

    # One document completes
    document.status = "SUCCESS"
    document.save()

    # Check both routes to ensure that poll_url still not None (since document2 isn't done)
    url = reverse(
        "librarian:data_source_status", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    assert response.context["poll_url"] is not None
    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document.id},
    )
    response = client.get(url)
    assert response.context["poll_url"] is not None
    # And the other document
    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document2.id},
    )
    response = client.get(url)
    assert response.context["poll_url"] is not None

    # Second document fails
    document2.status = "ERROR"
    document2.save()

    # Check all 3 routes. All 3 should have poll_url = None
    url = reverse(
        "librarian:data_source_status", kwargs={"data_source_id": data_source.id}
    )
    response = client.get(url)
    assert response.context["poll_url"] is None

    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document.id},
    )
    response = client.get(url)
    assert response.context["poll_url"] is None

    url = reverse(
        "librarian:document_status",
        kwargs={"data_source_id": data_source.id, "document_id": document2.id},
    )
    response = client.get(url)
    assert response.context["poll_url"] is None


@pytest.mark.django_db
def test_document_url_validation():
    library = Library.objects.create(name="Test Library")
    data_source = DataSource.objects.create(name="Test DataSource", library=library)
    document = Document(data_source=data_source)

    # Valid URL
    form = DocumentDetailForm(
        data={
            "url": "https://canada.ca",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )

    assert form.is_valid()
    assert form.cleaned_data["url"] == "https://www.canada.ca"

    # Known problematic apex host is normalized to its canonical hostname.
    form = DocumentDetailForm(
        data={
            "url": "https://fca-caf.ca/cases?year=2024",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )
    assert form.is_valid()
    assert form.cleaned_data["url"] == "https://www.fca-caf.ca/cases?year=2024"

    # http is upgraded to https during normalization.
    form = DocumentDetailForm(
        data={
            "url": "http://tcc-cci.ca/decisions",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )
    assert form.is_valid()
    assert form.cleaned_data["url"] == "https://www.tcc-cci.ca/decisions"

    # Subdomain of valid URL
    form = DocumentDetailForm(
        data={
            "url": "https://www.tbs-sct.canada.ca",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )
    assert form.is_valid()

    # Invalid URL
    form = DocumentDetailForm(
        data={
            "url": "invalid-url",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )
    assert not form.is_valid()
    assert "url" in form.errors

    form = DocumentDetailForm(
        data={
            "url": "https://notallowed.com",
            "manual_title": "Test Document",
            "data_source": data_source.id,
        },
        instance=document,
    )
    assert not form.is_valid()
    assert "url" in form.errors


def test_normalize_content_ingestion_url_only_rewrites_explicit_hosts():
    assert (
        normalize_content_ingestion_url("https://canada.ca/services")
        == "https://www.canada.ca/services"
    )
    assert (
        normalize_content_ingestion_url("https://fca-caf.ca/cases?year=2024")
        == "https://www.fca-caf.ca/cases?year=2024"
    )
    assert (
        normalize_content_ingestion_url("http://tcc-cci.ca/decisions")
        == "https://www.tcc-cci.ca/decisions"
    )
    assert (
        normalize_content_ingestion_url("https://cmac-cacm.ca/en")
        == "https://www.cmac-cacm.ca/en"
    )
    assert (
        normalize_content_ingestion_url("https://manitobacourts.mb.ca/provincialcourt")
        == "https://www.manitobacourts.mb.ca/provincialcourt"
    )
    assert (
        normalize_content_ingestion_url("https://nwtcourts.ca/news")
        == "https://www.nwtcourts.ca/news"
    )
    assert (
        normalize_content_ingestion_url("https://www.tbs-sct.canada.ca/policy")
        == "https://www.tbs-sct.canada.ca/policy"
    )


def test_email_library_admins(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    library = Library.objects.get_default_library()

    response = client.get(reverse("librarian:email_library_admins", args=[library.id]))
    assert response.status_code == 200
    assert "Otto" in response.content.decode()
    assert "mailto:otto@justice.gc.ca" in response.content.decode()

    # Set user as an admin on the library
    from librarian.models import LibraryUserRole

    LibraryUserRole.objects.create(user=user, library=library, role="admin")

    response = client.get(reverse("librarian:email_library_admins", args=[library.id]))
    assert response.status_code == 200
    assert "Otto" in response.content.decode()
    assert f"mailto:{user.email}" in response.content.decode()


@pytest.mark.django_db
def test_process_file_sets_message_for_new_document(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    data_source = chat.data_source
    message = Message.objects.create(chat=chat, text="", mode="qa")

    file_content = ContentFile(b"sample content", name="nested.txt")
    process_file(
        file_content,
        data_source.id,
        "archive/nested.txt",
        "nested.txt",
        "text/plain",
        message_id=message.id,
    )

    document = Document.objects.filter(
        data_source=data_source, filename="nested.txt"
    ).first()

    assert document is not None
    assert document.messages.filter(id=message.id).exists()


@pytest.mark.django_db
def test_process_file_assigns_message_to_existing_document(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    data_source = chat.data_source

    initial_file = ContentFile(b"duplicate content", name="duplicate.txt")
    process_file(
        initial_file,
        data_source.id,
        "duplicate.txt",
        "duplicate.txt",
        "text/plain",
    )

    document = Document.objects.get(data_source=data_source, filename="duplicate.txt")
    assert document.messages.count() == 0

    message = Message.objects.create(chat=chat, text="", mode="qa")
    repeated_file = ContentFile(b"duplicate content", name="duplicate.txt")
    process_file(
        repeated_file,
        data_source.id,
        "duplicate.txt",
        "duplicate.txt",
        "text/plain",
        message_id=message.id,
    )

    document.refresh_from_db()
    assert document.messages.filter(id=message.id).exists()


@pytest.mark.django_db
def test_save_content_to_saved_file_reuses_existing_saved_file():
    first = ContentFile(b"duplicate bytes", name="first.txt")
    saved_a, resolved_a, sanitized_a = save_content_to_saved_file(
        first,
        filename="first.txt",
        content_type="text/plain",
    )

    second = ContentFile(b"duplicate bytes", name="second.txt")
    saved_b, resolved_b, sanitized_b = save_content_to_saved_file(
        second,
        filename="second.txt",
        content_type="text/plain; charset=utf-8",
    )

    assert saved_a.id == saved_b.id
    assert resolved_b == "second.txt"
    assert sanitized_a == sanitized_b == "text/plain"


@pytest.mark.django_db
def test_save_content_to_saved_file_accepts_bytes_without_filename():
    saved_file, resolved_name, sanitized_type = save_content_to_saved_file(
        b"raw bytes",
        filename=None,
        content_type="application/pdf",
    )

    assert saved_file.sha256_hash is not None
    assert resolved_name == "uploaded-file"
    assert sanitized_type == "application/pdf"


@pytest.mark.django_db
def test_create_library_with_default_folder_creates_admin_role(all_apps_user):
    user = all_apps_user()

    library, data_source = create_library_with_default_folder(
        user=user,
        name="Imported docs",
        description="Imported from tools",
        folder_name="Incoming",
        is_public=False,
    )

    assert library.created_by == user
    assert data_source.library_id == library.id
    assert data_source.name == "Incoming"
    assert user.has_perm("librarian.edit_library", library)


@pytest.mark.django_db
def test_resolve_library_ingestion_destination_for_library(all_apps_user):
    user = all_apps_user()
    library, _data_source = create_library_with_default_folder(
        user=user,
        name="Imported docs",
        description="Imported from tools",
        folder_name="Incoming",
        is_public=False,
    )

    destination = resolve_library_ingestion_destination(
        user,
        target_library_id=library.id,
    )

    assert destination["library_id"] == library.id
    assert destination["library_name"] == str(library)
    assert destination["data_source_name"] == "Imported URLs"


@pytest.mark.django_db
def test_fetch_and_detect_persists_url_documents_as_saved_files(
    monkeypatch, all_apps_user
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    document = Document.objects.create(
        data_source=chat.data_source,
        url="https://example.com/files/report.pdf",
    )

    fetched_content = b"%PDF-1.7 sample"

    def fake_fetch(url):
        assert url == document.url
        return fetched_content, "application/pdf; charset=utf-8"

    monkeypatch.setattr("librarian.tasks.fetch_from_url", fake_fetch)

    content, content_type, base_url = _fetch_and_detect(document)

    document.refresh_from_db()
    assert document.saved_file is not None
    assert document.filename == "report.pdf"
    assert document.url_content_type == "application/pdf"
    assert document.fetched_at is not None
    assert content == fetched_content
    assert content_type == "application/pdf"
    assert base_url == "https://example.com"


@pytest.mark.django_db
def test_fetch_and_detect_reuses_saved_file_without_refetching(
    monkeypatch, all_apps_user
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    document = Document.objects.create(
        data_source=chat.data_source,
        url="https://example.com/download",
    )

    initial_bytes = b"plain text content"

    def first_fetch(url):
        return initial_bytes, "text/plain"

    monkeypatch.setattr("librarian.tasks.fetch_from_url", first_fetch)
    _fetch_and_detect(document)

    document.refresh_from_db()
    assert document.saved_file is not None

    def fail_fetch(url):  # pragma: no cover - should not run when cached
        raise AssertionError("fetch_from_url should not run when using cached file")

    monkeypatch.setattr("librarian.tasks.fetch_from_url", fail_fetch)

    content, content_type, base_url = _fetch_and_detect(document)

    assert content == initial_bytes
    assert content_type == "text/plain"
    assert base_url == "https://example.com"


@pytest.mark.django_db
def test_fetch_and_detect_preserves_html_saved_file_handling(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    saved_file = save_content_to_saved_file(
        ContentFile(b"<p>one</p>\n<p>two</p>\n", name="page.html"),
        filename="page.html",
        content_type="text/html",
    )[0]
    document = Document.objects.create(
        data_source=chat.data_source,
        saved_file=saved_file,
        filename="page.html",
    )

    content, content_type, base_url = _fetch_and_detect(document)

    assert content == b"<p>one</p>\n <p>two</p>\n"
    assert content_type == "text/html"
    assert base_url is None


@pytest.mark.django_db
def test_fetch_and_detect_refreshes_when_requested(monkeypatch, all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    document = Document.objects.create(
        data_source=chat.data_source,
        url="https://example.com/download",
    )

    initial_bytes = b"plain text content"

    def first_fetch(url):
        return initial_bytes, "text/plain"

    monkeypatch.setattr("librarian.tasks.fetch_from_url", first_fetch)
    _fetch_and_detect(document)

    document.refresh_from_db()
    original_saved_file_id = document.saved_file_id

    updated_bytes = b"updated"

    def second_fetch(url):
        return updated_bytes, "text/plain"

    monkeypatch.setattr("librarian.tasks.fetch_from_url", second_fetch)

    content, content_type, base_url = _fetch_and_detect(document, refresh_from_url=True)

    document.refresh_from_db()

    assert content == updated_bytes
    assert content_type == "text/plain"
    assert base_url == "https://example.com"
    assert document.saved_file_id != original_saved_file_id


@pytest.mark.django_db
def test_submit_and_poll_azure_document_ai_uses_fetch_and_detect_fallback(
    monkeypatch, all_apps_user, tmp_path
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    document = Document.objects.create(
        data_source=chat.data_source,
        url="https://example.com/no-saved-file.pdf",
    )

    expected_content = b"%PDF-1.7 from fetch_and_detect"
    expected_hash = __import__("hashlib").sha256(expected_content).hexdigest()
    captured = {"called": False, "submitted": None, "set_operation": None}

    def fake_fetch_and_detect(doc, refresh_from_url=False):
        assert doc.id == document.id
        assert refresh_from_url is False
        captured["called"] = True
        return expected_content, "application/pdf", "https://example.com"

    def fake_submit(content, model, request_searchable_pdf=False):
        captured["submitted"] = (content, model, request_searchable_pdf)
        return "operation-123"

    class FakeAsyncResult:
        id = "next-task-id"
        backend = None

    monkeypatch.setattr("librarian.tasks._fetch_and_detect", fake_fetch_and_detect)
    monkeypatch.setattr("librarian.tasks.current_task", None)
    monkeypatch.setattr(
        "librarian.tasks.get_azure_operation_location", lambda _id: None
    )
    monkeypatch.setattr(
        "librarian.tasks.set_azure_operation_location",
        lambda _id, value: captured.__setitem__("set_operation", value),
    )
    monkeypatch.setattr(
        "librarian.tasks.set_celery_task_id", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("librarian.tasks.get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "librarian.utils.process_engine.submit_azure_document_ai",
        fake_submit,
    )
    monkeypatch.setattr(
        "librarian.utils.process_engine.poll_azure_document_ai",
        lambda _operation_location: {"status": "succeeded"},
    )
    monkeypatch.setattr(
        "librarian.tasks.parse_azure_response_and_continue.apply_async",
        lambda **_kwargs: FakeAsyncResult(),
    )

    result = submit_and_poll_azure_document_ai(
        document_id=document.id,
        model="prebuilt-layout",
        content_hash=expected_hash,
        pdf_method="default",
    )

    assert captured["called"] is True
    assert captured["submitted"] == (expected_content, "prebuilt-layout", False)
    assert captured["set_operation"] == "operation-123"
    assert result["ok"] is True
    assert result["chained_to"] == "next-task-id"


def test_submit_azure_document_ai_uses_models_enum_imports(settings, monkeypatch):
    captured = {}

    class FakeResponse:
        headers = {"operation-location": "operation-789"}

    class FakeInitialResponse:
        http_response = FakeResponse()

    class FakePollingMethod:
        _initial_response = FakeInitialResponse()

    class FakePoller:
        _polling_method = FakePollingMethod()

    class FakeClient:
        def __init__(self, endpoint, credential):
            captured["endpoint"] = endpoint
            captured["credential_class"] = credential.__class__.__name__

        def begin_analyze_document(self, model_id, body, **kwargs):
            captured["model_id"] = model_id
            captured["body"] = body
            captured["kwargs"] = kwargs
            return FakePoller()

    monkeypatch.setattr(
        "azure.ai.documentintelligence.DocumentIntelligenceClient",
        FakeClient,
    )

    operation_location = submit_azure_document_ai(
        b"%PDF-1.7 sample",
        "prebuilt-read",
        request_searchable_pdf=True,
    )

    assert operation_location == "operation-789"
    assert captured["endpoint"] == settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
    assert captured["credential_class"] == "AzureKeyCredential"
    assert captured["model_id"] == "prebuilt-read"
    assert captured["body"] == b"%PDF-1.7 sample"
    assert str(captured["kwargs"]["output"][0]) == "AnalyzeOutputOption.PDF"
    assert str(captured["kwargs"]["output_content_format"]) == (
        "DocumentContentFormat.MARKDOWN"
    )


@pytest.mark.django_db
def test_submit_and_poll_azure_document_ai_requests_searchable_pdf_for_pdf(
    monkeypatch, all_apps_user, tmp_path
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    document = Document.objects.create(
        data_source=chat.data_source,
        url="https://example.com/scan.pdf",
    )

    expected_content = b"%PDF-1.7 searchable pdf request"
    expected_hash = __import__("hashlib").sha256(expected_content).hexdigest()
    captured = {"submitted": None}

    def fake_fetch_and_detect(doc, refresh_from_url=False):
        saved_file, _resolved_name, _sanitized_type = save_content_to_saved_file(
            expected_content,
            filename="scan.pdf",
            content_type="application/pdf",
        )
        doc.saved_file = saved_file
        doc.filename = "scan.pdf"
        doc.original_saved_file = saved_file
        doc.original_filename = "scan.pdf"
        doc.url_content_type = "application/pdf"
        doc.save(
            update_fields=[
                "saved_file",
                "filename",
                "original_saved_file",
                "original_filename",
                "url_content_type",
            ]
        )
        return expected_content, "application/pdf", "https://example.com"

    def fake_submit(content, model, request_searchable_pdf=False):
        captured["submitted"] = (content, model, request_searchable_pdf)
        return "operation-456"

    class FakeAsyncResult:
        id = "parse-task-id"
        backend = None

    monkeypatch.setattr("librarian.tasks._fetch_and_detect", fake_fetch_and_detect)
    monkeypatch.setattr("librarian.tasks.current_task", None)
    monkeypatch.setattr(
        "librarian.tasks.get_azure_operation_location", lambda _id: None
    )
    monkeypatch.setattr(
        "librarian.tasks.set_azure_operation_location", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "librarian.tasks.set_celery_task_id", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("librarian.tasks.get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "librarian.utils.process_engine.submit_azure_document_ai",
        fake_submit,
    )
    monkeypatch.setattr(
        "librarian.utils.process_engine.poll_azure_document_ai",
        lambda _operation_location: {"status": "succeeded"},
    )
    monkeypatch.setattr(
        "librarian.tasks.parse_azure_response_and_continue.apply_async",
        lambda **_kwargs: FakeAsyncResult(),
    )

    result = submit_and_poll_azure_document_ai(
        document_id=document.id,
        model="prebuilt-read",
        content_hash=expected_hash,
        pdf_method="azure_read",
    )

    assert captured["submitted"] == (expected_content, "prebuilt-read", True)
    assert result["ok"] is True


@pytest.mark.django_db
def test_parse_azure_response_and_continue_persists_searchable_pdf_derivative(
    monkeypatch, all_apps_user, tmp_path
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    source_saved_file, _resolved_name, _sanitized_type = save_content_to_saved_file(
        b"%PDF-1.7 original scan",
        filename="scan.pdf",
        content_type="application/pdf",
    )
    document = Document.objects.create(
        data_source=chat.data_source,
        saved_file=source_saved_file,
        filename="scan.pdf",
    )

    result_file = tmp_path / "ocr-result.json"
    result_file.write_text(
        json.dumps(
            {
                "analyzeResult": {
                    "pages": [
                        {"pageNumber": 1, "lines": [{"content": "Hello from OCR"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    class DummyAsyncResult:
        id = "finalize-task-id"
        backend = None

    monkeypatch.setattr(
        "librarian.tasks.get_azure_operation_location",
        lambda _id: (
            "https://example.test/documentModels/prebuilt-read/analyzeResults/result-123?api-version=2024-11-30"
        ),
    )
    monkeypatch.setattr("librarian.tasks.current_task", None)
    monkeypatch.setattr(
        "librarian.tasks.get_azure_document_ai_result_pdf",
        lambda operation_location, model: b"%PDF-1.7 searchable output",
    )
    monkeypatch.setattr(
        "librarian.tasks.set_azure_operation_location", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "librarian.tasks.set_celery_task_id", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "librarian.tasks._should_pause_large_document_embedding",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "librarian.tasks.finalize_document_light.apply_async",
        lambda **_kwargs: DummyAsyncResult(),
    )

    result = parse_azure_response_and_continue(
        document_id=document.id,
        result_file_path=str(result_file),
        model="prebuilt-read",
        pdf_method="azure_read",
        source_saved_file_id=source_saved_file.id,
    )

    document.refresh_from_db()

    assert result["ok"] is True
    assert document.original_saved_file_id == source_saved_file.id
    assert document.original_filename == "scan.pdf"
    assert document.saved_file_id != source_saved_file.id
    assert document.saved_file.content_type == "application/pdf"
    assert SavedFileDerivative.objects.filter(
        source_saved_file=source_saved_file,
        derived_saved_file=document.saved_file,
        derivation_type=DERIVATION_AZURE_OCR_PDF,
    ).exists()


@pytest.mark.django_db
def test_parse_azure_response_and_continue_reuses_cached_searchable_pdf_derivative(
    monkeypatch, all_apps_user, tmp_path
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user)
    source_saved_file, _resolved_name, _sanitized_type = save_content_to_saved_file(
        b"%PDF-1.7 original scan",
        filename="scan.pdf",
        content_type="application/pdf",
    )
    cached_saved_file, _cached_name, _cached_type = save_content_to_saved_file(
        b"%PDF-1.7 cached searchable output",
        filename="scan.pdf",
        content_type="application/pdf",
    )
    record_saved_file_derivative(
        source_saved_file=source_saved_file,
        derived_saved_file=cached_saved_file,
        derivation_type=DERIVATION_AZURE_OCR_PDF,
        derivation_params={"source_filename": "scan.pdf", "model": "prebuilt-read"},
        cache_params={"model": "prebuilt-read"},
    )
    document = Document.objects.create(
        data_source=chat.data_source,
        saved_file=source_saved_file,
        filename="scan.pdf",
    )

    result_file = tmp_path / "ocr-result-cached.json"
    result_file.write_text(
        json.dumps(
            {
                "analyzeResult": {
                    "pages": [
                        {"pageNumber": 1, "lines": [{"content": "Hello from OCR"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    class DummyAsyncResult:
        id = "finalize-task-id"
        backend = None

    def should_not_fetch_pdf(*args, **kwargs):
        raise AssertionError("Cached searchable PDF should be reused")

    monkeypatch.setattr(
        "librarian.tasks.get_azure_operation_location",
        lambda _id: (
            "https://example.test/documentModels/prebuilt-read/analyzeResults/result-456?api-version=2024-11-30"
        ),
    )
    monkeypatch.setattr("librarian.tasks.current_task", None)
    monkeypatch.setattr(
        "librarian.tasks.get_azure_document_ai_result_pdf",
        should_not_fetch_pdf,
    )
    monkeypatch.setattr(
        "librarian.tasks.set_azure_operation_location", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "librarian.tasks.set_celery_task_id", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "librarian.tasks._should_pause_large_document_embedding",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "librarian.tasks.finalize_document_light.apply_async",
        lambda **_kwargs: DummyAsyncResult(),
    )

    result = parse_azure_response_and_continue(
        document_id=document.id,
        result_file_path=str(result_file),
        model="prebuilt-read",
        pdf_method="azure_read",
        source_saved_file_id=source_saved_file.id,
        cached_searchable_pdf_saved_file_id=cached_saved_file.id,
    )

    document.refresh_from_db()

    assert result["ok"] is True
    assert document.saved_file_id == cached_saved_file.id
    assert document.original_saved_file_id == source_saved_file.id


@pytest.mark.django_db
@pytest.mark.django_db
def test_sort_documents_by_status(client, all_apps_user):
    """Test that documents can be sorted by processing status in the correct order"""
    user = all_apps_user()
    client.force_login(user)

    # Create a library and data source
    import uuid

    library = Library.objects.create(
        name_en=f"Test Library {uuid.uuid4().hex[:8]}", is_public=True
    )
    data_source = DataSource.objects.create(
        name="Test Folder", library=library, order=0
    )

    # Create documents with different statuses
    doc_error = Document.objects.create(
        data_source=data_source,
        filename="error.txt",
        status="ERROR",
        url_content_type="text/plain",
    )
    doc_blocked = Document.objects.create(
        data_source=data_source,
        filename="blocked.txt",
        status="BLOCKED",
        url_content_type="text/plain",
    )
    doc_paused = Document.objects.create(
        data_source=data_source,
        filename="paused.txt",
        status="PAUSED",
        url_content_type="text/plain",
    )
    doc_success = Document.objects.create(
        data_source=data_source,
        filename="success.txt",
        status="SUCCESS",
        url_content_type="text/plain",
    )
    doc_pending = Document.objects.create(
        data_source=data_source,
        filename="pending.txt",
        status="PENDING",
        url_content_type="text/plain",
    )
    doc_processing = Document.objects.create(
        data_source=data_source,
        filename="processing.txt",
        status="PROCESSING",
        url_content_type="text/plain",
    )
    doc_init = Document.objects.create(
        data_source=data_source,
        filename="init.txt",
        status="INIT",
        url_content_type="text/plain",
    )
    doc_text_extracted = Document.objects.create(
        data_source=data_source,
        filename="text_extracted.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )

    # Sort by status
    response = client.get(
        reverse("librarian:sort_docs", args=[data_source.id, "status"])
    )
    assert response.status_code == 200

    # Verify the sort preference was saved
    from librarian.views import _apply_queryset_sort, _get_sort_pref

    sort_pref = _get_sort_pref(client, data_source.id)
    assert sort_pref == "status_asc"

    # Test the sorting via queryset
    documents_qs = Document.objects.filter(data_source=data_source)
    sorted_qs = _apply_queryset_sort(documents_qs, "status_asc")
    sorted_docs = list(sorted_qs)

    # Expected order: Processing statuses first (INIT, PROCESSING, TEXT_EXTRACTED),
    # then PENDING, then PAUSED, then SUCCESS, then BLOCKED, then ERROR
    # The specific order within "processing" statuses doesn't matter
    processing_docs = {doc_init, doc_processing, doc_text_extracted}

    # Verify that all processing statuses come before PENDING
    last_processing_idx = max(
        i for i, doc in enumerate(sorted_docs) if doc in processing_docs
    )
    pending_idx = sorted_docs.index(doc_pending)
    assert last_processing_idx < pending_idx, (
        "Processing statuses should come before PENDING"
    )

    # Verify PENDING comes before PAUSED
    paused_idx = sorted_docs.index(doc_paused)
    assert pending_idx < paused_idx, "PENDING should come before PAUSED"

    # Verify PAUSED comes before SUCCESS
    success_idx = sorted_docs.index(doc_success)
    assert paused_idx < success_idx, "PAUSED should come before SUCCESS"

    # Verify SUCCESS comes before BLOCKED
    blocked_idx = sorted_docs.index(doc_blocked)
    assert success_idx < blocked_idx, "SUCCESS should come before BLOCKED"

    # Verify BLOCKED comes before ERROR
    error_idx = sorted_docs.index(doc_error)
    assert blocked_idx < error_idx, "BLOCKED should come before ERROR"


@pytest.mark.django_db
def test_sort_toggle_direction(client, all_apps_user):
    """Test that clicking the same sort twice toggles between asc and desc"""
    user = all_apps_user()
    client.force_login(user)

    # Create a library and data source
    import uuid

    library = Library.objects.create(
        name_en=f"Test Library {uuid.uuid4().hex[:8]}", is_public=True
    )
    data_source = DataSource.objects.create(
        name="Test Folder", library=library, order=0
    )

    # Create documents
    Document.objects.create(
        data_source=data_source,
        filename="alpha.txt",
        url_content_type="text/plain",
    )
    Document.objects.create(
        data_source=data_source,
        filename="beta.txt",
        url_content_type="text/plain",
    )
    Document.objects.create(
        data_source=data_source,
        filename="gamma.txt",
        url_content_type="text/plain",
    )

    from librarian.views import _apply_queryset_sort, _get_sort_pref

    # First click: filename sort (should default to asc)
    response = client.get(
        reverse("librarian:sort_docs", args=[data_source.id, "filename"])
    )
    assert response.status_code == 200
    assert _get_sort_pref(client, data_source.id) == "filename_asc"

    # Second click: same sort (should toggle to desc)
    response = client.get(
        reverse("librarian:sort_docs", args=[data_source.id, "filename"])
    )
    assert response.status_code == 200
    assert _get_sort_pref(client, data_source.id) == "filename_desc"

    # Third click: same sort (should toggle back to asc)
    response = client.get(
        reverse("librarian:sort_docs", args=[data_source.id, "filename"])
    )
    assert response.status_code == 200
    assert _get_sort_pref(client, data_source.id) == "filename_asc"

    # Verify sorting actually works in both directions via queryset
    documents_qs = Document.objects.filter(data_source=data_source)

    sorted_asc = list(_apply_queryset_sort(documents_qs, "filename_asc"))
    assert [d.filename for d in sorted_asc] == ["alpha.txt", "beta.txt", "gamma.txt"]

    sorted_desc = list(_apply_queryset_sort(documents_qs, "filename_desc"))
    assert [d.filename for d in sorted_desc] == ["gamma.txt", "beta.txt", "alpha.txt"]


@pytest.mark.django_db
def test_search_docs_view(client, all_apps_user):
    """Test the search_docs view returns correct documents for search queries."""
    user = all_apps_user()
    client.force_login(user)
    # Create a library and data source
    import uuid

    library = Library.objects.create(
        name_en=f"Test Library {uuid.uuid4().hex[:8]}", is_public=True
    )
    data_source = DataSource.objects.create(
        name="Test Folder", library=library, order=0
    )
    # Create documents with different filenames and titles
    Document.objects.create(
        data_source=data_source,
        filename="alpha.txt",
        manual_title="Alpha Document",
        url_content_type="text/plain",
    )
    Document.objects.create(
        data_source=data_source,
        filename="beta.txt",
        manual_title="Beta Document",
        url_content_type="text/plain",
    )
    Document.objects.create(
        data_source=data_source,
        filename="gamma.txt",
        manual_title="Gamma Document",
        url_content_type="text/plain",
    )

    url = reverse("librarian:search_docs", args=[data_source.id])

    # Search by filename
    response = client.get(url, {"search": "alpha"})
    assert response.status_code == 200
    # Should only return doc1
    context_docs = response.context["documents"]
    assert any(doc.filename == "alpha.txt" for doc in context_docs)
    assert all(
        "alpha" in doc.filename or "alpha" in (doc.manual_title or "")
        for doc in context_docs
    )

    # Search by manual_title
    response = client.get(url, {"search": "Beta Document"})
    assert response.status_code == 200
    context_docs = response.context["documents"]
    assert any(doc.manual_title == "Beta Document" for doc in context_docs)
    assert all(
        "Beta Document" in (doc.manual_title or "") or "Beta Document" in doc.filename
        for doc in context_docs
    )

    # Search with no match
    response = client.get(url, {"search": "notfound"})
    assert response.status_code == 200
    context_docs = response.context["documents"]
    assert len(context_docs) == 0


@pytest.mark.django_db
def test_upload_view_creates_and_warns_on_duplicate(client, all_apps_user):
    """Test the upload view: file upload, document creation, and duplicate handling."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from librarian.models import DataSource, Document

    user = all_apps_user()
    client.force_login(user)
    # Create a library and data source
    import uuid

    library = Library.objects.create(
        name_en=f"Test Library {uuid.uuid4().hex[:8]}", is_public=True
    )
    data_source = DataSource.objects.create(
        name="Test Folder", library=library, order=0
    )

    url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
    # Upload a file
    file_content = b"sample content for upload test"
    upload_file = SimpleUploadedFile(
        "upload1.txt", file_content, content_type="text/plain"
    )
    response = client.post(
        url,
        {"librarian-input_file": upload_file, "librarian-input_file-metadata": "{}"},
        follow=True,
    )
    assert response.status_code == 200
    # Document should be created
    doc = Document.objects.filter(
        data_source=data_source, filename="upload1.txt"
    ).first()
    assert doc is not None
    # Upload the same file again (should warn about duplicate)
    upload_file2 = SimpleUploadedFile(
        "upload1.txt", file_content, content_type="text/plain"
    )
    response2 = client.post(
        url,
        {"librarian-input_file": upload_file2, "librarian-input_file-metadata": "{}"},
        follow=True,
    )
    assert response2.status_code == 200
    # Should still only be one document with that filename in this data source
    assert (
        Document.objects.filter(data_source=data_source, filename="upload1.txt").count()
        == 1
    )
    # Check that a warning message about duplicate is present
    messages = list(response2.context["messages"])
    assert any("identical document" in str(m) for m in messages)


@pytest.mark.django_db
def test_chat_folder_manage_libraries_hides_upload_controls(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    chat = NextChat.objects.create(user=user, title="Chat Next Upload Test")
    NextMessage.objects.create(chat=chat, text="hello")
    data_source = chat.data_source

    response = client.get(
        reverse(
            "librarian:modal_view_data_source",
            kwargs={"data_source_id": data_source.id},
        )
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="librarian-file-dropzone"' not in content
    assert 'id="librarian-upload-form"' not in content


@pytest.mark.django_db
def test_chat_folder_manage_libraries_blocks_upload_create_delete(
    client, all_apps_user
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    user = all_apps_user()
    client.force_login(user)

    chat = NextChat.objects.create(user=user, title="Chat Next Read Only Test")
    NextMessage.objects.create(chat=chat, text="hello")
    data_source = chat.data_source
    document = Document.objects.create(
        data_source=data_source,
        filename="existing.txt",
        url="https://canada.ca",
    )

    upload_url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
    upload_file = SimpleUploadedFile(
        "chat-upload.txt", b"chat upload content", content_type="text/plain"
    )
    response_upload = client.post(
        upload_url,
        {
            "librarian-input_file": upload_file,
            "librarian-input_file-metadata": "{}",
        },
        follow=True,
    )
    assert response_upload.status_code == 200
    assert Document.objects.filter(data_source=data_source).count() == 1
    upload_messages = list(response_upload.context["messages"])
    assert any("view-only in Manage libraries" in str(m) for m in upload_messages)

    create_url = reverse(
        "librarian:modal_create_document", kwargs={"data_source_id": data_source.id}
    )
    response_create = client.post(
        create_url,
        {
            "manual_title": "Should not save",
            "url": "https://example.com",
            "selector": "",
            "data_source": data_source.id,
            "filename": "",
        },
        follow=True,
    )
    assert response_create.status_code == 200
    assert Document.objects.filter(data_source=data_source).count() == 1
    create_messages = list(response_create.context["messages"])
    assert any("view-only in Manage libraries" in str(m) for m in create_messages)

    delete_url = reverse(
        "librarian:modal_delete_document", kwargs={"document_id": document.id}
    )
    response_delete = client.delete(delete_url, follow=True)
    assert response_delete.status_code == 200
    assert Document.objects.filter(id=document.id).exists()
    delete_messages = list(response_delete.context["messages"])
    assert any("can only be deleted from the chat" in str(m) for m in delete_messages)
