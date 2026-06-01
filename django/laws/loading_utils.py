import hashlib
import os
import re
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta

from django.conf import settings
from django.utils.timezone import now

import requests
from llama_index.core import get_tokenizer
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from lxml import etree as ET
from sqlalchemy import create_engine, text
from structlog import get_logger

logger = get_logger(__name__)

# Temporary collection of skipped texts (placeholder-only sections/schedules/documents)
# TODO: This is a temporary mechanism to collect skipped texts; remove or replace with
# an operator-reporting mechanism once we've verified behavior.
SKIPPED_TEXTS = []
SKIPPED_TEXTS_LOCK = threading.Lock()


SAMPLE_LAW_IDS = [
    "A-0.6",  # Accessible Canada Act
    "SOR-2021-241",  # Accessible Canada Regulations
    "A-2",  # Aeronautics Act
    "B-9.01",  # Broadcasting Act
    "SOR-97-555",  # Broadcasting Distribution Regulations
    "SOR-96-433",  # Canadian Aviation Regulations
    "SOR-2011-318",  # Canadian Aviation Security Regulations, 2012
    "C-15.1",  # Canadian Energy Regulator Act
    "C-15.31",  # Canadian Environmental Protection Act, 1999
    "C-24.5",  # Cannabis Act
    "SOR-2018-144",  # Cannabis Regulations
    "C-46",  # Criminal Code
    "SOR-2021-25",  # Cross-border Movement of Hazardous Waste and Hazardous Recyclable Material Regulations
    "F-14",  # Fisheries Act
    "SOR-93-53",  # Fishery (General) Regulations
    "C.R.C.,_c._870",  # Food and Drug Regulations
    "F-27",  # Food and Drugs Act
    "I-2.5",  # Immigration and Refugee Protection Act
    "SOR-2002-227",  # Immigration and Refugee Protection Regulations
    "I-21",  # Interpretation Act
    "SOR-2016-151",  # Multi-Sector Air Pollutants Regulations
    "SOR-2010-189",  # Renewable Fuels Regulations
    "S-22",  # Statutory Instruments Act
    "C.R.C.,_c._1509",  # Statutory Instruments Regulations
    "A-1",  # Access to Information Act
    "F-11",  # Financial Administration Act
    "N-22",  # Canadian Navigable Waters Act
]

constitution_dir = os.path.join(settings.BASE_DIR, "laws", "data")
CONSTITUTION_FILE_PATHS = (
    os.path.join(constitution_dir, "Constitution 2020_E.xml"),
    os.path.join(constitution_dir, "Constitution 2020_F_Rapport.xml"),
)


def _download_repo():
    # Download and extract to media folder, with periodic sleep to allow Celery heartbeat
    # With gevent monkey patching, time.sleep becomes gevent-friendly automatically
    repo_url = (
        "https://github.com/justicecanada/laws-lois-xml/archive/refs/heads/main.zip"
    )
    zip_file_path = os.path.join(settings.MEDIA_ROOT, "laws-lois-xml.zip")

    logger.info("Downloading laws-lois-xml repo to media folder...")

    # Do the download in chunks
    # With gevent monkey patching, requests becomes cooperative automatically
    response = requests.get(repo_url, stream=True)
    response.raise_for_status()

    chunk_size = 5 * 1024 * 1024  # 5MB per chunk
    with open(zip_file_path, "wb") as file:
        for i, chunk in enumerate(response.iter_content(chunk_size=chunk_size)):
            if chunk:
                file.write(chunk)
                file.flush()
            # Brief sleep every 10 chunks (~50MB) to yield to other gevent greenlets
            # With monkey patching, time.sleep() yields control to other greenlets
            if i % 10 == 0 and i > 0:
                time.sleep(0.1)

    logger.info("Download complete, extracting zip...")

    # Extract zip file - disk I/O is the bottleneck, not extraction method
    # Periodically yield during extraction
    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        members = zip_ref.namelist()
        for i, member in enumerate(members):
            zip_ref.extract(member, settings.MEDIA_ROOT)
            # Yield every 100 files
            if i % 100 == 0 and i > 0:
                time.sleep(0.01)

    logger.info("Extraction complete, cleaning up zip...")
    os.remove(zip_file_path)


def _get_fr_matching_id(eng_id):
    return eng_id.replace("SOR-", "DORS-").replace("SI-", "TR-").replace("_c.", "_ch.")


def _build_law_file_cache(laws_dir):
    """
    Build a cache of available law files to avoid repeated os.path.exists() calls.
    Returns dict mapping law_id -> path for fast O(1) lookups.
    """
    cache = {
        "en": {"acts": {}, "regulations": {}},
        "fr": {"acts": {}, "regulations": {}},
    }

    # Define directory mappings for each language
    dir_configs = [
        ("en", "acts", os.path.join(laws_dir, "eng", "acts")),
        ("en", "regulations", os.path.join(laws_dir, "eng", "regulations")),
        ("fr", "acts", os.path.join(laws_dir, "fra", "lois")),
        ("fr", "regulations", os.path.join(laws_dir, "fra", "reglements")),
    ]

    # Cache all law files
    for lang, law_type, dir_path in dir_configs:
        if os.path.exists(dir_path):
            for filename in os.listdir(dir_path):
                if filename.endswith(".xml"):
                    law_id = filename[:-4]  # Remove .xml extension
                    cache[lang][law_type][law_id] = os.path.join(dir_path, filename)

    return cache


def _get_en_file_path(eng_id, laws_dir, cache=None):
    """Get English file path, optionally using cache to avoid os.path.exists() calls."""
    if cache:
        # Use cache for fast lookup
        return cache["en"]["acts"].get(eng_id) or cache["en"]["regulations"].get(eng_id)

    # Fallback to filesystem checks
    act_path = os.path.join(laws_dir, "eng", "acts", f"{eng_id}.xml")
    reg_path = os.path.join(laws_dir, "eng", "regulations", f"{eng_id}.xml")
    if os.path.exists(act_path):
        return act_path
    elif os.path.exists(reg_path):
        return reg_path
    else:
        return None


def _get_fr_file_path(fr_id, laws_dir, cache=None):
    """Get French file path, optionally using cache to avoid os.path.exists() calls."""
    if cache:
        # Use cache for fast lookup
        return cache["fr"]["acts"].get(fr_id) or cache["fr"]["regulations"].get(fr_id)

    # Fallback to filesystem checks
    act_path = os.path.join(laws_dir, "fra", "lois", f"{fr_id}.xml")
    reg_path = os.path.join(laws_dir, "fra", "reglements", f"{fr_id}.xml")
    if os.path.exists(act_path):
        return act_path
    elif os.path.exists(reg_path):
        return reg_path
    else:
        logger.debug(f"Could not find French file for {fr_id}")
        logger.debug(f"(FR: {act_path}, FR: {reg_path})")
        return None


