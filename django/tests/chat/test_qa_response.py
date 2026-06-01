from types import SimpleNamespace

from django.http import StreamingHttpResponse

import pytest

from chat._views import qa_response


def test_build_query_info_details_includes_prompt_and_search_for_rag():
    reformulation = SimpleNamespace(
        rag_query="project timeline", llm_prompt="Summarize the latest roadmap"
    )

    details = qa_response.build_query_info_details(reformulation, qa_mode="rag")

    assert "Search" in details
    assert "project timeline" in details
    assert "Prompt" in details
    assert "Summarize the latest roadmap" in details


def test_build_query_info_details_skips_search_for_full_documents():
    reformulation = SimpleNamespace(
        rag_query="budget", llm_prompt="Provide the budget overview"
    )

    details = qa_response.build_query_info_details(reformulation, qa_mode="summarize")

    assert "Search" not in details
    assert details.strip().startswith("Prompt")


@pytest.mark.asyncio
async def test_qa_stream_with_progress_full_documents_mode(
    chat_factory, response_message_factory, documents_factory, monkeypatch
):
    chat = chat_factory(qa_mode="summarize")
    response_message = response_message_factory()
    documents = documents_factory("Alpha Report")
    llm = SimpleNamespace()

    def fake_synthesize(*args, **kwargs):
        return qa_response.QueryReformulation(
            should_search=True,
            llm_prompt="Refined question",
            rag_query="keywords",
            history_answer=None,
        )

    def fake_full_doc_answer(*args, **kwargs):
        async def replacer():
            yield "FULL DOC RESPONSE"

        return replacer()

    monkeypatch.setattr(qa_response, "synthesize_retrieval_query", fake_synthesize)
    monkeypatch.setattr(qa_response, "full_doc_answer", fake_full_doc_answer)

    stream = qa_response._qa_stream_with_progress(
        chat,
        response_message,
        documents,
        llm,
    )

    outputs = []
    async for chunk in stream:
        outputs.append(chunk)

    assert outputs[0]["progress_events"][0]["status"] == "in_progress"
    assert outputs[1]["progress_events"][0]["status"] == "complete"
    assert outputs[1]["progress_events"][0]["details"].endswith("Refined question")
    assert outputs[-1] == "FULL DOC RESPONSE"


@pytest.mark.asyncio
async def test_qa_stream_with_progress_rag_combined_mode(
    chat_factory, response_message_factory, documents_factory, monkeypatch
):
    chat = chat_factory(qa_mode="rag", qa_process_mode="combined_docs")
    response_message = response_message_factory()
    documents = documents_factory("Doc A", "Doc B")
    llm = SimpleNamespace()

    def fake_synthesize(*args, **kwargs):
        return qa_response.QueryReformulation(
            should_search=True,
            llm_prompt="Combine answers",
            rag_query="search terms",
            history_answer=None,
        )

    def fake_rag_answer(*args, **kwargs):
        async def replacer():
            yield {"text": "COMBINED RESPONSE"}

        return {
            "response_replacer": replacer(),
            "response_generator": None,
            "source_groups": [["source-node"]],
        }

    monkeypatch.setattr(qa_response, "synthesize_retrieval_query", fake_synthesize)
    monkeypatch.setattr(qa_response, "rag_answer", fake_rag_answer)

    stream = qa_response._qa_stream_with_progress(
        chat,
        response_message,
        documents,
        llm,
    )

    outputs = []
    async for chunk in stream:
        outputs.append(chunk)

    assert outputs[0]["progress_events"][0]["status"] == "in_progress"
    assert outputs[1]["progress_events"][1]["title"].startswith("Searching")
    assert outputs[2]["progress_events"][0]["status"] == "complete"

    source_payload = next(
        item for item in outputs if isinstance(item, dict) and "source_nodes" in item
    )
    assert source_payload["source_nodes"] == [["source-node"]]

    assert outputs[-1] == {"text": "COMBINED RESPONSE"}


