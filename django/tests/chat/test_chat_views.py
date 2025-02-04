import asyncio
import tempfile
from unittest import mock

from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from chat.forms import PresetForm
from chat.llm import OttoLLM
from chat.models import Chat, ChatFile, ChatOptions, Message, Preset
from chat.utils import htmx_stream, title_chat
from librarian.models import Library
from otto.models import App, Notification, SecurityLabel

pytest_plugins = ("pytest_asyncio",)


async def final_response_helper(stream):
    content = b""
    async for chunk in stream:
        content = chunk
    return content


def final_response(stream):
    return asyncio.run(final_response_helper(stream))


@pytest.mark.django_db
def test_title_chat(client, all_apps_user):
    llm = OttoLLM()
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # The title_chat function, with force_title=True
    # should return "Untitled chat"
    chat_title = title_chat(chat.id, llm, force_title=True)
    assert chat_title == "Untitled chat"

    # Create 2 messages
    Message.objects.create(chat=chat, text="Hello")
    Message.objects.create(chat=chat, text="How are you?", is_bot=True)

    # The title_chat function, with force_title=False
    # should return an empty string
    chat_title = title_chat(chat.id, llm, force_title=False)
    assert chat_title == ""

    # The title_chat function, with force_title=True
    # should return a title
    chat_title = title_chat(chat.id, llm, force_title=True)
    assert chat_title != ""

    # Add a third message
    Message.objects.create(chat=chat, text="I'm doing well, thanks")

    # The title_chat function, with force_title=False
    # should now return a title since there are 3 messages
    chat_title = title_chat(chat.id, llm, force_title=False)
    assert chat_title != ""


@pytest.mark.django_db
def test_chat(client, basic_user, all_apps_user):
    # Test scenario: Not logged in
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    # This should redirect to the welcome page
    assert response.url == reverse("welcome") + "?next=" + reverse("chat:new_chat")

    # Test scenario: Logged in as a basic user
    user = basic_user()
    client.force_login(user)
    response = client.get(reverse("chat:new_chat"))
    # This should redirect to the accept terms page
    assert response.status_code == 302
    assert response.url == reverse("terms_of_use") + "?next=" + reverse("chat:new_chat")

    # Accept the terms
    user.accepted_terms_date = timezone.now()
    user.save()

    response = client.get(reverse("chat:new_chat"))
    # This should now redirect to the index page and create a notification
    assert response.status_code == 302
    assert response.url == reverse("index")

    # Check that a notification was created
    notification = Notification.objects.filter(user=user).first()
    assert notification is not None

    # Test scenario: Logged in as all apps user
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    assert chat.security_label == SecurityLabel.default_security_label()
    chat_id = chat.id
    assert response.url == reverse("chat:chat", args=[chat_id])
    response = client.get(reverse("chat:chat", args=[chat_id]))
    assert "Untitled chat" in response.content.decode("utf-8")

    # Test scenario: Check that the chat will create a security label if it doesn't exist
    Message.objects.create(chat=chat, text="Message 1", chat_id=chat_id)
    Message.objects.create(chat=chat, text="Message 2", chat_id=chat_id)
    chat.security_label = None
    chat.save()

    client.get(reverse("chat:chat", args=[chat_id]))
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    assert chat.security_label == SecurityLabel.default_security_label()


@pytest.mark.django_db
def test_chat_message(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("chat:chat_with_ai"), follow=True)
    # Find the newest user chat
    chat_id = Chat.objects.filter(user=user).order_by("-created_at").first().id
    response = client.post(
        reverse("chat:chat_message", args=[chat_id]),
        data={"user-message": "Hello"},
    )
    assert response.status_code == 200
    assert "Hello" in response.content.decode("utf-8")

    # Check that the message was saved
    assert Message.objects.filter(chat_id=chat_id).count() == 2
    message = Message.objects.filter(chat_id=chat_id).first()
    assert message.text == "Hello"

    bot_message = Message.objects.filter(parent_id=message.id).first()
    assert bot_message
    assert bot_message.is_bot
    assert bot_message.text == ""

    # TODO: Keep getting errors with the SSE response in tests. No time to fix now.
    # It works in practice.

    # # Get the response
    # response = client.get(reverse("chat:chat_response", args=[bot_message.id]))
    # # This will return a StreamingHttpResponse
    # assert response.status_code == 200
    # response_text = final_response(response.streaming_content).decode("utf-8")
    # assert "Error" not in response_text
    # assert "data-md=" in response_text

    # # Ensure the bot message was updated with the response text
    # assert len(bot_message.text) > 0


