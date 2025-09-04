from django.urls import reverse

import pytest

from chat.forms import ChatOptionsForm
from chat.models import Chat, ChatOptions, Message, Preset
from librarian.models import Library, LibraryUserRole

pytest_plugins = ("pytest_asyncio",)


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
    preset = Preset.objects.get(name_en="Cowboy AI")

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
        Preset.objects.get(name_en="Cowboy AI").options.chat_system_prompt
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
                            "Chat uploads",
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
        "Chat uploads",
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
