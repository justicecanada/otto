from __future__ import annotations

import re
from typing import Any

from django.conf import settings

import requests
from structlog import get_logger

from chat_next._tools.approval import parse_function_call_arguments
from chat_next._tools.approval_review import (
    get_sanitized_tool_review_arguments,
    has_custom_tool_review_arguments,
)

logger = get_logger(__name__)

_MAX_REVIEW_TEXT_CHARS = 5000
_LARGE_PAYLOAD_THRESHOLD_CHARS = 500
_AZURE_LANGUAGE_TIMEOUT_SECONDS = 10
_DEFAULT_FLAGGED_AZURE_PII_CATEGORIES: tuple[str, ...] = (
    "Address",
    "Email",
    "IPAddress",
    "NumericIdentifier",
    "Organization",
    "Person",
    "PhoneNumber",
)

_LOCAL_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "PhoneNumber",
        re.compile(
            r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\w)"
        ),
    ),
    (
        "SocialInsuranceNumber",
        re.compile(r"\b\d{3}[- ]?\d{3}[- ]?\d{3}\b"),
    ),
)

_SENSITIVE_MARKERS: tuple[
    tuple[str, tuple[re.Pattern[str], ...], str],
    ...,
] = (
    (
        "privileged_or_classified",
        (
            re.compile(r"\bsolicitor[- ]client\b", re.IGNORECASE),
            re.compile(r"\battorney[- ]client\b", re.IGNORECASE),
            re.compile(r"\blegal privilege\b", re.IGNORECASE),
            re.compile(r"\bprivileged and confidential\b", re.IGNORECASE),
            re.compile(r"\bcabinet confidence\b", re.IGNORECASE),
            re.compile(r"\bconfidences? du cabinet\b", re.IGNORECASE),
            re.compile(r"\bprotected\s*[ABC]\b", re.IGNORECASE),
            re.compile(r"\bclassified\b", re.IGNORECASE),
            re.compile(r"\btop secret\b", re.IGNORECASE),
        ),
        "Contains terms associated with privileged, Cabinet-confidence, or classified information.",
    ),
    (
        "credentials_or_secrets",
        (
            re.compile(r"\bapi key\b", re.IGNORECASE),
            re.compile(r"\baccess token\b", re.IGNORECASE),
            re.compile(r"\brefresh token\b", re.IGNORECASE),
            re.compile(r"\bbearer token\b", re.IGNORECASE),
            re.compile(r"\bclient secret\b", re.IGNORECASE),
            re.compile(r"\bconnection string\b", re.IGNORECASE),
            re.compile(r"\bsas token\b", re.IGNORECASE),
            re.compile(r"\bshared access signature\b", re.IGNORECASE),
            re.compile(r"\bpassword\b", re.IGNORECASE),
        ),
        "Contains terms that look like credentials or secrets.",
    ),
)


def _normalize_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    return endpoint if endpoint.endswith("/") else f"{endpoint}/"


