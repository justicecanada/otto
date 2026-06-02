"""Helpers for the A2AJ public legal data API."""

from __future__ import annotations

import html
import re

import requests

A2AJ_BASE_URL = "https://api.a2aj.ca"
A2AJ_TIMEOUT = 30
MAX_A2AJ_SEARCH_RESULTS = 50
DEFAULT_A2AJ_FETCH_END_CHAR = 25000
VALID_A2AJ_DOC_TYPES = ("cases", "laws")


def _compact_whitespace(value: str | None) -> str:
    return " ".join((value or "").split())


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _strip_markup(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", "", value)
    return _compact_whitespace(html.unescape(value))


def _normalize_date(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value.split("T", 1)[0]


def _normalize_coverage_item(result: dict) -> dict:
    descriptions = _pick_language_map(result, "description", _compact_whitespace)
    normalized = {
        "dataset": _compact_whitespace(result.get("dataset")),
        "earliest_document_date": _normalize_date(result.get("earliest_document_date")),
        "latest_document_date": _normalize_date(result.get("latest_document_date")),
        "number_of_documents": result.get("number_of_documents"),
    }

    description = descriptions.get("en") or descriptions.get("fr")
    if description:
        normalized["description"] = description
    if descriptions:
        normalized["descriptions"] = descriptions

    return {
        key: value for key, value in normalized.items() if value not in ("", {}, None)
    }


def _pick_language_map(result: dict, prefix: str, normalizer) -> dict[str, str]:
    values = {}
    for lang in ("en", "fr"):
        raw_value = result.get(f"{prefix}_{lang}")
        normalized = normalizer(raw_value)
        if normalized:
            values[lang] = normalized
    return values


def _normalize_case_metadata(result: dict) -> dict:
    citations = _pick_language_map(result, "citation", _compact_whitespace)
    alternate_citations = _pick_language_map(result, "citation2", _compact_whitespace)
    names = _pick_language_map(result, "name", _compact_whitespace)
    official_urls = _pick_language_map(
        result, "url", lambda value: (value or "").strip()
    )

    normalized = {
        "dataset": _compact_whitespace(result.get("dataset")),
    }

    citation = citations.get("en") or citations.get("fr")
    if citation:
        normalized["citation"] = citation
    if citations:
        normalized["citations"] = citations

    alternate_citation = alternate_citations.get("en") or alternate_citations.get("fr")
    if alternate_citation:
        normalized["alternate_citation"] = alternate_citation
    if alternate_citations:
        normalized["alternate_citations"] = alternate_citations

    name = names.get("en") or names.get("fr")
    if name:
        normalized["name"] = name
    if names:
        normalized["names"] = names

    decision_date = _normalize_date(
        result.get("document_date_en") or result.get("document_date_fr")
    )
    if decision_date:
        normalized["decision_date"] = decision_date

    if official_urls:
        normalized["official_urls"] = official_urls

    return {
        key: value for key, value in normalized.items() if value not in ("", {}, None)
    }


def _normalize_law_metadata(result: dict) -> dict:
    citations = _pick_language_map(result, "citation", _compact_whitespace)
    alternate_citations = _pick_language_map(result, "citation2", _compact_whitespace)
    names = _pick_language_map(result, "name", _compact_whitespace)
    source_urls = _pick_language_map(
        result, "source_url", lambda value: (value or "").strip()
    )

    normalized = {
        "dataset": _compact_whitespace(result.get("dataset")),
    }

    citation = citations.get("en") or citations.get("fr")
    if citation:
        normalized["citation"] = citation
    if citations:
        normalized["citations"] = citations

    alternate_citation = alternate_citations.get("en") or alternate_citations.get("fr")
    if alternate_citation:
        normalized["alternate_citation"] = alternate_citation
    if alternate_citations:
        normalized["alternate_citations"] = alternate_citations

    name = names.get("en") or names.get("fr")
    if name:
        normalized["title"] = name
    if names:
        normalized["titles"] = names

    effective_date = _normalize_date(
        result.get("document_date_en") or result.get("document_date_fr")
    )
    if effective_date:
        normalized["document_date"] = effective_date

    num_sections = result.get("num_sections_en") or result.get("num_sections_fr")
    if num_sections is not None:
        normalized["num_sections"] = num_sections

    if source_urls:
        normalized["source_urls"] = source_urls

    return {
        key: value for key, value in normalized.items() if value not in ("", {}, None)
    }


def _normalize_coverage_item(result: dict) -> dict:
    descriptions = _pick_language_map(result, "description", _compact_whitespace)
    normalized = {
        "dataset": _compact_whitespace(result.get("dataset")),
        "earliest_document_date": _normalize_date(result.get("earliest_document_date")),
        "latest_document_date": _normalize_date(result.get("latest_document_date")),
        "number_of_documents": result.get("number_of_documents"),
    }

    description = descriptions.get("en") or descriptions.get("fr")
    if description:
        normalized["description"] = description
    if descriptions:
        normalized["descriptions"] = descriptions

    return {
        key: value for key, value in normalized.items() if value not in ("", {}, None)
    }


def _request(path: str, params: dict) -> list[dict]:
    filtered_params = {
        key: value for key, value in params.items() if value not in (None, "")
    }
    try:
        response = requests.get(
            f"{A2AJ_BASE_URL}{path}",
            params=filtered_params,
            timeout=A2AJ_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"A2AJ API request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("A2AJ API returned invalid JSON.") from exc

    results = payload.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError("A2AJ API returned an unexpected response shape.")
    return results


def list_dataset_coverage(*, doc_type: str = "both") -> dict:
    if doc_type == "both":
        case_coverage = list_dataset_coverage(doc_type="cases")
        law_coverage = list_dataset_coverage(doc_type="laws")
        total_datasets = case_coverage["result_count"] + law_coverage["result_count"]
        return {
            "doc_type": "both",
            "result_count": total_datasets,
            "results": {
                "cases": case_coverage["results"],
                "laws": law_coverage["results"],
            },
            "TIP": (
                "Use the returned dataset codes as the dataset filter in "
                "search_canadian_case_law or search_canadian_legislation."
            ),
        }

    if doc_type not in VALID_A2AJ_DOC_TYPES:
        raise RuntimeError("doc_type must be 'cases', 'laws', or 'both'.")

    results = _request("/coverage", {"doc_type": doc_type})
    normalized_results = [_normalize_coverage_item(result) for result in results]
    return {
        "doc_type": doc_type,
        "result_count": len(normalized_results),
        "results": normalized_results,
        "TIP": (
            "Use the returned dataset codes as the dataset filter in "
            "search_canadian_case_law or search_canadian_legislation."
        ),
    }


def search_cases(
    *,
    query: str,
    search_type: str = "full_text",
    size: int = 5,
    search_language: str = "en",
    sort_results: str = "default",
    dataset: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    size = max(1, min(int(size), MAX_A2AJ_SEARCH_RESULTS))
    results = _request(
        "/search",
        {
            "query": query,
            "search_type": search_type,
            "doc_type": "cases",
            "size": size,
            "search_language": search_language,
            "sort_results": sort_results,
            "dataset": dataset,
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    normalized_results = []
    for result in results:
        normalized = _normalize_case_metadata(result)
        snippet = _strip_markup(result.get("snippet"))
        if snippet:
            normalized["snippet"] = snippet
        score = result.get("score")
        if score is not None:
            try:
                normalized["score"] = round(float(score), 3)
            except (TypeError, ValueError):
                pass
        normalized_results.append(normalized)

    response = {
        "query": query,
        "search_type": search_type,
        "search_language": search_language,
        "sort_results": sort_results,
        "result_count": len(normalized_results),
        "results": normalized_results,
        "TIP": (
            "Use fetch_canadian_case_by_citation with the citation from a result "
            "to retrieve the decision text. The default first-pass "
            "window is 25000 chars and is usually fine for screening. If a case looks "
            "genuinely relevant, perform one larger follow-up fetch (end_char=-1)."
        ),
    }
    if dataset:
        response["dataset_filter"] = dataset
    if start_date:
        response["start_date"] = start_date
    if end_date:
        response["end_date"] = end_date
    if not normalized_results:
        response["message"] = "No relevant Canadian case law was found for this query."
    return response


def fetch_case_by_citation(
    *,
    citation: str,
    output_language: str = "en",
    start_char: int = 0,
    end_char: int = DEFAULT_A2AJ_FETCH_END_CHAR,
) -> dict:
    results = _request(
        "/fetch",
        {
            "citation": citation,
            "doc_type": "cases",
            "output_language": output_language,
            "start_char": start_char,
            "end_char": end_char,
        },
    )

    normalized_results = []
    for result in results:
        normalized = _normalize_case_metadata(result)

        if output_language == "both":
            texts = _pick_language_map(result, "unofficial_text", _normalize_text)
            if texts:
                normalized["texts"] = texts
        else:
            preferred_key = f"unofficial_text_{output_language}"
            text = _normalize_text(result.get(preferred_key))
            if not text:
                fallback_texts = _pick_language_map(
                    result, "unofficial_text", _normalize_text
                )
                text = fallback_texts.get("en") or fallback_texts.get("fr") or ""
            if text:
                normalized["text"] = text
                normalized["text_language"] = output_language

        normalized["requested_window"] = {
            "start_char": int(start_char),
            "end_char": int(end_char),
        }
        normalized_results.append(normalized)

    response = {
        "citation_requested": citation,
        "output_language": output_language,
        "result_count": len(normalized_results),
        "results": normalized_results,
    }
    if end_char != -1:
        response["TIP"] = (
            "If you need more of the decision, call this tool again with a higher "
            f"start_char (for example start_char={int(end_char)}). Prefer a single "
            "larger continuation (end_char=-1) when you want the rest/full text—rather "
            "than many small contiguous windows."
        )
    if not normalized_results:
        response["message"] = "No Canadian case was found for that citation."
    return response


def search_laws(
    *,
    query: str,
    search_type: str = "full_text",
    size: int = 5,
    search_language: str = "en",
    sort_results: str = "default",
    dataset: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    size = max(1, min(int(size), MAX_A2AJ_SEARCH_RESULTS))
    results = _request(
        "/search",
        {
            "query": query,
            "search_type": search_type,
            "doc_type": "laws",
            "size": size,
            "search_language": search_language,
            "sort_results": sort_results,
            "dataset": dataset,
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    normalized_results = []
    for result in results:
        normalized = _normalize_law_metadata(result)
        snippet = _strip_markup(result.get("snippet"))
        if snippet:
            normalized["snippet"] = snippet
        score = result.get("score")
        if score is not None:
            try:
                normalized["score"] = round(float(score), 3)
            except (TypeError, ValueError):
                pass
        normalized_results.append(normalized)

    response = {
        "query": query,
        "search_type": search_type,
        "search_language": search_language,
        "sort_results": sort_results,
        "result_count": len(normalized_results),
        "results": normalized_results,
        "TIP": (
            "Use fetch_canadian_legislation_by_citation with a citation from a result "
            "to retrieve a compact excerpt or a specific section. Prefer a section-specific "
            "fetch when possible; otherwise the default 25000-char window is usually a good "
            "first pass, and end_char=-1 is appropriate when you truly need the rest/full text."
        ),
    }
    if dataset:
        response["dataset_filter"] = dataset
    if start_date:
        response["start_date"] = start_date
    if end_date:
        response["end_date"] = end_date
    if not normalized_results:
        response["message"] = "No Canadian legislation was found for this query."
    return response


def fetch_law_by_citation(
    *,
    citation: str,
    output_language: str = "en",
    section: str = "",
    start_char: int = 0,
    end_char: int = DEFAULT_A2AJ_FETCH_END_CHAR,
) -> dict:
    results = _request(
        "/fetch",
        {
            "citation": citation,
            "doc_type": "laws",
            "output_language": output_language,
            "section": section,
            "start_char": start_char,
            "end_char": end_char,
        },
    )

    normalized_results = []
    for result in results:
        normalized = _normalize_law_metadata(result)

        if output_language == "both":
            texts = _pick_language_map(result, "unofficial_text", _normalize_text)
            if texts:
                normalized["texts"] = texts
        else:
            preferred_key = f"unofficial_text_{output_language}"
            text = _normalize_text(result.get(preferred_key))
            if not text:
                fallback_texts = _pick_language_map(
                    result, "unofficial_text", _normalize_text
                )
                text = fallback_texts.get("en") or fallback_texts.get("fr") or ""
            if text:
                normalized["text"] = text
                normalized["text_language"] = output_language

        if section:
            normalized["section_requested"] = section
        else:
            normalized["requested_window"] = {
                "start_char": int(start_char),
                "end_char": int(end_char),
            }

        normalized_results.append(normalized)

    response = {
        "citation_requested": citation,
        "output_language": output_language,
        "result_count": len(normalized_results),
        "results": normalized_results,
    }
    if section:
        response["section_requested"] = section
    elif end_char != -1:
        response["TIP"] = (
            "If you need more of the legislation, call this tool again with a higher "
            f"start_char (for example start_char={int(end_char)}), provide a specific "
            "section, or use end_char=-1 when you truly need the rest/full text. Avoid "
            "many small contiguous windows."
        )
    if not normalized_results:
        response["message"] = "No Canadian legislation was found for that citation."
    return response
