from django.urls import reverse
from django.utils import timezone

import pytest

from chat.models import Chat, Message


@pytest.mark.django_db
def test_chat(client, basic_user, all_apps_user):
    # Create a chat with all apps user
    # path("chat-with-ai/", views.new_chat_with_ai, name="chat_with_ai"),
    user = all_apps_user()
    client.force_login(user)
    response = client.post(reverse("chat:chat_with_ai"))
    # This should redirect to the chat page
    assert response.status_code == 302
    chat = Chat.objects.first()
    assert chat.user == user

    # Post a message to the chat
    # path("id/<str:chat_id>/message/", views.chat_message, name="chat_message"),
    response = client.post(
        reverse("chat:chat_message", kwargs={"chat_id": chat.id}),
        {"user-message": "Hello, world!"},
    )
    assert response.status_code == 200
    # Check that the message was added to the chat
    chat.refresh_from_db()
    assert chat.messages.count() == 2  # 1 user message, 1 empty bot response
    message = chat.messages.filter(is_bot=False).first()
    assert message.text == "Hello, world!"

    # Get the chat via the normal chat route
    # path("id/<str:chat_id>/", views.chat, name="chat"),
    response = client.get(reverse("chat:chat", kwargs={"chat_id": chat.id}))
    assert response.status_code == 200
    assert str(chat.id) in response.request["PATH_INFO"]
    assert "Hello, world!" in response.content.decode()
    # Also check that there is a chat prompt (not readonly)
    assert "chat-prompt" in response.content.decode()
    assert "read only" not in response.content.decode().lower()

    # Now, login as a different user
    user = basic_user()
    # Accept terms
    user.accepted_terms_date = timezone.now()
    user.save()
    client.force_login(user)
    # Get the chat
    response = client.get(reverse("chat:chat", kwargs={"chat_id": chat.id}))
    assert response.status_code == 200
    assert str(chat.id) in response.request["PATH_INFO"]
    assert "Hello, world!" in response.content.decode()
    # Check that there is no chat prompt (readonly)
    assert "chat-prompt" not in response.content.decode()
    assert "read-only" in response.content.decode().lower()
