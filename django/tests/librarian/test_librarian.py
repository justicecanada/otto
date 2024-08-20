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


@pytest.mark.django_db
def test_editable_library_list_and_library_form(client, all_apps_user, basic_user):
    # All apps user should be able to edit all public libraries
    user = all_apps_user()
    client.force_login(user)
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count()
    # Add a library for the all apps user
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library", "is_public": False, "order": 0}
    )
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 1
    # All apps user can create public libraries
    form = LibraryDetailForm(
        user=user, data={"name_en": "Test Library 2", "is_public": True, "order": 0}
    )
    form.save()
    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 1
    # Public libraries must have a name
    form = LibraryDetailForm(
        user=user, data={"name_en": "", "is_public": True, "order": 0}
    )
    with pytest.raises(ValueError):
        form.save()

    # Basic user should not be able to edit any libraries at this point
    other_user = basic_user()
    client.force_login(other_user)
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 0
    # Add a library for the basic user
    form = LibraryDetailForm(user=other_user, data={"is_public": False, "order": 0})
    form.save()
    user_libraries = get_editable_libraries(other_user)
    assert len(user_libraries) == 1
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
    assert len(user_libraries) == Library.objects.filter(is_public=True).count() + 1


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


# @pytest.mark.django_db
# def test_modal_edit_library_post(client, all_apps_user, library):
#     client.force_login(all_apps_user())
#     url = reverse("librarian:modal_edit_library", kwargs={"library_id": library.id})
#     response = client.post(url, {"name_en": "Updated Library", "is_public": True})
#     assert response.status_code == 200  # or 302 if it redirects after update


# @pytest.mark.django_db
# def test_modal_delete_library(client, all_apps_user, library):
#     client.force_login(all_apps_user())
#     url = reverse("librarian:modal_delete_library", kwargs={"library_id": library.id})
#     response = client.delete(url)
#     assert response.status_code == 200  # or 302 if it redirects after deletion


# # Tests for permission checks
# @pytest.mark.django_db
# def test_modal_create_library_post_no_permission(client, basic_user):
#     client.force_login(basic_user())
#     url = reverse("librarian:modal_create_library")
#     response = client.post(url, {"name_en": "New Library", "is_public": True})
#     assert response.status_code == 403


# @pytest.mark.django_db
# def test_modal_edit_library_post_no_permission(client, basic_user, library):
#     client.force_login(basic_user())
#     url = reverse("librarian:modal_edit_library", kwargs={"library_id": library.id})
#     response = client.post(url, {"name_en": "Updated Library", "is_public": True})
#     assert response.status_code == 403


# @pytest.mark.django_db
# def test_modal_delete_library_no_permission(client, basic_user, library):
#     client.force_login(basic_user())
#     url = reverse("librarian:modal_delete_library", kwargs={"library_id": library.id})
#     response = client.delete(url)
#     assert response.status_code == 403


# TODO: These all need to be rewritten for the new librarian / Celery rewrite
"""
@pytest.mark.django_db
@skip_on_github_actions
def test_library(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a library
    Library.objects.create(
        name="Test Library 1",
        description="Library description",
        vector_store_table="test_library_1",
        modified_by=user,
    )

    # Get the library
    library = Library.objects.get(name="Test Library 1")

    # Assert that the library was created
    assert library.description == "Library description"

    # Test that changing the vector_store_table to an invalid value raises a ValidationError
    with pytest.raises(ValidationError):
        library.vector_store_table = "invalid vector table"
        library.save()

    # Test that changing the vector_store_table to a valid value does not raise a ValidationError
    library.vector_store_table = "valid_vector_table"
    library.save()

    # Test that the library can be updated
    library.name = "Updated Library"
    library.save()

    # Assert that the library was updated
    assert library.name == "Updated Library"

    library_pk = library.pk

    # Test that the library can be deleted
    library.delete()

    # Assert that the library was deleted
    assert Library.objects.filter(pk=library_pk).count() == 0


@pytest.mark.django_db
def test_data_source(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a library
    library = Library.objects.create(
        name="Test Library 2",
        description="Library description",
        vector_store_table="test_library_2",
        modified_by=user,
    )

    # Create a data source associated with the library
    data_source = DataSource.objects.create(
        name="Test DataSource",
        library=library,
        modified_by=user,
    )

    assert data_source.security_label == SecurityLabel.default_security_label()

    # Retrieve the created data source
    retrieved_data_source = DataSource.objects.get(name="Test DataSource")

    # Assert the data source was created and associated with the library
    assert retrieved_data_source.name == "Test DataSource"
    assert retrieved_data_source.library == library

    # Test updating the data source
    retrieved_data_source.name = "Updated DataSource"
    retrieved_data_source.save()

    # Assert that the data source was updated
    assert retrieved_data_source.name == "Updated DataSource"

    ds_pk = retrieved_data_source.pk
    # Test that the data source can be deleted
    retrieved_data_source.delete()

    # Assert that the data source was deleted
    assert DataSource.objects.filter(pk=ds_pk).count() == 0


# Test behavior upon Library deletion
@pytest.mark.django_db
@skip_on_github_actions
def test_data_source_deletion_on_library_delete(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    library = Library.objects.create(
        name="Test Library 4",
        description="Library description",
        vector_store_table="test_library_4",
        modified_by=user,
    )

    data_source = DataSource.objects.create(
        name="Test DataSource XYZ",
        library=library,
        modified_by=user,
    )

    # Deleting the library and checking if DataSource gets deleted as well
    library.delete()
    assert DataSource.objects.filter(name="Test DataSource XYZ").count() == 0


@skip_on_github_actions
def test_fetch_and_process_data_source(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a library
    library = Library.objects.create(
        name="Test Library 6",
        description="Library description",
        vector_store_table="test_library_6",
        modified_by=user,
    )

    # Create a data source associated with the library
    data_source = DataSource.objects.create(
        name="Test DataSource",
        library=library,
        modified_by=user,
    )

    # Discover content for the data source
    data_source.discover_content()

    # Fetch and process the data source
    data_source.fetch_and_process()

    # Confirm that documents exist in the database after the fetch and process
    assert Document.objects.filter(data_source=data_source).exists()

"""
