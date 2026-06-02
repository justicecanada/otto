from django.utils.translation import gettext as _

from chat_next._llm import get_model

CONTEXT_WINDOW_ERROR_CODES = {
    "context_length_exceeded",
    "maximum_context_length_exceeded",
    "input_too_long",
}

CONTEXT_WINDOW_ERROR_FRAGMENTS = (
    "context length",
    "context window",
    "too many tokens",
    "too much context",
    "reduce the length",
    "maximum context length",
    "maximum input length",
    "input exceeds the context window",
)


def _get_response_model_id(chat, response_message) -> str:
    model_overrides = (getattr(response_message, "details", None) or {}).get(
        "model_overrides"
    ) or {}
    return model_overrides.get("chat_model") or chat.settings.chat_model


def extract_api_error_info(error_message) -> tuple[str, str]:
    """Return normalized (code, message) details from an API exception."""
    body = getattr(error_message, "body", None)
    error_payload = body.get("error", {}) if isinstance(body, dict) else {}
    error_code = str(error_payload.get("code") or "").strip().lower()
    payload_message = str(error_payload.get("message") or "").strip().lower()
    fallback_message = str(error_message or "").strip().lower()
    return error_code, payload_message or fallback_message


def is_context_window_error(error_message) -> bool:
    """Return True when the exception looks like a context-window overflow."""
    error_code, error_text = extract_api_error_info(error_message)
    if error_code in CONTEXT_WINDOW_ERROR_CODES:
        return True
    return any(fragment in error_text for fragment in CONTEXT_WINDOW_ERROR_FRAGMENTS)


def build_context_window_error_message(chat, response_message, error_id: str) -> str:
    """Build a deterministic user-facing message for context-window overflows."""
    current_model = get_model(_get_response_model_id(chat, response_message)).model_id
    context_management = getattr(chat.settings, "chat_context_management", "compact")

    suggestions = []
    if current_model != "gpt-5.4":
        suggestions.append(
            _("Switch to GPT-5.4 for the largest available context window.")
        )

    if context_management not in {"compact", "truncate"}:
        suggestions.append(
            _(
                'In `Settings > Advanced`, set "Context window management" to "Compact" or "Truncate".'
            )
        )

    if not suggestions:
        suggestions.append(
            _("Start a new chat if you no longer need the earlier history.")
        )

    suggestion_lines = "\n".join(f"- {suggestion}" for suggestion in suggestions)

    response_str = _(
        "**Error:** This chat exceeded the context window for `%(model_id)s`.\n\n"
        "**What to do next:**\n%(suggestions)s"
    ) % {
        "model_id": current_model,
        "suggestions": suggestion_lines,
    }
    error_id_label = _("Error ID:")
    return f"{response_str}\n\n_({error_id_label} {error_id})_"