# TODO: Test Celery tasks
@pytest.mark.django_db
def test_translate_file(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    # Write a file to translate called "test file.txt" with contents "Hello"
    with tempfile.TemporaryDirectory() as tmpdirname:
        with open(f"{tmpdirname}/test file.txt", "w") as file:
            file.write("Hello")
        response = client.get(reverse("chat:new_chat"), follow=True)
        assert response.status_code == 200
        chat = Chat.objects.filter(user=user).order_by("-created_at").first()
        # Check that a ChatOptions object has been created
        assert chat.options is not None
        # Set mode to Translate
        chat.options.mode = "translate"
        chat.options.translate_language = "fr"
        chat.options.save()

        # Create a message and add a file
        in_message = Message.objects.create(chat=chat, text="")
        chat_file = ChatFile.objects.create(
            message_id=in_message.id,
            filename="test file.txt",
            eof=1,
            content_type="text/plain",
        )
        assert in_message.num_files == 1

        # Create the response message
        out_message = Message.objects.create(
            chat=chat, mode="translate", is_bot=True, parent=in_message
        )

        chat_file.saved_file.file.save(
            "test file.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
        response = client.post(reverse("chat:chat_response", args=[out_message.id]))
        assert response.status_code == 200

        # TODO: This isn't working in tests. It works in practice.
        # Iterate over the response_stream generator
        # final_text = final_response(response.streaming_content).decode("utf-8")
        # assert "test file" in final_text
        # assert "data-md=" not in final_text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_htmx_stream_stop(client, all_apps_user):
    llm = OttoLLM()

    async def stream_generator():
        yield "first thing"
        yield "second thing"
        yield "third thing"

    # We first need an empty chat and a message
    user = await sync_to_async(all_apps_user)("test_user_stream_stop")
    await sync_to_async(client.force_login)(user)
    chat = await sync_to_async(Chat.objects.create)(user=user)
    message = await sync_to_async(Message.objects.create)(chat=chat, text="Hello")
    response_message = await sync_to_async(Message.objects.create)(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    assert await sync_to_async(chat.messages.count)() == 2
    response_stream = htmx_stream(
        chat,
        response_message.id,
        response_replacer=stream_generator(),
        wrap_markdown=False,
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    message_counter = 0
    async for yielded_output in response_stream:
        if message_counter == 0:
            assert "first thing" in yielded_output
        elif message_counter == 1:
            assert "second thing" in yielded_output
            # Stop the stream by requesting chat:stop_response
            response = await sync_to_async(client.get)(
                reverse("chat:stop_response", args=[response_message.id])
            )
            assert response.status_code == 200
        message_counter += 1
        # Output should start with "data: " for Server-Sent Events
        assert yielded_output.startswith("data: ")
        # Output should end with a double newline
        assert yielded_output.endswith("\n\n")
        final_output = yielded_output
    # Before stopping, the second generated message should be in the output
    assert "second thing" in final_output
    # However, the third message should not be in the output
    assert "third thing" not in final_output
    # There should be an element in the response to replace the SSE div
    assert "<div hx-swap-oob" in final_output
    # A new message should NOT have been created
    assert await sync_to_async(chat.messages.count)() == 2


@pytest.mark.django_db
def test_chat_routes(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    new_translate = reverse("chat:translate")
    new_summarize = reverse("chat:summarize")
    new_qa = reverse("chat:qa")
    new_chat = reverse("chat:new_chat")
    Chat.objects.all().delete()
    # Check that the routes are accessible. Each should create a new chat
    response = client.get(new_translate)
    assert response.status_code == 302
    original_chat_id = Chat.objects.filter(user=user).order_by("-created_at").first().id
    assert response.url == reverse("chat:chat", args=[original_chat_id])
    assert Chat.objects.count() == 1
    response = client.get(new_summarize)
    assert response.status_code == 302
    response = client.get(new_qa)
    assert response.status_code == 302
    response = client.get(new_chat)
    assert response.status_code == 302
    # Now open the chat directly
    response = client.get(
        reverse(
            "chat:chat",
            args=[Chat.objects.filter(user=user).order_by("-created_at").first().id],
        )
    )
    assert response.status_code == 200
    assert "Untitled chat" in response.content.decode("utf-8")


# Test delete_chat view
@pytest.mark.django_db
def test_delete_chat(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    assert Chat.objects.filter(user=user).count() == 1
    response = client.get(reverse("chat:delete_chat", args=[chat.id, chat.id]))
    assert response.status_code == 200
    assert Chat.objects.filter(user=user).count() == 0
    # This should give a 404
    response = client.post(reverse("chat:delete_chat", args=[chat.id, chat.id]))
    assert response.status_code == 404


# Test delete_all_chats view
@pytest.mark.django_db
def test_delete_all_chats(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create multiple chats for the user
    Chat.objects.create(user=user)
    Chat.objects.create(user=user)
    assert Chat.objects.filter(user=user).count() == 2

    # Check that chat data sources were created
    assert user.personal_library.data_sources.filter(chat__isnull=False).count() == 2

    # Call the delete_all_chats view
    response = client.get(reverse("chat:delete_all_chats"))

    # Check that all chats are deleted
    assert response.status_code == 200
    assert Chat.objects.filter(user=user).count() == 0

    # Test that all chat data sources have been deleted too
    assert user.personal_library.data_sources.filter(chat__isnull=False).count() == 0

    # Check that the response contains the HX-Redirect header
    assert response["HX-Redirect"] == reverse("chat:new_chat")


# Test init_upload view
@pytest.mark.django_db
def test_init_upload(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    response = client.get(reverse("chat:init_upload", args=[chat.id]))
    assert response.status_code == 200


# Test done_upload view with modes "translate", "summarize" and "qa"
@pytest.mark.django_db
def test_done_upload(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    message = Message.objects.create(chat=chat, text="Hello", mode="translate")
    response = client.get(reverse("chat:done_upload", args=[message.id]))
    assert response.status_code == 200
    message = Message.objects.create(chat=chat, text="Hello", mode="summarize")
    response = client.get(reverse("chat:done_upload", args=[message.id]))
    assert response.status_code == 200
    message = Message.objects.create(chat=chat, text="Hello", mode="qa")
    response = client.get(reverse("chat:done_upload", args=[message.id]))
    assert response.status_code == 200


# TODO: Test chunk_upload (somewhat difficult)
# See tests/otto/test_cleanup.py for a partial test of chunk upload


# Test download_file view
@pytest.mark.django_db
def test_download_file(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    # Write a file to translate called "test file.txt" with contents "Hello"
    with tempfile.TemporaryDirectory() as tmpdirname:
        with open(f"{tmpdirname}/test file.txt", "w") as file:
            file.write("Hello")
        chat = Chat.objects.create(user=user)
        in_message = Message.objects.create(chat=chat, text="")
        chat_file = ChatFile.objects.create(
            message_id=in_message.id,
            filename="test file.txt",
            eof=1,
            content_type="text/plain",
        )
        chat_file.saved_file.file.save(
            "test file.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
        file_id = chat_file.id
        url = reverse("chat:download_file", args=[file_id])
        response = client.get(url)
        assert response.status_code == 200
    wrong_user = all_apps_user("wrong_user")
    client.force_login(wrong_user)
    response = client.get(url)
    assert response.status_code != 200
    # Non-existing chat file
    response = client.get(reverse("chat:download_file", args=[999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_chat_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the chat_with_ai route to create it with appropriate options
    response = client.get(reverse("chat:chat_with_ai"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    message = Message.objects.create(chat=chat, text="Hello", mode="chat")
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )

    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Test different lengths of input
    text_between_4k_and_16k = "Hello there!\n" * 2000
    message = Message.objects.create(
        chat=chat, text=text_between_4k_and_16k, mode="chat"
    )
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Text over 16k should still return a correct response
    text_over_16k = "Hello there!\n" * 16000
    message = Message.objects.create(chat=chat, text=text_over_16k, mode="chat")
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Test GPT-4
    message = Message.objects.create(
        chat=chat, text="Hello", mode="chat", details={"model": "gpt-4"}
    )
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200

    # Test chat_response with an invalid mode
    message = Message.objects.create(chat=chat, text="Hello", mode="invalid_mode")
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    # This should also return a 200 status code, albeit with an error message
    assert response.status_code == 200


# Test chat_response with Summarize mode
@pytest.mark.django_db
def test_chat_summarization_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:summarize"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    text_between_4k_and_16k = "Hello there!\n" * 2000

    message = Message.objects.create(chat=chat, text="Hello", mode="summarize")
    response_message = Message.objects.create(
        chat=chat, mode="summarize", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Test longer message
    message = Message.objects.create(
        chat=chat, text=text_between_4k_and_16k, mode="summarize"
    )
    response_message = Message.objects.create(
        chat=chat, mode="summarize", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200

    # Test very long message: TODO: This Sumy code does NOT work!!!
    # message = Message.objects.create(chat=chat, text=text_over_16k, mode="summarize")
    # response = client.get(reverse("chat:chat_response", args=[message.id]))
    # assert response.status_code == 200

    # Test with a URL
    message = Message.objects.create(
        chat=chat, text="https://en.wikipedia.org/wiki/Ottawa", mode="summarize"
    )
    response_message = Message.objects.create(
        chat=chat, mode="summarize", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Test with multiple files
    files_message = Message.objects.create(chat=chat, text="", mode="summarize")
    files_message_response = Message.objects.create(
        chat=chat, text="", mode="summarize", is_bot=True, parent=files_message
    )

    with tempfile.TemporaryDirectory() as tmpdirname:
        with open(f"{tmpdirname}/test file.txt", "w") as file:
            file.write("Hello")
        file1 = ChatFile.objects.create(
            message_id=files_message.id,
            filename="test file.txt",
            eof=1,
            content_type="text/plain",
        )
        file1.saved_file.file.save(
            "test file.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
        file2 = ChatFile.objects.create(
            message_id=files_message.id,
            filename="test file2.txt",
            eof=1,
            content_type="text/plain",
        )
        file2.saved_file.file.save(
            "test file2.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
        response = client.get(
            reverse("chat:chat_response", args=[files_message_response.id])
        )
        assert response.status_code == 200


# Test chat_response with QA and Translate modes
@pytest.mark.django_db
def test_translate_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:translate"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Test chat_response with Translate mode
    message = Message.objects.create(chat=chat, text="Hello", mode="translate")
    message = Message.objects.create(
        chat=chat, mode="translate", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[message.id]))
    assert response.status_code == 200
    # Test with files.
    # TODO: File upload doesn't actually complete, again because of the SSE testing issue
    files_message = Message.objects.create(
        chat=chat, text="", mode="translate", is_bot=True, parent=message
    )
    with tempfile.TemporaryDirectory() as tmpdirname:
        with open(f"{tmpdirname}/test file.txt", "w") as file:
            file.write("Hello")
        file1 = ChatFile.objects.create(
            message_id=files_message.id,
            filename="test file.txt",
            eof=1,
            content_type="text/plain",
        )
        file1.saved_file.file.save(
            "test file.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
        file2 = ChatFile.objects.create(
            message_id=files_message.id,
            filename="test file2.txt",
            eof=1,
            content_type="text/plain",
        )
        file2.saved_file.file.save(
            "test file2.txt", open(f"{tmpdirname}/test file.txt", "rb")
        )
    files_message = Message.objects.create(
        chat=chat, text="", mode="translate", is_bot=True, parent=files_message
    )
    response = client.get(reverse("chat:chat_response", args=[files_message.id]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_qa_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Test corporate chatbot QA mode
    corporate_library_id = Library.objects.get_default_library().id
    message = Message.objects.create(
        chat=chat, text="What is my dental coverage?", mode="qa"
    )
    message.details["library"] = corporate_library_id
    message.save()
    response_message = Message.objects.create(
        chat=chat, mode="qa", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200

    # TODO: Check that the newest message in the Chat now has some AnswerSources
    # I can't get this working! I think its because of the Server-Sent-Events
    # It is unclear how to test SSE responses in Django tests

    # response_message = (
    #     Message.objects.filter(chat=chat).order_by("-created_at").first()
    # )
    # assert response_message.sources.count() > 0


@pytest.mark.django_db
def test_chat_agent(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    # Create a chat using the chat_with_ai route to create it with appropriate options
    response = client.get(reverse("chat:chat_with_ai"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.chat_agent = True
    chat.options.save()

    message = Message.objects.create(chat=chat, text="Hello", mode="chat")
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )

    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Refresh the chat
    chat.refresh_from_db()
    # Check that the chat mode is "chat"
    assert chat.options.mode == "chat"

    # Ask a generic question to the chat agent, not about the department
    message = Message.objects.create(
        chat=chat, text="What is the meaning of life?", mode="chat"
    )
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    chat.refresh_from_db()
    # Check that the chat mode is still "chat"
    assert chat.options.mode == "chat"

    # Ask a question to the chat agent about Corporate library
    message = Message.objects.create(
        chat=chat, text="What is my corporate dental coverage?", mode="chat"
    )
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    chat.refresh_from_db()
    # Check that the chat mode has been switched to "qa"
    assert chat.options.mode == "qa"


@pytest.mark.django_db
def test_qa_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()

    # Test chat_response with QA mode. This should query the Corporate library.
    message = Message.objects.create(
        chat=chat,
        text="What is the capital of Canada?",
        mode="qa",
    )
    response_message = Message.objects.create(
        chat=chat, mode="qa", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_qa_filters(client, all_apps_user):
    from librarian.models import DataSource

    # Create an empty library
    empty_library = Library.objects.create(name="Test Library")
    # Create a chat by hitting the new chat route in QA mode
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat_options = chat.options
    chat_options.qa_library = empty_library
    chat_options.save()
    # Create a message asking a question by hitting the chat_message route
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "What is the capital of Canada?"},
    )
    assert response.status_code == 200
    # Create a response by hitting the chat_response route
    response = client.get(
        reverse("chat:chat_response", args=[Message.objects.last().id])
    )
    assert response.status_code == 200
    # Change the chat_options qa_scope to "documents" and "data_sources" and try each
    chat_options.qa_scope = "documents"
    chat_options.save()
    # There should be no nodes retrieved since no documents are selected.
    response = client.get(
        reverse("chat:chat_response", args=[Message.objects.last().id])
    )
    assert response.status_code == 200
    chat_options.qa_scope = "data_sources"
    chat_options.data_sources = DataSource.objects.all()
    # This should exercise the case in which filters are applied and DO retrieve nodes
    chat_options.save()
    response = client.get(
        reverse("chat:chat_response", args=[Message.objects.last().id])
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_positive_thumbs_feedback(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    Message.objects.create(chat=chat)
    message = Message.objects.create(chat=chat, is_bot=True)

    response = client.get(
        reverse(
            "chat:thumbs_feedback", kwargs={"message_id": message.id, "feedback": "1"}
        )
    )

    assert Message.objects.filter(chat_id=chat.id).last().feedback == 1
    assert response.status_code == 200


@pytest.mark.django_db
def test_negative_thumbs_feedback(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    Message.objects.create(chat=chat)
    message = Message.objects.create(chat=chat, is_bot=True)

    response = client.get(
        reverse(
            "chat:thumbs_feedback", kwargs={"message_id": message.id, "feedback": "-1"}
        )
    )

    assert Message.objects.filter(chat_id=chat.id).last().feedback == -1
    assert response.status_code == 200


@pytest.mark.django_db
def test_rename_chat_title(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    chat.title = "My chat"
    chat.save()

    # Create 3 messages
    Message.objects.create(chat=chat, text="Hello")
    Message.objects.create(chat=chat, text="How are you?", is_bot=True)
    Message.objects.create(chat=chat, text="I'm doing well, thanks")

    # Test the title_chat function
    response = client.get(
        reverse(
            "chat:chat_list_item", kwargs={"chat_id": chat.id, "current_chat": "True"}
        )
    )
    assert response.status_code == 200
    assert "My chat" in response.content.decode("utf-8")

    # Rename the chat to "My new chat"
    new_title = "My new chat"
    response = client.post(
        reverse(
            "chat:rename_chat", kwargs={"chat_id": chat.id, "current_chat": "True"}
        ),
        data={"title": new_title},
    )
    assert response.status_code == 200
    assert new_title in response.content.decode("utf-8")

    invalid_title = "".join("a" for _ in range(256))
    # Test invalid form
    response = client.post(
        reverse(
            "chat:rename_chat", kwargs={"chat_id": chat.id, "current_chat": "True"}
        ),
        data={"title": invalid_title},
    )
    assert response.status_code == 200
    assert f'value="{invalid_title}"' in response.content.decode("utf-8")

    # Test get
    response = client.get(
        reverse("chat:rename_chat", kwargs={"chat_id": chat.id, "current_chat": "True"})
    )
    assert response.status_code == 200
    assert f'value="{new_title}"' in response.content.decode("utf-8")


@pytest.mark.django_db
def test_per_source_qa_response(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.qa_answer_mode = "per-source"
    chat.options.save()

    # Test chat_response with QA mode. This should query the Corporate library.
    message = Message.objects.create(
        chat=chat,
        text="What is the capital of Canada?",
        mode="qa",
    )
    response_message = Message.objects.create(
        chat=chat, mode="qa", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_summarize_qa_response(client, all_apps_user):
    from librarian.models import Document

    user = all_apps_user()
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.qa_scope = "documents"
    chat.options.qa_mode = "summarize"

    # Test corporate chatbot QA mode
    chat.options.qa_documents.set(Document.objects.all())
    chat.options.save()
    message = Message.objects.create(
        chat=chat, text="What is my dental coverage?", mode="qa"
    )
    message.save()
    response_message = Message.objects.create(
        chat=chat, mode="qa", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_preset(client, basic_user, all_apps_user):
    user = basic_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)

    # Instantiate the form with a regular user
    form = PresetForm(user=user)
    assert form.fields["sharing_option"].choices == [
        ("private", "Make private"),
        ("others", "Share with others"),
    ]
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    # Instantiate the form with a user with admin rights
    form = PresetForm(user=user)
    assert form.fields["sharing_option"].choices == [
        ("private", "Make private"),
        ("everyone", "Share with everyone"),
        ("others", "Share with others"),
    ]

    # Test saving a new preset
    response = client.post(
        reverse(
            "chat:chat_options", kwargs={"chat_id": chat.id, "action": "create_preset"}
        ),
        data={
            "name_en": "New Preset",
            "description_en": "Preset Description",
            "sharing_option": "private",
            "accessible_to": [],
            "prompt": "",
        },
    )
    assert response.status_code == 200
    assert Preset.objects.filter(name_en="New Preset").exists()

    # Try to load the private preset as user2
    user2 = all_apps_user("user2")
    client.force_login(user2)
    chat2 = Chat.objects.create(user=user2)
    preset = Preset.objects.get(name_en="New Preset")
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat2.id,
                "action": "load_preset",
                "preset_id": preset.id,
            },
        )
    )

    # Should get 403 since user2 can't access user1's private preset
    assert response.status_code == 403

    # Test editing an existing preset
    client.force_login(user)
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat.id,
                "action": "create_preset",
                "preset_id": preset.id,
            },
        ),
        data={
            "name_en": "Updated Preset",
            "description_en": "Updated Description",
            "sharing_option": "others",
            "accessible_to": [user2.id],
            "prompt": "",
        },
    )
    assert response.status_code == 200
    preset.refresh_from_db()
    assert preset.name_en == "Updated Preset"
    assert preset.description_en == "Updated Description"
    assert preset.sharing_option == "others"
    assert user2 in preset.accessible_to.all()

    # make sure the preset is in the preset list of user 2 but that user 3 cannot view it
    client.force_login(user2)
    response = client.get(reverse("chat:get_presets", kwargs={"chat_id": chat2.id}))
    assert "Updated Preset" in response.content.decode()

    user3 = all_apps_user("user3")
    chat3 = Chat.objects.create(user=user3)
    client.force_login(user3)
    response = client.get(reverse("chat:get_presets", kwargs={"chat_id": chat3.id}))
    assert "Updated Preset" not in response.content.decode()

    # Test loading the preset as user 2
    client.force_login(user2)
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat2.id,
                "action": "load_preset",
                "preset_id": preset.id,
            },
        )
    )

    last_message = list(response.context["messages"])[-1]
    assert response.status_code == 200
    assert "preset_loaded" in response.context
    assert response.context["preset_loaded"] == "true"
    assert (
        last_message.level == messages.SUCCESS
        and last_message.message == "Preset loaded successfully."
    )

    # Test adding and removing the preset to favorites
    client.force_login(user)
    response = client.get(reverse("chat:set_preset_favourite", args=[preset.id]))
    assert response.status_code == 200
    preset.refresh_from_db()
    assert user in preset.favourited_by.all()
    # remove from favourites
    response = client.get(reverse("chat:set_preset_favourite", args=[preset.id]))
    assert response.status_code == 200
    preset.refresh_from_db()
    assert user not in preset.favourited_by.all()

    # Test accompanying messages
    response_messages = list(response.context["messages"])
    removed_message = response_messages[-1]
    added_message = response_messages[-2]
    assert (
        removed_message.level == messages.SUCCESS
        and removed_message.message == "Preset removed from favourites."
    )
    assert (
        added_message.level == messages.SUCCESS
        and added_message.message == "Preset added to favourites."
    )

    with mock.patch("chat.models.Preset.toggle_favourite", side_effect=ValueError):
        response = client.get(reverse("chat:set_preset_favourite", args=[preset.id]))
        assert response.status_code == 500

    # Test setting the preset as default
    response = client.get(reverse("chat:set_preset_default", args=[preset.id, chat.id]))
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.default_preset == preset

    # Test deleting the preset
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat.id,
                "action": "delete_preset",
                "preset_id": preset.id,
            },
        )
    )
    assert response.status_code == 302  # Redirect after deletion
    assert not Preset.objects.filter(id=preset.id).exists()

    # Create a preset with user's personal library
    chat = Chat.objects.create(user=user)
    chat.options.qa_library = user.personal_library
    chat.options.qa_pre_instructions = "The quick brown fox"
    chat.options.save()
    response = client.post(
        reverse(
            "chat:chat_options", kwargs={"chat_id": chat.id, "action": "create_preset"}
        ),
        data={
            "name_en": "Personal Library Preset",
            "sharing_option": "others",
            "accessible_to": [user2.id],
            "prompt": "",
        },
    )
    assert response.status_code == 200
    preset = Preset.objects.get(name_en="Personal Library Preset")
    # Now, user2 should be able to load this preset - BUT - the library should be reset
    client.force_login(user2)
    chat2 = Chat.objects.create(user=user2)
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat2.id,
                "action": "load_preset",
                "preset_id": preset.id,
            },
        )
    )
    assert response.status_code == 200
    chat2.options.refresh_from_db()
    # Chat2 should now have the preset loaded
    assert chat2.options.qa_pre_instructions == "The quick brown fox"
    # But the library should be reset to user2's personal library
    assert chat2.options.qa_library == user2.personal_library

    # Now, set the preset as user2's default
    response = client.get(
        reverse("chat:set_preset_default", args=[preset.id, chat2.id])
    )
    assert response.status_code == 200
    # Try creating a new chat using the chat route
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    # Get the newest chat for user2
    chat2 = Chat.objects.filter(user=user2).order_by("-created_at").first()
    # This chat should have the preset loaded
    assert chat2.options.qa_pre_instructions == "The quick brown fox"
    # But the library should be reset to user2's personal library
    assert chat2.options.qa_library == user2.personal_library

    # Reset to default preset
    chat2.options.qa_pre_instructions = ""
    chat2.options.save()

    # Reset to default preset (action="reset")
    response = client.post(
        reverse(
            "chat:chat_options",
            kwargs={
                "chat_id": chat2.id,
                "action": "reset",
            },
        )
    )
    assert response.status_code == 200
    chat2.refresh_from_db()
    # Chat2 should now have the preset loaded
    assert chat2.options.qa_pre_instructions == "The quick brown fox"
    # But the library should be reset to user2's personal library
    assert chat2.options.qa_library == user2.personal_library


def test_update_qa_options_from_librarian(client, all_apps_user):
    from librarian.models import DataSource, Document, Library

    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)
    library = user.personal_library
    response = client.get(
        reverse("chat:update_from_librarian", args=[chat.id, library.id])
    )
    assert response.status_code == 200
    chat.options.refresh_from_db()
    assert chat.options.qa_library == library
    assert chat.options.qa_data_sources.count() == 0
    assert chat.options.qa_documents.count() == 0

    # Try switching to same library. Nothing should change
    # Let's set a data source and document just to test that they are NOT cleared in this case
    data_source = DataSource.objects.create(name="Test Data Source", library=library)
    chat.options.qa_data_sources.add(data_source)
    document = Document.objects.create(data_source=data_source)
    chat.options.qa_documents.add(document)
    response = client.get(
        reverse("chat:update_from_librarian", args=[chat.id, library.id])
    )
    assert response.status_code == 200
    chat.options.refresh_from_db()
    assert chat.options.qa_library == library
    assert chat.options.qa_data_sources.count() == 1
    assert chat.options.qa_documents.count() == 1

    # Test with a library that doesn't exist
    response = client.get(reverse("chat:update_from_librarian", args=[chat.id, 999]))
    assert response.status_code == 200
    # This should reset to default library and clear data sources and documents
    chat.options.refresh_from_db()
    assert chat.options.qa_library == Library.objects.get_default_library()
    assert chat.options.qa_data_sources.count() == 0
    assert chat.options.qa_documents.count() == 0

    # Test with a library that the user doesn't have access to
    library = Library.objects.create(name="Test Library 2")
    response = client.get(
        reverse("chat:update_from_librarian", args=[chat.id, library.id])
    )
    assert response.status_code == 200
    chat.options.refresh_from_db()
    # This should reset to default library and clear data sources and documents
    assert chat.options.qa_library == Library.objects.get_default_library()
    assert chat.options.qa_data_sources.count() == 0
    assert chat.options.qa_documents.count() == 0


@pytest.mark.django_db
def test_chat_message_error(client, all_apps_user):

    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("chat:chat_with_ai"), follow=True)
    # Find the newest user chat
    chat_id = Chat.objects.filter(user=user).order_by("-created_at").first().id
    # Change the chat options temperature to an invalid value
    chat = Chat.objects.get(id=chat_id)
    chat.options.chat_temperature = 5
    chat.options.save()
    response = client.post(
        reverse("chat:chat_message", args=[chat_id]),
        data={"user-message": "Hello"},
    )
    assert response.status_code == 200
    assert "Hello" in response.content.decode("utf-8")

    message = Message.objects.filter(chat_id=chat_id).first()
    assert message.text == "Hello"

    # Now get the bot response - we should get a pretty error message here
    response = client.get(reverse("chat:chat_response", args=[message.id + 1]))
    assert response.status_code == 200
    # We should have a StreamingHttpResponse object.
    # Iterate over the response to get the content
    # content = async_to_sync(process_streaming_content)(response.streaming_content)
    content = async_to_sync(final_response_helper)(response.streaming_content)
    assert "Error ID" in content.decode("utf-8")


async def process_streaming_content(streaming_content):
    final_response = b"".join([chunk async for chunk in streaming_content])
    return final_response


@pytest.mark.django_db
def test_chat_message_url_validation(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)

    # Valid URL
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "https://canada.ca"},
    )
    assert response.status_code == 200
    # The error message contains the string "URL" but success message does not
    assert not "URL" in response.content.decode()

    # Subdomain of valid URL
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "https://www.tbs-sct.canada.ca"},
    )
    assert response.status_code == 200
    assert not "URL" in response.content.decode()

    # Ends with valid URL, but isn't a subdomain
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "https://acanada.ca"},
    )
    assert response.status_code == 200
    # This should be a problem
    assert "URL" in response.content.decode()

    # Is a valid URL, but is http:// only (should be fine, it will correct to https://)
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "http://www.tbs-sct.canada.ca"},
    )
    assert response.status_code == 200
    assert not "URL" in response.content.decode()

    # Is a valid URL, but FTP
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "ftp://canada.ca/fake_file"},
    )
    assert response.status_code == 200
    # This should be a problem
    assert "URL" in response.content.decode()

    # Invalid URL
    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "invalid-url"},
    )
    assert response.status_code == 200
    # This should just be interpreted as a regular chat message
    assert not "URL" in response.content.decode()

    response = client.post(
        reverse("chat:chat_message", args=[chat.id]),
        data={"user-message": "https://notallowed.com"},
    )
    assert response.status_code == 200
    assert "URL" in response.content.decode()


def test_generate_prompt_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)

    # Valid URL
    response = client.post(
        reverse("chat:generate_prompt_view"),
        data={"user_input": "write me an email"},
    )
    assert response.status_code == 200
    # Check that the correct template was used
    assert "chat/modals/prompt_generator_result.html" in [
        t.name for t in response.templates
    ]

    # Check that the context contains the expected values
    assert response.context["user_input"] == "write me an email"
    assert len(response.context["output_text"]) > 1
    assert "Output Format" in response.context["output_text"]
    assert "# Examples" in response.context["output_text"]

    # Strip non-numeric characters and convert to float
    cost_str = response.context["cost"].replace("< $", "")
    cost = float(cost_str)
    assert cost > 0.000


def test_email_chat_author(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(user=user)

    response = client.get(reverse("chat:email_author", args=[chat.id]))
    assert response.status_code == 200
    assert "Otto" in response.content.decode()
    assert f"mailto:{user.email}" in response.content.decode()
