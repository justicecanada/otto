from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse

import pytest

from otto.utils.middleware import HtmxMessageMiddleware


@pytest.fixture
def htmx_middleware(rf):
    return HtmxMessageMiddleware(lambda request: HttpResponse())


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
