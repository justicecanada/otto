from django.urls import reverse

import pytest

from chat._llm import DEFAULT_TRANSLATE_MODEL_ID
from chat.forms import ChatOptionsForm
from chat.models import Chat, Message, Preset
from librarian.models import Library, LibraryUserRole

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.django_db
def test_translate_model_dropdown_uses_gpt_54_series_and_default(all_apps_user):
    user = all_apps_user()
    form = ChatOptionsForm(user=user)

    translate_choices = list(form.fields["translate_model"].widget.choices)
    translate_choice_ids = [choice[0] for choice in translate_choices]

    assert translate_choice_ids[:4] == [
        "azure",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ]
    assert "gpt-5-mini" not in translate_choice_ids
    assert form["translate_model"].value() == DEFAULT_TRANSLATE_MODEL_ID


@pytest.mark.django_db
def test_new_chat_options_default_translate_model(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Translate default")
    assert chat.options.translate_model == DEFAULT_TRANSLATE_MODEL_ID


@pytest.mark.django_db
def test_chat_options(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat by hitting the new chat route
    # Need to follow redirects to have it create the ChatOptions (in "chat" view)
    response = client.get(reverse("chat:new_chat"), follow=True)
    assert response.status_code == 200
    new_chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    # Check that a ChatOptions object has been created
    assert new_chat.options is not None

    # ChatOptions GET route should not work, since we need to POST the form
    response = client.get(reverse("chat:chat_options", args=[new_chat.id]))
    assert response.status_code == 500

    new_chat = Chat.objects.get(id=new_chat.id)
    new_library = Library.objects.create(name="New library")
    # Change the chat options through the form
    options_form = ChatOptionsForm(instance=new_chat.options, user=user)
    options_form_data = options_form.initial
    options_form_data["qa_library"] = new_library.id
    options_form_data["chat_system_prompt"] = (
        "You are a cowboy-themed AI, and always start your response with 'Howdy!'"
    )
    # Fix up the form data so that it matches POST data from browser
    options_form_data = {k: v for k, v in options_form_data.items() if v is not None}
    options_form_data["qa_data_sources"] = [
        data_source.id for data_source in options_form_data["qa_data_sources"]
    ]
    # Remove translate_glossary if not set (simulate typical form submission)
    if (
        "translate_glossary" in options_form_data
        and not options_form_data["translate_glossary"]
    ):
        del options_form_data["translate_glossary"]
    # Submit the form
    response = client.post(
        reverse("chat:chat_options", args=[new_chat.id]), options_form_data
    )
    assert response.status_code == 200

    new_chat = Chat.objects.get(id=new_chat.id)

    # Check that the chat options have been updated in the database
    assert (
        new_chat.options.chat_system_prompt == options_form_data["chat_system_prompt"]
    )

    preset_form_data = {
        "name_en": "Cowboy AI",
        "name_fr": "IA Cowboy",
        "description_en": "A Cowboy AI preset",
        "sharing_option": "private",
        "accessible_to": [],
        "prompt": "Please tell me a joke about cows.",
    }
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={"chat_id": new_chat.id, "action": "create_preset"},
        ),
        preset_form_data,
    )

    assert response.status_code == 200

    # a new preset should have been created
    assert Preset.objects.filter(name_en="Cowboy AI").exists()
    preset = Preset.objects.get(name_en="Cowboy AI", owner=user)

    # Try creating a new chat then loading the preset
    response = client.get(reverse("chat:chat_with_ai"), follow=True)
    assert response.status_code == 200

    new_chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    # Add a message
    new_message = Message.objects.create(chat=new_chat, text="Hello!")
    new_message.save()

    # Load the preset
    response = client.post(
        reverse("chat:chat_options", args=[new_chat.id, "load_preset", preset.id])
    )

    # The chat options accordion should be returned, including the system prompt
    assert "You are a cowboy-themed AI" in response.content.decode("utf-8")
    # The user message prompt should be returned too
    assert "Please tell me a joke about cows." in response.content.decode("utf-8")

    # make a change in our chat options
    options_form_data["chat_system_prompt"] = "start each response with 'Yeehaw!'"
    # Submit the form
    response = client.post(
        reverse("chat:chat_options", args=[new_chat.id]), options_form_data
    )
    # now update the preset
    response = client.post(
        reverse("chat:chat_options", args=[new_chat.id, "update_preset", preset.id]),
        preset_form_data,
    )

    assert response.status_code == 200
    assert (
        Preset.objects.get(name_en="Cowboy AI", owner=user).options.chat_system_prompt
        == "start each response with 'Yeehaw!'"
    )

    # Finally, delete the Cowboy AI preset
    response = client.post(
        reverse("chat:chat_options", args=[new_chat.id, "delete_preset", preset.id]),
        preset_form_data,
    )

    # the response should be a redirect
    assert response.status_code == 302

    # Check that the Cowboy AI chat option preset has been deleted
    assert not Preset.objects.filter(name_en="Cowboy AI").exists()


