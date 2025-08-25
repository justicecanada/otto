import hashlib
import os
import time
import zipfile
from datetime import timedelta

from django.conf import settings
from django.utils.timezone import now

import markdown
import requests
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from lxml import etree as ET
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger

from librarian.utils.markdown_splitter import MarkdownSplitter

md = markdown.Markdown(extensions=["fenced_code", "nl2br", "tables"], tab_length=2)

logger = get_logger(__name__)


# SAMPLE_LAW_IDS = [
#     "A-0.6",  # Accessible Canada Act
#     "SOR-2021-241",  # Accessible Canada Regulations
#     "A-2",  # Aeronautics Act
#     "B-9.01",  # Broadcasting Act
#     "SOR-97-555",  # Broadcasting Distribution Regulations
#     "SOR-96-433",  # Canadian Aviation Regulations
#     "SOR-2011-318",  # Canadian Aviation Security Regulations, 2012
#     "C-15.1",  # Canadian Energy Regulator Act
#     "C-15.31",  # Canadian Environmental Protection Act, 1999
#     "C-24.5",  # Cannabis Act
#     "SOR-2018-144",  # Cannabis Regulations
#     "C-46",  # Criminal Code
#     "SOR-2021-25",  # Cross-border Movement of Hazardous Waste and Hazardous Recyclable Material Regulations
#     "F-14",  # Fisheries Act
#     "SOR-93-53",  # Fishery (General) Regulations
#     "C.R.C.,_c._870",  # Food and Drug Regulations
#     "F-27",  # Food and Drugs Act
#     "I-2.5",  # Immigration and Refugee Protection Act
#     "SOR-2002-227",  # Immigration and Refugee Protection Regulations
#     "I-21",  # Interpretation Act
#     "SOR-2016-151",  # Multi-Sector Air Pollutants Regulations
#     "SOR-2010-189",  # Renewable Fuels Regulations
#     "S-22",  # Statutory Instruments Act
#     "C.R.C.,_c._1509",  # Statutory Instruments Regulations
#     "A-1",  # Access to Information Act
#     "F-11",  # Financial Administration Act
#     "N-22",  # Canadian Navigable Waters Act
# ]

SAMPLE_LAW_IDS = [
    "SOR-2021-25",  # Cross-border Movement of Hazardous Waste and Hazardous Recyclable Material Regulations
]

constitution_dir = os.path.join(settings.BASE_DIR, "laws", "data")
CONSTITUTION_FILE_PATHS = (
    os.path.join(constitution_dir, "Constitution 2020_E.xml"),
    os.path.join(constitution_dir, "Constitution 2020_F_Rapport.xml"),
)


def _download_repo():
    # Download and extract to media folder
    repo_url = (
        "https://github.com/justicecanada/laws-lois-xml/archive/refs/heads/main.zip"
    )
    zip_file_path = os.path.join(settings.MEDIA_ROOT, "laws-lois-xml.zip")

    logger.info("Downloading laws-lois-xml repo to media folder...")
    response = requests.get(repo_url)
    response.raise_for_status()

    with open(zip_file_path, "wb") as file:
        file.write(response.content)

    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        zip_ref.extractall(settings.MEDIA_ROOT)

    os.remove(zip_file_path)


def _get_fr_matching_id(eng_id):
    return eng_id.replace("SOR-", "DORS-").replace("SI-", "TR-").replace("_c.", "_ch.")


def _get_en_file_path(eng_id, laws_dir):
    act_path = os.path.join(laws_dir, "eng", "acts", f"{eng_id}.xml")
    reg_path = os.path.join(laws_dir, "eng", "regulations", f"{eng_id}.xml")
    if os.path.exists(act_path):
        return act_path
    elif os.path.exists(reg_path):
        return reg_path
    else:
        return None


def _get_fr_file_path(fr_id, laws_dir):
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


def _get_en_fr_law_file_paths(laws_dir, eng_law_id):
    """
    Search for the English and French file paths for each law ID
    Return a list of tuples (EN, FR) where each element is a full file path
    """

    if eng_law_id in ["Constitution", "Constitution 2020"]:
        return CONSTITUTION_FILE_PATHS

    file_paths = None
    en_file_path = _get_en_file_path(eng_law_id, laws_dir)
    fr_file_path = _get_fr_file_path(_get_fr_matching_id(eng_law_id), laws_dir)
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
    num_sections = len(d["all_chunkable_sections"])
    nodes = [
        section_to_nodes(section, d["lang"]) for section in d["all_chunkable_sections"]
    ]
    # Flatten nodes
    nodes = [node for sublist in nodes for node in sublist]
    file_id = d["title_str"]
    d["nodes"] = nodes
    return d


