from django.urls import reverse

import pytest
from chat_next.models import Chat


@pytest.mark.django_db
def test_chat_message_no_file(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    url = reverse("chat_next:chat_message", args=[chat.id])

    # Send message without file
    response = client.post(url, {"user-message": "hello"}, follow=True)

    assert response.status_code == 200
