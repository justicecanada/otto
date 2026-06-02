from unittest.mock import AsyncMock

import pytest

from otto import asgi


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/healthz", "/healthz/"])
async def test_http_application_short_circuits_health_checks(monkeypatch, path):
    django_app = AsyncMock()
    monkeypatch.setattr(asgi, "django_asgi_app", django_app)
    messages = []

    async def send(message):
        messages.append(message)

    await asgi.http_application(
        {
            "type": "http",
            "path": path,
            "method": "GET",
            "headers": [],
        },
        _empty_receive,
        send,
    )

    django_app.assert_not_awaited()
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 200
    headers = dict(messages[0]["headers"])
    assert headers[b"content-type"] == b"application/json"
    assert headers[b"content-length"] == str(len(asgi.HEALTH_CHECK_BODY)).encode()
    assert headers[b"cache-control"] == b"no-store"
    assert messages[1] == {
        "type": "http.response.body",
        "body": asgi.HEALTH_CHECK_BODY,
    }


@pytest.mark.asyncio
async def test_http_application_returns_empty_body_for_head_health_check(monkeypatch):
    django_app = AsyncMock()
    monkeypatch.setattr(asgi, "django_asgi_app", django_app)
    messages = []

    async def send(message):
        messages.append(message)

    await asgi.http_application(
        {
            "type": "http",
            "path": "/healthz/",
            "method": "HEAD",
            "headers": [],
        },
        _empty_receive,
        send,
    )

    django_app.assert_not_awaited()
    assert messages[0]["status"] == 200
    assert messages[1] == {
        "type": "http.response.body",
        "body": b"",
    }


@pytest.mark.asyncio
async def test_http_application_delegates_non_health_requests(monkeypatch):
    django_app = AsyncMock()
    monkeypatch.setattr(asgi, "django_asgi_app", django_app)
    messages = []
    scope = {
        "type": "http",
        "path": "/",
        "method": "GET",
        "headers": [],
    }

    async def send(message):
        messages.append(message)

    await asgi.http_application(scope, _empty_receive, send)

    django_app.assert_awaited_once_with(scope, _empty_receive, send)
    assert messages == []


def test_health_check_route_still_returns_expected_payload(client):
    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
