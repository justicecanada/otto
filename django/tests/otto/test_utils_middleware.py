from django.conf import settings
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse

import pytest

from otto.utils.auth import ApiAwareAzureMiddleware
from otto.utils.middleware import ExtendSessionMiddleware, HtmxMessageMiddleware


class DummySession(dict):
    def __init__(self):
        super().__init__()
        self.expiry = None

    def set_expiry(self, value):
        self.expiry = value


@pytest.fixture
def htmx_middleware(rf):
    return HtmxMessageMiddleware(lambda request: HttpResponse())


@pytest.fixture
def extend_session_middleware(rf):
    return ExtendSessionMiddleware(lambda request: HttpResponse())


@pytest.fixture
def request_with_messages(rf):
    from django.contrib.messages import INFO

    request = rf.get("/")
    request.session = {}
    messages = FallbackStorage(request)
    messages.add(INFO, "Test message")
    request._messages = messages
    return request


def test_process_response_no_hx_request(htmx_middleware, rf):
    request = rf.get("/")
    response = HttpResponse()
    processed_response = htmx_middleware.process_response(request, response)
    assert processed_response == response


def test_process_response_with_hx_request_no_messages(htmx_middleware, rf):
    request = rf.get("/", HTTP_HX_REQUEST="true")
    response = HttpResponse()
    processed_response = htmx_middleware.process_response(request, response)
    assert processed_response == response


def test_process_response_with_hx_request_with_messages(
    basic_user, htmx_middleware, request_with_messages
):
    user = basic_user(accept_terms=True)
    request_with_messages.headers = dict(request_with_messages.headers)
    request_with_messages.user = user
    request_with_messages.headers["HX-Request"] = "true"
    response = HttpResponse()
    processed_response = htmx_middleware.process_response(
        request_with_messages, response
    )
    assert processed_response.status_code == 200
    assert "Test message" in processed_response.content.decode()


@pytest.mark.parametrize("path", ["/healthz/", "/metrics/"])
def test_extend_session_skips_probe_paths(extend_session_middleware, rf, path):
    request = rf.get(path)
    request.session = DummySession()

    response = HttpResponse()
    processed_response = extend_session_middleware.process_response(request, response)

    assert processed_response == response
    assert "last_activity" not in request.session
    assert request.session.expiry is None


def test_extend_session_updates_non_probe_paths(extend_session_middleware, rf):
    request = rf.get("/")
    request.session = DummySession()

    response = HttpResponse()
    processed_response = extend_session_middleware.process_response(request, response)

    assert processed_response == response
    assert "last_activity" in request.session
    assert request.session.expiry == settings.SESSION_COOKIE_AGE


def test_api_aware_azure_middleware_bypasses_api_paths(rf):
    response = HttpResponse("ok")
    middleware = ApiAwareAzureMiddleware(lambda request: response)

    request = rf.get("/api/v1/reporting/user-activity/summary/")

    assert middleware(request) == response


def test_api_aware_azure_middleware_uses_azure_for_browser_paths(rf, monkeypatch):
    azure_response = HttpResponse("azure")
    middleware = ApiAwareAzureMiddleware(lambda request: HttpResponse("ok"))

    def fake_azure_call(self, request):
        return azure_response

    monkeypatch.setattr(
        "azure_auth.middleware.AzureMiddleware.__call__", fake_azure_call
    )

    request = rf.get("/manage_users/")

    assert middleware(request) == azure_response
