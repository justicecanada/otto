from decimal import Decimal

from structlog import get_logger

from chat_next.models import LOCAL_TOOL_CATEGORIES

# ============================================================================
# Helper functions for tool categories
# ============================================================================

logger = get_logger(__name__)

# Approximate characters per LLM token (used to convert text length to token counts)
EST_CHARS_PER_TOKEN = 4

# Tokens per vector DB chunk (LlamaIndex default chunk size)
TOKENS_PER_CHUNK = 768

# Estimated output tokens per document in batch processing (conservative default)
ESTIMATED_OUTPUT_TOKENS = 1024


def _get_model_id(chat) -> str | None:
    """Return the chat model ID from chat settings, or None if unavailable."""
    return getattr(getattr(chat, "settings", None), "chat_model", None)


def _estimate_tokens_from_text(text: str) -> int:
    """Estimate token count based on character count."""
    return len(text) // EST_CHARS_PER_TOKEN


def _calculate_cost_for_units(cost_type_name: str, unit_count: int | float) -> Decimal:
    """Calculate cost for a given number of units and cost type short name.

    Returns Decimal("0") and logs a warning if the CostType is not found,
    rather than raising an exception.
    """
    from otto.models import CostType

    try:
        cost_type = CostType.objects.get(short_name=cost_type_name)
    except CostType.DoesNotExist:
        logger.warning(
            "missing_cost_type",
            cost_type_name=cost_type_name,
            unit_count=unit_count,
        )
        return Decimal("0")

    return (unit_count * cost_type.unit_cost) / cost_type.unit_quantity


def is_local_tool_category(tool_type: str) -> bool:
    """Check if a tool type is a local function tool category."""
    return tool_type in LOCAL_TOOL_CATEGORIES


def get_enabled_local_categories(enabled_tools: list[str]) -> list[str]:
    """Extract local tool categories from a list of enabled tools."""
    return [t for t in enabled_tools if t in LOCAL_TOOL_CATEGORIES]


def has_local_tools_enabled(enabled_tools: list[str]) -> bool:
    """Check if any local function tools are enabled."""
    return any(t in LOCAL_TOOL_CATEGORIES for t in enabled_tools)


def get_local_tool_names() -> list[str]:
    """Get list of all registered local tool names."""
    from chat_next._tools.base import TOOL_REGISTRY

    return list(TOOL_REGISTRY.tools.keys())
