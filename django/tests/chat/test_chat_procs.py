import asyncio

from django.utils import timezone

import pytest
from asgiref.sync import sync_to_async

from chat.llm import OttoLLM
from chat.models import Chat, Message
from chat.utils import fix_source_links, get_chat_history_sections, htmx_stream

pytest_plugins = ("pytest_asyncio",)


def extract_data_md_content(wrapped_response):
    """
    Extract the content of the data-md attribute from the wrapped response.
    """
    import re

    match = re.search(r'data-md="([^"]*)"', wrapped_response)
    if match:
        return match.group(1)
    return wrapped_response


def test_fix_source_links():
    # test internal link where we need to clean the link text because of a resulting double slash when merging
    # (e.g. https://travel.gc.ca/travelling/advisories instead of https://travel.gc.ca//travelling/advisories)
    source_url = "https://travel.gc.ca/"
    internal_link = "[Travel Advice and Advisories](/travelling/advisories)"
    text_with_fixed_links = fix_source_links(internal_link, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == "[Travel Advice and Advisories](https://travel.gc.ca/travelling/advisories)"
    )

    # test internal link that needs to be merged at a specific point, in our case '/wiki/'
    # (e.g https://en.wikipedia.org/wiki/Grapheme instead of https://en.wikipedia.org/wiki/Glyph/wiki/Grapheme)
    source_url = "https://en.wikipedia.org/wiki/Glyph"
    internal_link = '[grapheme](/wiki/Grapheme "Grapheme")'
    text_with_fixed_links = fix_source_links(internal_link, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == '[grapheme](https://en.wikipedia.org/wiki/Grapheme "Grapheme")'
    )

    # Test internal links without source URL
    source_url = ""
    internal_link = '[grapheme](/wiki/Grapheme "Grapheme")'
    text_with_fixed_links = fix_source_links(internal_link, source_url)
    assert extract_data_md_content(text_with_fixed_links) == "grapheme"

    # Test anchor links
    source_url = "https://en.wikipedia.org/wiki/Glyph"
    anchor_link = "[[2]](#cite_note-Whistler_et_al-3)"
    text_with_fixed_links = fix_source_links(anchor_link, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == "[[2]](https://en.wikipedia.org/wiki/Glyph#cite_note-Whistler_et_al-3)"
    )

    # Test anchor links without source URL
    source_url = ""
    anchor_link = "[[2]](#cite_note-Whistler_et_al-3)"
    text_with_fixed_links = fix_source_links(anchor_link, source_url)
    assert extract_data_md_content(text_with_fixed_links) == "[2]"

    # Test external links
    source_url = "https://en.wikipedia.org/wiki/Glyph"
    external_link = '[external](https://example.com "Example")'
    text_with_fixed_links = fix_source_links(external_link, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == '[external](https://example.com "Example")'
    )

    # Test mixed links
    source_url = "https://en.wikipedia.org/wiki/Glyph"
    mixed_links = '[grapheme](/wiki/Grapheme "Grapheme") and [external](https://example.com "Example")'
    text_with_fixed_links = fix_source_links(mixed_links, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == '[grapheme](https://en.wikipedia.org/wiki/Grapheme "Grapheme") and [external](https://example.com "Example")'
    )

    # Test internal link with HTML file
    source_url = "https://example.com/docs"
    internal_html_link = '[documentation](this.html "Documentation")'
    text_with_fixed_links = fix_source_links(internal_html_link, source_url)
    assert (
        extract_data_md_content(text_with_fixed_links)
        == '[documentation](https://example.com/docs/this.html "Documentation")'
    )


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_htmx_stream_response_stream(all_apps_user):
    llm = OttoLLM()

    async def stream_generator():
        for char in "Hi!":
            yield char
            await asyncio.sleep(0.1)

    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_1")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_generator=stream_generator(),
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    async for yielded_output in response_stream:
        # Skip the final "done" event
        if yielded_output.startswith("event: done"):
            continue
        # Output should start with "data: " for Server-Sent Events
        assert yielded_output.startswith("data: ")
        # Output should end with a double newline
        assert yielded_output.endswith("\n\n")
        final_output = yielded_output
    assert "Hi!" in final_output
    # There should be an element in the response to replace the SSE div
    assert "<div hx-swap-oob" in final_output
    # Message should have been updated
    assert await sync_to_async(chat.messages.count)() == 1


