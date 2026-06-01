"""
Test that switching modes works correctly even when data sources are invalid/deleted.
This addresses the issue where uploading a file to Q&A mode, then switching modes
would fail with "Select a valid choice. X is not one of the available choices."
"""

from django.urls import reverse

import pytest

from chat.forms import ChatOptionsForm
from chat.models import Chat
from librarian.models import DataSource, Library


@pytest.mark.django_db
def test_mode_switch_with_invalid_data_sources(client, all_apps_user):
    """Test that switching modes works when qa_data_sources contains invalid IDs"""
    user = all_apps_user()
    client.force_login(user)

    # Create a chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    assert response.status_code == 200
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    assert chat.options is not None

    # Create a library and data source
    library = Library.objects.create(name="Test Library")
    data_source = DataSource.objects.create(
        library=library,
        name="Test Data Source",
    )

    # Associate the data source with the chat options
    chat.options.qa_library = library
    chat.options.qa_data_sources.add(data_source)
    chat.options.save()

    # Verify the data source is associated
    assert chat.options.qa_data_sources.count() == 1

    # Now delete the data source (simulating what happens when files are cleaned up)
    data_source_id = data_source.id
    data_source.delete()

    # Prepare form data using the existing chat options as a template
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    form_data = options_form.initial
    form_data["mode"] = "chat"  # Switch to chat mode
    # Fix up the form data so that it matches POST data from browser
    form_data = {k: v for k, v in form_data.items() if v is not None}
    form_data["qa_data_sources"] = [data_source_id]  # This will be the invalid ID
    # Remove translate_glossary if not set
    if "translate_glossary" in form_data and not form_data["translate_glossary"]:
        del form_data["translate_glossary"]

    # Try to switch modes by posting the form with the now-invalid data source ID
    # This simulates what happens in the browser when triggerOptionSave() is called
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]),
        form_data,
    )

    # Should succeed (200) instead of failing (500)
    assert response.status_code == 200

    # Reload chat options and verify the invalid data source was silently removed
    chat.refresh_from_db()
    assert chat.options.qa_data_sources.count() == 0


@pytest.mark.django_db
def test_mode_switch_with_mixed_valid_invalid_data_sources(client, all_apps_user):
    """Test that valid data sources are kept when some are invalid"""
    user = all_apps_user()
    client.force_login(user)

    # Create a chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    assert response.status_code == 200
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Create a library with two data sources
    library = Library.objects.create(name="Test Library")
    data_source_1 = DataSource.objects.create(
        library=library,
        name="Data Source 1",
    )
    data_source_2 = DataSource.objects.create(
        library=library,
        name="Data Source 2",
    )

    # Associate both data sources
    chat.options.qa_library = library
    chat.options.qa_data_sources.add(data_source_1, data_source_2)
    chat.options.save()

    # Delete one data source
    data_source_1_id = data_source_1.id
    data_source_1.delete()

    # Prepare form data using the existing chat options as a template
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    form_data = options_form.initial
    form_data["mode"] = "chat"
    # Fix up the form data so that it matches POST data from browser
    form_data = {k: v for k, v in form_data.items() if v is not None}
    form_data["qa_data_sources"] = [
        data_source_1_id,
        data_source_2.id,
    ]  # One invalid, one valid
    # Remove translate_glossary if not set
    if "translate_glossary" in form_data and not form_data["translate_glossary"]:
        del form_data["translate_glossary"]

    # Try to switch modes with both IDs (one valid, one invalid)
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]),
        form_data,
    )

    # Should succeed
    assert response.status_code == 200

    # Verify only the valid data source remains
    chat.refresh_from_db()
    assert chat.options.qa_data_sources.count() == 1
    assert data_source_2 in chat.options.qa_data_sources.all()


@pytest.mark.django_db
def test_form_validation_with_invalid_data_sources(all_apps_user):
    """Test the form's clean methods directly"""
    user = all_apps_user()

    # Create a library and data source
    library = Library.objects.create(name="Test Library")
    data_source = DataSource.objects.create(
        library=library,
        name="Test Data Source",
    )
    data_source_id = data_source.id

    # Create a chat with options
    chat = Chat.objects.create(user=user, title="Test Chat")
    chat.options.qa_library = library
    chat.options.qa_data_sources.add(data_source)
    chat.options.save()

    # Delete the data source
    data_source.delete()

    # Prepare form data using the existing chat options as a template
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    form_data = options_form.initial
    # Fix up the form data so that it matches POST data from browser
    form_data = {k: v for k, v in form_data.items() if v is not None}
    form_data["qa_data_sources"] = [str(data_source_id)]  # Invalid ID
    # Remove translate_glossary if not set
    if "translate_glossary" in form_data and not form_data["translate_glossary"]:
        del form_data["translate_glossary"]

    # Create a form with the invalid data source ID in the POST data
    form = ChatOptionsForm(
        data=form_data,
        instance=chat.options,
        user=user,
    )

    # Form should be valid (the clean method filters out invalid IDs)
    assert form.is_valid(), f"Form errors: {form.errors}"

    # The cleaned data should have an empty list for qa_data_sources
    assert form.cleaned_data["qa_data_sources"] == []
