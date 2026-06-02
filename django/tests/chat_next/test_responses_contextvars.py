from django.urls import reverse

import pytest
from chat_next.models import Chat, Message

from otto.models import Cost, CostGroup


@pytest.mark.django_db
def test_approval_stream_binds_chat_next_contextvars(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user, title="Context test")
    message = Message.objects.create(
        chat=chat,
        is_bot=True,
        text="",
        details={},
    )

    captured = {}

    def fake_bind_contextvars(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("chat_next.responses.bind_contextvars", fake_bind_contextvars)

    url = reverse("chat_next:approval_stream", kwargs={"message_id": message.id})
    response = client.get(url)

    assert response.status_code == 200
    assert captured["feature"] == "chat_next"
    assert captured["message_next_id"] == message.id
    assert captured["user_id"] == user.id
    assert "cost_group_id" in captured


@pytest.mark.django_db
def test_approval_stream_costs_are_attributed_to_chat_next(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-chat-next-approval",
        name="Chat Next Approval Group",
        monthly_max=999,
        active=True,
    )
    cost_group.users.add(user)

    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session.save()

    chat = Chat.objects.create(user=user, title="Cost attribution test")
    message = Message.objects.create(
        chat=chat,
        is_bot=True,
        text="",
        response_id="resp_prev_1",
        details={
            "pending_local_tool": {
                "name": "rag_search",
                "call_id": "call_1",
                "arguments": "{}",
                "pre_executed_outputs": [],
            }
        },
    )

    # Fake Responses client whose usage object creates a real Cost row.
    class _FakeUsage:
        def create_costs(self, model_id):
            Cost.objects.new(cost_type=f"{model_id}-in", count=1)
            return 0.0

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.last_usage = _FakeUsage()
            self.last_tool_calls = []
            self.last_code_interpreter_sessions = 0
            # This gets used as dedupe key in _create_costs_once_per_response.
            self.previous_response_id = "resp_new_approval_1"
            self.code_interpreter_container_id = None
            self.tools = []
            self.chat = kwargs.get("chat")
            self.user = kwargs.get("user")

    monkeypatch.setattr("chat_next.responses.ResponsesAPIClient", _FakeClient)

    # Keep the endpoint lightweight: invoke cost_callback and stop.
    def _fake_htmx_stream(*args, **kwargs):
        cb = kwargs.get("cost_callback")
        if cb:
            cb()
        yield "event: done\\ndata: complete\\n\\n"

    monkeypatch.setattr("chat_next.responses.htmx_stream", _fake_htmx_stream)

    url = reverse("chat_next:approval_stream", kwargs={"message_id": message.id})
    response = client.get(url)
    assert response.status_code == 200

    # Consume stream to ensure callback side effects are applied.
    list(response.streaming_content)

    attributed_cost = Cost.objects.filter(message_next=message).order_by("-id").first()
    assert attributed_cost is not None
    assert attributed_cost.feature == "chat_next"
    assert attributed_cost.cost_group_id == cost_group.id
