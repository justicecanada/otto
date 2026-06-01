from __future__ import annotations

from typing import Any

from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as translate
from django.utils.translation import gettext_lazy as _

from chat_next._tools.approval import parse_function_call_arguments
from chat_next._tools.approval_review import (
    get_sanitized_tool_review_arguments,
    has_custom_tool_review_arguments,
)

DEFAULT_FETCH_END_CHAR = 25_000

TERMIUM_INDEX_LABELS = {
    "ent": _("Exact English term"),
    "frt": _("Exact French term"),
    "alt": _("All terms"),
    "enw": _("Words in English terms"),
    "frw": _("Words in French terms"),
}

TERMIUM_INTERFACE_LANGUAGE_LABELS = {
    "eng": _("English"),
    "fra": _("French"),
    "spa": _("Spanish"),
    "por": _("Portuguese"),
}

SEARCH_TYPE_LABELS = {
    "full_text": _("Full text"),
    "name": _("Title / name"),
}

SEARCH_LANGUAGE_LABELS = {
    "en": _("English"),
    "fr": _("French"),
}

OUTPUT_LANGUAGE_LABELS = {
    "en": _("English"),
    "fr": _("French"),
    "both": _("English and French"),
}

SORT_RESULT_LABELS = {
    "default": _("Default relevance"),
    "newest_first": _("Newest first"),
    "oldest_first": _("Oldest first"),
}

DOC_TYPE_LABELS = {
    "cases": _("Case law"),
    "laws": _("Legislation"),
    "both": _("Case law and legislation"),
}