def section_to_nodes(section, lang, chunk_size=1024, chunk_overlap=100):
    if chunk_size < 50:
        raise ValueError("Chunk size must be at least 50 tokens.")
    # if "_schedule_" in section["section_id"]:
    #     chunks = section["text"]
    # else:
    #     splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    #     # Split the text into chunks
    #     chunks = splitter.split_text(section["text"])
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
        f'{metadata["file_id"]}, {metadata["section"]}\n' f'{metadata["headings"]}'
    )
    original_display_metadata = metadata["display_metadata"]
    original_section_id = metadata["section_id"]
    for i, chunk in enumerate(chunks):
        metadata["chunk"] = f"{i+1}/{len(chunks)}"
        if len(chunks) > 1:
            metadata["display_metadata"] = (
                f'{original_display_metadata} ({metadata["chunk"]})'
            )
            metadata["section_id"] = f"{original_section_id}_{i+1}"
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
    # Find header row (first or second <row> in <thead>)
    thead = table_elem.find(".//thead")
    # header_row = thead.findall("row")[1]
    header_rows = thead.findall("row") if thead is not None else []
    if len(header_rows) >= 2:
        header_row = header_rows[1]
    elif len(header_rows) == 1:
        header_row = header_rows[0]
    else:
        # No header row found; return empty headers and rows
        return [], []
    headers = [entry.text.strip() for entry in header_row.findall("entry")]

    # Find body rows
    tbody = table_elem.find(".//tbody")
    body_rows = []
    for row in tbody.findall("row"):
        cells = [
            entry.text.strip() if entry.text else "" for entry in row.findall("entry")
        ]
        body_rows.append(cells)
    return headers, body_rows


def markdown_header(headers):
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    return header_line + "\n" + separator


def markdown_rows(rows):
    return "\n".join(["| " + " | ".join(row) + " |" for row in rows])


def chunk_table(headers, body_rows, chunk_size=25):
    chunks = []
    for i in range(0, len(body_rows), chunk_size):
        chunk = body_rows[i : i + chunk_size]
        md = markdown_header(headers) + "\n" + markdown_rows(chunk)
        chunks.append(md)
    return chunks


def _get_schedule_joined_text(element):
    # for e in element.findall(".//table"):
    #     for child in e:
    #         if child.tag == "table":
    #             print(child.tag, child.text)
    tables = element.findall(".//table")

    for table_idx, table_elem in enumerate(tables):
        headers, body_rows = parse_table(table_elem)
        chunks = chunk_table(headers, body_rows, chunk_size=25)
        print(f"\n### Table {table_idx+1}\n")
        for chunk_idx, chunk in enumerate(chunks):
            print(f"#### Chunk {chunk_idx+1}\n{chunk}\n")


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
):
    # TODO: Improve table parsing
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
        if e.text and e.text.strip():
            all_text.append(stylized_text(e.text.strip(), e.tag))
        if e.tail and e.tail.strip():
            all_text.append(e.tail.strip())
        if e.tag in break_tags:
            all_text.append("\n")
        elif e.tag in double_break_tags:
            all_text.append("\n\n")
        if e.tag in pipe_tags:
            all_text.append("|")
        if e.tag == "tbody":
            all_text.append("\n<tbody>")
        if e.tag == "thead":
            print(e.tag)
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
        line = line.strip()
        if line.endswith("|"):
            lines[i] = "| " + line
        # Replace the <tbody> tag with | --- | --- | --- | etc. for tables
        if line == "<tbody>" and i > 0 and lines[i - 1].strip().endswith("|"):
            lines[i] = "| --- " * (len(lines[i - 1].split("|")) - 2) + "|"
        elif line == "<tbody>":
            lines[i] = ""
    text = "\n".join(lines)
    return text


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
                get_section(section) for section in root.findall(".//Section")
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
    d["doc_id"] = f'{d["id"]}_{d["lang"]}'
    d["title_str"] = d["short_title"] if d["short_title"] else d["long_title"]
    for section in d["sections"]:
        section["section_id"] = f'{d["doc_id"]}_section_{section["id"]}'
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
                f'{d["doc_id"]}_subsection_{section["id"]}{subsection["id"]}'
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
        schedule["section_id"] = f'{d["doc_id"]}_schedule_{schedule["id"]}'
        schedule["heading_str"] = get_heading_str(schedule)
        schedule["section_str"] = schedule["id"]
        schedule["all_str"] = "\n".join(
            [
                d["title_str"],
                (schedule["id"] if schedule["id"] else "Schedule"),
                "",
                schedule["text"],
            ]
        )
        print(f"Schedule headings: {schedule['heading_str']}")
    # Finally, the preamble also needs a "all_str" field
    if d["preamble"]:
        d["preamble"][0]["section_id"] = f'{d["doc_id"]}_preamble'
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
                f'{d["doc_id"]}_preamble_provision_{section["id"]+1}'
            )
            section["parent_id"] = d["preamble"][0]["section_id"]
            section["heading_str"] = get_heading_str(section)
            section["section_str"] = f"Preamble provision {section['id']+1}"
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
            {k: v for k, v in s.items() if k in keep_keys}
        )
    for i, s in enumerate(d["all_chunkable_sections"]):
        s["doc_id"] = d["doc_id"]
        s["doc_title"] = d["title_str"]
        s["index"] = i
        if s["marginal_note"]:
            s["text"] = f"**{s['marginal_note']}**\n{s['text']}"
    return d


