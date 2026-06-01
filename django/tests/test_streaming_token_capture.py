"""Test that streaming responses capture token counts correctly."""

from django.conf import settings

import pytest
from llama_index.core.llms import ChatMessage, MessageRole

from chat.llm import OttoLLM


@pytest.mark.skipif(
    not settings.AZURE_AI_SERVICES_KEY
    or settings.AZURE_AI_SERVICES_KEY == "test-key"
    or settings.IS_RUNNING_IN_GITHUB,
    reason="Requires real Azure OpenAI API with direct endpoint",
)
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_streaming_captures_tokens():
    """Verify that streaming responses capture token counts from API."""
    llm = OttoLLM(deployment="gpt-5-mini")
    llm._token_counter.reset_counts()

    chat_history = [ChatMessage(role=MessageRole.USER, content="What is 5+3?")]

    # Stream the response
    response_text = ""
    async for chunk in llm.chat_stream(chat_history):
        # Reasoning models return dicts with 'text' key
        if isinstance(chunk, dict):
            response_text = chunk.get("text", "")
        else:
            response_text += chunk

    # Verify we got a response
    assert len(response_text) > 0
    assert "8" in response_text

    # Verify tokens were captured
    assert llm._token_counter.prompt_llm_token_count > 0, (
        "Input tokens should be captured"
    )
    assert llm._token_counter.completion_llm_token_count > 0, (
        "Output tokens should be captured"
    )


@pytest.mark.skipif(
    not settings.AZURE_AI_SERVICES_KEY
    or settings.AZURE_AI_SERVICES_KEY == "test-key"
    or settings.IS_RUNNING_IN_GITHUB,
    reason="Requires real Azure OpenAI API with direct endpoint",
)
@pytest.mark.skip(
    reason="Reasoning models may not always return reasoning tokens depending on the query"
)
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_streaming_captures_reasoning_tokens():
    """Verify that reasoning tokens are captured when present."""
    llm = OttoLLM(deployment="o4-mini", temperature=0.0)
    llm._token_counter.reset_counts()

    # Use a query that should trigger reasoning
    chat_history = [
        ChatMessage(
            role=MessageRole.USER,
            content="Calculate the 10th fibonacci number step by step",
        )
    ]

    # Stream the response
    response_text = ""
    async for chunk in llm.chat_stream(chat_history):
        # Reasoning models return dicts with 'text' and 'thinking' keys
        if isinstance(chunk, dict):
            response_text = chunk.get("text", "")
            chunk.get("thinking", "")
        else:
            response_text += chunk

    # Verify we got a response
    assert len(response_text) > 0

    # Verify tokens were captured (reasoning tokens may or may not be present depending on the query)
    assert llm._token_counter.prompt_llm_token_count > 0, (
        "Input tokens should be captured"
    )
    assert llm._token_counter.completion_llm_token_count > 0, (
        "Output tokens should be captured"
    )
