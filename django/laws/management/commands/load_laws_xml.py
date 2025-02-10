import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from math import ceil

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import datetime

import requests
from django_extensions.management.utils import signalcommand
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from laws.models import Law, token_counter
from otto.models import Cost, OttoStatus
from otto.utils.common import display_cad_cost

logger = get_logger(__name__)


def _price_tokens(token_counter):
    return 0.000178 * token_counter.total_embedding_token_count / 1000


def _download_repo(force_download=False):
    repo_url = (
        "https://github.com/justicecanada/laws-lois-xml/archive/refs/heads/main.zip"
    )

    # Check if the repo was already downloaded
    if os.path.exists("/tmp/laws-lois-xml-main"):
        if force_download:
            logger.debug("Deleting existing repo before re-downloading")
            shutil.rmtree("/tmp/laws-lois-xml-main")
        else:
            logger.debug("Folder already exists, skipping download")
            return

    # Path to save the downloaded zip file
    zip_file_path = "/tmp/laws-lois-xml.zip"
    # Path to extract the zip file
    extract_path = "/tmp"

    # Download the zip file
    logger.debug("Downloading laws-lois-xml repo...")
    response = requests.get(repo_url)
    with open(zip_file_path, "wb") as file:
        file.write(response.content)

    # Extract the zip file
    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        zip_ref.extractall(extract_path)

    # Clean up the zip file
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


def _get_en_fr_law_file_paths(laws_dir, eng_law_ids=[]):
    """
    Search for the English and French file paths for each law ID
    Return a list of tuples (EN, FR) where each element is a full file path
    """

    file_paths = []
    for eng_law_id in eng_law_ids:
        en_file_path = _get_en_file_path(eng_law_id, laws_dir)
        fr_file_path = _get_fr_file_path(_get_fr_matching_id(eng_law_id), laws_dir)
        if en_file_path and fr_file_path:
            file_paths.append((en_file_path, fr_file_path))
        else:
            logger.debug(
                f"Could not find both English and French files for {eng_law_id}"
            )
            logger.debug(f"(EN: {en_file_path}, FR: {fr_file_path})")

    logger.debug(f"{len(file_paths)} laws found in both languages")
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


import os

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import (
    Document,
    NodeRelationship,
    RelatedNodeInfo,
    TextNode,
)
from lxml import etree as ET


def law_xml_to_nodes(file_path):
    d = get_dict_from_xml(file_path)
    num_sections = len(d["all_chunkable_sections"])
    nodes = [section_to_nodes(section) for section in d["all_chunkable_sections"]]
    # Flatten nodes
    nodes = [node for sublist in nodes for node in sublist]
    file_id = d["title_str"]
    d["nodes"] = nodes
    return d