EDIT_SKILL_FIELD_LABELS = {
    "skill_id": _("Skill ID"),
    "display_name_en": _("English name"),
    "display_name_fr": _("French name"),
    "description_en": _("English description"),
    "description_fr": _("French description"),
    "body_en": _("English instructions"),
    "body_fr": _("French instructions"),
    "context_hints": _("Context hints"),
}


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate_text(value: str, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _render_multiline_text(value: str, *, limit: int = 400):
    text = _truncate_text(value, limit=limit)
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return format_html_join("<br>", "{}", ((line,) for line in lines))


def _render_list_items(values: list[Any]):
    if not values:
        return ""
    rendered_items = []
    for value in values:
        rendered_value = _render_value(value)
        if rendered_value:
            rendered_items.append(rendered_value)
    if not rendered_items:
        return ""
    return format_html(
        '<ul class="mb-0 ps-3">{}</ul>',
        format_html_join("", "<li>{}</li>", ((item,) for item in rendered_items)),
    )


def _render_nested_definition_list(value: dict[str, Any]):
    rows = []
    for key, item in value.items():
        if item in (None, "", [], {}):
            continue
        rows.append((_prettify_key(key), _render_value(item)))
    if not rows:
        return ""
    return _render_definition_list(rows, nested=True)


def _render_value(value: Any):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    if isinstance(value, (int, float)):
        return format_html("{}", value)
    if isinstance(value, str):
        return _render_multiline_text(value)
    if isinstance(value, list):
        return _render_list_items(value)
    if isinstance(value, dict):
        return _render_nested_definition_list(value)
    return format_html("{}", value)


def _render_definition_list(rows: list[tuple[str, Any]], *, nested: bool = False):
    non_empty_rows = [
        (label, value) for label, value in rows if value not in (None, "")
    ]
    if not non_empty_rows:
        return ""

    classes = "reasoning-approval-fields reasoning-approval-fields-nested mb-0"
    if not nested:
        classes = "reasoning-approval-fields mb-0"

    return format_html(
        '<div class="reasoning-approval-input"><dl class="{}">{}</dl></div>',
        classes,
        format_html_join(
            "",
            '<div class="reasoning-approval-field"><dt>{}</dt><dd><mark>{}</mark></dd></div>',
            ((label, value) for label, value in non_empty_rows),
        ),
    )


def _prettify_key(key: str) -> str:
    return str(key or "").replace("_", " ").strip().capitalize()


def _render_labelled_list(values: list[str]):
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return ""
    return _render_list_items(cleaned)


def _render_csv_list(value: str):
    values = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return _render_labelled_list(values)


def _format_date_range(start_date: str | None, end_date: str | None) -> str:
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if start and end:
        return translate("{} to {}").format(start, end)
    if start:
        return translate("From {} onward").format(start)
    if end:
        return translate("Up to {}").format(end)
    return ""


def _format_requested_text_window(
    arguments: dict[str, Any], *, include_default: bool = True
) -> str:
    if (
        not include_default
        and "start_char" not in arguments
        and "end_char" not in arguments
    ):
        return ""

    start_char = _coerce_int(arguments.get("start_char"), 0)
    end_char = _coerce_int(arguments.get("end_char"), DEFAULT_FETCH_END_CHAR)

    if start_char is None and end_char is None:
        return ""

    if end_char == -1:
        if not start_char:
            return _("Full text from the beginning")
        return translate("From character {} to the end").format(f"{start_char:,}")

    if start_char in (None, 0):
        return translate("First {} characters").format(f"{end_char:,}")

    return translate("Characters {} to {}").format(
        f"{start_char:,}",
        f"{end_char:,}",
    )


def _render_termium_lookup(arguments: dict[str, Any]):
    rows = [
        (
            _("Search term"),
            _render_multiline_text(arguments.get("query", ""), limit=300),
        ),
        (
            _("Search mode"),
            _render_value(
                TERMIUM_INDEX_LABELS.get(arguments.get("index"), arguments.get("index"))
            ),
        ),
    ]

    interface_language = arguments.get("lang", "eng")
    if interface_language not in (None, "", "eng"):
        rows.append(
            (
                _("Interface language"),
                _render_value(
                    TERMIUM_INTERFACE_LANGUAGE_LABELS.get(
                        interface_language, interface_language
                    )
                ),
            )
        )

    max_records = _coerce_int(arguments.get("max_records"), 5)
    if max_records not in (None, 5):
        rows.append((_("Max records"), _render_value(max_records)))

    return _render_definition_list(rows)


def _render_list_canadian_legal_datasets(arguments: dict[str, Any]):
    if "doc_type" not in arguments:
        return ""

    return _render_definition_list(
        [
            (
                _("Coverage"),
                _render_value(
                    DOC_TYPE_LABELS.get(
                        arguments.get("doc_type", "both"),
                        arguments.get("doc_type", "both"),
                    )
                ),
            )
        ]
    )


def _render_search_canadian_case_law(arguments: dict[str, Any]):
    rows = [(_("Query"), _render_multiline_text(arguments.get("query", ""), limit=450))]

    search_type = arguments.get("search_type", "full_text")
    if search_type not in (None, "", "full_text"):
        rows.append(
            (
                _("Search in"),
                _render_value(SEARCH_TYPE_LABELS.get(search_type, search_type)),
            )
        )

    dataset = arguments.get("dataset")
    if dataset:
        rows.append((_("Datasets"), _render_csv_list(dataset)))

    date_range = _format_date_range(
        arguments.get("start_date"), arguments.get("end_date")
    )
    if date_range:
        rows.append((_("Date range"), _render_value(date_range)))

    search_language = arguments.get("search_language", "en")
    if search_language not in (None, "", "en"):
        rows.append(
            (
                _("Search language"),
                _render_value(
                    SEARCH_LANGUAGE_LABELS.get(search_language, search_language)
                ),
            )
        )

    sort_results = arguments.get("sort_results", "default")
    if sort_results not in (None, "", "default"):
        rows.append(
            (
                _("Sort"),
                _render_value(SORT_RESULT_LABELS.get(sort_results, sort_results)),
            )
        )

    size = _coerce_int(arguments.get("size"), 5)
    if size not in (None, 5):
        rows.append((_("Result count"), _render_value(size)))

    return _render_definition_list(rows)


def _render_fetch_canadian_case_by_citation(arguments: dict[str, Any]):
    rows = [(_("Citation"), _render_value(arguments.get("citation")))]

    requested_text = _format_requested_text_window(arguments, include_default=False)
    if requested_text:
        rows.append((_("Requested text"), _render_value(requested_text)))

    output_language = arguments.get("output_language", "en")
    if output_language not in (None, "", "en"):
        rows.append(
            (
                _("Output language"),
                _render_value(
                    OUTPUT_LANGUAGE_LABELS.get(output_language, output_language)
                ),
            )
        )

    return _render_definition_list(rows)


def _render_search_canadian_legislation(arguments: dict[str, Any]):
    rows = [(_("Query"), _render_multiline_text(arguments.get("query", ""), limit=450))]

    search_type = arguments.get("search_type", "full_text")
    if search_type not in (None, "", "full_text"):
        rows.append(
            (
                _("Search in"),
                _render_value(SEARCH_TYPE_LABELS.get(search_type, search_type)),
            )
        )

    dataset = arguments.get("dataset")
    if dataset:
        rows.append((_("Datasets"), _render_csv_list(dataset)))

    date_range = _format_date_range(
        arguments.get("start_date"), arguments.get("end_date")
    )
    if date_range:
        rows.append((_("Date range"), _render_value(date_range)))

    search_language = arguments.get("search_language", "en")
    if search_language not in (None, "", "en"):
        rows.append(
            (
                _("Search language"),
                _render_value(
                    SEARCH_LANGUAGE_LABELS.get(search_language, search_language)
                ),
            )
        )

    sort_results = arguments.get("sort_results", "default")
    if sort_results not in (None, "", "default"):
        rows.append(
            (
                _("Sort"),
                _render_value(SORT_RESULT_LABELS.get(sort_results, sort_results)),
            )
        )

    size = _coerce_int(arguments.get("size"), 5)
    if size not in (None, 5):
        rows.append((_("Result count"), _render_value(size)))

    return _render_definition_list(rows)


def _render_fetch_canadian_legislation_by_citation(arguments: dict[str, Any]):
    rows = [(_("Citation"), _render_value(arguments.get("citation")))]

    section = str(arguments.get("section") or "").strip()
    if section:
        rows.append((_("Section"), _render_value(section)))
    else:
        requested_text = _format_requested_text_window(arguments, include_default=False)
        if requested_text:
            rows.append((_("Requested text"), _render_value(requested_text)))

    output_language = arguments.get("output_language", "en")
    if output_language not in (None, "", "en"):
        rows.append(
            (
                _("Output language"),
                _render_value(
                    OUTPUT_LANGUAGE_LABELS.get(output_language, output_language)
                ),
            )
        )

    return _render_definition_list(rows)


def _render_edit_skill(arguments: dict[str, Any]):
    rows = []
    for key in (
        "skill_id",
        "display_name_en",
        "display_name_fr",
        "description_en",
        "description_fr",
        "body_en",
        "body_fr",
        "context_hints",
    ):
        value = arguments.get(key)
        if value in (None, "", [], {}):
            continue
        if key.startswith("body_"):
            rendered_value = _render_multiline_text(value, limit=600)
        elif key.startswith("description_"):
            rendered_value = _render_multiline_text(value, limit=320)
        else:
            rendered_value = _render_value(value)
        rows.append(
            (EDIT_SKILL_FIELD_LABELS.get(key, _prettify_key(key)), rendered_value)
        )

    return _render_definition_list(rows)


def _render_generic(arguments: dict[str, Any], raw_arguments: Any = None):
    if arguments:
        rows = [
            (_prettify_key(key), _render_value(value))
            for key, value in arguments.items()
        ]
        return _render_definition_list(rows)

    raw_text = str(raw_arguments or "").strip()
    if not raw_text or raw_text == "{}":
        return ""

    return _render_definition_list(
        [(_("Request"), _render_multiline_text(raw_text, limit=500))]
    )


APPROVAL_INPUT_RENDERERS = {
    "termium_lookup": _render_termium_lookup,
    "list_canadian_legal_datasets": _render_list_canadian_legal_datasets,
    "search_canadian_case_law": _render_search_canadian_case_law,
    "fetch_canadian_case_by_citation": _render_fetch_canadian_case_by_citation,
    "search_canadian_legislation": _render_search_canadian_legislation,
    "fetch_canadian_legislation_by_citation": _render_fetch_canadian_legislation_by_citation,
    "edit_skill": _render_edit_skill,
}


def render_tool_approval_input_html(tool_name: str, raw_arguments: Any) -> str:
    function_call = {"arguments": raw_arguments}
    arguments = parse_function_call_arguments(function_call)
    sanitized_arguments = get_sanitized_tool_review_arguments(
        tool_name,
        arguments or {},
    )
    renderer = APPROVAL_INPUT_RENDERERS.get(tool_name)
    has_custom_sanitizer = has_custom_tool_review_arguments(tool_name)

    if has_custom_sanitizer and not sanitized_arguments:
        return ""

    if renderer:
        rendered = str(renderer(sanitized_arguments or {}))
        if rendered:
            return rendered

    return str(
        _render_generic(
            sanitized_arguments or {},
            raw_arguments=None if has_custom_sanitizer else raw_arguments,
        )
    )
