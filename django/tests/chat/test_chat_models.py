from django.db import IntegrityError
from django.utils import timezone

import pytest

from chat.models import Chat, Message


@pytest.mark.django_db
def test_message_feedback_toggle(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(title="test", user=user)
    message = Message.objects.create(chat=chat, feedback=1)

    assert message.feedback == 1

    negative_feedback = message.get_toggled_feedback(-1)
    assert negative_feedback == -1

    negative_feedback = message.get_toggled_feedback(1)
    assert negative_feedback == 0

    try:
        message.get_toggled_feedback(2)
    except ValueError:
        assert True


@pytest.mark.django_db
def test_message_parent_relationship(all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(title="test", user=user)

    message = Message.objects.create(chat=chat, is_bot=False)
    assert message.parent == None

    response_message = Message.objects.create(chat=chat, is_bot=True)
    assert response_message.parent == None

    response_message.parent = message
    response_message.save()
    assert response_message.parent == message

    try:
        user_message = Message.objects.create(chat=chat, is_bot=False)
        message.parent = user_message
        message.save()
        assert message.parent == None
    except IntegrityError:
        assert True