def _get_en_fr_law_file_paths(laws_dir, eng_law_id, cache=None):
    """
    Search for the English and French file paths for each law ID.
    If cache is provided, use it for O(1) lookups instead of os.path.exists() calls.
    Return a tuple (EN, FR) where each element is a full file path, or None if not found.
    """

    if eng_law_id in ["Constitution", "Constitution 2020"]:
        return CONSTITUTION_FILE_PATHS

    file_paths = None
    en_file_path = _get_en_file_path(eng_law_id, laws_dir, cache)
    fr_file_path = _get_fr_file_path(_get_fr_matching_id(eng_law_id), laws_dir, cache)
    if en_file_path and fr_file_path:
        file_paths = (en_file_path, fr_file_path)
    else:
        logger.debug(f"Could not find both English and French files for {eng_law_id}")
        logger.debug(f"(EN: {en_file_path}, FR: {fr_file_path})")

    return file_paths


def _get_all_eng_law_ids(laws_dir):
    """
    Get all English law IDs from the laws directory
    """
    act_dir = os.path.join(laws_dir, "eng", "acts")
    reg_dir = os.path.join(laws_dir, "eng", "regulations")
    act_ids = [f.replace(".xml", "") for f in os.listdir(act_dir) if f.endswith(".xml")]
    reg_ids = [f.replace(".xml", "") for f in os.listdir(reg_dir) if f.endswith(".xml")]
    return act_ids + reg_ids


def law_xml_to_nodes(file_path):
    d = get_dict_from_xml(file_path)
    # If the document contains only a single placeholder text like [Transitional Provision]
    # or [Amendment] (i.e. a single bracketed token and nothing else), mark it and
    # return no nodes so it won't be loaded into the vector DB.
    if d.get("is_placeholder_only"):
        logger.info(
            "Document flagged as placeholder-only, skipping node generation",
            doc_id=d.get("doc_id"),
        )
        d["nodes"] = []
        return d

    nodes = [
        section_to_nodes(section, d["lang"]) for section in d["all_chunkable_sections"]
    ]
    # Flatten nodes
    nodes = [node for sublist in nodes for node in sublist]
    d["nodes"] = nodes
    return d


def section_to_nodes(section, lang, chunk_size=1024, chunk_overlap=100):
    # Skip placeholder-only sections/schedules early
    if section.get("is_placeholder_only"):
        return []
    if chunk_size < 50:
        raise ValueError("Chunk size must be at least 50 tokens.")
    if "_schedule_" in section["section_id"]:
        # Schedules are chunked during XML parsing
        chunks = section["chunks"]
    else:
        splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        # Split the text into chunks
        chunks = splitter.split_text(section["text"])
    # Create a node from each chunk
    nodes = []
    metadata = {
        "section_id": section["section_id"],
        "parent_id": section.get("parent_id", None),
        "file_id": section["doc_title"],
        "section": section["section_str"],
        "headings": section["heading_str"],
        "doc_id": section["doc_id"],
        "in_force_start_date": section["in_force_start_date"],
        "last_amended_date": section["last_amended_date"],
        "lims_id": section["lims_id"],
        "marginal_note": section["marginal_note"],
        "internal_refs": section["internal_refs"],
        "external_refs": section["external_refs"],
        "node_type": "chunk",
        "lang": lang,
    }
    exclude_embed_keys = list(metadata.keys()) + ["chunk"]
    exclude_llm_keys = exclude_embed_keys.copy()
    exclude_llm_keys.remove("section_id")
    metadata["display_metadata"] = (
        f"{metadata['file_id']}, {metadata['section']}\n{metadata['headings']}"
    )
    original_display_metadata = metadata["display_metadata"]
    original_section_id = metadata["section_id"]
    for i, chunk in enumerate(chunks):
        metadata["chunk"] = f"{i + 1}/{len(chunks)}"
        if len(chunks) > 1:
            metadata["display_metadata"] = (
                f"{original_display_metadata} ({metadata['chunk']})"
            )
            metadata["section_id"] = f"{original_section_id}_{i + 1}"
        nodes.append(
            TextNode(
                text=chunk,
                metadata=metadata,
                excluded_llm_metadata_keys=exclude_llm_keys,
                excluded_embed_metadata_keys=exclude_embed_keys,
                metadata_template="{value}",
                text_template="{metadata_str}\n---\n{content}",
            )
        )
    return nodes


def _get_text(element):
    return "".join(element.itertext()) if element is not None else None


def _get_link(element):
    return (
        element.attrib["link"]
        if element is not None and "link" in element.attrib.keys()
        else None
    )


def parse_table(table_elem):
    tgroup_elem = table_elem.find("tgroup")
    # Parse headers
    thead = tgroup_elem.find("thead") if tgroup_elem is not None else None
    header_rows = []
    if thead is not None:
        for header_row in thead.findall("row"):
            row_cells = [
                " ".join(entry.itertext()).strip() if entry.itertext() else ""
                for entry in header_row.findall("entry")
            ]
            header_rows.append(row_cells)
    # Parse body
    tbody = tgroup_elem.find("tbody") if tgroup_elem is not None else None
    body_rows = []
    if tbody is not None:
        for row in tbody.findall("row"):
            cells = [extract_entry_text(entry) for entry in row.findall("entry")]
            body_rows.append(cells)
    return header_rows, body_rows


def extract_entry_text(entry):
    # Check for <List> child
    list_elem = entry.find("List")
    if list_elem is not None:
        items = []
        for item in list_elem.findall("Item"):
            text_elem = item.find("Text")
            if text_elem is not None:
                # Use itertext() to get all text, including from child tags
                item_text = "".join(text_elem.itertext()).strip()
                if item_text:
                    items.append(item_text)
        return " ".join(items)
    # Otherwise, use direct text (if any)
    return " ".join(entry.itertext()).strip() if entry.itertext() else ""


def markdown_header(header_rows):
    # Join each header row as a Markdown row
    md = ""
    for i, row in enumerate(header_rows):
        md += "| " + " | ".join(row) + " |\n"
        # Add separator after the last header row
        if i == len(header_rows) - 1:
            md += "| " + " | ".join(["---"] * len(row)) + " |\n"
    return md


def markdown_rows(rows):
    return "\n".join(["| " + " | ".join(row) + " |" for row in rows])


# Extract any text after the last <table> in a TableGroup
def get_tablegroup_suffix(elem):
    suffix_texts = []
    found_table = False
    for child in elem:
        if found_table:
            text = _get_joined_text(child).strip()
            if text:
                suffix_texts.append(text)
        if child.tag == "table":
            found_table = True
    return "\n\n".join(suffix_texts)