def test_rag_answer_granular_groups_sources(monkeypatch, chat_factory):
    chat = chat_factory(qa_mode="rag", qa_process_mode="combined_docs")
    chat.options.qa_granular_toggle = True
    chat.options.qa_granularity = 1024
    chat.options.qa_source_order = "score"
    chat.options.qa_scope = "documents"
    chat.options.qa_library = SimpleNamespace(
        uuid_hex="table", use_hnsw_for_query=lambda: False
    )
    chat.options.qa_prompt_combined = SimpleNamespace(
        message_templates=[
            SimpleNamespace(content="SYS"),
            SimpleNamespace(
                content="{mode_metadata}BODY {pre_instructions} {post_instructions}"
            ),
        ],
        format_messages=lambda **kwargs: [
            SimpleNamespace(content="SYS"),
            SimpleNamespace(content=kwargs.get("mode_metadata", "") + "BODY PRE POST"),
        ],
    )
    chat.options.qa_pre_instructions = "PRE"
    chat.options.qa_post_instructions = "POST"
    chat.options.qa_vector_ratio = 0.5
    chat.options.qa_topk = 2

    response_message = SimpleNamespace(id=77)
    reformulation = SimpleNamespace(rag_query="r-q", llm_prompt="llm-q")
    llm = SimpleNamespace(
        get_retriever=lambda *args, **kwargs: SimpleNamespace(
            retrieve=lambda query: [
                SimpleNamespace(text="alpha", score=0.9, metadata={"doc_id": "d1"}),
                SimpleNamespace(text="beta", score=0.8, metadata={"doc_id": "d1"}),
                SimpleNamespace(text="gamma", score=0.7, metadata={"doc_id": "d2"}),
            ]
        )
    )
    documents = [
        SimpleNamespace(uuid_hex="d1", name="Doc1"),
        SimpleNamespace(uuid_hex="d2", name="Doc2"),
    ]

    monkeypatch.setattr(qa_response, "get_qa_mode_metadata", lambda *args: "meta")
    monkeypatch.setattr(
        qa_response,
        "group_sources_into_docs",
        lambda nodes: [[nodes[0], nodes[1]], [nodes[2]]],
    )
    monkeypatch.setattr(qa_response, "sort_by_max_score", lambda groups: groups)
    monkeypatch.setattr(
        qa_response,
        "num_tokens_from_string",
        lambda text, encoding="cl100k_base": len(text),
    )
    monkeypatch.setattr(
        qa_response, "cache", SimpleNamespace(get=lambda *args, **kwargs: False)
    )

    captured_contexts = []

    def fake_format_source_nodes_as_context(nodes):
        context = "|".join(node.text for node in nodes)
        captured_contexts.append(context)
        return context

    async def fake_stream(**kwargs):
        yield {"text": f"CTX:{kwargs['context_str']}"}

    def fake_qa_chat_stream(**kwargs):
        async def generator():
            yield {"text": f"{kwargs['context_str']}::{kwargs['query_str']}"}

        return generator()

    monkeypatch.setattr(
        qa_response,
        "format_source_nodes_as_context",
        fake_format_source_nodes_as_context,
    )
    monkeypatch.setattr(qa_response, "qa_chat_stream", fake_qa_chat_stream)
    monkeypatch.setattr(
        qa_response, "create_batches", lambda items, batch_size: [list(items)]
    )
    monkeypatch.setattr(
        qa_response,
        "combine_response_replacers",
        lambda responses, titles: responses[0],
    )
    monkeypatch.setattr(
        qa_response,
        "combine_batch_generators",
        lambda generators, total_count=None: generators[0],
    )
    monkeypatch.setattr(
        qa_response, "get_source_titles", lambda nodes: ["Doc1", "Doc2"]
    )

    result = qa_response.rag_answer(
        chat,
        response_message,
        llm,
        documents,
        qa_scope="documents",
        reformulation=reformulation,
    )

    assert len(result["source_groups"]) == 2
    assert captured_contexts == ["alpha|beta", "gamma"]