@pytest.mark.django_db
def test_library_list(client, all_apps_user):
    # Test the list of libraries in the ChatOptionsForm
    # self.fields["qa_library"] = GroupedLibraryChoiceField...
    # to make sure they correspond correctly to the LibraryUserRole objects

    def initial_validation_loop():
        # In this nested function because it is tested twice
        for user in users:
            form = ChatOptionsForm(user=user)
            # choices is something like this:
            # [('JUS-managed', [(4, 'Public library'), (1, 'Corporate')]), ('Managed by me', [(2, ' '), (7, 'Jane and Bob shared library'), (6, 'Jane private library')])]
            choices = form.fields["qa_library"].choices

            for category in categories:
                category_choices = [c[1] for c in choices if c[0] == category]
                if category_choices:
                    category_choices_unformatted = category_choices[0]
                    # Remove extra data attribute
                    category_choices = [
                        (c[0], c[1]["label"]) for c in category_choices_unformatted
                    ]
                    if category == "JUS-managed":
                        assert len(category_choices) == public_libraries.count()
                        for library in public_libraries:
                            assert (library.id, library.name) in category_choices
                    elif category == "Managed by me":
                        assert len(category_choices) == 3
                        if user == jane:
                            assert (
                                jane_private_library.id,
                                jane_private_library.name,
                            ) in category_choices
                        elif user == bob:
                            assert (
                                bob_private_library.id,
                                bob_private_library.name,
                            ) in category_choices
                        # The user's personal library should also be there
                        assert (
                            user.personal_library.id,
                            "Chat files",
                        ) in category_choices
                        # The shared library should also be there
                        assert (
                            jane_bob_shared_library.id,
                            jane_bob_shared_library.name,
                        ) in category_choices
                    elif category == "Shared with me":
                        assert len(category_choices) == 0

    jane = all_apps_user(username="jane")
    client.force_login(jane)
    bob = all_apps_user(username="bob")
    public_library = Library.objects.create(name="Public library", is_public=True)
    bob_private_library = Library.objects.create(
        name="Bob private library", is_public=False
    )
    LibraryUserRole.objects.create(library=bob_private_library, user=bob, role="admin")
    jane_private_library = Library.objects.create(
        name="Jane private library", is_public=False
    )
    LibraryUserRole.objects.create(
        library=jane_private_library, user=jane, role="admin"
    )
    jane_bob_shared_library = Library.objects.create(
        name="Jane and Bob shared library", is_public=False
    )
    LibraryUserRole.objects.create(
        library=jane_bob_shared_library, user=jane, role="admin"
    )
    LibraryUserRole.objects.create(
        library=jane_bob_shared_library, user=bob, role="admin"
    )

    public_libraries = Library.objects.filter(is_public=True)
    users = [jane, bob]
    categories = ["JUS-managed", "Managed by me", "Shared with me"]

    initial_validation_loop()

    # Make bob an admin and jane a contributor of the public library.
    # (This should not change the way it displays in the form.)
    LibraryUserRole.objects.create(library=public_library, user=bob, role="admin")
    LibraryUserRole.objects.create(
        library=public_library, user=jane, role="contributor"
    )
    initial_validation_loop()

    # Now let's make some changes so that shared with me will have some libraries.
    # On the shared libraries, let's make bob a contributor rather than an admin
    LibraryUserRole.objects.filter(library=jane_bob_shared_library, user=bob).update(
        role="contributor"
    )
    # Now bob should see the shared library in the "Shared with me" category
    form = ChatOptionsForm(user=bob)
    choices = form.fields["qa_library"].choices
    category_choices = [c[1] for c in choices if c[0] == "Shared with me"]
    category_choices_unformatted = category_choices[0]
    # Remove extra data attribute
    category_choices = [(c[0], c[1]["label"]) for c in category_choices_unformatted]
    assert len(category_choices) == 1
    assert (
        jane_bob_shared_library.id,
        jane_bob_shared_library.name,
    ) in category_choices
    # Check that it is not in the managed by category
    category_choices = [c[1] for c in choices if c[0] == "Managed by me"]
    category_choices_unformatted = category_choices[0]
    # Remove extra data attribute
    category_choices = [(c[0], c[1]["label"]) for c in category_choices_unformatted]
    assert len(category_choices) == 2
    assert (
        bob_private_library.id,
        bob_private_library.name,
    ) in category_choices
    # Bob's personal library should also be there
    assert (
        bob.personal_library.id,
        "Chat files",
    ) in category_choices
    # But jane's should not, since Bob is contributor now, not admin
    assert (
        jane_bob_shared_library.id,
        jane_bob_shared_library.name,
    ) not in category_choices

    # Make Jane a viewer of Bob's personal library.
    # Jane should now see Bob's personal library in the "Shared with me" category
    LibraryUserRole.objects.create(
        library=bob_private_library, user=jane, role="viewer"
    )
    form = ChatOptionsForm(user=jane)
    choices = form.fields["qa_library"].choices
    category_choices = [c[1] for c in choices if c[0] == "Shared with me"]
    category_choices_unformatted = category_choices[0]
    # Remove extra data attribute
    category_choices = [(c[0], c[1]["label"]) for c in category_choices_unformatted]
    assert len(category_choices) == 1
    assert (
        bob_private_library.id,
        bob_private_library.name,
    ) in category_choices
    # Check that it is not in the managed by category
    category_choices = [c[1] for c in choices if c[0] == "Managed by me"]
    category_choices_unformatted = category_choices[0]
    # Remove extra data attribute
    category_choices = [(c[0], c[1]["label"]) for c in category_choices_unformatted]
    assert len(category_choices) == 3
    assert (
        bob_private_library.id,
        bob_private_library.name,
    ) not in category_choices