@pytest.mark.asyncio
@pytest.mark.django_db()
async def test_htmx_stream_response_str(all_apps_user):
    llm = OttoLLM()
    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_2")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_str="Hi!",
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    async for yielded_output in response_stream:
        # Skip the final "done" event
        if yielded_output.startswith("event: done"):
            continue
        # Output should start with "data: " for Server-Sent Events
        assert yielded_output.startswith("data: ")
        # Output should end with a double newline
        assert yielded_output.endswith("\n\n")
        final_output = yielded_output
    assert "Hi!" in final_output
    # There should be an element in the response to replace the SSE div
    assert "<div hx-swap-oob" in final_output
    # Message should have been updated
    assert await sync_to_async(chat.messages.count)() == 1


@pytest.mark.asyncio
@pytest.mark.django_db()
async def test_htmx_stream_response_generator(all_apps_user):
    llm = OttoLLM()

    class FakeFile:
        def __init__(self, name, text):
            self.name = name
            self.text = text

    async def fake_summarize(text):
        """Fake async generator that yields a mock summary."""
        yield f"Summary of: {text[:20]}..."

    async def stream_generator():
        """
        Async generator that yields file names and summaries.
        Uses fake summaries instead of actual LLM calls to avoid network requests.
        """
        files = [
            FakeFile("file1.txt", "Summary of first file"),
            FakeFile("file2.txt", "Summary of second file"),
        ]
        for i, file in enumerate(files):
            yield f"**{file.name}**\n"
            await asyncio.sleep(0.01)  # Simulate async processing
            summary = file.text  # Use fake summary directly
            if i < len(files) - 1:
                yield f"{summary}\n\n-----\n"
            else:
                yield f"{summary}\n"

    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_3")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_generator=stream_generator(),
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    async for yielded_output in response_stream:
        # Skip the final "done" event
        if yielded_output.startswith("event: done"):
            continue
        # Output should start with "data: " for Server-Sent Events
        assert yielded_output.startswith("data: ")
        # Output should end with a double newline
        assert yielded_output.endswith("\n\n")
        final_output = yielded_output
    assert "file1.txt" in final_output
    assert "file2.txt" in final_output
    # There should be an element in the response to replace the SSE div
    assert "<div hx-swap-oob" in final_output
    # Message should have been updated
    assert await sync_to_async(chat.messages.count)() == 1


@pytest.mark.asyncio
@pytest.mark.django_db()
async def test_htmx_stream_response_replacer(basic_user):
    llm = OttoLLM()

    async def stream_generator():
        yield "first thing"
        yield "second thing"

    # We first need an empty chat and a message
    user = await sync_to_async(basic_user)("test_user_4")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_replacer=stream_generator(),
        wrap_markdown=False,
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    first = True
    async for yielded_output in response_stream:
        # Skip the final "done" event
        if yielded_output.startswith("event: done"):
            continue
        if first:
            assert "first thing" in yielded_output
            first = False
        else:
            assert "second thing" in yielded_output
        # Output should start with "data: " for Server-Sent Events
        assert yielded_output.startswith("data: ")
        # Output should end with a double newline
        assert yielded_output.endswith("\n\n")
        final_output = yielded_output
    assert "first thing" not in final_output
    assert "second thing" in final_output
    # There should be an element in the response to replace the SSE div
    assert "<div hx-swap-oob" in final_output
    # A new message should NOT have been created
    assert await sync_to_async(chat.messages.count)() == 1


@pytest.mark.asyncio
@pytest.mark.django_db()
async def test_htmx_stream_logs_stream_summary(all_apps_user, monkeypatch):
    llm = OttoLLM()

    async def stream_generator():
        yield "first thing"
        yield "second thing"

    user = await sync_to_async(all_apps_user)("test_user_stream_log")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr("chat.utils.logger.info", fake_info)

    response_stream = htmx_stream(
        chat,
        message.id,
        response_replacer=stream_generator(),
        wrap_markdown=False,
        llm=llm,
    )

    async for _ in response_stream:
        pass

    stream_events = [
        payload for payload in captured if payload[0] == "legacy_sse_stream_completed"
    ]
    assert len(stream_events) == 1
    _, event_kwargs = stream_events[0]
    assert event_kwargs["response_char_count"] == len("second thing")
    assert event_kwargs["generation_stopped"] is False
    assert event_kwargs["query_info_count"] == 0
    assert event_kwargs["reasoning_step_count"] == 0
    assert event_kwargs["stream_duration_ms"] >= 0


