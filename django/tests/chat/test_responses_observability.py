from django.http import StreamingHttpResponse

import pytest

from chat import responses


@pytest.mark.django_db
def test_summarize_response_logs_request_shape(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory()
    chat.options.mode = "summarize"
    chat.options.summarize_prompt = "TL;DR"
    chat.options.summarize_model = "gpt-5-mini"
    chat.options.summarize_reasoning_effort = "minimal"
    chat.options.summarize_verbosity = "medium"
    response_message = response_message_factory(text="Plain text to summarize")

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    def fake_summarize_long_text(text, llm, summarize_prompt):
        async def generator():
            yield {"text": f"summary:{text}"}

        return generator()

    def fake_htmx_stream(*args, **kwargs):
        async def generator():
            yield "sentinel"

        return generator()

    monkeypatch.setattr("chat.responses.logger.info", fake_info)
    monkeypatch.setattr(responses, "OttoLLM", lambda *args, **kwargs: object())
    monkeypatch.setattr(responses, "summarize_long_text", fake_summarize_long_text)
    monkeypatch.setattr(responses, "htmx_stream", fake_htmx_stream)

    response = responses.summarize_response(chat, response_message, skip_cost=True)

    assert isinstance(response, StreamingHttpResponse)
    events = [
        payload for payload in captured if payload[0] == "summarize_request_shape"
    ]
    assert len(events) == 1
    _, event_kwargs = events[0]
    assert event_kwargs["input_kind"] == "text"
    assert event_kwargs["file_count"] == 0
    assert event_kwargs["input_text_chars"] == len("Plain text to summarize")
    assert event_kwargs["summarize_model"] == "gpt-5-mini"


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_summarize_response_logs_document_wait_path(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory()
    chat.options.mode = "summarize"
    chat.options.summarize_prompt = "TL;DR"
    chat.options.summarize_model = "gpt-5-mini"
    chat.options.summarize_reasoning_effort = "minimal"
    chat.options.summarize_verbosity = "medium"
    response_message = response_message_factory(text="https://example.com/report")

    captured_logs = []
    captured_htmx = {}

    class FakeDocument:
        def __init__(self):
            self.id = 1
            self.status = "PROCESSING"
            self.status_details = ""
            self.file_path = "report.pdf"
            self.filename = "report.pdf"
            self.name = "report.pdf"
            self.extracted_text = "Extracted body"
            self.created_at = 1
            self.messages = type("Messages", (), {"add": lambda self, msg: None})()

        def process(self, *args, **kwargs):
            return None

        def refresh_from_db(self):
            return None

    fake_document = FakeDocument()

    class FakeQuerySet:
        def __init__(self, docs, first_doc=None):
            self.docs = docs
            self._first_doc = first_doc

        def first(self):
            return self._first_doc

        def exists(self):
            return bool(self.docs)

        def count(self):
            return len(self.docs)

        def order_by(self, *args):
            return self.docs

        def __iter__(self):
            return iter(self.docs)

    class FakeDocumentManager:
        def __init__(self, doc):
            self.doc = doc
            self.get_calls = 0

        def filter(self, **kwargs):
            if "url" in kwargs:
                return FakeQuerySet([], first_doc=None)
            if "messages" in kwargs:
                return FakeQuerySet([self.doc])
            raise AssertionError(f"Unexpected filter kwargs: {kwargs}")

        def create(self, **kwargs):
            return self.doc

        def get(self, **kwargs):
            self.get_calls += 1
            self.doc.status = "SUCCESS"
            return self.doc

    def fake_info(event, **kwargs):
        captured_logs.append((event, kwargs))

    def fake_summarize_long_text(text, llm, summarize_prompt):
        async def generator():
            yield {"text": f"summary:{text}"}

        return generator()

    def fake_combine_response_replacers(batch_responses, batch_titles):
        return batch_responses[0]

    def fake_combine_batch_generators(batch_generators, total_count=None):
        async def generator():
            for batch_generator in batch_generators:
                async for chunk in batch_generator:
                    yield chunk

        return generator()

    def fake_htmx_stream(*args, **kwargs):
        captured_htmx["kwargs"] = kwargs

        async def generator():
            yield "sentinel"

        return generator()

    def immediate_sync_to_async(func):
        async def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    async def fake_sleep(_):
        return None

    monkeypatch.setattr("chat.responses.logger.info", fake_info)
    monkeypatch.setattr(responses, "OttoLLM", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        responses,
        "Document",
        type("DocStub", (), {"objects": FakeDocumentManager(fake_document)}),
    )
    monkeypatch.setattr(
        responses, "update_qa_library_for_chat_uploads", lambda chat: "<div></div>"
    )
    monkeypatch.setattr(
        responses, "generate_cost_warning", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(responses, "summarize_long_text", fake_summarize_long_text)
    monkeypatch.setattr(
        responses, "combine_response_replacers", fake_combine_response_replacers
    )
    monkeypatch.setattr(
        responses, "combine_batch_generators", fake_combine_batch_generators
    )
    monkeypatch.setattr(responses, "sync_to_async", immediate_sync_to_async)
    monkeypatch.setattr(responses.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(responses, "get_celery_task_id", lambda doc_id: "task-1")
    monkeypatch.setattr(
        responses,
        "cache",
        type("CacheStub", (), {"get": lambda self, *args, **kwargs: False})(),
    )
    monkeypatch.setattr(responses, "htmx_stream", fake_htmx_stream)

    response = responses.summarize_response(chat, response_message, skip_cost=True)

    assert isinstance(response, StreamingHttpResponse)
    replacer = captured_htmx["kwargs"]["response_replacer"]
    outputs = []
    async for chunk in replacer:
        outputs.append(chunk)

    wait_started = [
        payload
        for payload in captured_logs
        if payload[0] == "summarize_document_wait_started"
    ]
    wait_finished = [
        payload
        for payload in captured_logs
        if payload[0] == "summarize_document_wait_finished"
    ]

    assert len(wait_started) == 1
    assert wait_started[0][1]["pending_document_count"] == 1
    assert len(wait_finished) == 1
    assert wait_finished[0][1]["ready_document_count"] == 1
    assert wait_finished[0][1]["wait_ms"] >= 0
    assert any("Text extraction complete" in chunk for chunk in outputs)
    assert captured_htmx["kwargs"]["stream_context"]["processing_wait_ms"] >= 0
