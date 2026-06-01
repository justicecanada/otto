from django.urls import reverse

import pytest
from chat_next.models import Chat, Message


@pytest.mark.django_db
@pytest.mark.parametrize("viewer_factory_name", ["basic_user", "all_apps_user"])
def test_chat_next_shared_chat_is_readonly_for_other_logged_in_users(
    client, request, all_apps_user, viewer_factory_name
):
    owner = all_apps_user("chat_next_owner")
    client.force_login(owner)

    chat = Chat.objects.create(user=owner)
    Message.objects.create(
        chat=chat,
        text="Hello from the owner!",
        is_bot=False,
    )
    Message.objects.create(
        chat=chat,
        text="Hello from Otto!",
        is_bot=True,
    )

    owner_response = client.get(reverse("chat_next:chat", args=[chat.id]))
    assert owner_response.status_code == 200
    owner_content = owner_response.content.decode()
    assert "Hello from the owner!" in owner_content
    assert "This chat was shared with you and is read-only." not in owner_content
    assert "Enter a message to chat with the AI" in owner_content

    viewer_factory = request.getfixturevalue(viewer_factory_name)
    if viewer_factory_name == "basic_user":
        viewer = viewer_factory("chat_next_viewer", accept_terms=True)
    else:
        viewer = viewer_factory("chat_next_viewer")

    client.force_login(viewer)
    shared_response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert shared_response.status_code == 200
    shared_content = shared_response.content.decode()
    assert "Hello from the owner!" in shared_content
    assert "Hello from Otto!" in shared_content
    assert "This chat was shared with you and is read-only." in shared_content
    assert "Enter a message to chat with the AI" not in shared_content