@pytest.mark.asyncio
@pytest.mark.django_db()
async def test_htmx_stream_logs_request_classification(all_apps_user, monkeypatch):
    llm = OttoLLM()

    async def stream_generator():
        yield "classification payload"

    user = await sync_to_async(all_apps_user)("test_user_stream_classification")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr("chat.utils.logger.info", fake_info)

    response_stream = htmx_stream(
        chat,
        message.id,
        response_replacer=stream_generator(),
        wrap_markdown=False,
        llm=llm,
        stream_context={
            "route": "chat:response",
            "workload_kind": "summarize",
            "processing_wait_ms": 9000,
            "document_count": 1,
        },
    )

    async for _ in response_stream:
        pass

    events = [
        payload for payload in captured if payload[0] == "legacy_sse_request_classified"
    ]
    assert len(events) == 1
    _, event_kwargs = events[0]
    assert event_kwargs["route"] == "chat:response"
    assert event_kwargs["workload_kind"] == "summarize"
    assert event_kwargs["request_phase"] == "wait_heavy"
    assert event_kwargs["processing_wait_ms"] == 9000


@pytest.mark.asyncio
async def test_combine_response_replacers():
    from chat.utils import combine_response_replacers

    # Test with multiple generators (status-based streaming)
    async def stream_generator1():
        yield "first thing"
        yield "second thing"

    async def stream_generator2():
        yield "third thing"
        yield "fourth thing"

    async def stream_generator3():
        yield "fifth thing"
        yield "sixth thing"

    titles = ["Title 1", "Title 2", "Title 3"]
    generators = [stream_generator1(), stream_generator2(), stream_generator3()]
    response_stream = combine_response_replacers(generators, titles)

    # Collect all outputs
    all_outputs = []
    async for yielded_output in response_stream:
        all_outputs.append(yielded_output)

    # For multiple docs, should yield {"streaming": True, ...} during processing
    streaming_outputs = [o for o in all_outputs if o.get("streaming")]
    assert len(streaming_outputs) > 0, "Should yield streaming status during processing"

    # Final output should have the combined final_text
    final_output = all_outputs[-1]
    assert "final_text" in final_output
    final_text = final_output["final_text"]

    # Final text should contain all the final values from each generator (replacer semantics)
    assert "second thing" in final_text  # Final value from generator 1
    assert "fourth thing" in final_text  # Final value from generator 2
    assert "sixth thing" in final_text  # Final value from generator 3
    assert "Title 1" in final_text
    assert "Title 2" in final_text
    assert "Title 3" in final_text

    # Check the ordering in final text
    assert final_text.index("Title 1") < final_text.index("second thing")
    assert final_text.index("second thing") < final_text.index("Title 2")
    assert final_text.index("Title 2") < final_text.index("fourth thing")
    assert final_text.index("fourth thing") < final_text.index("Title 3")
    assert final_text.index("Title 3") < final_text.index("sixth thing")


@pytest.mark.asyncio
async def test_combine_response_replacers_logs_batch_summary(monkeypatch):
    from chat.utils import combine_response_replacers

    async def stream_generator1():
        yield "first thing"
        yield "second thing"

    async def stream_generator2():
        yield "third thing"
        yield "fourth thing"

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr("chat.utils.logger.info", fake_info)

    response_stream = combine_response_replacers(
        [stream_generator1(), stream_generator2()], ["Title 1", "Title 2"]
    )

    async for _ in response_stream:
        pass

    events = [
        payload
        for payload in captured
        if payload[0] == "combine_response_batch_completed"
    ]
    assert len(events) == 1
    _, event_kwargs = events[0]
    assert event_kwargs["doc_count"] == 2
    assert event_kwargs["title_count"] == 2
    assert event_kwargs["total_chars"] == len("second thing") + len("fourth thing")