def get_table_group_and_table_prefix(elem):
    # All siblings of <table> before <table> in <TableGroup>
    prefix_texts = []
    table_elem = None
    for child in elem:
        if child.tag == "table":
            table_elem = child
            break
        text = _get_joined_text(child).strip()
        if text:
            prefix_texts.append(text)
    # All siblings of <tgroup> before <tgroup> in <table>
    if table_elem is not None:
        for child in table_elem:
            if child.tag == "tgroup":
                break
            text = _get_joined_text(child).strip()
            if text:
                prefix_texts.append(text)
    return "\n\n".join(prefix_texts)


def chunk_table(
    headers,
    body_rows,
    chunk_size=1024,
    chunk_overlap=100,
    rows_per_chunk=25,
):
    initial_chunks = []
    md_headers = markdown_header(headers)
    for i in range(0, len(body_rows), rows_per_chunk):
        chunk = body_rows[i : i + rows_per_chunk]
        initial_chunks.append(markdown_rows(chunk))
    splitter = SentenceSplitter(
        chunk_size=chunk_size - len(get_tokenizer()(md_headers)),
        chunk_overlap=chunk_overlap,
        paragraph_separator="\n",
        separator="|",
    )
    chunks = splitter.split_text("\n".join(initial_chunks))
    chunks_with_headers = [md_headers + "\n" + chunk for chunk in chunks]
    return chunks_with_headers


def parse_schedule_with_all_prefix_suffix(schedule_elem):
    # Schedule prefix: everything before first TableGroup
    prefix_elems = schedule_elem.xpath(
        "./*[not(preceding-sibling::*[descendant-or-self::TableGroup]) and not(ancestor-or-self::TableGroup)]"
    )

    # If schedule_elem has a TableGroup "grandchild" (i.e. nested one level lower than expected), the above will still contain content after the tables
    # Thus, we supply an extra argument to _get_joined_text that skips this content
    schedule_prefix = "\n".join(
        _get_joined_text(e, get_schedule_prefix=True)
        for e in prefix_elems
        if _get_joined_text(e, get_schedule_prefix=True).strip()
    )

    # Content outside of, and after, all TableGroups (e.g. HistoricalNotes)
    # (We get this here, outside of the loop, because the get_tablegroup_suffix function only looks WITHIN TableGroups for content after the table, e.g. Footnotes)
    elements_after_tables = schedule_elem.xpath(
        "./*[preceding-sibling::*[descendant-or-self::TableGroup] and not(following-sibling::*[descendant-or-self::TableGroup]) and not(ancestor-or-self::TableGroup)]"
    )
    content_after_tables = "\n".join(
        _get_joined_text(e)
        for e in elements_after_tables
        if _get_joined_text(e).strip()
    )

    tables = []
    for tg in schedule_elem.findall(".//TableGroup"):
        # NOTE: this would be a problem for any files with multiple non-nested tables in a single TableGroup,
        # although I haven't seen any yet
        table = tg.find("table")
        if table is None:
            continue
        header_rows, body_rows = parse_table(table)
        table_group_and_table_prefix = get_table_group_and_table_prefix(tg)
        schedule_suffix = get_tablegroup_suffix(tg)
        tables.append(
            {
                "schedule_prefix": schedule_prefix,
                "table_group_and_table_prefix": table_group_and_table_prefix,
                "header_rows": header_rows,
                "body_rows": body_rows,
                "schedule_suffix": schedule_suffix + "\n\n" + content_after_tables,
            }
        )
    return schedule_prefix, tables


def _chunk_schedule_text(element, chunk_size=1024, chunk_overlap=100):
    prefix_text, tables = parse_schedule_with_all_prefix_suffix(element)
    all_chunks = []
    text_splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    # Deal with Schedule elements that have text instead of tables
    if not tables:
        return text_splitter.split_text(prefix_text)

    for table_info in tables:
        chunk_prefix = ""
        if table_info["schedule_prefix"]:
            chunk_prefix += table_info["schedule_prefix"] + "\n\n"
        if table_info["table_group_and_table_prefix"]:
            chunk_prefix += table_info["table_group_and_table_prefix"] + "\n\n"
        chunk_suffix = (
            "\n\n" + table_info["schedule_suffix"]
            if table_info["schedule_suffix"]
            else ""
        )
        chunks = chunk_table(
            table_info["header_rows"],
            table_info["body_rows"],
            chunk_size,
            chunk_overlap,
            rows_per_chunk=25,
        )

        # In some cases, there may be really long prefixes or suffixes (e.g. Food and Drug Regulations)
        # These create problems because they can exceed the max node insertion length of 8192 when combined with tables
        # It's *probably* safe to assume that ridiculously long prefixes/suffixes don't actually need to be attached to the tables,
        # so in that case we split them and include them as separate chunks instead
        chunk_prefix_pieces = text_splitter.split_text(chunk_prefix)
        chunk_suffix_pieces = text_splitter.split_text(chunk_suffix)

        all_chunks.extend(
            (chunk_prefix_pieces if len(chunk_prefix_pieces) > 1 else [])
            + [
                (chunk_prefix if len(chunk_prefix_pieces) < 2 else "")
                + chunk
                + (chunk_suffix + "\n" if len(chunk_suffix_pieces) < 2 else "")
                for chunk in chunks
            ]
            + (chunk_suffix_pieces if len(chunk_suffix_pieces) > 1 else [])
        )

    return all_chunks