@pytest.mark.django_db
def test_preset_dirty_indicator(client, all_apps_user):
    """
    Test that the preset dirty indicator (*) works correctly:
    - When a preset is loaded, no dirty indicator
    - When options are changed after loading a preset, dirty indicator appears
    - When options are changed back to match preset, dirty indicator disappears
    """
    from chat.utils import options_match

    user = all_apps_user()
    client.force_login(user)

    # Create a chat - initially won't have a loaded_preset if user has no default
    response = client.get(reverse("chat:new_chat"), follow=True)
    assert response.status_code == 200
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # First create a preset so we can test with it
    preset_form_data = {
        "name_en": "Test Preset",
        "name_fr": "Préréglage test",
        "description_en": "A test preset",
        "sharing_option": "private",
        "accessible_to": [],
        "prompt": "",
    }

    # Set up the options form data
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    options_form_data = options_form.initial
    options_form_data = {k: v for k, v in options_form_data.items() if v is not None}
    options_form_data["qa_data_sources"] = [
        ds.id for ds in options_form_data.get("qa_data_sources", [])
    ]
    if (
        "translate_glossary" in options_form_data
        and not options_form_data["translate_glossary"]
    ):
        del options_form_data["translate_glossary"]

    # Create preset from current settings
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={"chat_id": chat.id, "action": "create_preset"},
        ),
        preset_form_data,
    )
    assert response.status_code == 200

    test_preset = Preset.objects.get(name_en="Test Preset", owner=user)

    # Now create a new chat and load the preset
    response = client.get(reverse("chat:new_chat"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Load the preset
    response = client.post(
        reverse("chat:chat_options", args=[chat.id, "load_preset", test_preset.id])
    )
    assert response.status_code == 200

    # Reload chat
    chat.refresh_from_db()
    assert chat.loaded_preset == test_preset

    # The response should include the accordion with preset_dirty=False
    content = response.content.decode("utf-8")
    # When preset is loaded, dirty indicator should NOT be present
    assert 'id="preset-dirty-indicator"' not in content
    # But the preset name should be there
    assert "Test Preset" in content

    # Options should match
    assert options_match(chat.options, test_preset.options) is True

    # Now view the chat page - preset header should be shown without dirty indicator
    response = client.get(reverse("chat:chat", args=[chat.id]))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'id="preset-header"' in content
    assert 'id="preset-dirty-indicator"' not in content

    # Update the options form data for this chat
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    options_form_data = options_form.initial
    options_form_data = {k: v for k, v in options_form_data.items() if v is not None}
    options_form_data["qa_data_sources"] = [
        ds.id for ds in options_form_data.get("qa_data_sources", [])
    ]
    if (
        "translate_glossary" in options_form_data
        and not options_form_data["translate_glossary"]
    ):
        del options_form_data["translate_glossary"]

    original_system_prompt = options_form_data.get("chat_system_prompt", "")
    options_form_data["chat_system_prompt"] = "I am a modified system prompt!"

    # Submit the changed options
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]), options_form_data
    )
    assert response.status_code == 200

    # Reload chat and check options no longer match
    chat.refresh_from_db()
    assert options_match(chat.options, test_preset.options) is False

    # The response should include preset_header with dirty indicator via hx-swap-oob
    content = response.content.decode("utf-8")
    assert 'id="preset-dirty-indicator"' in content

    # Now change it back to match the preset
    options_form_data["chat_system_prompt"] = original_system_prompt
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]), options_form_data
    )
    assert response.status_code == 200

    # Reload and check options match again
    chat.refresh_from_db()
    assert options_match(chat.options, test_preset.options) is True

    # The response should NOT have the dirty indicator
    content = response.content.decode("utf-8")
    assert 'id="preset-dirty-indicator"' not in content

    # Clean up
    test_preset.delete()