@pytest.mark.asyncio
async def test_combine_response_replacers_single_doc():
    from chat.utils import combine_response_replacers

    # Test with single generator (direct streaming - no status messages)
    async def stream_generator():
        yield "first thing"
        yield "second thing"

    titles = ["Title 1"]
    generators = [stream_generator()]
    response_stream = combine_response_replacers(generators, titles)

    # Collect all outputs
    all_outputs = []
    async for yielded_output in response_stream:
        all_outputs.append(yielded_output)

    # For single doc, should yield {"text": ...} during streaming (actual content)
    text_outputs = [o for o in all_outputs if o.get("text")]
    assert len(text_outputs) > 0, "Should yield text during streaming for single doc"

    # Should also have final_text at the end for consistency
    final_output = all_outputs[-1]
    assert "final_text" in final_output
    assert "Title 1" in final_output["final_text"]
    assert "second thing" in final_output["final_text"]

    # Streaming text should show content progressively
    assert "Title 1" in text_outputs[0]["text"]
    assert "first thing" in text_outputs[0]["text"]


@pytest.mark.asyncio
async def test_combine_batch_generators():
    from chat.utils import (
        combine_batch_generators,
        combine_response_replacers,
        create_batches,
    )

    async def stream_generator1():
        yield "first thing"
        yield "second thing"

    async def stream_generator2():
        yield "third thing"
        yield "fourth thing"

    async def stream_generator3():
        yield "fifth thing"
        yield "sixth thing"

    titles = ["Title 1", "Title 2", "Title 3"]
    generators = [stream_generator1(), stream_generator2(), stream_generator3()]

    title_batches = create_batches(titles, 2)
    generator_batches = create_batches(generators, 2)

    batch_generators = [
        combine_response_replacers(batch_responses, batch_titles)
        for batch_responses, batch_titles in zip(generator_batches, title_batches)
    ]
    # Batches should be [[first, second], [third]]
    assert len(batch_generators) == 2

    response_stream = combine_batch_generators(batch_generators, total_count=3)

    # Collect all outputs
    all_outputs = []
    async for yielded_output in response_stream:
        all_outputs.append(yielded_output)

    # For multiple docs, should yield text status messages during streaming
    text_outputs = [o for o in all_outputs if isinstance(o, dict) and o.get("text")]
    boundary_outputs = [o for o in all_outputs if o == "<|batchboundary|>"]

    assert len(text_outputs) > 0, "Should yield text outputs"
    assert len(boundary_outputs) == 2, "Should yield 2 batch boundaries"

    # Get the final text output (last one with text)
    final_output = ""
    for o in reversed(all_outputs):
        if isinstance(o, dict) and o.get("text"):
            final_output = o["text"]
            break

    # Final output should contain combined text from all batches
    assert "second thing" in final_output  # Final value from generator 1
    assert "fourth thing" in final_output  # Final value from generator 2
    assert "sixth thing" in final_output  # Final value from generator 3
    assert "Title 1" in final_output
    assert "Title 2" in final_output
    assert "Title 3" in final_output

    # Check the ordering in final text
    assert final_output.index("Title 1") < final_output.index("second thing")
    assert final_output.index("second thing") < final_output.index("Title 2")
    assert final_output.index("Title 2") < final_output.index("fourth thing")
    assert final_output.index("fourth thing") < final_output.index("Title 3")
    assert final_output.index("Title 3") < final_output.index("sixth thing")


@pytest.mark.asyncio
async def test_combine_batch_generators_logs_stream_summary(monkeypatch):
    from chat.utils import (
        combine_batch_generators,
        combine_response_replacers,
        create_batches,
    )

    async def stream_generator1():
        yield "first thing"
        yield "second thing"

    async def stream_generator2():
        yield "third thing"
        yield "fourth thing"

    titles = ["Title 1", "Title 2"]
    generators = [stream_generator1(), stream_generator2()]
    title_batches = create_batches(titles, 1)
    generator_batches = create_batches(generators, 1)
    batch_generators = [
        combine_response_replacers(batch_responses, batch_titles)
        for batch_responses, batch_titles in zip(generator_batches, title_batches)
    ]

    captured = []

    def fake_info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr("chat.utils.logger.info", fake_info)

    response_stream = combine_batch_generators(batch_generators, total_count=2)
    async for _ in response_stream:
        pass

    events = [
        payload
        for payload in captured
        if payload[0] == "combine_batch_stream_completed"
    ]
    assert len(events) == 1
    _, event_kwargs = events[0]
    assert event_kwargs["batch_count"] == 2
    assert event_kwargs["total_document_count"] == 2
    assert event_kwargs["total_chars"] == len("second thing") + len("fourth thing")


