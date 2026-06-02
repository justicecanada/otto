import pytest
from chat_next.models import Chat, Message
from chat_next.responses import _create_costs_once_per_response


class _DummyUsage:
    def __init__(self):
        self.calls = 0

    def create_costs(self, model_id):
        self.calls += 1
        return 0.25


class _DummyClient:
    def __init__(
        self,
        response_id,
        usage,
        tool_calls=None,
        sessions=0,
    ):
        self.previous_response_id = response_id
        self.last_usage = usage
        self.last_tool_calls = tool_calls or []
        self.last_code_interpreter_sessions = sessions


@pytest.mark.django_db
def test_create_costs_once_per_response_is_idempotent(monkeypatch, all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Cost test")
    message = Message.objects.create(chat=chat, text="", is_bot=True)

    usage = _DummyUsage()
    client = _DummyClient(
        response_id="resp_abc123",
        usage=usage,
        tool_calls=[{"tool_type": "web_search_preview", "query": "test"}],
        sessions=1,
    )

    tool_cost_calls = {"count": 0}

    def fake_create_tool_costs(*args, **kwargs):
        tool_cost_calls["count"] += 1
        return 0.1

    monkeypatch.setattr("chat_next._llm.create_tool_costs", fake_create_tool_costs)

    _create_costs_once_per_response(
        message.id,
        client,
        "gpt-5.1",
        reuse_container=False,
    )
    _create_costs_once_per_response(
        message.id,
        client,
        "gpt-5.1",
        reuse_container=False,
    )

    message.refresh_from_db()

    assert usage.calls == 1
    assert tool_cost_calls["count"] == 1
    assert "resp_abc123" in (message.details or {}).get("billed_response_ids", [])


@pytest.mark.django_db
def test_create_costs_once_per_response_without_response_id_not_deduped(
    monkeypatch, all_apps_user
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Cost test")
    message = Message.objects.create(chat=chat, text="", is_bot=True)

    usage = _DummyUsage()
    client = _DummyClient(
        response_id=None,
        usage=usage,
        tool_calls=[{"tool_type": "web_search_preview", "query": "test"}],
        sessions=1,
    )

    tool_cost_calls = {"count": 0}

    def fake_create_tool_costs(*args, **kwargs):
        tool_cost_calls["count"] += 1
        return 0.1

    monkeypatch.setattr("chat_next._llm.create_tool_costs", fake_create_tool_costs)

    _create_costs_once_per_response(
        message.id,
        client,
        "gpt-5.1",
        reuse_container=False,
    )
    _create_costs_once_per_response(
        message.id,
        client,
        "gpt-5.1",
        reuse_container=False,
    )

    assert usage.calls == 2
    assert tool_cost_calls["count"] == 2
