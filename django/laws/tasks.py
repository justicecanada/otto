import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import datetime

import requests
from django_extensions.management.utils import signalcommand
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import (
    Document,
    MetadataMode,
    NodeRelationship,
    RelatedNodeInfo,
    TextNode,
)
from lxml import etree as ET
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.models import Cost, OttoStatus
from otto.utils.common import display_cad_cost

from .loading_utils import (
    SAMPLE_LAW_IDS,
    _download_repo,
    _get_all_eng_law_ids,
    _get_en_fr_law_file_paths,
    drop_hnsw_index,
    get_sha_256_hash,
    law_xml_to_nodes,
    recreate_indexes,
)
from .models import Law

logger = get_logger(__name__)


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
        print("Processing file: ", file_path)
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
    print(
        "Creating Law object (and embeddings) for document:\n",
        document_en.metadata,
    )
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
            import traceback

            logger.error(traceback.format_exc())
            if "Law with this Node id already exists" in str(e):
                load_results["exists"].append(file_paths)
            else:
                load_results["error"].append(file_paths)
            cost = 0
    return load_results, cost


def load_laws(
    small=False,
    full=False,
    const_only=False,
    reset=False,
    force_download=False,
    mock_embedding=False,
    debug=False,
    force_update=False,
    skip_cleanup=False,
    reset_hnsw=False,
):
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
        law_ids = SAMPLE_LAW_IDS

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
        OttoLLM().get_retriever("laws_lois__", hnsw=True).retrieve("?")

    xslt_path = os.path.join(laws_root, "xslt", "LIMS2HTML.xsl")

    start_time = time.time()
    total_cost = 0

    if reset_hnsw:
        drop_hnsw_index()

    for i, file_paths in enumerate(file_path_tuples):
        try:
            result, cost = process_en_fr_paths(
                file_paths,
                mock_embedding,
                debug,
                constitution_file_paths,
                force_update,
            )
            for k, v in result.items():
                load_results[k].extend(v)
            total_cost += cost
            # Handle the result
        except Exception as e:
            # Handle exceptions
            pass

    if not (small or skip_cleanup):
        # Clean up the downloaded repo
        shutil.rmtree(laws_root)

    print("Done loading XML files - running Post-load SQL (create indexes)")

    # Run SQL to (re)create the JSONB and Node ID metadata indexes
    recreate_indexes(node_id=True, jsonb=True, hnsw=reset_hnsw)

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
