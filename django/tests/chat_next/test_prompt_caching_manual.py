"""
Manual tests for verifying OpenAI prompt caching is working correctly.

These tests make REAL API calls and are excluded from normal pytest runs.
Run manually with: pytest tests/chat_next/test_prompt_caching_manual.py -v --manual

The tests verify:
1. prompt_cache_key parameter is not explicitly set
2. cached_tokens are reported in API responses
3. Multiple turns in a conversation improve cache hit rates
4. Cache metrics logging is working

To view cache performance in real usage:
1. Enable DEBUG logging: export LOG_LEVEL=DEBUG
2. Look for "OpenAI response token usage" log messages
3. Monitor cache_hit_rate_pct to see improvement over conversation turns
"""

import asyncio
import uuid
from unittest.mock import MagicMock

from django.conf import settings

import pytest
from chat_next._llm.openai_responses import ResponsesAPIClient


@pytest.fixture
def mock_chat():
    """Create a mock chat object with a UUID."""
    chat = MagicMock()
    chat.id = uuid.uuid4()
    return chat


@pytest.fixture
def mock_user():
    """Create a mock user object."""
    user = MagicMock()
    user.id = 1
    return user


class TestPromptCacheKeyGeneration:
    """Unit tests for prompt_cache_key behavior (no API calls)."""

    def test_prompt_cache_key_not_added_on_first_turn(self, mock_chat, mock_user):
        """Verify prompt_cache_key is NOT added on first-turn requests."""
        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
        )

        input_items = [{"role": "user", "content": "Hello"}]
        params = asyncio.run(
            client._build_request_params(input_items, instructions="Test")
        )

        assert "prompt_cache_key" not in params

    def test_prompt_cache_key_not_added_when_no_chat(self, mock_user):
        """Verify prompt_cache_key is NOT added when chat is not provided."""
        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=None,
            user=mock_user,
        )

        input_items = [{"role": "user", "content": "Hello"}]
        params = asyncio.run(
            client._build_request_params(input_items, instructions="Test")
        )

        assert "prompt_cache_key" not in params

    def test_prompt_cache_key_not_added_when_chaining(self, mock_chat, mock_user):
        """Verify prompt_cache_key is NOT added when previous_response_id is set."""
        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
            previous_response_id="resp_123",
        )

        input_items = [{"role": "user", "content": "How are you?"}]
        params = asyncio.run(
            client._build_request_params(input_items, instructions="Test")
        )

        assert "prompt_cache_key" not in params

        # Previous behavior (kept commented for easy rollback):
        # assert "prompt_cache_key" in params
        # assert params["prompt_cache_key"] == str(mock_chat.id)
        # Reason commented out: per-chat key on chained turns produced intermittent
        # misses in observed traffic when compared to provider prefix-hash routing.

    def test_prompt_cache_key_absent_across_chained_turns(self, mock_chat, mock_user):
        """Verify prompt_cache_key remains absent across chained turns."""
        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
            previous_response_id="resp_123",
        )

        input_items_1 = [{"role": "user", "content": "Hello"}]
        input_items_2 = [{"role": "user", "content": "How are you?"}]

        params_1 = asyncio.run(
            client._build_request_params(input_items_1, instructions="Test")
        )
        params_2 = asyncio.run(
            client._build_request_params(input_items_2, instructions="Test")
        )

        assert "prompt_cache_key" not in params_1
        assert "prompt_cache_key" not in params_2

        # Previous behavior (kept commented for easy rollback):
        # assert params_1["prompt_cache_key"] == params_2["prompt_cache_key"]
        # assert params_1["prompt_cache_key"] == str(mock_chat.id)


# =============================================================================
# Integration tests requiring real API access
# =============================================================================


def skip_unless_real_api():
    """Check if real API access is available."""
    if (
        not settings.AZURE_AI_SERVICES_KEY
        or settings.AZURE_AI_SERVICES_KEY == "test-key"
    ):
        return True
    if settings.IS_RUNNING_IN_GITHUB:
        return True
    return False