def _get_joined_text(
    element,
    exclude_tags=["MarginalNote", "Label"],
    break_tags=[
        "Provision",
        "Subsection",
        "Paragraph",
        "Definition",
        "row",
        "TableGroup",
        "HistoricalNote",
        "MarginalNote",
        # "OriginatingRef",
    ],
    double_break_tags=["Subsection", "TableGroup"],
    pipe_tags=["entry"],
    em_tags=[
        "DefinedTermEn",
        "DefinedTermFr",
        "XRefExternal",
        "XRefInternal",
        "Emphasis",
    ],
    strong_tags=["MarginalNote", "TitleText"],
    underline_tags=[],
    tab_tags={"Subparagraph": 2, "Clause": 3, "Subclause": 4},
    get_schedule_prefix=False,
):
    def stylized_text(text, tag):
        if tag in em_tags:
            return f"*{text}*"
        if tag in strong_tags:
            return f"**{text}**"
        if tag in underline_tags:
            return f"__{text}__"
        # if tag in strike_tags:
        #     return f"~~{text}~~"
        return text

    all_text = []
    exclude_tags = exclude_tags.copy()
    for e in element.iter():
        if e.tag in exclude_tags:
            exclude_tags.remove(e.tag)
            continue
        if get_schedule_prefix and e.tag == "TableGroup":
            break  # Removes content after nested TableGroups in schedule prefixes, if necessary (see above)
        if e.text and e.text.strip():
            all_text.append(stylized_text(e.text.strip(), e.tag))
        if e.tail and e.tail.strip():
            all_text.append(e.tail.strip())
        if e.tag in break_tags or (e.tag == "Section" and element.tag == "Schedule"):
            all_text.append("\n")
        elif e.tag in double_break_tags:
            all_text.append("\n\n")
        elif e.tag in tab_tags:
            all_text.append(f"\n{' ' * tab_tags[e.tag]}-")
        if e.tag in pipe_tags:
            all_text.append("|")
        if e.tag == "tbody":
            all_text.append("\n<tbody>")
    text = (
        " ".join(all_text)
        .replace(" \n ", "\n")
        .strip()
        .replace("\u2002", " ")
        .replace("( ", "(")
        .replace(" )", ")")
        .replace(" .", ".")
        .replace("* ;", "*;")
        .replace("* ,", "*,")
        .replace("* .", "*.")
        .strip()
    )
    # When a line ends in a pipe, it should also start with a pipe and space
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("-"):
            line = "\n" + line.rstrip()
            lines[i] = line
        else:
            line = line.strip()
            if i > 0 and lines[i - 1].strip().startswith("-"):
                line = "\n" + line
                lines[i] = line
        if line.endswith("|"):
            lines[i] = "| " + line
        # Replace the <tbody> tag with | --- | --- | --- | etc. for tables
        if line == "<tbody>" and i > 0 and lines[i - 1].strip().endswith("|"):
            lines[i] = "| --- " * (len(lines[i - 1].split("|")) - 2) + "|"
        elif line == "<tbody>":
            lines[i] = ""
    text = "\n".join(lines)
    return text


def _add_cumulative_counts(dup_list):
    """
    Adds "cumulative count" suffixes to duplicated items in a list
    e.g. [apple, orange, apple, banana, cherry, apple, orange] becomes
    [apple1, orange1, apple2, banana, cherry, apple3, orange2]
    """

    counter = Counter(dup_list)
    cumulative_counts = {name: 1 for name in counter}

    deduped = []
    for name in dup_list:
        new = f"{name}__{str(cumulative_counts[name])}" if counter[name] > 1 else name
        cumulative_counts[name] += 1
        deduped.append(new)

    return deduped