@pytest.mark.asyncio
async def test_qa_stream_with_progress_rag_per_document_mode(
    chat_factory, response_message_factory, documents_factory, monkeypatch
):
    chat = chat_factory(qa_mode="rag", qa_process_mode="per_doc")
    response_message = response_message_factory(message_id=42)
    documents = documents_factory("Brief 1", "Brief 2")
    llm = SimpleNamespace()

    def fake_synthesize(*args, **kwargs):
        return qa_response.QueryReformulation(
            should_search=True,
            llm_prompt="Compare individually",
            rag_query="compare terms",
            history_answer=None,
        )

    def fake_rag_answer(
        chat_arg, response_message_arg, llm_arg, document_list, qa_scope, reformulation
    ):
        document = document_list[0]

        async def replacer():
            yield {"text": f"{document.name} RESPONSE"}

        return {
            "response_replacer": replacer(),
            "source_groups": [[f"{document.name} SOURCE"]],
        }

    class DummyCache:
        def get(self, key, default=None):
            return False

    def fake_combine_response_replacers(batch_responses, batch_titles):
        async def generator():
            for title, response in zip(batch_titles, batch_responses):
                yield {"text": f"{title} HEADER"}
                async for chunk in response:
                    yield chunk

        return generator()

    def fake_combine_batch_generators(batch_generators, total_count=None):
        async def generator():
            for gen in batch_generators:
                async for chunk in gen:
                    yield chunk

        return generator()

    monkeypatch.setattr(qa_response, "synthesize_retrieval_query", fake_synthesize)
    monkeypatch.setattr(qa_response, "rag_answer", fake_rag_answer)
    monkeypatch.setattr(qa_response, "cache", DummyCache())
    monkeypatch.setattr(
        qa_response, "combine_response_replacers", fake_combine_response_replacers
    )
    monkeypatch.setattr(
        qa_response, "combine_batch_generators", fake_combine_batch_generators
    )

    stream = qa_response._qa_stream_with_progress(
        chat,
        response_message,
        documents,
        llm,
    )

    outputs = []
    async for chunk in stream:
        outputs.append(chunk)

    source_payload = next(
        item for item in outputs if isinstance(item, dict) and "source_nodes" in item
    )
    assert source_payload["source_nodes"] == [["Brief 1 SOURCE"], ["Brief 2 SOURCE"]]

    text_chunks = [
        item["text"]
        for item in outputs
        if isinstance(item, dict)
        and "progress_events" not in item
        and "source_nodes" not in item
    ]

    assert "Brief 1 HEADER" in text_chunks
    assert "Brief 1 RESPONSE" in text_chunks
    assert "Brief 2 HEADER" in text_chunks
    assert "Brief 2 RESPONSE" in text_chunks


