from django.urls import reverse

import pytest
from chat_next.models import Chat, Message
from chat_next.tasks import generate_chat_title_task


@pytest.mark.django_db
def test_new_chat_renders_directly_and_pushes_url(client, all_apps_user):
    user = all_apps_user("chat-next-direct-new-chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:new_chat"))

    assert response.status_code == 200
    assert "Location" not in response.headers

    created_chat = Chat.objects.get(user=user)
    content = response.content.decode()

    assert f'const chat_id = "{created_chat.id}";' in content
    assert (
        f"history.replaceState(null, '', '{reverse('chat_next:chat', args=[created_chat.id])}');"
        in content
    )


@pytest.mark.django_db
def test_new_chat_start_tour_pushes_query_string(client, all_apps_user):
    user = all_apps_user("chat-next-direct-new-chat-tour")
    client.force_login(user)

    response = client.get(reverse("chat_next:new_chat"), {"start_tour": "true"})

    assert response.status_code == 200

    created_chat = Chat.objects.get(user=user)
    content = response.content.decode()

    assert (
        f"history.replaceState(null, '', '{reverse('chat_next:chat', args=[created_chat.id])}?start_tour=true');"
        in content
    )


@pytest.mark.django_db
def test_new_chat_preserves_open_skill_query_string(client, all_apps_user):
    user = all_apps_user("chat-next-direct-new-chat-open-skill")
    client.force_login(user)

    response = client.get(reverse("chat_next:new_chat"), {"open_skill": "42"})

    assert response.status_code == 200

    created_chat = Chat.objects.get(user=user)
    content = response.content.decode()

    assert (
        f"history.replaceState(null, '', '{reverse('chat_next:chat', args=[created_chat.id])}?open_skill=42');"
        in content
    )


@pytest.mark.django_db
def test_new_chat_preserves_open_skill_with_start_tour_query_string(
    client, all_apps_user
):
    user = all_apps_user("chat-next-direct-new-chat-open-skill-tour")
    client.force_login(user)

    response = client.get(
        reverse("chat_next:new_chat"),
        {"open_skill": "42", "start_tour": "true"},
    )

    assert response.status_code == 200

    created_chat = Chat.objects.get(user=user)
    content = response.content.decode()
    expected_path = reverse("chat_next:chat", args=[created_chat.id])

    assert f"history.replaceState(null, '', '{expected_path}?" in content
    assert "start_tour=true" in content
    assert "open_skill=42" in content