def get_dict_from_xml(xml_filename):
    # Extract a JSON serializable dictionary from a act/regulation XML file
    dom = ET.parse(xml_filename)
    root = dom.getroot()
    # French regulations have slightly different filenames, but we want a unique ID
    # to link the English and French versions
    filename = os.path.basename(xml_filename).replace(".xml", "")

    # Band-aid fix for Constitution Act(s)
    if "_E" in filename:
        d_lang = "eng"
        filename = filename.replace("_E", "")
    elif "_F" in filename:
        d_lang = "fra"
        filename = filename.replace("_F_Rapport", "")
    else:
        d_lang = os.path.basename(os.path.dirname(os.path.dirname(xml_filename)))

    # Replace "DORS-" with "SOR-", "TR-" with "SI-" and "_ch." with "_c."
    eng_id = (
        filename.replace("DORS-", "SOR-").replace("TR-", "SI-").replace("_ch.", "_c.")
    )
    d = {
        "id": eng_id,
        "lang": d_lang,
        "filename": filename,
        "type": "act" if root.tag == "Statute" else "regulation",
        "short_title": _get_text(root.find(".//ShortTitle")),
        "long_title": _get_text(root.find(".//LongTitle")),
        "bill_number": _get_text(root.find(".//BillNumber")),
        "instrument_number": _get_text(root.find(".//InstrumentNumber")),
        "consolidated_number": _get_text(root.find(".//ConsolidatedNumber")),
        "last_amended_date": root.attrib.get(
            "{http://justice.gc.ca/lims}lastAmendedDate", None
        ),
        "current_date": root.attrib.get(
            "{http://justice.gc.ca/lims}current-date", None
        ),
        "in_force_start_date": root.attrib.get(
            "{http://justice.gc.ca/lims}inforce-start-date", None
        ),
        "enabling_authority": _get_link(root.find(".//EnablingAuthority/XRefExternal")),
        "preamble": get_preamble(root),
        "sections": [
            section
            for section in [
                get_section(section, xml_filename=filename)
                for section in root.findall(".//Section")
            ]
            if section is not None
        ],
        "schedules": [
            schedule
            for schedule in [
                get_schedule(schedule) for schedule in root.findall(".//Schedule")
            ]
            if schedule is not None
        ],
    }
    # Aggregate all internal and external references and count instances of each
    for ref_name in ["internal_refs", "external_refs"]:
        ref_list = [
            ref
            for section in d["sections"]
            for ref in section[ref_name]
            if ref["link"] is not None
        ]
        ref_list_set = set([ref["link"] for ref in ref_list])
        d[ref_name] = [
            {
                "link": link,
                "count": len([ref for ref in ref_list if ref["link"] == link]),
            }
            for link in ref_list_set
        ]
    # Some pretty-print and/or unique versions of the fields
    d["doc_id"] = f"{d['id']}_{d['lang']}"
    d["title_str"] = d["short_title"] if d["short_title"] else d["long_title"]
    for section in d["sections"]:
        section["section_id"] = f"{d['doc_id']}_section_{section['id']}"
        section["heading_str"] = get_heading_str(section)
        section["section_str"] = f"Section {section['id']}"
        section["all_str"] = "\n".join(
            [
                d["title_str"],
                section["section_str"],
                section["heading_str"],
                section["text"],
            ]
        )
        for i, subsection in enumerate(section["subsections"]):
            subsection["section_id"] = (
                f"{d['doc_id']}_subsection_{section['id']}{subsection['id']}"
            )
            subsection["parent_id"] = section["section_id"]
            subsection["heading_str"] = get_heading_str(subsection)
            subsection["section_str"] = (
                f"Sub{section['section_str'].lower()}{subsection['id']}"
            )
            # Often the first subsection should have a marginal note (both as metadata, and as bold text in first line of "text")
            # but the XML is coded oddly so we need to pull this from the parent section.
            if i == 0 and section["marginal_note"]:
                subsection["marginal_note"] = section["marginal_note"]
            subsection["all_str"] = "\n".join(
                [
                    d["title_str"],
                    subsection["section_str"],
                    subsection["heading_str"],
                    subsection["text"],
                ]
            )
    for schedule in d["schedules"]:
        schedule["section_id"] = f"{d['doc_id']}_schedule_{schedule['id']}"
        schedule["heading_str"] = get_heading_str(schedule)
        schedule["section_str"] = schedule["id"]
        schedule["all_str"] = "\n".join(
            [
                d["title_str"],
                (schedule["id"] if schedule["id"] else "Schedule"),
                "",
                "\n".join(schedule["chunks"]),
            ]
        )
    # Finally, the preamble also needs a "all_str" field
    if d["preamble"]:
        d["preamble"][0]["section_id"] = f"{d['doc_id']}_preamble"
        d["preamble"][0]["heading_str"] = get_heading_str(d["preamble"][0])
        d["preamble"][0]["section_str"] = "Preamble"
        d["preamble"][0]["all_str"] = "\n".join(
            [
                d["title_str"],
                "Preamble",
                "",
                d["preamble"][0]["text"],
            ]
        )
        for section in d["preamble"][0]["subsections"]:
            section["section_id"] = (
                f"{d['doc_id']}_preamble_provision_{section['id'] + 1}"
            )
            section["parent_id"] = d["preamble"][0]["section_id"]
            section["heading_str"] = get_heading_str(section)
            section["section_str"] = f"Preamble provision {section['id'] + 1}"
            section["all_str"] = "\n".join(
                [
                    d["title_str"],
                    section["section_str"],
                    section["heading_str"],
                    section["text"],
                ]
            )
    # Add a list of all sections, including preamble and schedules and subsections
    d["all_chunkable_sections"] = []
    keep_keys = [
        "section_id",
        "parent_id",
        "section_str",
        "heading_str",
        "text",
        "id",
        "marginal_note",
        "in_force_start_date",
        "last_amended_date",
        "internal_refs",
        "external_refs",
        "lims_id",
        "is_placeholder_only",
    ]
    if d["preamble"]:
        # Keep only the keys we need from d["preamble"][0]
        d["all_chunkable_sections"].append(
            {k: v for k, v in d["preamble"][0].items() if k in keep_keys}
        )
        for p in d["preamble"][0]["subsections"]:
            d["all_chunkable_sections"].append(
                {k: v for k, v in p.items() if k in keep_keys}
            )
    for s in d["sections"]:
        d["all_chunkable_sections"].append(
            {k: v for k, v in s.items() if k in keep_keys}
        )
        for ss in s["subsections"]:
            d["all_chunkable_sections"].append(
                {k: v for k, v in ss.items() if k in keep_keys}
            )
    for s in d["schedules"]:
        d["all_chunkable_sections"].append(
            {k: v for k, v in s.items() if k in keep_keys or k == "chunks"}
        )

    cumu_count_section_ids = _add_cumulative_counts(
        [s["section_id"] for s in d["all_chunkable_sections"]]
    )

    for i, s in enumerate(d["all_chunkable_sections"]):
        s["doc_id"] = d["doc_id"]
        s["section_id"] = cumu_count_section_ids[i]
        s["doc_title"] = d["title_str"]
        s["index"] = i
        if s["marginal_note"]:
            if "text" in s:
                s["text"] = f"**{s['marginal_note']}**\n{s['text']}"
            elif s["chunks"]:
                # In rare cases of Schedules with marginal notes, just append them to the first chunk
                # NOTE: this assumes that the marginal_note will always be short enough NOT to take the chunk over the length limit
                # I have only seen this case happen once, and the marginal note was a single word.
                # But it COULD break Schedules with giant tables and big marginal notes, if any exist
                s["chunks"][0] = f"**{s['marginal_note']}**\n{s['chunks'][0]}"
        # If this specific section/schedule is placeholder-only, record it in SKIPPED_TEXTS
        if s.get("is_placeholder_only"):
            entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "file": xml_filename,
                "doc_id": d.get("doc_id"),
                "section_index": i,
                "section_id": s.get("section_id"),
                "text": (
                    s.get("text") if s.get("text") else "\n".join(s.get("chunks", []))
                )
                if (s.get("text") or s.get("chunks"))
                else "",
            }
            with SKIPPED_TEXTS_LOCK:
                SKIPPED_TEXTS.append(entry)
    # Detect documents that only contain a single placeholder bracketed token
    # e.g. files where the only substantive text is "[Transitional Provision]" or "[Amendment]"
    # Gather all non-empty chunkable texts (section text or schedule chunks)
    texts = []
    for s in d["all_chunkable_sections"]:
        if "text" in s and s["text"] and s["text"].strip():
            texts.append(s["text"].strip())
        elif "chunks" in s and s["chunks"]:
            for c in s["chunks"]:
                if c and c.strip():
                    texts.append(c.strip())

    # If there is exactly one non-empty text and it matches a single bracketed token, flag it
    is_placeholder_only = False
    if len(texts) == 1:
        candidate = texts[0]
        # Match strings like "[Transitional Provision]" (allow inner content and optional surrounding whitespace)
        if re.match(r"^\s*\[.+\]\s*$", candidate):
            is_placeholder_only = True

    d["is_placeholder_only"] = is_placeholder_only
    if is_placeholder_only:
        logger.info(
            "XML file contains only a bracketed placeholder; flagging as placeholder-only",
            file=xml_filename,
            doc_id=d.get("doc_id"),
        )

    return d


def get_heading_str(section):
    return " > ".join(section["headings"])


def get_section(section, last_amended_date=None, xml_filename: str = None):
    # If the section has an ancestor <Schedule> tag, record and skip it
    if section.xpath("ancestor::Schedule"):
        try:
            # Record skipped section to disk/in-memory for reporting only in DEBUG
            if getattr(settings, "DEBUG", False):
                save_skipped_section_texts(section, xml_filename=xml_filename)
        except Exception:
            # Best-effort: don't fail parsing if saving the skipped section fails
            logger.exception("Failed to save skipped section text")
        return None
    # Subsections do not have a last_amended_date, so we pass it down from the parent
    last_amended_date = section.attrib.get(
        "{http://justice.gc.ca/lims}lastAmendedDate", last_amended_date
    )
    text = _get_joined_text(section)
    # Detect placeholder-only section (single bracketed token)
    is_placeholder_only = bool(text and re.match(r"^\s*\[.+\]\s*$", text.strip()))

    return {
        "id": _get_text(section.find(".//Label")),
        "headings": get_headings(section),
        "marginal_note": _get_text(section.find("MarginalNote")),
        "text": text,
        "is_placeholder_only": is_placeholder_only,
        "in_force_start_date": section.attrib.get(
            "{http://justice.gc.ca/lims}inforce-start-date", None
        ),
        "last_amended_date": last_amended_date,
        "subsections": [
            get_section(subsection, last_amended_date, xml_filename)
            for subsection in section.findall(".//Subsection")
        ],
        "external_refs": get_external_xrefs(section),
        "internal_refs": get_internal_xrefs(section),
        "lims_id": section.attrib.get("{http://justice.gc.ca/lims}id", None),
    }