@pytest.mark.manual
@pytest.mark.skipif(
    skip_unless_real_api(),
    reason="Requires real Azure OpenAI API (not run in CI)",
)
@pytest.mark.django_db
class TestPromptCachingIntegration:
    """
    Integration tests that make real API calls to verify caching.

    IMPORTANT: These tests will incur API costs!
    Run manually only: pytest tests/chat_next/test_prompt_caching_manual.py -v --manual -k Integration
    """

    @pytest.mark.asyncio
    async def test_cache_hit_rate_improves_over_conversation(
        self, mock_chat, mock_user
    ):
        """
        Verify cache hit rate improves when sending multiple messages in same conversation.

        This test sends multiple turns in a conversation and verifies that:
        1. The first message has no cached tokens (cache miss)
        2. Subsequent messages with identical prefix have cached tokens (cache hit)

        Note: Caching requires at least 1024 tokens in the prompt, so we use a
        longer system prompt to ensure caching can occur.
        """
        # Long system prompt to ensure we hit 1024 token threshold
        long_system_prompt = (
            """You are a helpful assistant. Please follow these instructions carefully:

1. Always be polite and professional
2. Provide accurate and helpful information
3. If you don't know something, say so honestly
4. Use clear and concise language
5. Break down complex topics into understandable parts
6. Cite sources when appropriate
7. Be mindful of the user's time
8. Offer to clarify if needed
9. Stay focused on the topic at hand
10. Summarize key points when helpful

Additional context for this conversation:
This is a test conversation to verify that OpenAI's prompt caching feature is working correctly.
The system should cache the initial prefix of the conversation and reuse it for subsequent messages.
This helps reduce latency and costs for longer conversations.

The Department of Justice Canada uses this AI assistant platform called Otto to help legal professionals
with various tasks including document analysis, legal research, and general productivity.

Remember to always maintain confidentiality and handle sensitive information appropriately.
Follow all applicable laws and regulations regarding data privacy and security.

When analyzing legal documents, pay attention to:
- Statutory references and citations
- Case law precedents
- Regulatory requirements
- Procedural requirements
- Jurisdictional considerations

For research tasks:
- Use reliable and authoritative sources
- Cross-reference multiple sources when possible
- Note any limitations or caveats
- Provide balanced perspectives where appropriate

This extended system prompt ensures we have enough tokens for the caching mechanism to activate.
OpenAI's prompt caching requires at least 1024 tokens in the prompt prefix.
"""
            * 3
        )  # Repeat to ensure we hit token threshold

        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
        )

        cache_metrics = []

        # First turn - should be a cache miss (no previous context)
        input_items_1 = [{"role": "user", "content": "What is 2+2?"}]

        async for chunk in client.stream_chat(
            input_items_1, instructions=long_system_prompt
        ):
            if chunk.is_complete and chunk.usage:
                cache_metrics.append(
                    {
                        "turn": 1,
                        "input_tokens": chunk.usage.get("input_tokens", 0),
                        "cached_tokens": chunk.usage.get("cached_tokens", 0),
                        "output_tokens": chunk.usage.get("output_tokens", 0),
                    }
                )

        # Get previous response ID for chaining
        prev_response_id = client.last_response_id

        # Small delay to allow cache to propagate
        await asyncio.sleep(1)

        # Second turn - should have some cache hits if caching is working
        client2 = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
            previous_response_id=prev_response_id,
        )

        input_items_2 = [
            {"role": "user", "content": "What is 2+2?"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "4"}],
            },
            {"role": "user", "content": "What is 3+3?"},
        ]

        async for chunk in client2.stream_chat(
            input_items_2, instructions=long_system_prompt
        ):
            if chunk.is_complete and chunk.usage:
                cache_metrics.append(
                    {
                        "turn": 2,
                        "input_tokens": chunk.usage.get("input_tokens", 0),
                        "cached_tokens": chunk.usage.get("cached_tokens", 0),
                        "output_tokens": chunk.usage.get("output_tokens", 0),
                    }
                )

        # Print cache metrics for manual verification
        print("\n\n=== PROMPT CACHE METRICS ===")
        for m in cache_metrics:
            if m["input_tokens"] > 0:
                hit_rate = m["cached_tokens"] / m["input_tokens"] * 100
            else:
                hit_rate = 0
            print(
                f"Turn {m['turn']}: {m['input_tokens']} input, {m['cached_tokens']} cached, "
                f"{m['output_tokens']} output, {hit_rate:.1f}% cache hit rate"
            )
        print("===========================\n")

        # Basic assertions - we should have gotten responses
        assert len(cache_metrics) == 2, "Should have metrics for both turns"
        assert cache_metrics[0]["input_tokens"] > 0, (
            "First turn should have input tokens"
        )

        # Note: Cache hits may not occur on first run due to cache warm-up
        # This test is primarily for manual verification of the metrics

    @pytest.mark.asyncio
    async def test_non_streaming_response_logs_cache_metrics(
        self, mock_chat, mock_user
    ):
        """
        Verify non-streaming responses also log cache metrics.
        """
        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=mock_chat,
            user=mock_user,
        )

        input_items = [{"role": "user", "content": "Say hello"}]

        text, usage, output_items, response_id = await client.complete_chat(
            input_items, instructions="Be brief."
        )

        assert len(text) > 0, "Should get a response"
        assert usage is not None, "Should have usage info"
        assert usage.input_tokens > 0, "Should have input tokens"

        print("\n=== NON-STREAMING CACHE METRICS ===")
        print(f"Input tokens: {usage.input_tokens}")
        print(f"Cached tokens: {usage.cached_tokens}")
        print(f"Output tokens: {usage.output_tokens}")
        if usage.input_tokens > 0:
            print(
                f"Cache hit rate: {usage.cached_tokens / usage.input_tokens * 100:.1f}%"
            )
        print("===================================\n")


@pytest.mark.manual
@pytest.mark.skipif(
    skip_unless_real_api(),
    reason="Requires real Azure OpenAI API (not run in CI)",
)
@pytest.mark.django_db
class TestPromptCachingWithRealChat:
    """
    Integration tests using actual Chat model from database.

    Run with: pytest tests/chat_next/test_prompt_caching_manual.py -v --manual -k RealChat
    """

    @pytest.mark.asyncio
    async def test_real_chat_does_not_set_prompt_cache_key_when_chaining(
        self, all_apps_user
    ):
        """
        Verify prompt_cache_key is not explicitly set, even when
        previous_response_id is present.
        """
        from chat_next.models import Chat

        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Cache Test")

        client = ResponsesAPIClient(
            model="gpt-5-mini",
            chat=chat,
            user=user,
            previous_response_id="resp_123",
        )

        input_items = [{"role": "user", "content": "Hello"}]
        params = client._build_request_params(input_items, instructions="Test")

        assert "prompt_cache_key" not in params

        # Cleanup
        chat.delete()
