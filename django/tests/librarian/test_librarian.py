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
def test_delete_chat_data_source(client, all_apps_user):
    from chat.models import Chat, Message

    user = all_apps_user()
    client.force_login(user)
    # Create a chat by hitting the chat route
    url = reverse("chat:new_chat")
    chat = Chat.objects.filter(users=user).last()
    assert chat is not None
    # Add a message to the chat