def get_external_xrefs(section):
    # External references have an explicit link attribute
    return [
        {
            "link": xref.attrib.get("link", None),
            "reference_type": xref.attrib.get("reference-type", None),
            "text": xref.text,
        }
        for xref in section.findall(".//XRefExternal")
    ]


def get_internal_xrefs(section):
    # Internal references are always a section number which is the text
    return [
        {
            "link": xref.text,
        }
        for xref in section.findall(".//XRefInternal")
    ]


def get_preamble(root):
    # Returns an array with a single element, the preamble, or no elements
    # so that it can be easily prepended to the sections array
    preamble = root.find(".//Preamble")
    if preamble is None:
        return []
    preamble.findall(".//Provision")
    return [
        {
            "id": "preamble",
            "headings": get_headings(preamble),
            "marginal_note": None,
            "text": _get_joined_text(preamble),
            "is_placeholder_only": False,
            "in_force_start_date": preamble.attrib.get(
                "{http://justice.gc.ca/lims}inforce-start-date", None
            ),
            "last_amended_date": preamble.attrib.get(
                "{http://justice.gc.ca/lims}lastAmendedDate", None
            ),
            "subsections": [
                {
                    "id": i,
                    "text": _get_joined_text(provision),
                    "is_placeholder_only": bool(
                        re.match(
                            r"^\s*\[.+\]\s*$",
                            (_get_joined_text(provision) or "").strip(),
                        )
                    ),
                    "headings": get_headings(provision),
                    "marginal_note": None,
                    "in_force_start_date": provision.attrib.get(
                        "{http://justice.gc.ca/lims}inforce-start-date", None
                    ),
                    "last_amended_date": provision.attrib.get(
                        "{http://justice.gc.ca/lims}lastAmendedDate", None
                    ),
                    "internal_refs": get_internal_xrefs(provision),
                    "external_refs": get_external_xrefs(provision),
                    "lims_id": provision.attrib.get(
                        "{http://justice.gc.ca/lims}id", None
                    ),
                }
                for i, provision in enumerate(preamble.findall(".//Provision"))
            ],
            "internal_refs": get_internal_xrefs(preamble),
            "external_refs": get_external_xrefs(preamble),
            "lims_id": preamble.attrib.get("{http://justice.gc.ca/lims}id", None),
        }
    ]


def get_schedule(schedule):
    # if schedule "id" attribute is RelatedProvs or NifProvs, skip it
    if schedule.attrib.get("id", None) in ["RelatedProvs", "NifProvs"]:
        return None
    chunks = _chunk_schedule_text(schedule)
    # Detect placeholder-only schedule (single chunk that is a bracketed token)
    texts = [c.strip() for c in chunks if c and c.strip()]
    is_placeholder_only = False
    if len(texts) == 1 and re.match(r"^\s*\[.+\]\s*$", texts[0]):
        is_placeholder_only = True

    return {
        "id": _get_text(schedule.find(".//Label")),
        "headings": [
            _get_text(schedule.find(".//TitleText")) or "",
        ],
        "marginal_note": _get_text(schedule.find(".//MarginalNote")),
        "chunks": chunks,
        "is_placeholder_only": is_placeholder_only,
        "in_force_start_date": schedule.attrib.get(
            "{http://justice.gc.ca/lims}inforce-start-date", None
        ),
        "last_amended_date": schedule.attrib.get(
            "{http://justice.gc.ca/lims}lastAmendedDate", None
        ),
        "subsections": [],
        "internal_refs": get_internal_xrefs(schedule),
        "external_refs": get_external_xrefs(schedule),
        "originating_ref": _get_text(schedule.find(".//OriginatingRef")),
        "lims_id": schedule.attrib.get("{http://justice.gc.ca/lims}id", None),
    }


def get_headings(element):
    """
    Headings are found in the inner text of <Heading> tags.
    Returns an array of headings, i.e. ["HeadingLevel1", "HeadingLevel2", "HeadingLevel3"]
    In each case (level 1, 2, 3), the returned heading is always the one CLOSEST (i.e. above) the element
    Note that headings are NOT correctly nested in the hierarchy
    They may be siblings to the element etc. We cannot rely on xpath
    """
    # Brute force solution: Traverse document from top to bottom, keeping track of headings until we hit the element
    headings = [None, None, None, None, None, None]  # 6 levels of headings
    root = element.getroottree().getroot()
    for e in root.iter():
        if e.tag == "Heading":
            level = int(e.attrib.get("level", 1))
            headings[level - 1] = _get_joined_text(e)
            # Remove formatting (e.g. bold) from headings
            headings[level - 1] = (
                headings[level - 1].replace("**", "").replace("__", "")
            )
            for i in range(level, 6):
                headings[i] = None
        if e == element:
            break
    return [h for h in headings if h is not None]


