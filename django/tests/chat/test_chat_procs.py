import asyncio

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import pytest
from asgiref.sync import sync_to_async
from bs4 import BeautifulSoup as bs

from chat.llm import OttoLLM
from chat.models import Chat, Message
from chat.utils import (
    fix_source_links,
    get_chat_history_sections,
    htmx_stream,
    summarize_long_text_async,
    url_to_text,
    wrap_llm_response,
)
from otto.models import User

pytest_plugins = ("pytest_asyncio",)


def test_url_to_text():
    # Test that a URL with a valid article returns the article text
    url = "https://en.wikipedia.org/wiki/Ottawa"
    text = url_to_text(url)
    assert text
    assert len(text) > 100
    assert "Ottawa" in text
    assert "Canada" in text

    # Test that a URL with an invalid article returns an empty string
    url = "http://aofwgyauhwfg.awfognahwofg/"
    text = url_to_text(url)
    assert text == ""


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

    async def stream_generator():
        files = [
            FakeFile("file1.txt", "This is the first file"),
            FakeFile("file2.txt", "This is the second file"),
        ]
        for i, file in enumerate(files):
            yield f"**{file.name}**\n"
            summary = await summarize_long_text_async(file.text, llm, "short")
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
async def test_combine_response_generators():
    from chat.utils import combine_response_generators

    llm = OttoLLM()

    # The function should take a list of (sync) generators and a list of titles
    # and combine the output of the generators into a single stream
    # By the end, it will have output all the text from all the generators
    # With text from generator 1, then a divider, then text from generator 2, etc.
    def stream_generator1():
        yield "first thing"
        yield "second thing"

    def stream_generator2():
        yield "third thing"
        yield "fourth thing"

    def stream_generator3():
        yield "fifth thing"
        yield "sixth thing"

    titles = ["Title 1", "Title 2", "Title 3"]
    generators = [stream_generator1(), stream_generator2(), stream_generator3()]
    response_stream = combine_response_generators(generators, titles, query="", llm=llm)
    final_output = ""
    async for yielded_output in response_stream:
        final_output = yielded_output
    assert "first thing" in final_output
    assert "fifth thing" in final_output
    assert "Title 1" in final_output
    # Check the ordering
    assert final_output.index("Title 1") < final_output.index("first thing")
    assert final_output.index("first thing") < final_output.index("second thing")
    assert final_output.index("second thing") < final_output.index("Title 2")
    assert final_output.index("Title 2") < final_output.index("third thing")
    assert final_output.index("third thing") < final_output.index("fourth thing")
    assert final_output.index("fourth thing") < final_output.index("Title 3")
    assert final_output.index("Title 3") < final_output.index("fifth thing")
    assert final_output.index("fifth thing") < final_output.index("sixth thing")


@pytest.mark.asyncio
async def test_combine_response_replacers():
    from chat.utils import combine_response_replacers

    # This one takes in async generators
    # The function should take a list of generators and a list of titles
    # and combine the output of the generators into a single stream
    # By the end, it will have output all the text from all the generators
    # With text from generator 1, then a divider, then text from generator 2, etc.
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
    final_output = ""
    async for yielded_output in response_stream:
        final_output = yielded_output
    assert "second thing" in final_output
    assert "fifth thing" not in final_output
    assert "sixth thing" in final_output
    assert "Title 1" in final_output
    # Check the ordering
    assert final_output.index("Title 1") < final_output.index("second thing")
    assert final_output.index("second thing") < final_output.index("Title 2")
    assert final_output.index("Title 2") < final_output.index("fourth thing")
    assert final_output.index("fourth thing") < final_output.index("Title 3")
    assert final_output.index("Title 3") < final_output.index("sixth thing")


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
        yield _("**No relevant sources found.**")

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

    response_stream = combine_batch_generators(batch_generators)

    final_output = ""
    async for yielded_output in response_stream:
        if yielded_output != "<|batchboundary|>":
            final_output = yielded_output

    assert "second thing" in final_output
    assert "third thing" not in final_output
    assert "fifth thing" not in final_output
    assert "Title 1" in final_output

    # Check the ordering
    assert final_output.index("Title 1") < final_output.index("second thing")
    assert final_output.index("second thing") < final_output.index("Title 2")
    assert final_output.index("Title 2") < final_output.index("fourth thing")
    assert final_output.index("fourth thing") < final_output.index("Title 3")
    assert final_output.index("Title 3") < final_output.index(
        "**No relevant sources found.**"
    )

    # Test pruning using single batch with no title
    pruning_generators = [stream_generator3()]
    pruning_test_stream = combine_batch_generators(pruning_generators, pruning=True)

    # First, it should yield values from the generator
    assert await pruning_test_stream.__anext__() == "fifth thing"
    assert await pruning_test_stream.__anext__() == _("**No relevant sources found.**")
    # Afterwards, should yield an empty string from irrelevant batch
    assert await pruning_test_stream.__anext__() == ""
    # Then the batch boundary token
    assert await pruning_test_stream.__anext__() == "<|batchboundary|>"
    # Finally, return pruning message due to empty final stream
    assert await pruning_test_stream.__anext__() == _("**No relevant sources found.**")


@pytest.mark.django_db
def test_get_chat_history_sections(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create chats with different last_modification_date
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
    assert sections[0]["label"] == "Today"
    assert sections[0]["chats"] == [chat_today]
    assert sections[1]["label"] == "Yesterday"
    assert sections[1]["chats"] == [chat_yesterday]
    assert sections[2]["label"] == "Last 7 days"
    assert sections[2]["chats"] == [chat_last_7_days]
    assert sections[3]["label"] == "Last 30 days"
    assert sections[3]["chats"] == [chat_last_30_days]
    assert sections[4]["label"] == "Older"
    assert sections[4]["chats"] == [chat_older]
