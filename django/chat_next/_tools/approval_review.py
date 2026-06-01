from __future__ import annotations

import re
from typing import Any, Callable

_SAFE_DATASET_FILTER_PATTERN = re.compile(r"^[A-Z0-9-]+(?:\s*,\s*[A-Z0-9-]+)*$")
_SAFE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_enum_value(*allowed_values: str) -> Callable[[Any], bool]:
    allowed = {str(value).strip() for value in allowed_values}

    def predicate(value: Any) -> bool:
        return str(value).strip() in allowed

    return predicate


def _is_integer_in_range(
    minimum: int,
    maximum: int,
    *,
    allow_minus_one: bool = False,
) -> Callable[[Any], bool]:
    def predicate(value: Any) -> bool:
        try:
            normalized_value = int(value)
        except (TypeError, ValueError):
            return False

        if allow_minus_one and normalized_value == -1:
            return True

        return minimum <= normalized_value <= maximum

    return predicate


def _is_safe_dataset_filter(value: Any) -> bool:
    return bool(_SAFE_DATASET_FILTER_PATTERN.fullmatch(str(value or "").strip()))


def _is_safe_date(value: Any) -> bool:
    return bool(_SAFE_DATE_PATTERN.fullmatch(str(value or "").strip()))


_TOOL_REVIEW_SAFE_FIELD_RULES: dict[str, dict[str, Callable[[Any], bool]]] = {
    "termium_lookup": {
        "index": _is_enum_value("ent", "frt", "alt", "enw", "frw"),
        "lang": _is_enum_value("eng", "fra", "spa", "por"),
        "max_records": _is_integer_in_range(1, 10),
    },
    "list_canadian_legal_datasets": {
        "doc_type": _is_enum_value("cases", "laws", "both"),
    },
    "search_canadian_case_law": {
        "search_type": _is_enum_value("full_text", "name"),
        "size": _is_integer_in_range(1, 50),
        "search_language": _is_enum_value("en", "fr"),
        "sort_results": _is_enum_value("default", "newest_first", "oldest_first"),
        "dataset": _is_safe_dataset_filter,
        "start_date": _is_safe_date,
        "end_date": _is_safe_date,
    },
    "fetch_canadian_case_by_citation": {
        "output_language": _is_enum_value("en", "fr", "both"),
        "start_char": _is_integer_in_range(0, 99_999_999),
        "end_char": _is_integer_in_range(0, 99_999_999, allow_minus_one=True),
    },
    "search_canadian_legislation": {
        "search_type": _is_enum_value("full_text", "name"),
        "size": _is_integer_in_range(1, 50),
        "search_language": _is_enum_value("en", "fr"),
        "sort_results": _is_enum_value("default", "newest_first", "oldest_first"),
        "dataset": _is_safe_dataset_filter,
        "start_date": _is_safe_date,
        "end_date": _is_safe_date,
    },
    "fetch_canadian_legislation_by_citation": {
        "output_language": _is_enum_value("en", "fr", "both"),
        "start_char": _is_integer_in_range(0, 99_999_999),
        "end_char": _is_integer_in_range(0, 99_999_999, allow_minus_one=True),
    },
}


def _without_empty_values(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (arguments or {}).items()
        if value not in (None, "", [], {})
    }


def has_custom_tool_review_arguments(tool_name: str | None) -> bool:
    return str(tool_name or "") in _TOOL_REVIEW_SAFE_FIELD_RULES


def get_sanitized_tool_review_arguments(
    tool_name: str | None, arguments: dict[str, Any] | None
) -> dict[str, Any]:
    normalized_arguments = _without_empty_values(arguments or {})
    safe_field_rules = _TOOL_REVIEW_SAFE_FIELD_RULES.get(str(tool_name or ""))

    if safe_field_rules is None:
        return normalized_arguments

    return {
        field_name: field_value
        for field_name, field_value in normalized_arguments.items()
        if not (
            field_name in safe_field_rules and safe_field_rules[field_name](field_value)
        )
    }
