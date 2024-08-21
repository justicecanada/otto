import asyncio

import pytest
from asgiref.sync import sync_to_async
from bs4 import BeautifulSoup as bs

from chat.models import Chat, Message
from chat.utils import (
    htmx_stream,
    llm_response_to_html,
    summarize_long_text_async,
    url_to_text,
)

pytest_plugins = ("pytest_asyncio",)

tags_outside_backticks = """
<div>Here is some text</div>
<div>Here is some more text</div>
"""

tags_inside_triple_backticks = """
<div>Here is some text</div>
```
<div>Here is some more text</div>
```
"""

tags_inside_single_backticks = """
<div>Here is some text</div>
`<div>Here is some more text</div>`
"""

markdown_list = """
* Item 1
* Item 2
"""


def test_llm_response_formatter():

    # Test that markdown is parsed
    html = llm_response_to_html(markdown_list)
    soup = bs(html, "html.parser")
    assert soup.find_all("li")
    assert soup.find_all("ul")
    assert soup.find_all("li")[0].text == "Item 1"
    assert soup.find_all("li")[1].text == "Item 2"


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


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_htmx_stream_response_stream(all_apps_user):
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
        response_stream=stream_generator(),
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
    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_2")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_str="Hi!",
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
            summary = await summarize_long_text_async(file.text, "short")
            if i < len(files) - 1:
                yield f"{summary}\n\n-----\n"
            else:
                yield f"{summary}\n<<END>>"

    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_3")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_generator=stream_generator(),
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
    async def stream_generator():
        yield "first thing"
        yield "second thing<<END>>"

    # We first need an empty chat and a message
    user = await sync_to_async(basic_user)("test_user_4")
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    assert await sync_to_async(chat.messages.count)() == 1
    response_stream = htmx_stream(
        chat,
        message.id,
        response_replacer=stream_generator(),
        save_message=False,
        format=False,
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
    # A new message should NOT have been created since save_message=False
    assert await sync_to_async(chat.messages.count)() == 1