def section_to_nodes(section, chunk_size=1024, chunk_overlap=100):
    if chunk_size < 50:
        raise ValueError("Chunk size must be at least 50 tokens.")
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
    }
    exclude_keys = list(metadata.keys()) + ["chunk"]
    metadata["display_metadata"] = (
        f'{metadata["file_id"]}, {metadata["section"]}\n' f'{metadata["headings"]}'
    )
    original_display_metadata = metadata["display_metadata"]
    for i, chunk in enumerate(chunks):
        metadata["chunk"] = f"{i+1}/{len(chunks)}"
        if len(chunks) > 1:
            metadata["display_metadata"] = (
                f'{original_display_metadata} ({metadata["chunk"]})'
            )
        nodes.append(
            TextNode(
                text=chunk,
                metadata=metadata,
                excluded_llm_metadata_keys=exclude_keys,
                excluded_embed_metadata_keys=exclude_keys,
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
    # heading_str = ""
    # for i, heading in enumerate(section["headings"]):
    #     heading_str += f"{' ' * (i+2)}{heading}\n"
    # if section["marginal_note"]:
    #     # heading_str += f"{' ' * (len(section['headings'])+2)}{section['marginal_note']}\n"
    #     heading_str += f"\n**{section['marginal_note']}**"
    # return heading_str
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


from llama_index.core.schema import MetadataMode


class Command(BaseCommand):
    help = "Load laws XML from github"

    def add_arguments(self, parser):
        parser.add_argument(
            "--full", action="store_true", help="Performs a full load of all data"
        )
        parser.add_argument(
            "--small",
            action="store_true",
            help="Only loads the smallest 1 act and 1 regulation",
        )
        parser.add_argument(
            "--const_only",
            action="store_true",
            help="Only loads the constitution",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Resets the database before loading",
        )
        parser.add_argument(
            "--accept_reset",
            action="store_true",
            help="Accept the reset of the database",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Write node markdown to source directories. Does not alter database.",
        )
        parser.add_argument(
            "--mock_embedding",
            action="store_true",
            help="Mock embedding the nodes (save time/cost for debugging)",
        )
        parser.add_argument(
            "--skip_cleanup",
            action="store_true",
            help="Skip cleanup of the laws-lois-xml repo",
        )
        parser.add_argument(
            "--force_update",
            action="store_true",
            help="Force update of existing laws (even if they have not changed)",
        )
        parser.add_argument(
            "--max_workers",
            type=int,
            default=1,
            help="Number of workers to use for parallel processing",
        )
        parser.add_argument(
            "--force_download",
            action="store_true",
            help="Force re-download of the laws-lois-xml repo, even if it exists",
        )

    @signalcommand
    def handle(self, *args, **options):
        total_cost = 0
        full = options.get("full", False)
        reset = options.get("reset", False)
        accept_reset = options.get("accept_reset", False)
        if reset:
            if not accept_reset:
                # Confirm the user wants to delete all laws
                print("This will delete all laws in the database. Are you sure?")
                response = input("Type 'yes' to confirm: ")
                if response != "yes":
                    print("Aborted")
                    return
        small = options.get("small", False)
        const_only = options.get("const_only", False)
        force_update = options.get("force_update", False)
        force_download = options.get("force_download", False)
        max_workers = options.get("max_workers", 1)
        debug = options.get("debug", False)
        mock_embedding = options.get("mock_embedding", False)

        if small:
            laws_root = os.path.join(
                os.path.dirname(settings.BASE_DIR),
                "django",
                "tests",
                "laws",
                "xml_sample",
            )
        else:
            _download_repo(force_download)
            laws_root = "/tmp/laws-lois-xml-main"
        if full:
            law_ids = _get_all_eng_law_ids(laws_root)
        elif small:
            law_ids = [
                "SOR-2010-203",  # Certain Ships Remission Order, 2010 (5kb)
                "S-14.3",  # An Act to grant access to records of the Special Committee on the Defence of Canada Regulations (5kb)
            ]
        else:
            # Subset of legislation, for testing
            law_ids = [
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
                # "C-46",  # Criminal Code
                # "SOR-2021-25",  # Cross-border Movement of Hazardous Waste and Hazardous Recyclable Material Regulations
                # "F-14",  # Fisheries Act
                # "SOR-93-53",  # Fishery (General) Regulations
                # "C.R.C.,_c._870",  # Food and Drug Regulations
                # "F-27",  # Food and Drugs Act
                # "I-2.5",  # Immigration and Refugee Protection Act
                # "SOR-2002-227",  # Immigration and Refugee Protection Regulations
                # "I-21",  # Interpretation Act
                # "SOR-2016-151",  # Multi-Sector Air Pollutants Regulations
                # "SOR-2010-189",  # Renewable Fuels Regulations
                # "S-22",  # Statutory Instruments Act
                # "C.R.C.,_c._1509",  # Statutory Instruments Regulations
                # "A-1",  # Access to Information Act
                # "F-11",  # Financial Administration Act
                # "N-22",  # Canadian Navigable Waters Act
            ]

        file_path_tuples = _get_en_fr_law_file_paths(laws_root, law_ids)
        # Create constitution file paths
        constitution_dir = os.path.join(settings.BASE_DIR, "laws", "data")
        constitution_file_paths = [
            (
                os.path.join(constitution_dir, "Constitution 2020_E.xml"),
                os.path.join(constitution_dir, "Constitution 2020_F_Rapport.xml"),
            )
        ]
        if const_only:
            file_path_tuples = constitution_file_paths
        elif not small:
            file_path_tuples += constitution_file_paths

        flattened_file_paths = [p for t in file_path_tuples for p in t]
        num_to_load = len(file_path_tuples)
        total_file_size = sum(
            [os.path.getsize(file_path) for file_path in flattened_file_paths]
        )
        file_size_so_far = 0
        load_results = {
            "empty": [],
            "error": [],
            "added": [],
            "exists": [],
            "updated": [],
        }

        # Reset the Django and LlamaIndex tables
        if reset:
            Law.reset()
            # Recreate the table
            OttoLLM().get_retriever("laws_lois__", hnsw=True, use_jsonb=True).retrieve(
                "?"
            )

        xslt_path = os.path.join(laws_root, "xslt", "LIMS2HTML.xsl")

        start_time = time.time()

        # for j, file_paths in enumerate(file_path_tuples):
        #     process_en_fr_paths(
        #         file_paths, mock_embedding, debug, constitution_file_paths
        #     )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    process_en_fr_paths,
                    file_paths,
                    mock_embedding,
                    debug,
                    constitution_file_paths,
                    force_update,
                )
                for file_paths in file_path_tuples
            ]
            for i, future in enumerate(futures):
                try:
                    result, cost = future.result()
                    for k, v in result.items():
                        load_results[k].extend(v)
                    total_cost += cost
                    # Handle the result
                except Exception as e:
                    # Handle exceptions
                    pass

        if options.get("download", False) and not small:
            if not options.get("skip_cleanup", False):
                # Clean up the downloaded repo
                shutil.rmtree(laws_root)
        print("Done!")
        added_count = len(load_results["added"])
        exist_count = len(load_results["exists"])
        empty_count = len(load_results["empty"])
        error_count = len(load_results["error"])
        updated_count = len(load_results["updated"])
        if error_count:
            logger.debug("\nError files:")
            for error in load_results["error"]:
                logger.error(error)
        if empty_count:
            logger.debug("\nEmpty files:")
            for empty in load_results["empty"]:
                logger.debug(empty)
        if exist_count:
            logger.debug("\nExisting files:")
            for exist in load_results["exists"]:
                logger.debug(exist)
        if updated_count:
            logger.debug("\nUpdated files:")
            for updated in load_results["updated"]:
                logger.debug(updated)
        if added_count:
            logger.debug("\nAdded files:")
            for added in load_results["added"]:
                logger.debug(added)
        logger.debug(
            f"\nAdded: {added_count}; Updated: {updated_count}; Already exists: {exist_count}; Empty laws: {empty_count}; Errors: {error_count}"
        )
        logger.debug(
            f"Total time to load XML files: {time.time() - start_time:.2f} seconds"
        )
        logger.debug(f"Total cost: {display_cad_cost(total_cost)}")

        otto_status = OttoStatus.objects.singleton()
        otto_status.laws_last_refreshed = datetime.now()
        otto_status.save()


