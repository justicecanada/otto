import os
import time

from django.conf import settings
from django.urls import reverse

import pytest

from librarian.forms import LibraryDetailForm
from librarian.models import DataSource, Document, Library
from librarian.views import get_editable_libraries

skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)

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
def test_chat_data_source(client, all_apps_user):
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

    from chat.llm import OttoLLM
    from chat.models import Chat
    from librarian.tasks import (
        delete_documents_from_vector_store,
        process_document_helper,
    )

    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    assert data_source is not None
    # Upload a file to the data source
    # Don't automatically start processing; we will trigger it manually to avoid using Celery
    url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
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

    # Now, delete the chat.
    chat.delete()
    # Ensure that the data source and document were deleted
    assert not DataSource.objects.filter(id=data_source.id).exists()
    assert not Document.objects.filter(id=document.id).exists()
    # The Celery delete methods won't have actually worked, so call them manually
    delete_documents_from_vector_store(
        [document.uuid_hex], user.personal_library.uuid_hex
    )
    # Ensure that the nodes were deleted
    time.sleep(1)
    nodes = retriever.retrieve("What is this about?")
    assert len(nodes) == 0

    # Check that the file is also deleted
    # assert not os.path.exists(file_path)