def _append_summary_item(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _append_unique_strings(items: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in items:
            items.append(value)


def _normalized_categories(values: list[str] | tuple[str, ...] | None) -> list[str]:
    categories: list[str] = []
    for value in values or []:
        category_text = str(value).strip()
        if category_text and category_text not in categories:
            categories.append(category_text)
    return categories


def _collect_text_fragments(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []

    if isinstance(value, dict):
        for key, item in value.items():
            key_prefix = f"{prefix}.{key}" if prefix else str(key)
            fragments.extend(_collect_text_fragments(item, key_prefix))
        return fragments

    if isinstance(value, list):
        for index, item in enumerate(value):
            list_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            fragments.extend(_collect_text_fragments(item, list_prefix))
        return fragments

    if isinstance(value, str):
        text = value.strip()
        if text:
            fragments.append((prefix or "value", text))

    return fragments


def _build_review_text(fragments: list[tuple[str, str]]) -> str:
    review_text = "\n".join(
        f"{label}: {text}" if label else text for label, text in fragments
    )
    return review_text[:_MAX_REVIEW_TEXT_CHARS]


def _get_review_policy() -> dict[str, Any]:
    policy = {
        "flag_large_payloads": True,
        "flag_local_pii": True,
        "flag_credentials_or_secrets": True,
        "flag_privileged_or_classified": False,
        "flagged_azure_pii_categories": list(_DEFAULT_FLAGGED_AZURE_PII_CATEGORIES),
    }

    try:
        from otto.models import OttoStatus

        status = OttoStatus.objects.singleton()
    except Exception:
        return policy

    configured_categories = _normalized_categories(
        getattr(status, "external_tool_review_flagged_azure_pii_categories", None)
    )
    if configured_categories:
        policy["flagged_azure_pii_categories"] = configured_categories

    for attribute_name, policy_key in (
        ("external_tool_review_flag_large_payloads", "flag_large_payloads"),
        ("external_tool_review_flag_local_pii", "flag_local_pii"),
        (
            "external_tool_review_flag_credentials_or_secrets",
            "flag_credentials_or_secrets",
        ),
        (
            "external_tool_review_flag_privileged_or_classified",
            "flag_privileged_or_classified",
        ),
    ):
        attribute_value = getattr(status, attribute_name, None)
        if isinstance(attribute_value, bool):
            policy[policy_key] = attribute_value

    return policy


def _run_local_marker_review(review_text: str) -> tuple[list[str], list[str]]:
    matched_marker_ids: list[str] = []
    summary_items: list[str] = []

    if not review_text:
        return matched_marker_ids, summary_items

    for marker_id, patterns, summary_text in _SENSITIVE_MARKERS:
        if any(pattern.search(review_text) for pattern in patterns):
            matched_marker_ids.append(marker_id)
            _append_summary_item(summary_items, summary_text)

    return matched_marker_ids, summary_items


def _run_local_pii_review(review_text: str) -> list[str]:
    categories: list[str] = []
    if not review_text:
        return categories

    for category, pattern in _LOCAL_PII_PATTERNS:
        if pattern.search(review_text) and category not in categories:
            categories.append(category)

    return categories


def _run_azure_language_pii_review(review_text: str) -> dict | None:
    if not review_text:
        return None

    if not getattr(settings, "EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENABLED", False):
        return None

    endpoint = _normalize_endpoint(
        getattr(settings, "EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENDPOINT", None)
        or getattr(settings, "AZURE_AI_SERVICES_ENDPOINT", None)
    )
    api_key = getattr(settings, "AZURE_AI_SERVICES_KEY", None)
    api_version = getattr(
        settings,
        "EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_API_VERSION",
        "2022-05-01",
    )

    if not endpoint or not api_key:
        return None

    url = f"{endpoint}language/:analyze-text?api-version={api_version}"
    payload = {
        "kind": "PiiEntityRecognition",
        "parameters": {"modelVersion": "latest"},
        "analysisInput": {
            "documents": [
                {
                    "id": "1",
                    "text": review_text,
                }
            ]
        },
    }
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=_AZURE_LANGUAGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json() or {}
    except requests.RequestException:
        logger.warning("Azure Language PII review failed", endpoint=url, exc_info=True)
        return None

    documents = (payload.get("results") or {}).get("documents") or []
    if not documents:
        errors = (payload.get("results") or {}).get("errors") or []
        if errors:
            logger.warning(
                "Azure Language PII review returned document errors", errors=errors
            )
        return {
            "pii_flagged": False,
            "pii_entity_categories": [],
            "redacted_text": "",
        }

    document = documents[0] or {}
    entities = document.get("entities") or []
    categories = sorted(
        {entity.get("category") for entity in entities if entity.get("category")}
    )
    redacted_text = str(document.get("redactedText") or "")[:500]

    return {
        "pii_flagged": bool(categories),
        "pii_entity_categories": categories,
        "redacted_text": redacted_text,
    }


def review_external_tool_call(*, function_call: dict, tool=None) -> dict:
    tool_name = getattr(tool, "name", None) or (function_call or {}).get("name")
    arguments = parse_function_call_arguments(function_call or {})
    raw_arguments = (function_call or {}).get("arguments")
    review_policy = _get_review_policy()
    sanitized_arguments = get_sanitized_tool_review_arguments(
        tool_name, arguments or {}
    )
    fragments = _collect_text_fragments(sanitized_arguments)

    if (
        not fragments
        and not has_custom_tool_review_arguments(tool_name)
        and isinstance(raw_arguments, str)
    ):
        raw_text = raw_arguments.strip()
        if raw_text and raw_text != "{}":
            fragments = [("arguments", raw_text)]

    review_text = _build_review_text(fragments)
    summary_items: list[str] = []
    review_sources: list[str] = []
    pii_entity_categories: list[str] = []
    detected_pii_entity_categories: list[str] = []

    largest_fragment_chars = max((len(text) for _, text in fragments), default=0)
    matched_marker_ids, marker_summary_items = _run_local_marker_review(review_text)
    local_pii_categories = _run_local_pii_review(review_text)
    flagged_marker_ids: list[str] = []

    if largest_fragment_chars >= _LARGE_PAYLOAD_THRESHOLD_CHARS:
        if review_policy["flag_large_payloads"]:
            _append_summary_item(
                summary_items,
                "Large outbound text payload; confirm the request is sanitized and minimal.",
            )
            _append_summary_item(review_sources, "heuristic")

    if marker_summary_items:
        marker_summary_by_id = dict(zip(matched_marker_ids, marker_summary_items))
        if (
            review_policy["flag_credentials_or_secrets"]
            and "credentials_or_secrets" in marker_summary_by_id
        ):
            flagged_marker_ids.append("credentials_or_secrets")
            _append_summary_item(
                summary_items,
                marker_summary_by_id["credentials_or_secrets"],
            )
            _append_summary_item(review_sources, "heuristic")
        if (
            review_policy["flag_privileged_or_classified"]
            and "privileged_or_classified" in marker_summary_by_id
        ):
            flagged_marker_ids.append("privileged_or_classified")
            _append_summary_item(
                summary_items,
                marker_summary_by_id["privileged_or_classified"],
            )
            _append_summary_item(review_sources, "heuristic")

    if local_pii_categories:
        _append_unique_strings(detected_pii_entity_categories, local_pii_categories)
        if review_policy["flag_local_pii"]:
            _append_summary_item(
                summary_items,
                "Local review suggests personal information: "
                + ", ".join(local_pii_categories)
                + ".",
            )
            _append_unique_strings(pii_entity_categories, local_pii_categories)
            _append_summary_item(review_sources, "heuristic")

    azure_review = _run_azure_language_pii_review(review_text)
    if azure_review is not None:
        if azure_review.get("pii_flagged"):
            azure_categories = _normalized_categories(
                azure_review.get("pii_entity_categories") or []
            )
            _append_unique_strings(detected_pii_entity_categories, azure_categories)
            flagged_azure_categories = [
                category
                for category in azure_categories
                if category in set(review_policy["flagged_azure_pii_categories"])
            ]
            if flagged_azure_categories:
                _append_unique_strings(pii_entity_categories, flagged_azure_categories)
                _append_unique_strings(summary_items, flagged_azure_categories)
                _append_summary_item(review_sources, "azure_language")

    pii_flagged = bool(pii_entity_categories)
    flagged = bool(
        pii_flagged
        or flagged_marker_ids
        or (
            largest_fragment_chars >= _LARGE_PAYLOAD_THRESHOLD_CHARS
            and review_policy["flag_large_payloads"]
        )
    )

    return {
        "flagged": flagged,
        "pii_flagged": pii_flagged,
        "summary_items": summary_items,
        "review_sources": review_sources,
        "pii_entity_categories": pii_entity_categories,
        "detected_pii_entity_categories": detected_pii_entity_categories,
        "matched_marker_ids": matched_marker_ids,
        "text_chars_reviewed": len(review_text),
        "azure_language_used": azure_review is not None,
        "redacted_preview": (azure_review or {}).get("redacted_text", ""),
        "tool_name": tool_name,
    }
