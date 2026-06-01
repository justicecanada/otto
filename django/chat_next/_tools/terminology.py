from django.utils.translation import gettext_lazy as _

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next.models import TOOL_CATEGORY_TERMINOLOGY


def _clean_text(value: str) -> str:
    return " ".join((value or "").split())


def _extract_section_list(panel, header_keywords: list[str]) -> list[str]:
    for section in panel.find_all("section"):
        header = section.find("h5")
        if not header:
            continue
        header_text = _clean_text(header.get_text(" ", strip=True))
        if any(keyword.lower() in header_text.lower() for keyword in header_keywords):
            return [
                _clean_text(li.get_text(" ", strip=True)) for li in section.select("li")
            ]
    return []


def _extract_term_list(panel, header_keyword: str) -> list[dict]:
    """Extract term entries from a TERMIUM panel section."""
    items = []
    for header in panel.find_all("h5"):
        header_text = _clean_text(header.get_text(" ", strip=True))
        if header_keyword.lower() not in header_text.lower():
            continue
        container = header.find_parent()
        if not container:
            continue
        ul = container.find_next("ul", class_="list-unstyled")
        if not ul:
            continue
        for li in ul.find_all("li", recursive=False):
            # Extract the term from <strong> tag (contains <mark> with actual term)
            strong = li.find("strong")
            if strong:
                term = _clean_text(strong.get_text(" ", strip=True))
            else:
                # Fallback: get text but remove hidden spans
                for hidden in li.select(".hidden"):
                    hidden.decompose()
                term = _clean_text(li.get_text(" ", strip=True))

            # Extract status from text-muted div
            status_el = li.find("div", class_="text-muted")
            status = (
                _clean_text(status_el.get_text(" ", strip=True)) if status_el else ""
            )

            # Remove status from term if it got included
            if status and status in term:
                term = _clean_text(term.replace(status, ""))

            if term:  # Only add if we have actual content
                items.append({"term": term, "status": status})
        break
    return items


def _extract_textual_support(panel) -> list[dict]:
    """Extract textual support entries (DEF, OBS, CONT, etc.) from a TERMIUM panel."""
    supports = []
    for abbr in panel.find_all("abbr"):
        label = _clean_text(abbr.get_text(" ", strip=True))
        parent = abbr.find_parent("h5")
        if not parent:
            continue
        text_parts = []
        for sibling in parent.find_next_siblings():
            if sibling.name == "h5":
                break
            if sibling.name == "p":
                # Clone the element to avoid modifying the original
                p_copy = sibling.__copy__()
                # Remove hidden spans that contain noise
                for hidden in p_copy.select(".hidden"):
                    hidden.decompose()
                text_parts.append(_clean_text(p_copy.get_text(" ", strip=True)))
        if text_parts:
            supports.append(
                {
                    "label": label,
                    "text": "\n".join(text_parts),
                }
            )
    return supports


async def termium_lookup(arguments: dict, context: ToolContext) -> dict:
    """
    Search TERMIUM Plus and parse records into structured data.

    Returns per-record language blocks with terms, abbreviations, synonyms,
    subject fields, and textual support (DEF/OBS/CONT/etc.).
    """
    import re

    import requests
    from bs4 import BeautifulSoup

    query = (arguments.get("query") or "").strip()
    index = (arguments.get("index") or "").strip()
    lang = (arguments.get("lang") or "eng").strip()
    max_records = arguments.get("max_records", 5)

    if not query:
        return {"error": "query is required"}
    if not index:
        return {"error": "index is required"}

    try:
        max_records = int(max_records)
    except (TypeError, ValueError):
        max_records = 5
    max_records = max(1, min(max_records, 10))

    base_url = "https://www.btb.termiumplus.gc.ca/tpv2alpha/alpha-eng.html"
    params = {
        "lang": lang,
        "srchtxt": query,
        "i": "1",
        "index": index,
        "codom2nd_wet": "1",
    }

    response = requests.get(base_url, params=params, timeout=30)
    if response.status_code != 200:
        return {"error": f"TERMIUM request failed ({response.status_code})."}

    # Note: Must use 'lxml' parser as 'html.parser' fails to parse TERMIUM's HTML correctly
    soup = BeautifulSoup(response.text, "lxml")
    record_sections = soup.select("section.panel.recordSet")

    records = []
    for record_section in record_sections[:max_records]:
        header = record_section.select_one(".panel-heading")
        header_text = _clean_text(header.get_text(" ", strip=True)) if header else ""
        record_number = None
        match = re.search(r"Record\s+(\d+)", header_text, re.IGNORECASE)
        if match:
            record_number = int(match.group(1))
        date_modified = None
        date_el = record_section.select_one(".termdate")
        if date_el:
            date_modified = _clean_text(date_el.get_text(" ", strip=True))

        record_data = {
            "record_number": record_number,
            "header": header_text,
            "date_modified": date_modified,
            "languages": [],
        }

        for panel in record_section.select("div.col-md-4 > section.panel"):
            lang_title_el = panel.select_one(".panel-heading h4")
            lang_label = (
                _clean_text(lang_title_el.get_text(" ", strip=True))
                if lang_title_el
                else ""
            )

            subject_fields = _extract_section_list(
                panel,
                ["Subject field", "Domaine", "Campo"],
            )

            main_terms = _extract_term_list(panel, "Main entry term")
            abbreviations = _extract_term_list(panel, "Abbreviations")
            synonyms = _extract_term_list(panel, "Synonyms")
            key_terms = _extract_section_list(panel, ["Key term", "Key term(s)"])
            textual_support = _extract_textual_support(panel)

            record_data["languages"].append(
                {
                    "language": lang_label,
                    "subject_fields": subject_fields,
                    "main_terms": main_terms,
                    "abbreviations": abbreviations,
                    "synonyms": synonyms,
                    "key_terms": key_terms,
                    "textual_support": textual_support,
                }
            )

        records.append(record_data)

    return {
        "query": query,
        "index": index,
        "lang": lang,
        "result_count": len(records),
        "records": records,
        "source_url": response.url,
        "copyright_notice": (
            "TERMIUM Plus® data is © Public Services and Procurement Canada."
        ),
    }


TOOL_REGISTRY.register(
    OttoTool(
        name="termium_lookup",
        description=(
            "Look up terms in TERMIUM Plus®, the Government of Canada's terminology bank."
            # "Use ONLY for bilingual/multilingual terminology definitions and usage notes. "
            # "Returns structured terminology records by language, including subject fields and definitions. "
            # "NOT for legal research, case law, or legislation lookups."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The term to look up.",
                },
                "index": {
                    "type": "string",
                    "description": (
                        "Search index code (e.g., 'ent' for English exact term, 'frt' for French exact term, "
                        "'alt' for all terms, 'enw' for words in English terms, 'frw' for words in French terms)."
                    ),
                },
                "lang": {
                    "type": "string",
                    "enum": ["eng", "fra", "spa", "por"],
                    "description": "Interface language for TERMIUM.",
                    "default": "eng",
                },
                "max_records": {
                    "type": "integer",
                    "description": "Maximum number of records to return (1-10).",
                    "default": 5,
                },
            },
            "required": ["query", "index"],
            "additionalProperties": False,
        },
        execute=termium_lookup,
        category=TOOL_CATEGORY_TERMINOLOGY,
        requires_user=True,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
        strict=False,
        requires_approval=True,
        approval_label=_("Termium lookup"),
        allow_auto_approve=False,
        is_external_tool=True,
        external_service_name="TERMIUM Plus®",
    )
)