@pytest.mark.django_db
def test_chat_page_marks_sidebar_placeholder_titles_pending(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("chat-next-lazy-title-page")
    current_chat = Chat.objects.create(user=user, title="Current chat")
    pending_chat = Chat.objects.create(user=user, title="")
    Message.objects.create(
        chat=pending_chat,
        text="A short first prompt that still needs a lazy title.",
        is_bot=False,
    )

    client.force_login(user)
    queued = []

    def fail_inline_title(*args, **kwargs):
        raise AssertionError("chat page should not generate sidebar titles inline")

    def fake_enqueue(chat_id, language=None, timeout=600):
        queued.append((chat_id, language, timeout))
        return True

    monkeypatch.setattr("chat_next.utils.title_chat", fail_inline_title)
    monkeypatch.setattr("chat_next.utils.enqueue_chat_title_generation", fake_enqueue)

    response = client.get(reverse("chat_next:chat", args=[current_chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="chat-list-item-{pending_chat.id}"' in content
    assert 'data-title-pending="true"' in content
    assert "Untitled chat" in content
    assert queued == [(pending_chat.id, "en", 600)]


@pytest.mark.django_db
def test_search_chats_marks_placeholder_titles_pending(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("chat-next-lazy-title-search")
    current_chat = Chat.objects.create(user=user, title="Current chat")
    pending_chat = Chat.objects.create(user=user, title="Untitled chat")
    Message.objects.create(
        chat=pending_chat,
        text="This result mentions a2aj and should queue lazy titling.",
        is_bot=False,
    )

    client.force_login(user)
    queued = []

    def fail_inline_title(*args, **kwargs):
        raise AssertionError("search should not generate sidebar titles inline")

    def fake_enqueue(chat_id, language=None, timeout=600):
        queued.append((chat_id, language, timeout))
        return True

    monkeypatch.setattr("chat_next.utils.title_chat", fail_inline_title)
    monkeypatch.setattr("chat_next.utils.enqueue_chat_title_generation", fake_enqueue)

    response = client.get(
        reverse("chat_next:search_chats"),
        {"search": "a2aj", "current_chat_id": current_chat.id},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="chat-list-item-{pending_chat.id}"' in content
    assert 'data-title-pending="true"' in content
    assert queued == [(pending_chat.id, "en", 600)]


@pytest.mark.django_db
def test_chat_page_search_prerender_marks_placeholder_titles_pending(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("chat-next-lazy-title-prerender")
    current_chat = Chat.objects.create(user=user, title="Current chat")
    pending_chat = Chat.objects.create(user=user, title="")
    Message.objects.create(
        chat=pending_chat,
        text="This search pre-render includes a2aj and should stay lazy.",
        is_bot=False,
    )

    client.force_login(user)
    queued = []

    def fail_inline_title(*args, **kwargs):
        raise AssertionError(
            "search pre-render should not generate sidebar titles inline"
        )

    def fake_enqueue(chat_id, language=None, timeout=600):
        queued.append((chat_id, language, timeout))
        return True

    monkeypatch.setattr("chat_next.utils.title_chat", fail_inline_title)
    monkeypatch.setattr("chat_next.utils.enqueue_chat_title_generation", fake_enqueue)

    response = client.get(
        reverse("chat_next:chat", args=[current_chat.id]),
        {"search": "a2aj"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="chat-list-item-{pending_chat.id}"' in content
    assert 'data-title-pending="true"' in content
    assert queued == [(pending_chat.id, "en", 600)]


@pytest.mark.django_db
def test_refresh_chat_titles_returns_oob_swaps_for_resolved_titles_only(
    client, all_apps_user
):
    user = all_apps_user("chat-next-refresh-titles")
    current_chat = Chat.objects.create(user=user, title="Current chat")
    resolved_chat = Chat.objects.create(user=user, title="Resolved title")
    pending_chat = Chat.objects.create(user=user, title="Untitled chat")

    client.force_login(user)

    response = client.get(
        reverse("chat_next:refresh_chat_titles", args=[current_chat.id]),
        {"chat_ids": [resolved_chat.id, pending_chat.id]},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="chat-list-item-{resolved_chat.id}"' in content
    assert 'hx-swap-oob="outerHTML"' in content
    assert 'hx-on::after-request="deleteChatSection(this)"' in content
    assert 'id="dropdownMenuButton-' in content
    assert (
        f'id="chat-list-item-{resolved_chat.id}"' in content
        and f'id="delete-chat-{resolved_chat.id}"' in content
        and content.index(f'id="chat-list-item-{resolved_chat.id}"')
        < content.index(f'id="delete-chat-{resolved_chat.id}"')
    )
    assert f'id="chat-list-item-{pending_chat.id}"' not in content
    assert "Resolved title" in content


@pytest.mark.django_db
def test_pin_chat_htmx_response_marks_placeholder_titles_pending(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("chat-next-lazy-title-pin")
    current_chat = Chat.objects.create(user=user, title="Current chat")
    pending_chat = Chat.objects.create(user=user, title="")
    Message.objects.create(
        chat=pending_chat,
        text="Pinned chats can still be lazily titled.",
        is_bot=False,
    )

    client.force_login(user)
    queued = []

    def fail_inline_title(*args, **kwargs):
        raise AssertionError("pin response should not generate sidebar titles inline")

    def fake_enqueue(chat_id, language=None, timeout=600):
        queued.append((chat_id, language, timeout))
        return True

    monkeypatch.setattr("chat_next.utils.title_chat", fail_inline_title)
    monkeypatch.setattr("chat_next.utils.enqueue_chat_title_generation", fake_enqueue)

    response = client.post(
        reverse(
            "chat_next:pin_chat",
            kwargs={"current_chat_id": current_chat.id, "chat_id": pending_chat.id},
        ),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="chat-list-item-{pending_chat.id}"' in content
    assert 'data-title-pending="true"' in content
    assert queued == [(pending_chat.id, "en", 600)]


@pytest.mark.django_db
def test_generate_chat_title_task_uses_non_verbatim_fallback(
    all_apps_user, monkeypatch
):
    user = all_apps_user("chat-next-title-task-fallback")
    chat = Chat.objects.create(user=user, title="")
    Message.objects.create(
        chat=chat,
        text="Please draft a project timeline update for the pilot rollout.",
        is_bot=False,
    )

    monkeypatch.setattr(
        "chat_next.utils.title_chat", lambda *args, **kwargs: "Untitled chat"
    )

    generate_chat_title_task(str(chat.id), language="en")

    chat.refresh_from_db()
    assert chat.title == "About project timeline update for the pilot"
    assert chat.title != "Please draft a project timeline update for the pilot rollout."
