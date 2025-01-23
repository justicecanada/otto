import os
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import Group
from django.urls import reverse

import pytest

from chat.models import Chat
from librarian.forms import DocumentDetailForm, LibraryDetailForm
from librarian.models import DataSource, Document, Library
from librarian.views import get_editable_libraries

this_dir = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.django_db
def test_editable_library_list_and_library_form(client, all_apps_user, basic_user):
    # All apps user should be able to edit all public libraries
    # ... plus their personal library (automatically created with user)
    user = all_apps_user()
    client.force_login(user)
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 1
    # Add a library for the all apps user
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library", "is_public": False, "order": 0}
    )
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 2
    # All apps user can create public libraries
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library 2", "is_public": True, "order": 0}
    )
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 2
    # Public libraries must have a name
    form = LibraryDetailForm(
        user=user, data={"name_en": "", "is_public": True, "order": 0}
    )
    with pytest.raises(ValueError):
        form.save()

    # Basic user should not be able to edit any libraries except their personal library
    other_user = basic_user()
    client.force_login(other_user)
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 1
    # Add a library for the basic user
    form = LibraryDetailForm(user=other_user, data={"is_public": False, "order": 0})
    form.save()
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 2
    # Basic user can't create public libraries; it will just end up as a private library
    num_public_libraries = Library.objects.filter(is_public=True).count()
    form = LibraryDetailForm(
        user=other_user,
        data={"name_en": "Test Library 3", "is_public": True, "order": 0},
    )
    form.save()
    assert Library.objects.filter(is_public=True).count() == num_public_libraries

    # Check that admin user can't edit the basic user's non-public library
    client.force_login(user)
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 2


@pytest.mark.django_db
def test_modal_library_list(client, all_apps_user):
    client.force_login(all_apps_user())
    url = reverse("librarian:modal")
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
def test_modal_edit_library_get(client, all_apps_user, basic_user):
    client.force_login(all_apps_user())
    library = Library.objects.get_default_library()
    url = reverse("librarian:modal_edit_library", kwargs={"library_id": library.id})
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
    url = reverse("librarian:modal_edit_library", kwargs={"library_id": library.id})
    response = client.get(url)
    assert response.status_code == 200
    user = basic_user()
    user.groups.add(Group.objects.get(name="AI Assistant user"))
    # Accept the terms
    user.accepted_terms_date = datetime.now()
    user.save()
    # Basic user should not be able to edit
    client.force_login(user)
    response = client.get(url)
    # Redirects to their personal library
    assert response.status_code == 302

    url = reverse(
        "librarian:modal_edit_library", kwargs={"library_id": user.personal_library.id}
    )
    assert response.url == url
    # Try going directly to user's personal library this should work
    response = client.get(url)

    assert response.status_code == 200


@pytest.mark.django_db
def test_chat_data_source(client, all_apps_user):
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

    from chat.llm import OttoLLM
    from librarian.tasks import (
        delete_documents_from_vector_store,
        process_document_helper,
    )

    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    data_source_id = data_source.id
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
    document_id = document.id
    assert document is not None

    # Get the file path of the uploaded file
    file_path = document.file.file.path

    llm = OttoLLM(mock_embedding=True)
    process_document_helper(document, llm)

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

    assert document.pdf_method == "text only"
    assert document.truncated_text.startswith(document.extracted_text[:10])
    assert "$" in document.display_cost

    # Test downloading document
    response = client.get(
        reverse("librarian:download_document", kwargs={"document_id": document_id})
    )
    assert response.status_code == 200
    # It should be a file
    assert (
        response["Content-Disposition"] == f"attachment; filename={document.filename}"
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
def test_start_stop(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    data_source_id = data_source.id
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
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
        modal_edit_data_source,
        name="modal_edit_data_source",
    ),
    """
    url = reverse(
        "librarian:modal_edit_data_source", kwargs={"data_source_id": data_source.id}
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
        modal_edit_document,
        name="modal_edit_document",
    ),
    """
    url = reverse("librarian:modal_edit_document", kwargs={"document_id": document.id})
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
    role = LibraryUserRole.objects.create(user=user, library=tmp_library, role="admin")
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
    # Check the library object is deleted
    assert not Library.objects.filter(id=tmp_library.id).exists()
    assert not LibraryUserRole.objects.filter(id=role.id).exists()


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