@pytest.mark.asyncio
async def test_combine_batch_generators_single_doc():
    from chat.utils import (
        combine_batch_generators,
        combine_response_replacers,
    )

    # Single document case - should stream directly without status messages
    async def stream_generator():
        yield "first thing"
        yield "second thing"

    titles = ["Title 1"]
    generators = [stream_generator()]
    batch_generators = [combine_response_replacers(generators, titles)]

    response_stream = combine_batch_generators(batch_generators, total_count=1)

    # Collect all outputs
    all_outputs = []
    async for yielded_output in response_stream:
        all_outputs.append(yielded_output)

    # For single doc, should pass through text directly (no status messages like "Processing...")
    text_outputs = [o for o in all_outputs if isinstance(o, dict) and o.get("text")]
    boundary_outputs = [o for o in all_outputs if o == "<|batchboundary|>"]

    assert len(text_outputs) > 0, "Should yield text outputs"
    assert len(boundary_outputs) == 1, "Should yield 1 batch boundary"

    # Should stream content directly, not status messages
    # The first text output should have actual document content, not "Processing..."
    assert "Title 1" in text_outputs[0]["text"]
    assert "Processing" not in text_outputs[0]["text"]

    # Final text should have the content
    final_text = text_outputs[-1]["text"]
    assert "second thing" in final_text
    assert "Title 1" in final_text


@pytest.mark.django_db
def test_get_chat_history_sections(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a pinned chat
    pinned_chats = Chat.objects.create(
        user=user, last_modification_date=timezone.now(), pinned=True
    )
    # Create a few chats with different last modification dates
    # to test the sectioning logic
    chat_today = Chat.objects.create(user=user, last_modification_date=timezone.now())
    chat_yesterday = Chat.objects.create(
        user=user, last_modification_date=timezone.now() - timezone.timedelta(days=1)
    )
    chat_last_7_days = Chat.objects.create(
        user=user, last_modification_date=timezone.now() - timezone.timedelta(days=5)
    )
    chat_last_30_days = Chat.objects.create(
        user=user, last_modification_date=timezone.now() - timezone.timedelta(days=20)
    )
    chat_older = Chat.objects.create(
        user=user, last_modification_date=timezone.now() - timezone.timedelta(days=40)
    )

    user_chats = [
        pinned_chats,
        chat_today,
        chat_yesterday,
        chat_last_7_days,
        chat_last_30_days,
        chat_older,
    ]

    # get list of sections
    # a section is (title, chat(s), index)
    sections = get_chat_history_sections(user_chats)

    # Check that each section contains the correct chat
    assert sections[0]["label"] == "Pinned chats"
    assert [c.id for c in sections[0]["chats"]] == [pinned_chats.id]
    assert sections[1]["label"] == "Today"
    assert [c.id for c in sections[1]["chats"]] == [chat_today.id]
    assert sections[2]["label"] == "Yesterday"
    assert [c.id for c in sections[2]["chats"]] == [chat_yesterday.id]
    assert sections[3]["label"] == "Last 7 days"
    assert [c.id for c in sections[3]["chats"]] == [chat_last_7_days.id]
    assert sections[4]["label"] == "Last 30 days"
    assert [c.id for c in sections[4]["chats"]] == [chat_last_30_days.id]
    assert sections[5]["label"] == "Older"
    assert [c.id for c in sections[5]["chats"]] == [chat_older.id]


@pytest.mark.asyncio
async def test_summarize_chat_stream_context_length_error():
    """Test that summarize_chat_stream returns a helpful error for overly long text."""
    from chat.utils import summarize_chat_stream

    # Create a mock LLM with a very small context window for testing
    class MockLLM:
        max_input_tokens = 100  # Very small for testing

        async def chat_stream(self, chat_history):
            # This should never be called since the context check should catch it first
            raise AssertionError(
                "chat_stream should not be called for overly long text"
            )
            yield {}

    llm = MockLLM()
    # Create text that exceeds the context limit (100 * 0.75 = 75 tokens max)
    # Each word is roughly 1 token, so 200 words should exceed
    very_long_text = "word " * 200

    responses = []
    async for response in summarize_chat_stream(llm, very_long_text):
        responses.append(response)

    # Should get exactly one response with the error message
    assert len(responses) == 1
    response = responses[0]
    assert isinstance(response, dict)
    assert "text" in response
    error_text = response["text"]
    # Verify the error message contains helpful information
    assert "Error" in error_text
    assert "too long" in error_text
    assert "tokens" in error_text
    assert "GPT-4.1" in error_text or "model" in error_text
