from django.conf import settings
from django.core.exceptions import ValidationError

import pytest

from librarian.models import DataSource, Document, Library
from otto.models import SecurityLabel

skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)

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