def get_sha_256_hash(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(4096):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def drop_indexes():
    db = settings.DATABASES["vector_db"]
    url = (
        f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    engine = create_engine(url)
    # Drop all indexes on table data_laws_lois__
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            DROP INDEX IF EXISTS data_laws_lois__chunk_text_idx;
            DROP INDEX IF EXISTS data_laws_lois__doc_id_idx;
            DROP INDEX IF EXISTS data_laws_lois__in_force_start_date_idx;
            DROP INDEX IF EXISTS data_laws_lois__lang_idx;
            DROP INDEX IF EXISTS data_laws_lois__last_amended_date_idx;
            DROP INDEX IF EXISTS data_laws_lois__node_id_idx;
            DROP INDEX IF EXISTS data_laws_lois___embedding_idx;
            DROP INDEX IF EXISTS laws_lois___idx;
            DROP INDEX IF EXISTS laws_lois___idx_1;
            DROP INDEX IF EXISTS laws_lois___idx_2;
            """
            )
        )


def recreate_indexes(node_id=True, jsonb=True, hnsw=True):
    """
    Recreate indexes on data_laws_lois__ table for optimal vector search performance.
    Uses CREATE INDEX CONCURRENTLY to avoid blocking queries during index creation.

    Key insight: COMPOUND INDEXES COMPETE WITH VECTOR INDEX!
    PostgreSQL query planner prefers any compound index over vector index
    when both language and node_type filters are present, causing 10-14x
    performance degradation (35ms -> 365ms).

    Solution: Minimal indexing strategy that preserves vector index usage.
    """
    # Build SQLAlchemy engine from Django settings
    db = settings.DATABASES["vector_db"]
    url = (
        f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    engine = create_engine(url)

    # List of indexes to create
    # Note: CREATE INDEX CONCURRENTLY cannot run inside a transaction block
    # so we need to use AUTOCOMMIT isolation level
    indexes_to_create = [
        # Node ID index
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__node_id_idx
            ON data_laws_lois__ (node_id)
        """,
        # Single-column indexes for filtering and sorting
        # These don't compete with vector index since they're not compound
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__doc_id_idx
          ON data_laws_lois__ USING btree((metadata_ ->> 'doc_id'))
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__lang_idx
          ON data_laws_lois__ USING btree((metadata_ ->> 'lang'))
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__in_force_start_date_idx
          ON data_laws_lois__ USING btree((metadata_ ->> 'in_force_start_date'))
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__last_amended_date_idx
          ON data_laws_lois__ USING btree((metadata_ ->> 'last_amended_date'))
        """,
        # Full-text search index (single, not compound/partial by lang)
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois__chunk_text_idx
          ON data_laws_lois__
          USING gin(text_search_tsv)
          WHERE (metadata_ ->> 'node_type') = 'chunk'
        """,
    ]

    # Add HNSW vector index if requested
    if hnsw:
        # High-performance vector (HNSW) index for chunks only
        # This MUST be the primary index used for similarity search
        # Match the exact WHERE clause pattern used by LlamaIndex queries
        indexes_to_create.append(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS data_laws_lois___embedding_idx
              ON data_laws_lois__
              USING hnsw(embedding vector_cosine_ops)
              WITH (m = 16, ef_construction = 256)
              WHERE (metadata_ ->> 'node_type') = 'chunk'
            """
        )

    # Create indexes with AUTOCOMMIT (required for CONCURRENTLY)
    with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        # Set maintenance_work_mem for better index build performance
        # Note: This setting is per-session and doesn't require transaction
        conn.execute(
            text(
                f"SET maintenance_work_mem = '{settings.VECTORDB_MAINTENANCE_WORK_MEM_HEAVY}'"
            )
        )

        # Create each index concurrently
        for index_sql in indexes_to_create:
            try:
                conn.execute(text(index_sql))
            except Exception as e:
                # Log but continue - index might already exist or there might be a transient issue
                logger.warning(f"Issue creating index (continuing): {e}")

    # ANALYZE only for fresh stats — VACUUM not necessary after index creation
    vacuum_analyze_laws_table(engine, analyze_only=True)

    # Note: CREATE INDEX CONCURRENTLY returns immediately while indexes build in background.
    # Pre-warming will be done asynchronously after polling for completion.


def wait_for_indexes_and_prewarm(max_wait_seconds=3600, check_interval=30):
    """
    Poll for index build completion, then pre-warm the indexes and table.

    CREATE INDEX CONCURRENTLY runs in the background. This function polls
    pg_stat_progress_create_index to detect when all indexes are ready,
    then pre-warms them into buffer cache for optimal query performance.

    Args:
        max_wait_seconds: Maximum time to wait for indexes (default 1 hour)
        check_interval: Seconds between checks (default 30s)

    Returns:
        dict: Status of pre-warming operation
    """
    import time

    db = settings.DATABASES["vector_db"]
    url = (
        f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    engine = create_engine(url)

    start_time = time.time()
    indexes_to_check = [
        "data_laws_lois__node_id_idx",
        "data_laws_lois__doc_id_idx",
        "data_laws_lois__lang_idx",
        "data_laws_lois__in_force_start_date_idx",
        "data_laws_lois__last_amended_date_idx",
        "data_laws_lois__chunk_text_idx",
        "data_laws_lois___embedding_idx",
    ]

    logger.info(
        "Starting to poll for index build completion...",
        indexes_count=len(indexes_to_check),
    )

    # Poll for completion
    while time.time() - start_time < max_wait_seconds:
        with engine.connect() as conn:
            # Check if any indexes are still building
            result = conn.execute(
                text(
                    """
                    SELECT COUNT(*) as building_count
                    FROM pg_stat_progress_create_index
                    WHERE relid = 'data_laws_lois__'::regclass
                """
                )
            )
            row = result.fetchone()
            building_count = row[0] if row else 0

            if building_count == 0:
                # Double-check that indexes actually exist
                result = conn.execute(
                    text(
                        """
                        SELECT indexname 
                        FROM pg_indexes 
                        WHERE tablename = 'data_laws_lois__'
                    """
                    )
                )
                existing_indexes = [row[0] for row in result.fetchall()]

                # Check if all expected indexes exist
                missing = [
                    idx for idx in indexes_to_check if idx not in existing_indexes
                ]
                if missing:
                    logger.warning(
                        "Some indexes missing, continuing to wait...",
                        missing_indexes=missing,
                        elapsed_seconds=int(time.time() - start_time),
                    )
                    time.sleep(check_interval)
                    continue

                logger.info(
                    "All indexes built successfully",
                    elapsed_seconds=int(time.time() - start_time),
                    indexes_count=len(existing_indexes),
                )
                break
            else:
                logger.debug(
                    "Indexes still building...",
                    building_count=building_count,
                    elapsed_seconds=int(time.time() - start_time),
                )
                time.sleep(check_interval)
    else:
        # Timeout reached
        logger.warning(
            "Timeout waiting for indexes to build, skipping pre-warm",
            max_wait_seconds=max_wait_seconds,
        )
        return {"status": "timeout", "waited_seconds": int(time.time() - start_time)}

    # All indexes ready, now pre-warm
    logger.info("Pre-warming indexes and table...")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_prewarm"))

        prewarmed = []
        failed = []

        # Pre-warm each index
        for index_name in indexes_to_check:
            try:
                result = conn.execute(
                    text(f"SELECT pg_prewarm('{index_name}', 'buffer')")
                )
                blocks = result.scalar()
                prewarmed.append({"name": index_name, "blocks": blocks})
                logger.debug(f"Pre-warmed {index_name}", blocks=blocks)
            except Exception as e:
                failed.append({"name": index_name, "error": str(e)})
                logger.warning(f"Failed to pre-warm {index_name}", error=str(e))

        # Pre-warm main table
        try:
            result = conn.execute(
                text("SELECT pg_prewarm('data_laws_lois__', 'buffer')")
            )
            blocks = result.scalar()
            prewarmed.append({"name": "data_laws_lois__", "blocks": blocks})
            logger.debug("Pre-warmed main table", blocks=blocks)
        except Exception as e:
            failed.append({"name": "data_laws_lois__", "error": str(e)})
            logger.warning("Failed to pre-warm main table", error=str(e))

    logger.info(
        "Pre-warming complete",
        prewarmed_count=len(prewarmed),
        failed_count=len(failed),
        total_wait_seconds=int(time.time() - start_time),
    )

    return {
        "status": "success",
        "waited_seconds": int(time.time() - start_time),
        "prewarmed": prewarmed,
        "failed": failed,
    }


def vacuum_analyze_laws_table(engine=None, analyze_only=False, timeout_seconds=None):
    """
    Refresh table statistics for data_laws_lois__.

    Default behavior is to run ANALYZE only (fast, non-intrusive). If analyze_only=False,
    will attempt VACUUM ANALYZE, but will fall back to ANALYZE if a concurrent VACUUM is
    already in progress for this table.

    Args:
        engine: Optional SQLAlchemy engine to reuse.
        analyze_only (bool): If True, run ANALYZE only. If False, try VACUUM ANALYZE.
        timeout_seconds (int|float|None): Optional statement timeout for this session.
    """
    import time as _time

    if engine is None:
        db = settings.DATABASES["vector_db"]
        url = (
            f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}"
            f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
        )
        engine = create_engine(url)

    start = _time.time()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # Optionally cap the session statement timeout, so we don't hang indefinitely
        if timeout_seconds and timeout_seconds > 0:
            ms = int(timeout_seconds * 1000)
            try:
                conn.execute(text(f"SET statement_timeout = '{ms}ms'"))
            except Exception:
                # Best-effort; continue even if setting fails
                pass

        # If we plan to VACUUM, but a VACUUM for this table is already running, just ANALYZE
        if not analyze_only:
            try:
                res = conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM pg_stat_progress_vacuum pv
                        JOIN pg_class c ON c.oid = pv.relid
                        WHERE c.relname = 'data_laws_lois__'
                        LIMIT 1
                        """
                    )
                )
                if res.fetchone() is not None:
                    logger.info(
                        "Concurrent VACUUM detected for data_laws_lois__, falling back to ANALYZE"
                    )
                    analyze_only = True
            except Exception:
                # If the introspection fails, proceed with requested operation
                pass

        try:
            if analyze_only:
                logger.info(
                    "Running ANALYZE on data_laws_lois__ (fast stats refresh)..."
                )
                conn.execute(text("ANALYZE data_laws_lois__;"))
            else:
                logger.info(
                    "Running VACUUM ANALYZE on data_laws_lois__ (may take a while)..."
                )
                conn.execute(text("VACUUM ANALYZE data_laws_lois__;"))
        finally:
            duration = int(_time.time() - start)
            logger.info(
                "Stats refresh complete", analyze_only=analyze_only, seconds=duration
            )


