"""
Test the core Otto views (index, login, etc.)
"""

import json

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

import pytest
from bs4 import BeautifulSoup

from chat.models import Chat, Message
from otto.forms import FeedbackForm
from otto.models import Feedback, Notification


@pytest.mark.django_db
def test_homepage(client, basic_user):
    user = basic_user()
    client.force_login(user)
    response = client.get(reverse("index"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    text = soup.get_text()
    assert "Otto" in text


@pytest.mark.django_db
def test_notifications(client, basic_user):
    """
    1. Create a Notification manually
    2. Check that a li.notification is included from the notifications route
    3. Test the delete notification route
    4. Check that it was deleted in the database
    5. Check that it was deleted via the notifications route
    """
    user = basic_user(accept_terms=True)
    client.force_login(user)
    Notification.objects.create(
        user=user,
        heading="Access controls",
        text=f"You are not authorized to access...",
        category="error",
    )
    notification = user.notifications.first()
    assert notification is not None
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    # Check that there is exactly one notification
    assert len(soup.find_all("li", class_="notification")) == 1
    response = client.delete(
        reverse("notification", kwargs={"notification_id": notification.id})
    )
    assert response.status_code == 200  # HTMX delete routes return a fragment to swap
    assert user.notifications.count() == 0
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    assert soup.find("li", class_="notification") is None


def test_valid_feedback_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    data = {
        "user": user,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test Message",
        "app": "Otto",
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback"),
        data=data,
    )
    assert response.status_code == 200


def test_valid_feedback_form_from_message(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    Message.objects.create(chat=chat)
    message = Message.objects.create(chat=chat, is_bot=True)
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    data = {
        "user": user,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test Message",
        "app": "chat",
        "chat_message_id": message.id,
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback", kwargs={"message_id": message.id}),
        data=data,
    )
    assert response.status_code == 200


def test_initialize_feedback_for_chat_mode(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    message = Message.objects.create(chat=chat, mode="translate", is_bot=False)

    data = {
        "user": user.id,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test feedback message for translation",
        "app": "translate",
        "chat_message_id": message.id,
        "modified_by": user.id,
        "otto_version": "v0",
    }

    client.post(
        reverse("user_feedback", kwargs={"message_id": message.id}),
        data=data,
    )

    form = FeedbackForm(user=user, message_id=message.id)
    form.initialize_chat_feedback(message.id)

    assert form.fields["app"].initial == "translate"


@pytest.mark.django_db
def test_manage_banner(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Test preview with just an English message
    response = client.post(
        reverse("manage_banner"),
        data={"message_en": "Hello there", "preview": "true", "category": "info"},
    )
    assert response.status_code == 200
    assert (
        '<div id="message-from-admins" class="info">Hello there</div>'
        in response.content.decode()
    )

    # Test banner creation
    response = client.post(
        reverse("manage_banner"),
        data={"message_en": "Hello", "message_fr": "Bonjour", "category": "danger"},
    )
    assert response.status_code == 200
    assert (
        '<div id="message-from-admins" class="danger">Hello</div>'
        in response.content.decode()
    )