def test_qa_response_returns_stream_when_url(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory(qa_mode="rag")
    response_message = response_message_factory(text="https://example.com/doc")

    link_calls = []
    captured_htmx = {}
    replacer_holder = {}

    def fake_is_valid_url(text):
        assert text == response_message.parent.text
        return True

    def fake_link(data_source, text, message):
        link_calls.append((data_source, text, message))

    def fake_stream_library_updates(user_message, adding_url=False):
        assert adding_url is True
        assert user_message is response_message.parent

        async def generator():
            yield "library updates"

        replacer_holder["gen"] = generator()
        return replacer_holder["gen"]

    def fake_htmx_stream(*args, **kwargs):
        captured_htmx["args"] = args
        captured_htmx["kwargs"] = kwargs

        async def generator():
            yield "sentinel"

        return generator()

    monkeypatch.setattr(qa_response, "_is_valid_url", fake_is_valid_url)
    monkeypatch.setattr(qa_response, "_link_url_to_library", fake_link)
    monkeypatch.setattr(
        qa_response, "_stream_library_updates", fake_stream_library_updates
    )
    monkeypatch.setattr(qa_response, "htmx_stream", fake_htmx_stream)
    monkeypatch.setattr(qa_response, "OttoLLM", lambda *args, **kwargs: object())

    response = qa_response.qa_response(chat, response_message, skip_cost=False)

    assert isinstance(response, StreamingHttpResponse)
    assert len(link_calls) == 1
    assert link_calls[0][0] == "test-source"
    assert link_calls[0][1] == "https://example.com/doc"
    assert link_calls[0][2] is response_message.parent

    assert captured_htmx["args"][0] is chat
    assert captured_htmx["args"][1] == response_message.id
    assert "response_replacer" in captured_htmx["kwargs"]
    assert captured_htmx["kwargs"]["response_replacer"] is replacer_holder["gen"]
    assert captured_htmx["kwargs"]["remove_stop"] is True


def test_qa_response_logs_request_shape(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory(qa_mode="rag", qa_process_mode="per_doc")
    chat.options.qa_scope = "documents"
    chat.options.qa_library = SimpleNamespace(access=lambda: None)
    response_message = response_message_factory(text="What is in these docs?")

    documents = [SimpleNamespace(name="Doc A"), SimpleNamespace(name="Doc B")]
    captured = []

    monkeypatch.setattr(qa_response, "_is_valid_url", lambda text: False)
    monkeypatch.setattr(qa_response, "_get_documents_for_scope", lambda *_: documents)
    monkeypatch.setattr(
        qa_response, "generate_cost_warning", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(qa_response, "OttoLLM", lambda *args, **kwargs: object())

    def fake_htmx_stream(*args, **kwargs):
        async def generator():
            yield "sentinel"

        return generator()

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr(qa_response, "htmx_stream", fake_htmx_stream)
    monkeypatch.setattr("chat._views.qa_response.logger.info", fake_info)

    response = qa_response.qa_response(chat, response_message, skip_cost=False)

    assert isinstance(response, StreamingHttpResponse)
    request_events = [
        payload for payload in captured if payload[0] == "qa_request_shape"
    ]
    assert len(request_events) == 1
    _, event_kwargs = request_events[0]
    assert event_kwargs["qa_mode"] == "rag"
    assert event_kwargs["qa_process_mode"] == "per_doc"
    assert event_kwargs["document_count"] == 2
    assert event_kwargs["batch_size"] == qa_response.batch_size


def test_link_url_to_library_creates_document(monkeypatch, document_stub_factory):
    url = "https://example.com"
    data_source = object()
    message = SimpleNamespace()
    manager, document_stub = document_stub_factory()
    monkeypatch.setattr(qa_response, "Document", document_stub)

    qa_response._link_url_to_library(data_source, url, message)

    assert manager.filter_kwargs == [{"data_source": data_source, "url": url}]
    assert len(manager.created) == 1
    created_doc = manager.created[0]
    assert created_doc.kwargs == {"data_source": data_source, "url": url}
    assert created_doc.messages_added == [message]
    assert created_doc.process_priorities == [qa_response.MEDIUM]


def test_link_url_to_library_reuses_existing_document(
    monkeypatch, document_stub_factory, fake_document_instance_class
):
    url = "https://example.com"
    data_source = object()
    message = SimpleNamespace()
    existing_doc = fake_document_instance_class(data_source=data_source, url=url)
    manager, document_stub = document_stub_factory(existing=existing_doc)
    monkeypatch.setattr(qa_response, "Document", document_stub)

    qa_response._link_url_to_library(data_source, url, message)

    assert manager.filter_kwargs == [{"data_source": data_source, "url": url}]
    assert manager.created == []
    assert existing_doc.messages_added == [message]
    assert existing_doc.process_priorities == [qa_response.MEDIUM]


@pytest.mark.asyncio
async def test_stream_library_updates_reports_progress_and_summary(
    monkeypatch, FakeDoc, FakeManager
):
    user_message = SimpleNamespace()
    docs = [
        FakeDoc("fresh.pdf", "SUCCESS", 1),
        FakeDoc("duplicate.pdf", "SUCCESS", 2),
        FakeDoc("error.docx", "ERROR", 1, status_details="Extraction failed"),
        FakeDoc("large.csv", "PAUSED", 1),
        FakeDoc("container.zip", "SUCCESS", 1, is_container=True),
    ]
    manager = FakeManager(docs, in_progress_counts=[2, 0])
    monkeypatch.setattr(qa_response, "Document", SimpleNamespace(objects=manager))

    def immediate_sync_to_async(func):
        async def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(qa_response, "sync_to_async", immediate_sync_to_async)
    monkeypatch.setattr(qa_response.asyncio, "sleep", fake_sleep)

    outputs = []
    async for chunk in qa_response._stream_library_updates(user_message):
        outputs.append(chunk)

    assert outputs[0] == "Adding to the Q&A library (2 file(s) still processing...)"
    completion = outputs[-1]
    assert "already exist in the library" in completion
    assert "Error processing the following document(s):" in completion
    assert 'won\'t appear in "Top excerpts (RAG)" results yet' in completion
    assert "1 new document(s) ready for Q&A." in completion


def test_format_source_nodes_as_context_joins_nodes():
    class FakeInnerNode:
        def __init__(self, text):
            self.text = text

        def get_content(self, metadata_mode):
            return f"{self.text}-ctx"

    nodes = [
        SimpleNamespace(node=FakeInnerNode("Node A")),
        SimpleNamespace(node=FakeInnerNode("Node B")),
    ]

    context = qa_response.format_source_nodes_as_context(nodes)

    assert context == "Node A-ctx\n\n---\n\nNode B-ctx"


@pytest.mark.asyncio
async def test_full_doc_answer_combined_mode_builds_context(monkeypatch, chat_factory):
    chat = chat_factory(qa_mode="summarize", qa_process_mode="combined_docs")
    chat.options.qa_prompt_combined = SimpleNamespace(
        message_templates=[
            SimpleNamespace(content="SYS"),
            SimpleNamespace(
                content="{mode_metadata}{pre_instructions}|CONTENT|{post_instructions}"
            ),
        ],
        format_messages=lambda **kwargs: [
            SimpleNamespace(content="SYS"),
            SimpleNamespace(
                content=kwargs.get("mode_metadata", "") + "PRE|CONTENT|POST"
            ),
        ],
    )
    chat.options.qa_pre_instructions = "PRE"
    chat.options.qa_post_instructions = "POST"

    documents = [
        SimpleNamespace(
            name="Doc One", file_path="file-one.txt", extracted_text="Alpha"
        ),
        SimpleNamespace(
            name="Doc Two", file_path="file-two.txt", extracted_text="Beta"
        ),
    ]
    response_message = SimpleNamespace(id=9)
    llm = object()
    reformulation = SimpleNamespace(llm_prompt="Explain")

    monkeypatch.setattr(qa_response, "get_qa_mode_metadata", lambda *args: "<meta>")

    captured = {}

    def fake_full_doc_chat_stream(**kwargs):
        captured["kwargs"] = kwargs

        async def generator():
            yield "combined-response"

        return generator()

    monkeypatch.setattr(qa_response, "full_doc_chat_stream", fake_full_doc_chat_stream)

    replacer = qa_response.full_doc_answer(
        chat, response_message, llm, documents, reformulation
    )

    outputs = []
    async for chunk in replacer:
        outputs.append(chunk)

    assert outputs == ["combined-response"]
    context_str = captured["kwargs"]["context_str"]
    assert "# Doc One" in context_str
    assert "file-one.txt" in context_str
    assert "Alpha" in context_str and "Beta" in context_str
    user_template = captured["kwargs"]["user_message_content"]
    assert user_template.startswith("<meta>PRE")
    assert user_template.endswith("POST")
    assert captured["kwargs"]["query_str"] == "Explain"


@pytest.mark.asyncio
async def test_full_doc_answer_logs_request_shape(monkeypatch, chat_factory):
    chat = chat_factory(qa_mode="summarize", qa_process_mode="combined_docs")
    chat.options.qa_prompt_combined = SimpleNamespace(
        format_messages=lambda **kwargs: [
            SimpleNamespace(content="SYS"),
            SimpleNamespace(content="PROMPT"),
        ]
    )
    documents = [
        SimpleNamespace(
            name="Doc One", file_path="file-one.txt", extracted_text="Alpha"
        ),
        SimpleNamespace(
            name="Doc Two", file_path="file-two.txt", extracted_text="BetaBeta"
        ),
    ]
    response_message = SimpleNamespace(id=9)
    llm = object()
    reformulation = SimpleNamespace(llm_prompt="Explain")

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    def fake_full_doc_chat_stream(**kwargs):
        async def generator():
            yield "combined-response"

        return generator()

    monkeypatch.setattr(qa_response, "get_qa_mode_metadata", lambda *args: "<meta>")
    monkeypatch.setattr("chat._views.qa_response.logger.info", fake_info)
    monkeypatch.setattr(qa_response, "full_doc_chat_stream", fake_full_doc_chat_stream)

    replacer = qa_response.full_doc_answer(
        chat, response_message, llm, documents, reformulation
    )

    outputs = []
    async for chunk in replacer:
        outputs.append(chunk)

    assert outputs == ["combined-response"]
    shape_events = [
        payload for payload in captured if payload[0] == "qa_full_doc_request_shape"
    ]
    assert len(shape_events) == 1
    _, event_kwargs = shape_events[0]
    assert event_kwargs["document_count"] == 2
    assert event_kwargs["total_extracted_chars"] == len("Alpha") + len("BetaBeta")
    assert event_kwargs["process_mode"] == "combined_docs"


@pytest.mark.asyncio
async def test_full_doc_answer_per_doc_mode_batches(monkeypatch, chat_factory):
    chat = chat_factory(qa_mode="summarize", qa_process_mode="per_doc")
    chat.options.qa_prompt_combined = SimpleNamespace(
        message_templates=[
            SimpleNamespace(content="SYS"),
            SimpleNamespace(
                content="{mode_metadata}{pre_instructions}|CTX|{post_instructions}"
            ),
        ],
        format_messages=lambda **kwargs: [
            SimpleNamespace(content="SYS"),
            SimpleNamespace(content=kwargs.get("mode_metadata", "") + "PRE|CTX|POST"),
        ],
    )
    chat.options.qa_pre_instructions = "PRE"
    chat.options.qa_post_instructions = "POST"

    documents = [
        SimpleNamespace(name="Doc A", file_path="a.txt", extracted_text="AAA"),
        SimpleNamespace(name="Doc B", file_path="b.txt", extracted_text="BBB"),
    ]
    response_message = SimpleNamespace(id=11)
    llm = object()
    reformulation = SimpleNamespace(llm_prompt="Question?")

    monkeypatch.setattr(qa_response, "get_qa_mode_metadata", lambda *args: "<meta>")
    monkeypatch.setattr(
        qa_response, "cache", SimpleNamespace(get=lambda *args, **kwargs: False)
    )

    def fake_create_batches(items, batch_size):
        return [list(items)]

    def fake_full_doc_chat_stream(**kwargs):
        context_str = kwargs["context_str"].splitlines()[0]

        async def generator():
            yield {"text": f"{context_str}|{kwargs['query_str']}"}

        return generator()

    def fake_combine_response_replacers(batch_responses, batch_titles):
        async def generator():
            for title, response in zip(batch_titles, batch_responses):
                yield {"text": f"{title} HEADER"}
                async for chunk in response:
                    yield chunk

        return generator()

    def fake_combine_batch_generators(batch_generators, total_count=None):
        async def generator():
            for gen in batch_generators:
                async for chunk in gen:
                    yield chunk

        return generator()

    monkeypatch.setattr(qa_response, "create_batches", fake_create_batches)
    monkeypatch.setattr(qa_response, "full_doc_chat_stream", fake_full_doc_chat_stream)
    monkeypatch.setattr(
        qa_response, "combine_response_replacers", fake_combine_response_replacers
    )
    monkeypatch.setattr(
        qa_response, "combine_batch_generators", fake_combine_batch_generators
    )

    replacer = qa_response.full_doc_answer(
        chat, response_message, llm, documents, reformulation
    )

    outputs = []
    async for chunk in replacer:
        outputs.append(chunk)

    texts = [item["text"] for item in outputs]
    assert any("Doc A HEADER" in text for text in texts)
    assert any("Doc B HEADER" in text for text in texts)
    assert any("a.txt" in text for text in texts)
    assert any("b.txt" in text for text in texts)


def test_get_chat_history_xml_disabled(chat_factory, response_message_factory):
    chat = chat_factory()
    chat.options.qa_history = False
    response_message = response_message_factory()

    assert qa_response.get_chat_history_xml(chat, response_message) == ""


def test_get_chat_history_xml_includes_messages(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory()
    chat.options.qa_history = True
    response_message = response_message_factory()

    history = [
        SimpleNamespace(role=SimpleNamespace(value="system"), content="system"),
        SimpleNamespace(role=SimpleNamespace(value="user"), content="Hello"),
        SimpleNamespace(role=SimpleNamespace(value="assistant"), content="Hi there"),
    ]

    monkeypatch.setattr(qa_response, "qa_to_history", lambda *_: history)

    xml = qa_response.get_chat_history_xml(chat, response_message)

    assert '<message role="user">Hello</message>' in xml
    assert '<message role="assistant">Hi there</message>' in xml


def test_get_chat_history_xml_empty_when_no_history(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory()
    chat.options.qa_history = True
    response_message = response_message_factory()

    history = [SimpleNamespace(role=SimpleNamespace(value="system"), content="system")]
    monkeypatch.setattr(qa_response, "qa_to_history", lambda *_: history)

    assert qa_response.get_chat_history_xml(chat, response_message) == ""


def test_synthesize_retrieval_query_without_history(
    chat_factory, response_message_factory, monkeypatch
):
    chat = chat_factory()
    chat.options.qa_history = False
    response_message = response_message_factory(text="What is X? ")

    class DummyOtto:
        def __init__(self, *args, **kwargs):
            self.llm = SimpleNamespace(as_structured_llm=lambda *args, **kwargs: None)

        def create_costs(self):
            return None

    monkeypatch.setattr(qa_response, "OttoLLM", DummyOtto)

    result = qa_response.synthesize_retrieval_query(chat, response_message, llm=None)

    assert result.should_search is True
    assert result.llm_prompt.strip() == "What is X?"
    assert result.rag_query.strip() == "What is X?"


def test_synthesize_retrieval_query_with_history(
    monkeypatch, chat_factory, response_message_factory
):
    chat = chat_factory()
    chat.options.qa_history = True
    chat.options.qa_scope = "documents"
    response_message = response_message_factory(text="Follow-up?")

    history = [
        SimpleNamespace(role=SimpleNamespace(value="system"), content="system"),
        SimpleNamespace(role=SimpleNamespace(value="user"), content="Hi"),
    ]

    monkeypatch.setattr(qa_response, "qa_to_history", lambda *_: history)

    captured_prompt_kwargs = {}

    def fake_build_prompt(qa_mode, qa_process_mode, document_names):
        captured_prompt_kwargs["document_names"] = document_names
        return "Prompt {history_str} :: {user_question}"

    monkeypatch.setattr(
        "chat.prompts.build_query_reformulation_prompt", fake_build_prompt
    )

    class FakeStructured:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return SimpleNamespace(
                raw=SimpleNamespace(
                    should_search=True,
                    llm_prompt="Refined",
                    rag_query="Keywords",
                    history_answer=None,
                )
            )

    created_llms = []

    class FakeOtto:
        def __init__(self, *args, **kwargs):
            self.structured = FakeStructured()
            self.llm = SimpleNamespace(as_structured_llm=lambda schema: self.structured)
            self.costs_created = False
            created_llms.append(self)

        def create_costs(self):
            self.costs_created = True

    monkeypatch.setattr(qa_response, "OttoLLM", FakeOtto)

    documents = [SimpleNamespace(name=f"Doc {i}") for i in range(1, 4)]

    result = qa_response.synthesize_retrieval_query(
        chat, response_message, llm=None, documents_qs=documents
    )

    assert captured_prompt_kwargs["document_names"] == ["Doc 1", "Doc 2", "Doc 3"]
    structured = created_llms[0].structured
    assert any("user: Hi" in prompt for prompt in structured.prompts)
    assert result.llm_prompt == "Refined"
    assert result.rag_query == "Keywords"
    assert created_llms[0].costs_created is True


def test_get_documents_for_scope_data_sources_applies_additional_and_excluded_filters(
    monkeypatch,
):
    class FakeQuerySet:
        def __init__(self, values):
            self.values = list(values)

        def __or__(self, other):
            return FakeQuerySet(self.values + other.values)

        def distinct(self):
            deduped = []
            for value in self.values:
                if value not in deduped:
                    deduped.append(value)
            return FakeQuerySet(deduped)

        def exclude(self, **kwargs):
            raw_ids = kwargs.get("id__in", [])
            excluded_ids = set(
                item["id"] if isinstance(item, dict) else item for item in raw_ids
            )
            return FakeQuerySet([v for v in self.values if v not in excluded_ids])

    class FakeManager:
        def filter(self, **kwargs):
            # Simulate documents coming from selected folders
            assert "data_source__in" in kwargs
            return FakeQuerySet([1, 2])

    monkeypatch.setattr(
        qa_response,
        "Document",
        SimpleNamespace(objects=FakeManager()),
    )

    options = SimpleNamespace(
        qa_scope="data_sources",
        qa_data_sources=SimpleNamespace(all=lambda: ["folder-a"]),
        qa_additional_documents=SimpleNamespace(all=lambda: FakeQuerySet([2, 3])),
        qa_excluded_documents=SimpleNamespace(
            values=lambda *args, **kwargs: [{"id": 2}]
        ),
    )

    documents = qa_response._get_documents_for_scope(options)

    assert isinstance(documents, FakeQuerySet)
    assert documents.values == [1, 3]


def test_get_documents_for_scope_data_sources_without_excluded_documents(monkeypatch):
    class FakeQuerySet:
        def __init__(self, values):
            self.values = list(values)

        def __or__(self, other):
            return FakeQuerySet(self.values + other.values)

        def distinct(self):
            deduped = []
            for value in self.values:
                if value not in deduped:
                    deduped.append(value)
            return FakeQuerySet(deduped)

        def exclude(self, **kwargs):
            raw_ids = kwargs.get("id__in", [])
            excluded_ids = set(
                item["id"] if isinstance(item, dict) else item for item in raw_ids
            )
            return FakeQuerySet([v for v in self.values if v not in excluded_ids])

    class FakeManager:
        def filter(self, **kwargs):
            assert "data_source__in" in kwargs
            return FakeQuerySet([10])

    monkeypatch.setattr(
        qa_response,
        "Document",
        SimpleNamespace(objects=FakeManager()),
    )

    options = SimpleNamespace(
        qa_scope="data_sources",
        qa_data_sources=SimpleNamespace(all=lambda: ["folder-a"]),
        qa_additional_documents=SimpleNamespace(all=lambda: FakeQuerySet([11])),
        qa_excluded_documents=SimpleNamespace(values=lambda *args, **kwargs: []),
    )

    documents = qa_response._get_documents_for_scope(options)

    assert documents.values == [10, 11]