def drop_legacy_compound_indexes():
    """
    Drop legacy compound/unused indexes that can compete with vector index selection
    and slow down maintenance. Safe to call repeatedly.

    Targets:
      - laws_lois___idx
      - laws_lois___idx_1
      - laws_lois___idx_2

    Uses DROP INDEX CONCURRENTLY to avoid blocking.
    """
    db = settings.DATABASES["vector_db"]
    url = (
        f"postgresql+psycopg2://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    engine = create_engine(url)
    legacy_indexes = [
        "laws_lois___idx",
        "laws_lois___idx_1",
        "laws_lois___idx_2",
    ]
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for idx in legacy_indexes:
            try:
                conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {idx};"))
                logger.info("Dropped legacy index (if existed)", index=idx)
            except Exception as e:
                logger.warning(
                    "Issue dropping legacy index (continuing)", index=idx, error=str(e)
                )


def calculate_job_elapsed_time(job_status):
    """
    Calculate elapsed time for a job.
    If the job is finished, return the total duration (finished_at - started_at).
    If the job is still running, return the current duration (now - started_at).

    Args:
        job_status: JobStatus object with started_at and finished_at fields

    Returns:
        str: Formatted elapsed time string (e.g., "0:05:23") or "-" if no start time
    """
    if not job_status.started_at:
        return "-"

    if job_status.finished_at:
        # Job is finished, use total duration
        elapsed = (job_status.finished_at - job_status.started_at).total_seconds()
    else:
        # Job is still running, use current duration
        elapsed = (now() - job_status.started_at).total_seconds()

    elapsed_td = timedelta(seconds=int(elapsed))
    return str(elapsed_td)


def save_skipped_texts(output_path: str = None) -> str:
    """
    Write the accumulated SKIPPED_TEXTS to a single text file and return the path.

    Default location: settings.MEDIA_ROOT/laws_skipped_texts.txt

    TODO: temporary reporting mechanism — replace with structured logging or
    admin-facing report once behavior is confirmed.
    """
    if output_path is None:
        output_path = os.path.join(settings.MEDIA_ROOT, "laws_skipped_texts.txt")

    # Snapshot and write
    with SKIPPED_TEXTS_LOCK:
        items = list(SKIPPED_TEXTS)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(
                f"[{it.get('timestamp')}] {it.get('file')} {it.get('doc_id')} section_index={it.get('section_index')} section_id={it.get('section_id')}\n"
            )
            fh.write(f"{it.get('text')}\n\n")

    logger.info("Saved skipped texts file", path=output_path, count=len(items))
    return output_path


def save_skipped_section_texts(
    section, xml_filename: str = None, output_path: str = None
) -> str:
    """
    If the provided lxml `section` element is a skipped section (i.e. it has an
    ancestor <Schedule>), record it in the in-memory `SKIPPED_TEXTS` list and
    append a human-readable entry to a text file under MEDIA_ROOT.

    Returns the path to the file written to, or None if the section was not
    considered skipped and therefore not written.

    Parameters:
        section: lxml.etree.Element - the <Section> element to inspect
        xml_filename: optional string to identify the source XML file in the
            saved report (if omitted, 'unknown' will be used)
        output_path: optional full path to write the report file. If omitted,
            defaults to settings.MEDIA_ROOT/laws_skipped_section_texts.txt
    """
    if section is None:
        return None

    # Only record skipped sections in debug mode to avoid writing files in prod
    if not getattr(settings, "DEBUG", False):
        return None

    # Only treat sections that are nested under a Schedule as "skipped"
    try:
        if not section.xpath("ancestor::Schedule"):
            return None
    except Exception:
        # If xpath fails for any reason, do not treat as skipped
        return None

    text = _get_joined_text(section) if section is not None else ""
    text = text.strip() if text else ""

    section_label = _get_text(section.find(".//Label"))

    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "file": xml_filename if xml_filename else "unknown",
        "doc_id": None,
        "section_index": None,
        "section_id": section_label,
        "text": text,
    }

    # Append to in-memory collection for parity with existing behavior
    with SKIPPED_TEXTS_LOCK:
        SKIPPED_TEXTS.append(entry)

    if output_path is None:
        output_path = os.path.join(
            settings.MEDIA_ROOT, "laws_skipped_section_texts.txt"
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(
            f"[{entry.get('timestamp')}] {entry.get('file')} section_id={entry.get('section_id')}\n"
        )
        fh.write(f"{entry.get('text')}\n\n")

    logger.info(
        "Saved skipped section text",
        path=output_path,
        file=entry.get("file"),
        section_id=entry.get("section_id"),
    )

    return output_path