def get_sha_256_hash(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(4096):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def process_en_fr_paths(
    file_paths, mock_embedding, debug, constitution_file_paths, force_update=False
):
    bind_contextvars(feature="laws_load")
    llm = OttoLLM(mock_embedding=mock_embedding)
    document_en = None
    document_fr = None
    nodes_en = None
    nodes_fr = None
    inner_loop_err = False
    load_results = {
        "empty": [],
        "error": [],
        "added": [],
        "exists": [],
        "updated": [],
    }

    # Check the hashes of both files
    en_hash = get_sha_256_hash(file_paths[0])
    fr_hash = get_sha_256_hash(file_paths[1])
    # If *BOTH* exist in the database, skip
    if (
        Law.objects.filter(sha_256_hash_en=en_hash).exists()
        and Law.objects.filter(sha_256_hash_fr=fr_hash).exists()
        and not force_update
    ):
        logger.debug(f"Duplicate EN/FR hashes found in database: {file_paths}")
        load_results["exists"].append(file_paths)
        return load_results, 0

    # Create nodes for the English and French XML files
    for k, file_path in enumerate(file_paths):
        # Get the directory path of the XML file
        directory = os.path.dirname(file_path)
        # Get the base name of the XML file
        base_name = os.path.basename(file_path)
        # Construct the output HTML file path
        html_file_path = os.path.join(
            directory, "html", f"{os.path.splitext(base_name)[0]}.html"
        )

        # Create nodes from XML
        node_dict = law_xml_to_nodes(file_path)
        if not node_dict["nodes"]:
            load_results["empty"].append(file_path)
            inner_loop_err = True
            break
        doc_metadata = {
            "id": node_dict["id"],
            "lang": node_dict["lang"],
            "filename": node_dict["filename"],
            "type": node_dict["type"],
            "short_title": node_dict["short_title"],
            "long_title": node_dict["long_title"],
            "bill_number": node_dict["bill_number"],
            "instrument_number": node_dict["instrument_number"],
            "consolidated_number": node_dict["consolidated_number"],
            "last_amended_date": node_dict["last_amended_date"],
            "current_date": node_dict["current_date"],
            "enabling_authority": node_dict["enabling_authority"],
            "node_type": "document",
        }
        if file_path in [p for t in constitution_file_paths for p in t]:
            # This is used as a reference in other Acts/Regulations
            doc_metadata["consolidated_number"] = "Const"
            # The date metadata in these files is missing
            # Last amendment reference I can find in the document
            doc_metadata["last_amended_date"] = "2011-12-16"
            # Date this script was written
            doc_metadata["current_date"] = "2024-05-23"
            doc_metadata["type"] = "act"

        exclude_keys = list(doc_metadata.keys())
        doc_metadata["display_metadata"] = (
            f'{doc_metadata["short_title"] or ""}'
            f'{": " if doc_metadata["short_title"] and doc_metadata["long_title"] else ""}'
            f'{doc_metadata["long_title"] or ""} '
            f'({doc_metadata["consolidated_number"] or doc_metadata["instrument_number"] or doc_metadata["bill_number"]})'
        )

        document = Document(
            text="",
            metadata=doc_metadata,
            excluded_llm_metadata_keys=exclude_keys,
            excluded_embed_metadata_keys=exclude_keys,
            metadata_template="{value}",
            text_template="{metadata_str}",
        )
        document.doc_id = f'{node_dict["id"]}_{node_dict["lang"]}'

        nodes = node_dict["nodes"]
        for i, node in enumerate(nodes):
            node.id_ = node.metadata["section_id"]
            if node.metadata["parent_id"] is not None:
                node.relationships[NodeRelationship.PARENT] = RelatedNodeInfo(
                    node_id=node.metadata["parent_id"]
                )
            node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
                node_id=document.doc_id
            )
        # Set prev/next relationships
        for i in range(len(nodes) - 1):
            nodes[i].relationships[NodeRelationship.NEXT] = RelatedNodeInfo(
                node_id=nodes[i + 1].node_id
            )
            nodes[i + 1].relationships[NodeRelationship.PREVIOUS] = RelatedNodeInfo(
                node_id=nodes[i].node_id
            )

        if doc_metadata["lang"] == "eng":
            document_en = document
            nodes_en = nodes
        elif doc_metadata["lang"] == "fra":
            document_fr = document
            nodes_fr = nodes

        # Write text files of nodes (for debugging purposes)
        if debug:
            nodes_file_path = os.path.join(
                directory, "nodes", f"{os.path.splitext(base_name)[0]}.md"
            )
            # Create the /nodes directory if it doesn't exist
            if not os.path.exists(os.path.dirname(nodes_file_path)):
                os.makedirs(os.path.dirname(nodes_file_path))
            with open(nodes_file_path, "w") as f:
                f.write(
                    f"{document.get_content(metadata_mode=MetadataMode.LLM)}\n\n---\n\n"
                )
                for node in nodes:
                    f.write(
                        f"{node.get_content(metadata_mode=MetadataMode.LLM)}\n\n---\n\n"
                    )

    if inner_loop_err:
        return load_results, 0
    # Nodes and document should be ready now! Let's add to our Django model
    # This will also handle the creation of LlamaIndex vector tables
    if not debug:
        try:
            # Check if law already exists.
            # node_id_en property will be unique in database
            node_id_en = document_en.doc_id
            if Law.objects.filter(node_id_en=node_id_en).exists():
                logger.debug(f"Updating existing law.")
                load_results["updated"].append(file_paths)
            else:
                load_results["added"].append(file_paths)
            logger.debug(
                f"Adding to database: {document_en.metadata['display_metadata']}"
            )
            # This method will update existing Law object if it already exists
            law = Law.objects.from_docs_and_nodes(
                document_en,
                nodes_en,
                document_fr,
                nodes_fr,
                sha_256_hash_en=en_hash,
                sha_256_hash_fr=fr_hash,
                add_to_vector_store=True,
                force_update=force_update,
                llm=llm,
            )
            bind_contextvars(law_id=law.id)
            llm.create_costs()
            cost_obj = Cost.objects.filter(law=law).order_by("date_incurred")
            if cost_obj.exists():
                cost = cost_obj.last().usd_cost
                logger.debug(f"Cost: {display_cad_cost(cost)}")

        except Exception as e:
            logger.error(f"*** Error processing file pair: {file_paths}\n {e}\n")
            if "Law with this Node id already exists" in str(e):
                load_results["exists"].append(file_paths)
            else:
                load_results["error"].append(file_paths)
            cost = 0
    return load_results, cost
