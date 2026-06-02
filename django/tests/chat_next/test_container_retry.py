from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_next._llm.openai_responses import ResponsesAPIClient
from openai import BadRequestError


@pytest.mark.asyncio
async def test_stream_chat_retries_on_container_error(mocker):
    # Mock settings
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    # Mock get_model to return a config with deployment name
    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    # Initialize client with a container ID
    client = ResponsesAPIClient(
        model="gpt-5.1",
        tools=["code_interpreter"],
        code_interpreter_container_id="expired-container-id",
    )

    # Verify client was initialized with container ID
    assert client.code_interpreter_container_id == "expired-container-id"

    # Mock the internal _stream_chat_impl to just yield chunks (successful behavior)
    # We want to test the wrapper logic in stream_chat

    # But wait, stream_chat calls _stream_chat_impl which calls _build_request_params.
    # We should let it run, but mock the networking call inside _stream_chat_impl.
    # Ah, I mocked the whole _stream_chat_impl logic inside stream_chat??
    # No, I wrapped it. stream_chat calls _stream_chat_impl.
    # The actual network call happens inside _stream_chat_impl (which calls _build_request_params and then client.responses.stream).

    # To test the RETRY logic in stream_chat, we need _stream_chat_impl to raise BadRequestError first time.

    # We can mock _stream_chat_impl directly on the instance

    # Helper async generator that raises on first call, yields on second
    call_count = 0

    async def mock_stream_impl(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: simulate expired container error
            err_response = MagicMock()
            err_response.status_code = 400
            # Azure often returns "Invalid container" or similar in message
            raise BadRequestError(
                message="Invalid container ID: expired-container-id",
                response=err_response,
                body={"error": {"code": "InvalidContainer"}},
            )
        else:
            # Second call: succeed
            yield MagicMock(text="Success")

    # Patch the method on the instance
    client._stream_chat_impl = mock_stream_impl

    # Execute stream_chat
    chunks = []
    async for chunk in client.stream_chat(input_items=[]):
        chunks.append(chunk)

    # Validations
    assert call_count == 2
    assert len(chunks) == 1
    assert chunks[0].text == "Success"
    # The container ID should have been cleared
    assert client.code_interpreter_container_id is None


@pytest.mark.asyncio
async def test_complete_chat_retries_on_container_error(mocker):
    # Mock settings etc same as above
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_KEY", "fake-key")
    mocker.patch(
        "django.conf.settings.AZURE_AI_SERVICES_ENDPOINT", "https://fake.azure.com"
    )
    mocker.patch("django.conf.settings.AZURE_AI_SERVICES_VERSION", "2025-03-01-preview")

    mock_model_config = MagicMock()
    mock_model_config.deployment_name = "test-deployment"
    mocker.patch(
        "chat_next._llm.openai_responses.get_model", return_value=mock_model_config
    )

    client = ResponsesAPIClient(
        model="gpt-5.1",
        tools=["code_interpreter"],
        code_interpreter_container_id="expired-container-id",
    )

    # Mock the responses.create call
    mock_create = AsyncMock()
    client._client.responses.create = mock_create

    # Setup side effect
    err_response = MagicMock()
    err_response.status_code = 400
    bad_req_err = BadRequestError(
        message="The container expired-container-id no longer exists",
        response=err_response,
        body={"error": {"code": "InvalidContainer"}},
    )

    success_response = MagicMock()
    success_response.output = []
    success_response.usage = None
    success_response.id = "new-resp-id"

    # raise error first time, return response second time
    mock_create.side_effect = [bad_req_err, success_response]

    # Execute complete_chat
    await client.complete_chat(input_items=[])

    # Validations
    assert mock_create.call_count == 2
    assert client.code_interpreter_container_id is None
