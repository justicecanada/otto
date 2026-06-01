from chat._llm.utils import _extract_status_and_retry_after


def test_extract_status_and_retry_after_from_response_headers(DummyResp, DummyExc):
    # response.status_code + Retry-After header
    resp = DummyResp(status_code=429, headers={"Retry-After": "12"})
    exc = DummyExc(response=resp)

    status, retry_after = _extract_status_and_retry_after(exc)

    assert status == 429
    assert retry_after == 12.0


def test_extract_status_and_retry_after_from_exception_headers(DummyExc):
    # headers directly on the exception object, lowercase key
    exc = DummyExc(status_code=500, headers={"retry-after": "3"})

    status, retry_after = _extract_status_and_retry_after(exc)

    assert status == 500
    assert retry_after == 3.0


def test_extract_status_and_retry_after_from_message_pattern(DummyExc):
    # No headers; fallback to parsing message text
    exc = DummyExc(message="rate limit reached, please retry after 56 seconds.")

    status, retry_after = _extract_status_and_retry_after(exc)

    assert status is None
    assert retry_after == 56.0


def test_extract_status_and_retry_after_no_info(DummyExc):
    # No status_code, no headers, no parsable message
    exc = DummyExc(message="some generic error")

    status, retry_after = _extract_status_and_retry_after(exc)

    assert status is None
    assert retry_after is None