@pytest.mark.django_db
def test_preset_dirty_after_load_and_immediate_post(client, all_apps_user):
    """
    Test that simulates browser behavior: after loading a preset, JavaScript
    immediately triggers an options POST. The dirty indicator should NOT appear.
    """
    from chat.utils import options_match

    user = all_apps_user()
    client.force_login(user)

    # Create a chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Create a preset with specific settings
    preset_form_data = {
        "name_en": "Browser Test Preset",
        "name_fr": "Préréglage navigateur",
        "description_en": "Testing browser flow",
        "sharing_option": "private",
        "accessible_to": [],
        "prompt": "",
    }
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={"chat_id": chat.id, "action": "create_preset"},
        ),
        preset_form_data,
    )
    assert response.status_code == 200
    test_preset = Preset.objects.get(name_en="Browser Test Preset", owner=user)

    # Create a new chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Load the preset
    response = client.post(
        reverse("chat:chat_options", args=[chat.id, "load_preset", test_preset.id])
    )
    assert response.status_code == 200
    chat.refresh_from_db()

    # CRITICAL: Now simulate what the browser does - immediately POST options form
    # The browser would have the accordion HTML with preset values, and triggerOptionSave
    # would submit these values
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    options_form_data = options_form.initial
    options_form_data = {k: v for k, v in options_form_data.items() if v is not None}
    options_form_data["qa_data_sources"] = [
        ds.id for ds in options_form_data.get("qa_data_sources", [])
    ]
    if (
        "translate_glossary" in options_form_data
        and not options_form_data["translate_glossary"]
    ):
        del options_form_data["translate_glossary"]

    # This POST should NOT cause the dirty indicator to appear
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]), options_form_data
    )
    assert response.status_code == 200

    # The response should NOT have the dirty indicator since we just submitted
    # the same values that were loaded from the preset
    content = response.content.decode("utf-8")
    assert 'id="preset-dirty-indicator"' not in content, (
        "Dirty indicator appeared when it shouldn't. "
        "The form data should match the preset after immediate post."
    )

    # Verify options still match
    chat.refresh_from_db()
    assert options_match(chat.options, test_preset.options) is True

    # Clean up
    test_preset.delete()