def get_heading_str(section):
    return " > ".join(section["headings"])


def get_section(section, last_amended_date=None):
    # If the section has an ancestor <Schedule> tag, skip it
    if section.xpath(".//Schedule"):
        return None
    # Subsections do not have a last_amended_date, so we pass it down from the parent
    last_amended_date = section.attrib.get(
        "{http://justice.gc.ca/lims}lastAmendedDate", last_amended_date
    )
    return {
        "id": _get_text(section.find(".//Label")),
        "headings": get_headings(section),
        "marginal_note": _get_text(section.find("MarginalNote")),
        "text": _get_joined_text(section),
        "in_force_start_date": section.attrib.get(
            "{http://justice.gc.ca/lims}inforce-start-date", None
        ),
        "last_amended_date": last_amended_date,
        "subsections": [
            get_section(subsection, last_amended_date)
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
    _get_schedule_joined_text(schedule)
    return {
        "id": _get_text(schedule.find(".//Label")),
        "headings": [
            _get_text(schedule.find(".//TitleText")) or "",
        ],
        "marginal_note": _get_text(schedule.find(".//MarginalNote")),
        "text": _get_joined_text(schedule),
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

    with engine.begin() as conn:
        conn.execute(
            text(
                f"SET maintenance_work_mem = '{settings.VECTORDB_MAINTENANCE_WORK_MEM_HEAVY}';"
            )
        )
        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__node_id_idx
                ON data_laws_lois__ (node_id);
            """
            )
        )

        # Useful single-column indexes for filtering and sorting
        # These don't compete with vector index since they're not compound
        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__doc_id_idx
              ON data_laws_lois__ USING btree((metadata_ ->> 'doc_id'));
            """
            )
        )

        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__lang_idx
              ON data_laws_lois__ USING btree((metadata_ ->> 'lang'));
            """
            )
        )

        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__in_force_start_date_idx
              ON data_laws_lois__ USING btree((metadata_ ->> 'in_force_start_date'));
            """
            )
        )

        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__last_amended_date_idx
              ON data_laws_lois__ USING btree((metadata_ ->> 'last_amended_date'));
            """
            )
        )

        # Full-text search index (single, not compound/partial by lang)
        conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS data_laws_lois__chunk_text_idx
              ON data_laws_lois__
              USING gin(text_search_tsv)
              WHERE (metadata_ ->> 'node_type') = 'chunk';
            """
            )
        )

        # CRITICAL: NO compound indexes on metadata fields that include node_type + lang!
        # Those specific combinations cause PostgreSQL to avoid the vector index.
        # Single-column indexes are fine and beneficial.

        if hnsw:
            # High-performance vector (HNSW) index for chunks only
            # This MUST be the primary index used for similarity search
            # Match the exact WHERE clause pattern used by LlamaIndex queries
            conn.execute(
                text(
                    """
                CREATE INDEX IF NOT EXISTS data_laws_lois___embedding_idx
                  ON data_laws_lois__
                  USING hnsw(embedding vector_cosine_ops)
                  WITH (m = 16, ef_construction = 256)
                  WHERE (metadata_ ->> 'node_type') = 'chunk';
                """
                )
            )

    # VACUUM/ANALYZE for fresh stats
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE data_laws_lois__;"))

        # Pre-warm the key indexes for performance
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_prewarm;"))
        conn.execute(text("SELECT pg_prewarm('data_laws_lois__node_id_idx','buffer');"))

        # Pre-warm the beneficial single-column indexes
        conn.execute(text("SELECT pg_prewarm('data_laws_lois__doc_id_idx','buffer');"))
        conn.execute(
            text(
                "SELECT pg_prewarm('data_laws_lois__in_force_start_date_idx','buffer');"
            )
        )
        conn.execute(
            text("SELECT pg_prewarm('data_laws_lois__last_amended_date_idx','buffer');")
        )
        conn.execute(
            text("SELECT pg_prewarm('data_laws_lois__chunk_text_idx','buffer');")
        )
        # Most important: pre-warm the vector index
        conn.execute(
            text("SELECT pg_prewarm('data_laws_lois___embedding_idx','buffer');")
        )
        # Pre-warm the main table
        conn.execute(text("SELECT pg_prewarm('data_laws_lois__','buffer');"))


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
