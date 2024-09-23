import json
import tempfile

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import pytest
from asgiref.sync import sync_to_async

from chat.llm import OttoLLM
from chat.models import Chat, ChatFile, Message
from chat.utils import htmx_stream, title_chat
from librarian.models import Library
from otto.models import App, Notification, SecurityLabel

pytest_plugins = ("pytest_asyncio",)
skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)


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
    assert response.url == reverse("accept_terms") + "?next=" + reverse("chat:new_chat")

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

    response = client.get(reverse("chat:chat_response", args=[message.id + 1]))
    assert response.status_code == 200


# TODO: Test Celery tasks
@skip_on_github_actions
@skip_on_devops_pipeline
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

        # TODO: Test the Celery task
        # Need to research best practices. See https://docs.celeryq.dev/en/main/userguide/testing.html

        # assert translated_file.name == "test_file_FR.txt"
        # assert translated_file.content_type == "text/plain"
        # assert translated_file.eof == 1
        # # The translated file should contain the translation of "Hello" to French
        # with open(translated_file.file.path, "r") as file:
        #     assert "Bonjour" in file.read()
        # # Check that the translated file was saved
        # assert ChatFile.objects.count() == chatfile_count + 1
        # assert ChatFile.objects.filter(message_id=out_message.id).count() == 1
        # assert out_message.sorted_files.count() == 1


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
        format=False,
        llm=llm,
    )
    # Iterate over the response_stream generator
    final_output = ""
    first = True
    async for yielded_output in response_stream:
        if first:
            assert "first thing" in yielded_output
            # Stop the stream by requesting chat:stop_response
            response = await sync_to_async(client.get)(
                reverse("chat:stop_response", args=[response_message.id])
            )
            first = False
            assert response.status_code == 200
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

    # Call the delete_all_chats view
    response = client.get(reverse("chat:delete_all_chats"))

    # Check that all chats are deleted
    assert response.status_code == 200
    assert Chat.objects.filter(user=user).count() == 0

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
# These require additional setup / authentications and won't run on GitHub
@skip_on_github_actions
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
def test_api_qa(client, all_apps_user, settings):

    # For some reason, the rule isn't getting loaded automatically in this test!
    # So I have to add the permission manually. TODO: Fix this...
    from rules import add_perm

    from otto.rules import can_access_app

    add_perm("otto:access_app", can_access_app)

    # Create a test user
    user = all_apps_user("api_user")
    client.force_login(user)

    # Define the endpoint
    url = reverse("chat:api_qa")

    # Define the request payload
    payload = {
        "upn": "api_user_upn",
        "library": "Corporate",
        "data_sources": ["Pay and Benefits"],
        "user_message": "What are my leave entitlements?",
    }

    # Override the token for test
    settings.OTTO_VERIFICATION_TOKEN = "test-token"

    # Define the headers
    headers = {
        "HTTP_X_VERIFICATION_TOKEN": settings.OTTO_VERIFICATION_TOKEN,
    }

    # Make the API call with the JSON payload
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
        follow=True,
    )

    print(response.content.decode("utf-8"))

    # Check the response status
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["status"] == "success"
    assert "redirect_url" in response_data

    # Check if the chat and messages were created correctly
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    library = Library.objects.get(name="Corporate")
    assert chat is not None
    assert chat.options.mode == "qa"
    assert chat.options.qa_library == library

    user_message = Message.objects.filter(chat=chat, is_bot=False).first()
    assert user_message is not None
    assert user_message.text == payload["user_message"]

    bot_message = Message.objects.filter(chat=chat, is_bot=True).first()
    assert bot_message is not None
    assert bot_message.parent == user_message

    # Test with missing verification token
    headers.pop("HTTP_X_VERIFICATION_TOKEN")
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
        follow=True,
    )
    assert response.status_code == 400
    response_data = response.json()
    assert response_data["error_code"] == "MISSING_TOKEN"

    # Test with invalid verification token
    headers["HTTP_X_VERIFICATION_TOKEN"] = "invalid-token"
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
        follow=True,
    )
    assert response.status_code == 403
    response_data = response.json()
    assert response_data["error_code"] == "INVALID_TOKEN"

    # Test with invalid user
    payload["upn"] = "invalid_user"
    headers["HTTP_X_VERIFICATION_TOKEN"] = settings.OTTO_VERIFICATION_TOKEN
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
        follow=True,
    )
    assert response.status_code == 401
    response_data = response.json()
    assert response_data["error_code"] == "USER_NOT_FOUND"

    # Test with invalid library
    payload["upn"] = "api_user_upn"
    payload["library"] = "Invalid Library"
    response = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
        follow=True,
    )
    assert response.status_code == 404
    response_data = response.json()
    assert response_data["error_code"] == "LIBRARY_NOT_FOUND"


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
    response = client.get(
        reverse("chat:chat_response", args=[Message.objects.last().id])
    )
    assert response.status_code == 200
    chat_options.qa_scope = "data_sources"
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
    assert (
        response.status_code == 200
        and "Provide feedback" in response.content.decode("utf-8")
    )


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