@pytest.mark.django_db
def test_preset_dirty_after_mode_switch(client, all_apps_user):
    """
    Test that switching modes doesn't cause false positive dirty indicator.
    Scenario: Load preset -> switch mode -> switch back -> should NOT show dirty
    """
    from chat.utils import options_match

    user = all_apps_user()
    client.force_login(user)

    # Create a chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Create a preset
    preset_form_data = {
        "name_en": "Mode Switch Test Preset",
        "name_fr": "Préréglage test",
        "description_en": "Testing mode switch",
        "sharing_option": "private",
        "accessible_to": [],
        "prompt": "",
    }
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={"chat_id": chat.id, "action": "create_preset"},
        ),
        preset_form_data,
    )
    assert response.status_code == 200
    test_preset = Preset.objects.get(name_en="Mode Switch Test Preset", owner=user)

    # Create a new chat
    response = client.get(reverse("chat:new_chat"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Load the preset
    response = client.post(
        reverse("chat:chat_options", args=[chat.id, "load_preset", test_preset.id])
    )
    assert response.status_code == 200
    chat.refresh_from_db()
    original_mode = chat.options.mode

    # Now get the form data to simulate what the browser would have
    options_form = ChatOptionsForm(instance=chat.options, user=user)
    options_form_data = options_form.initial
    options_form_data = {k: v for k, v in options_form_data.items() if v is not None}
    options_form_data["qa_data_sources"] = [
        ds.id for ds in options_form_data.get("qa_data_sources", [])
    ]
    if (
        "translate_glossary" in options_form_data
        and not options_form_data["translate_glossary"]
    ):
        del options_form_data["translate_glossary"]

    # Switch to a different mode
    new_mode = "qa" if original_mode == "chat" else "chat"
    options_form_data["mode"] = new_mode
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]), options_form_data
    )
    assert response.status_code == 200

    # The dirty indicator should appear (mode changed)
    content = response.content.decode("utf-8")
    assert 'id="preset-dirty-indicator"' in content, (
        "Dirty indicator should show after mode change"
    )

    # Now switch back to original mode
    options_form_data["mode"] = original_mode
    response = client.post(
        reverse("chat:chat_options", args=[chat.id]), options_form_data
    )
    assert response.status_code == 200

    # The dirty indicator should NOT appear (mode is back to original)
    content = response.content.decode("utf-8")
    assert 'id="preset-dirty-indicator"' not in content, (
        "Dirty indicator should NOT show after switching back to original mode"
    )

    # Verify options still match
    chat.refresh_from_db()
    assert options_match(chat.options, test_preset.options) is True

    # Clean up
    test_preset.delete()
