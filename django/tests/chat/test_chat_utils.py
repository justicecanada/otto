# pytest: disable=missing-function-docstring
import io
import types
from unittest.mock import MagicMock, patch

import pytest

from chat import utils


def test_format_reasoning_steps_basic():
    steps = [
        {"index": 0, "text": "# Title\nDetails here.", "complete": True},
        {"index": 1, "text": "**Bold Title**\nMore details.", "complete": True},
        {"index": 2, "text": "Plain title\nExtra info.", "complete": True},
        {"index": 3, "text": "Incomplete step", "complete": False},
    ]
    out = utils.format_reasoning_steps(steps)
    assert out[0]["title"] == "Title"
    assert out[0]["details"] == "Details here."
    assert out[1]["title"] == "Bold Title"
    assert out[1]["details"] == "More details."
    assert out[2]["title"] == "Plain title"
    assert out[2]["details"] == "Extra info."
    # Incomplete step with no newline should be skipped
    assert all("Incomplete" not in s["title"] for s in out)


def test_format_reasoning_steps_french():
    steps = [{"index": 0, "text": "# Titre\nDétails.", "complete": True}]
    out = utils.format_reasoning_steps(steps, language="fr")
    assert out[0]["title"].endswith("anglais)")


# The function closes any odd number of backticks, so for 'abc``def`' (3 backticks), it adds one more: 'abc``def``'
@pytest.mark.parametrize(
    "text,expected",
    [
        ("abc`def", "abc`def`"),
        ("abc```def", "abc```def\n```"),
        ("abc``def`", "abc``def``"),
        ("abc", "abc"),
    ],
)
def test_close_md_code_blocks(text, expected):
    assert utils.close_md_code_blocks(text) == expected


def test_get_model_name_translate_gpt(monkeypatch):
    class Opt:
        mode = "translate"
        translate_model = "gpt-4.1"

    # Patch the function in the correct module where it's imported in utils
    monkeypatch.setattr(
        "chat._llm.models.get_chat_model_choices",
        lambda: [("gpt-4.1", "GPT-4.1 (desc)")],
    )
    assert utils.get_model_name(Opt()) == "GPT-4.1"


def test_get_model_name_translate_azure(monkeypatch):
    class Opt:
        mode = "translate"
        translate_model = "azure"

    assert "Azure Translator" in utils.get_model_name(Opt())


def test_group_sources_into_docs():
    class Node:
        def __init__(self, doc_id, chunk, score=1):
            self.node = types.SimpleNamespace(ref_doc_id=doc_id)
            self.metadata = {"chunk_number": chunk}
            self.score = score

    nodes = [Node("doc1", 1), Node("doc1", 2), Node("doc2", 1)]
    groups = utils.group_sources_into_docs(nodes)
    assert len(groups) == 2
    assert all(isinstance(g, list) for g in groups)


def test_sort_by_max_score():
    class Node:
        def __init__(self, score):
            self.score = score

    groups = [[Node(1), Node(2)], [Node(5)], [Node(3), Node(2)]]
    sorted_groups = utils.sort_by_max_score(groups)
    assert [max(n.score for n in g) for g in sorted_groups] == [5, 3, 2]


@pytest.mark.parametrize(
    "content_type,filename,expected",
    [
        ("image/png", "file.png", True),
        (None, "file.jpg", True),
        (None, "file.txt", False),
        ("application/pdf", "file.pdf", False),
    ],
)
def test_is_image_file(content_type, filename, expected):
    chat_file = MagicMock()
    chat_file.filename = filename
    chat_file.saved_file = MagicMock()
    chat_file.saved_file.content_type = content_type
    assert utils.is_image_file(chat_file) == expected


@pytest.mark.parametrize(
    "content_type,filename,expected",
    [
        ("application/pdf", "file.pdf", True),
        (None, "file.pdf", True),
        (None, "file.txt", False),
        ("image/png", "file.png", False),
    ],
)
def test_is_pdf_file(content_type, filename, expected):
    chat_file = MagicMock()
    chat_file.filename = filename
    chat_file.saved_file = MagicMock()
    chat_file.saved_file.content_type = content_type
    assert utils.is_pdf_file(chat_file) == expected


@pytest.mark.skip(
    reason="Vision mode in Chat has been disabled - files auto-switch to Q&A mode"
)
def test_get_vision_blocks_for_message_image_and_pdf(tmp_path):
    # Patch ImageBlock/DocumentBlock to simple mocks
    with (
        patch("chat.utils.ImageBlock") as MockImageBlock,
        patch("chat.utils.DocumentBlock") as MockDocumentBlock,
    ):
        # Setup mocks - accept all kwargs since we pass detail="high"
        MockImageBlock.side_effect = lambda **kwargs: (
            "img",
            kwargs.get("image_mimetype"),
        )
        MockDocumentBlock.side_effect = lambda path, document_mimetype, title: (
            "doc",
            path,
            document_mimetype,
            title,
        )
        # Create fake files
        img_file = MagicMock()
        img_file.filename = "pic.png"
        img_file.saved_file = MagicMock()
        img_file.saved_file.content_type = "image/png"
        img_file.saved_file.file.open.return_value.__enter__.return_value.read.return_value = b"imgdata"
        pdf_file = MagicMock()
        pdf_file.filename = "doc.pdf"
        pdf_file.saved_file = MagicMock()
        pdf_file.saved_file.content_type = "application/pdf"
        pdf_file.saved_file.file.path = str(tmp_path / "doc.pdf")
        # Message mock
        message = MagicMock()
        message.files.exists.return_value = True
        message.files.all.return_value = [img_file, pdf_file]
        blocks = utils.get_vision_blocks_for_message(message)
        assert ("img", "image/png") in blocks
        assert any(b[0] == "doc" for b in blocks)


def test_qa_to_history_basic():
    # Patch chat_to_history to check call
    with patch("chat.utils.chat_to_history") as chat_to_history:
        chat = MagicMock()
        resp_msg = MagicMock()
        utils.qa_to_history(chat, resp_msg)
        chat_to_history.assert_called()


def test_swap_glossary_columns():
    import csv

    # Prepare a CSV file-like object
    content = "a,b,c\n1,2,3\n4,5,6\n"
    file = io.BytesIO(content.encode("utf-8"))
    swapped = utils.swap_glossary_columns(file)
    swapped.seek(0)
    reader = csv.reader(io.TextIOWrapper(swapped, encoding="utf-8"))
    rows = list(reader)
    assert rows[0][:2] == ["b", "a"]
    assert rows[1][:2] == ["2", "1"]


# ---------------------------------------------------------------------------
# get_request_route_label
# ---------------------------------------------------------------------------


def test_get_request_route_label_none_request():
    assert utils.get_request_route_label(None) is None


def test_get_request_route_label_uses_resolver_route():
    request = MagicMock()
    request.resolver_match.route = "chat/message/<int:message_id>/response/"
    request.resolver_match.view_name = "chat:response"
    assert (
        utils.get_request_route_label(request)
        == "chat/message/<int:message_id>/response/"
    )


def test_get_request_route_label_falls_back_to_view_name():
    request = MagicMock()
    request.resolver_match.route = ""
    request.resolver_match.view_name = "chat:response"
    request.resolver_match.url_name = "response"
    assert utils.get_request_route_label(request) == "chat:response"


def test_get_request_route_label_normalizes_path_ids():
    request = MagicMock()
    request.resolver_match = None
    request.path = "/chat/message/42/response/"
    assert utils.get_request_route_label(request) == "/chat/message/<id>/response/"


def test_get_request_route_label_normalizes_path_uuids():
    request = MagicMock()
    request.resolver_match = None
    request.path = "/chat/message/550e8400-e29b-41d4-a716-446655440000/response/"
    label = utils.get_request_route_label(request)
    assert "<uuid>" in label
    assert "550e8400" not in label


def test_get_request_route_label_empty_path_returns_none():
    request = MagicMock()
    request.resolver_match = None
    request.path = ""
    assert utils.get_request_route_label(request) is None


# ---------------------------------------------------------------------------
# classify_legacy_sse_stream
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "summary,expected_phase",
    [
        # generation_stopped wins over everything
        (
            {
                "generation_stopped": True,
                "stream_duration_ms": 1000,
                "processing_wait_ms": 900,
                "response_char_count": 0,
            },
            "stopped_early",
        ),
        # wait dominates (>= 50% of duration and >= 5000 ms)
        (
            {
                "generation_stopped": False,
                "stream_duration_ms": 12000,
                "processing_wait_ms": 8000,
                "response_char_count": 100,
            },
            "wait_heavy",
        ),
        # wait present but short relative to duration → wait_then_generate
        (
            {
                "generation_stopped": False,
                "stream_duration_ms": 30000,
                "processing_wait_ms": 2000,
                "response_char_count": 100,
            },
            "wait_then_generate",
        ),
        # no wait, large output
        (
            {
                "generation_stopped": False,
                "stream_duration_ms": 5000,
                "processing_wait_ms": 0,
                "response_char_count": 60000,
            },
            "large_generation",
        ),
        # no wait, small output
        (
            {
                "generation_stopped": False,
                "stream_duration_ms": 2000,
                "processing_wait_ms": 0,
                "response_char_count": 500,
            },
            "generation_heavy",
        ),
    ],
)
def test_classify_legacy_sse_stream_phases(summary, expected_phase):
    result = utils.classify_legacy_sse_stream(summary)
    assert result["request_phase"] == expected_phase


def test_classify_legacy_sse_stream_passes_through_fields():
    summary = {
        "stream_duration_ms": 3000,
        "processing_wait_ms": 0,
        "response_char_count": 200,
        "generation_stopped": False,
        "route": "chat/message/<id>/response/",
        "workload_kind": "summarize",
        "document_count": 3,
        "success_document_count": 2,
        "error_document_count": 1,
        "qa_process_mode": "per_doc",
    }
    result = utils.classify_legacy_sse_stream(summary)
    assert result["route"] == "chat/message/<id>/response/"
    assert result["workload_kind"] == "summarize"
    assert result["document_count"] == 3
    assert result["qa_process_mode"] == "per_doc"


# ---------------------------------------------------------------------------
# build_stream_context
# ---------------------------------------------------------------------------


def test_build_stream_context_with_request():
    request = MagicMock()
    request.resolver_match.route = "chat/message/<int:message_id>/response/"
    ctx = utils.build_stream_context(request, "summarize", document_count=5)
    assert ctx["workload_kind"] == "summarize"
    assert ctx["route"] == "chat/message/<int:message_id>/response/"
    assert ctx["document_count"] == 5


def test_build_stream_context_filters_none_values():
    request = MagicMock()
    request.resolver_match = None
    request.path = ""
    ctx = utils.build_stream_context(request, "chat", optional_field=None)
    assert "optional_field" not in ctx
    assert "route" not in ctx  # path was empty → get_request_route_label returns None


def test_build_stream_context_none_request():
    ctx = utils.build_stream_context(None, "chat")
    assert ctx == {"workload_kind": "chat"}
