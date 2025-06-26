import hashlib
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.timezone import now

import requests
from celery import shared_task
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


@shared_task(bind=True, max_retries=1)
def initiate_law_loading_task(
    self,
    small=False,
    full=False,
    const_only=False,
    reset=False,
    force_download=False,
    mock_embedding=False,
    debug=False,
    force_update=False,
    retry_failed=False,
):
    """
    Main orchestrator task that:
    1. Downloads repo to media folder
    2. Discovers laws to process
    3. Updates OttoStatus with initial state
    4. Queues individual law processing tasks
    5. Detects and deletes removed laws
    """
    try:
        bind_contextvars(feature="laws_load_init")

        # Initialize OttoStatus
        otto_status = OttoStatus.objects.singleton()
        laws_status = {
            "status": "downloading",
            "started_at": now().isoformat(),
            "total_laws": 0,
            "processed": 0,
            "failed": 0,
            "completed_law_ids": [],
            "failed_law_ids": [],
            "laws": {},
        }
        otto_status.laws_status = laws_status
        otto_status.save()

        # Determine laws root directory
        if small:
            laws_root = os.path.join(
                os.path.dirname(settings.BASE_DIR),
                "django",
                "tests",
                "laws",
                "xml_sample",
            )
        else:
            # Download to media folder for shared access across workers
            media_laws_dir = os.path.join(settings.MEDIA_ROOT, "laws-lois-xml-main")
            if force_download and os.path.exists(media_laws_dir):
                shutil.rmtree(media_laws_dir)

            if not os.path.exists(media_laws_dir):
                # Download and extract to media folder
                repo_url = "https://github.com/justicecanada/laws-lois-xml/archive/refs/heads/main.zip"
                zip_file_path = os.path.join(settings.MEDIA_ROOT, "laws-lois-xml.zip")

                logger.info("Downloading laws-lois-xml repo to media folder...")
                response = requests.get(repo_url)
                response.raise_for_status()

                with open(zip_file_path, "wb") as file:
                    file.write(response.content)

                with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                    zip_ref.extractall(settings.MEDIA_ROOT)

                os.remove(zip_file_path)

            laws_root = media_laws_dir

        # Get law IDs to process
        if retry_failed:
            # Get failed law IDs from previous run
            current_status = otto_status.laws_status or {}
            law_ids = current_status.get("failed_law_ids", [])
            if not law_ids:
                logger.info("No failed laws found to retry")
                return {"status": "complete", "message": "No failed laws to retry"}
        elif full:
            law_ids = _get_all_eng_law_ids(laws_root)
        elif small:
            law_ids = [
                "SOR-2010-203",  # Certain Ships Remission Order, 2010 (5kb)
                "S-14.3",  # An Act to grant access to records of the Special Committee on the Defence of Canada Regulations (5kb)
            ]
        else:
            # Subset of legislation, for testing
            law_ids = SAMPLE_LAW_IDS

        # Get file path tuples
        file_path_tuples = _get_en_fr_law_file_paths(laws_root, law_ids)

        # Add constitution files if not small load
        constitution_file_paths = []
        if const_only:
            constitution_dir = os.path.join(settings.BASE_DIR, "laws", "data")
            constitution_file_paths = [
                (
                    os.path.join(constitution_dir, "Constitution 2020_E.xml"),
                    os.path.join(constitution_dir, "Constitution 2020_F_Rapport.xml"),
                )
            ]
            file_path_tuples = constitution_file_paths
        elif not small:
            constitution_dir = os.path.join(settings.BASE_DIR, "laws", "data")
            constitution_file_paths = [
                (
                    os.path.join(constitution_dir, "Constitution 2020_E.xml"),
                    os.path.join(constitution_dir, "Constitution 2020_F_Rapport.xml"),
                )
            ]
            file_path_tuples += constitution_file_paths

        # Reset database if requested
        if reset:
            Law.reset()
            # Recreate the table
            OttoLLM().get_retriever("laws_lois__", hnsw=True).retrieve("?")

        # Detect and delete laws that no longer exist in repo
        deleted_laws = Law.objects.purge(keep_ids=law_ids)

        # Update status with discovered laws
        laws_status["status"] = "queuing"
        laws_status["total_laws"] = len(file_path_tuples)
        laws_status["deleted_laws"] = deleted_laws

        # Initialize individual law statuses
        for file_paths in file_path_tuples:
            # Extract law ID from English file path
            en_file = os.path.basename(file_paths[0]).replace(".xml", "")
            if "Constitution" in en_file:
                law_id = "Constitution 2020"
            else:
                law_id = en_file

            laws_status["laws"][law_id] = {
                "status": "queued",
                "queued_at": now().isoformat(),
                "error": None,
                "retry_count": 0,
            }

        otto_status.laws_status = laws_status
        otto_status.save()

        # Queue individual law processing tasks and store task IDs
        task_ids = []
        for file_paths in file_path_tuples:
            task_result = process_law_pair_task.apply_async(
                args=[file_paths, constitution_file_paths],
                kwargs={
                    "mock_embedding": mock_embedding,
                    "debug": debug,
                    "force_update": force_update,
                },
            )
            task_ids.append(task_result.id)

            # Also store task ID for each individual law
            en_file = os.path.basename(file_paths[0]).replace(".xml", "")
            if "Constitution" in en_file:
                law_id = "Constitution 2020"
            else:
                law_id = en_file

            if law_id in laws_status["laws"]:
                laws_status["laws"][law_id]["task_id"] = task_result.id

        # Store all task IDs for easy cancellation
        laws_status["task_ids"] = task_ids
        otto_status.laws_status = laws_status
        otto_status.save()

        logger.info(f"Queued {len(file_path_tuples)} law processing tasks")
        return {
            "status": "queued",
            "total_laws": len(file_path_tuples),
            "task_ids": task_ids,
        }

    except Exception as exc:
        logger.error(f"Error in initiate_law_loading_task: {exc}")
        # Update status to indicate failure
        try:
            otto_status = OttoStatus.objects.singleton()
            if otto_status.laws_status:
                otto_status.laws_status["status"] = "error"
                otto_status.laws_status["error"] = str(exc)
                otto_status.save()
        except Exception:
            pass
        raise self.retry(countdown=60, exc=exc)


@shared_task(bind=True, max_retries=1)
def process_law_pair_task(
    self,
    file_paths,
    constitution_file_paths,
    mock_embedding=False,
    debug=False,
    force_update=False,
):
    """
    Process a single law pair (EN/FR files).
    Updates progress in OttoStatus and handles completion checking.
    """
    try:
        bind_contextvars(feature="laws_load_pair")

        # Extract law ID from English file path
        en_file = os.path.basename(file_paths[0]).replace(".xml", "")
        if "Constitution" in en_file:
            law_id = "Constitution 2020"
        else:
            law_id = en_file

        # Update status to processing
        otto_status = OttoStatus.objects.singleton()
        laws_status = otto_status.laws_status or {}

        if law_id in laws_status.get("laws", {}):
            laws_status["laws"][law_id]["status"] = "processing"
            laws_status["laws"][law_id]["started_at"] = now().isoformat()
            otto_status.laws_status = laws_status
            otto_status.save()

        # Process the law pair
        result, cost = process_en_fr_paths(
            file_paths, mock_embedding, debug, constitution_file_paths, force_update
        )

        # Determine result status
        if result["error"]:
            final_status = "failed"
            error_msg = "Processing error occurred"
        elif result["empty"]:
            final_status = "failed"
            error_msg = "Empty law file"
        else:
            final_status = "complete"
            error_msg = None

        # Update individual law status and global counters
        try:
            with transaction.atomic():
                otto_status = OttoStatus.objects.singleton()
                laws_status = otto_status.laws_status or {}

                if law_id in laws_status.get("laws", {}):
                    laws_status["laws"][law_id]["status"] = final_status
                    laws_status["laws"][law_id]["completed_at"] = now().isoformat()
                    laws_status["laws"][law_id]["error"] = error_msg
                    # Convert Decimal to float for JSON serialization
                    laws_status["laws"][law_id]["cost"] = (
                        float(cost) if cost is not None else 0
                    )

                # Update global counters
                if final_status == "complete":
                    laws_status["completed_law_ids"] = laws_status.get(
                        "completed_law_ids", []
                    )
                    if law_id not in laws_status["completed_law_ids"]:
                        laws_status["completed_law_ids"].append(law_id)
                    laws_status["processed"] = len(laws_status["completed_law_ids"])
                else:
                    laws_status["failed_law_ids"] = laws_status.get(
                        "failed_law_ids", []
                    )
                    if law_id not in laws_status["failed_law_ids"]:
                        laws_status["failed_law_ids"].append(law_id)
                    laws_status["failed"] = len(laws_status["failed_law_ids"])

                # Check if all laws are complete
                total_laws = laws_status.get("total_laws", 0)
                processed_count = laws_status.get("processed", 0)
                failed_count = laws_status.get("failed", 0)

                if processed_count + failed_count >= total_laws:
                    # All laws processed - finalize
                    laws_status["status"] = "finalizing"
                    laws_status["completed_at"] = now().isoformat()
                    otto_status.laws_status = laws_status
                    otto_status.save()

                    # Run finalization
                    finalize_law_loading_task.apply_async()
                else:
                    otto_status.laws_status = laws_status
                    otto_status.save()

        except Exception as status_exc:
            logger.error(f"Error updating status for law {law_id}: {status_exc}")
            # If we can't update status due to serialization or other issues,
            # at least try to mark this law as failed
            try:
                otto_status = OttoStatus.objects.singleton()
                laws_status = otto_status.laws_status or {}
                if law_id in laws_status.get("laws", {}):
                    laws_status["laws"][law_id]["status"] = "failed"
                    laws_status["laws"][law_id][
                        "error"
                    ] = f"Status update error: {str(status_exc)}"
                    otto_status.laws_status = laws_status
                    otto_status.save()
            except Exception:
                logger.error(f"Failed to update status even for failure case: {law_id}")
            # Re-raise the exception to trigger task retry/failure
            raise status_exc

        logger.info(f"Completed processing law: {law_id} with status: {final_status}")
        return {
            "law_id": law_id,
            "status": final_status,
            "cost": float(cost) if cost is not None else 0.0,
        }

    except Exception as exc:
        logger.error(f"Error processing law pair {file_paths}: {exc}")

        # Update status to failed
        try:
            otto_status = OttoStatus.objects.singleton()
            laws_status = otto_status.laws_status or {}

            if law_id in laws_status.get("laws", {}):
                retry_count = laws_status["laws"][law_id].get("retry_count", 0)
                laws_status["laws"][law_id]["status"] = "failed"
                laws_status["laws"][law_id]["error"] = str(exc)
                laws_status["laws"][law_id]["retry_count"] = retry_count + 1

                # Add to failed list
                laws_status["failed_law_ids"] = laws_status.get("failed_law_ids", [])
                if law_id not in laws_status["failed_law_ids"]:
                    laws_status["failed_law_ids"].append(law_id)
                laws_status["failed"] = len(laws_status["failed_law_ids"])

                otto_status.laws_status = laws_status
                otto_status.save()
        except Exception:
            pass

        raise self.retry(countdown=60, exc=exc)


@shared_task
def finalize_law_loading_task():
    """
    Finalize the law loading process by rebuilding indexes and updating timestamps.
    """
    try:
        logger.info("Finalizing law loading process...")

        # Rebuild vector-specific indexes (node_id index is managed by Django model)
        recreate_indexes(node_id=False, jsonb=True, hnsw=False)

        # Update final status
        otto_status = OttoStatus.objects.singleton()
        laws_status = otto_status.laws_status or {}
        laws_status["status"] = "complete"
        laws_status["finalized_at"] = now().isoformat()

        otto_status.laws_status = laws_status
        otto_status.laws_last_refreshed = now()
        otto_status.save()

        logger.info("Law loading finalization complete")
        return {"status": "complete"}

    except Exception as exc:
        logger.error(f"Error in finalize_law_loading_task: {exc}")

        # Update status to indicate finalization error
        try:
            otto_status = OttoStatus.objects.singleton()
            laws_status = otto_status.laws_status or {}
            laws_status["status"] = "error"
            laws_status["error"] = f"Finalization error: {str(exc)}"
            otto_status.laws_status = laws_status
            otto_status.save()
        except Exception:
            pass

        raise exc


def process_en_fr_paths(
    file_paths, mock_embedding, debug, constitution_file_paths, force_update=False
):
    """
    Process EN/FR file pair with granular progress updates.
    """
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

    # Extract law ID for progress updates
    en_file = os.path.basename(file_paths[0]).replace(".xml", "")
    if "Constitution" in en_file:
        law_id = "Constitution 2020"
    else:
        law_id = en_file

    def update_law_progress(status, details=None):
        """Helper to update individual law progress."""
        try:
            otto_status = OttoStatus.objects.singleton()
            laws_status = otto_status.laws_status or {}

            if law_id in laws_status.get("laws", {}):
                laws_status["laws"][law_id]["progress"] = status
                if details:
                    laws_status["laws"][law_id]["details"] = details
                otto_status.laws_status = laws_status
                otto_status.save()
        except Exception as e:
            logger.warning(f"Failed to update progress for {law_id}: {e}")

    try:
        # Check the hashes of both files
        update_law_progress("checking_hashes")
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
        update_law_progress("parsing_xml")
        for k, file_path in enumerate(file_paths):
            logger.info(f"Processing file: {file_path}")

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
                    os.path.dirname(file_path),
                    "nodes",
                    f"{os.path.splitext(os.path.basename(file_path))[0]}.md",
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
        update_law_progress("creating_embeddings")
        logger.info(
            f"Creating Law object (and embeddings) for document: {document_en.metadata}"
        )

        if not debug:
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
            # It includes granular progress updates for embedding batches
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
                progress_callback=lambda batch_num, total_batches: update_law_progress(
                    "embedding", f"batch {batch_num}/{total_batches}"
                ),
            )

            bind_contextvars(law_id=law.id)
            llm.create_costs()
            cost_obj = Cost.objects.filter(law=law).order_by("date_incurred")
            # Convert Decimal to float for JSON serialization
            cost = float(cost_obj.last().usd_cost) if cost_obj.exists() else 0.0
            logger.debug(f"Cost: {display_cad_cost(cost)}")

        return load_results, cost

    except Exception as e:
        logger.error(f"*** Error processing file pair: {file_paths}\n {e}\n")
        import traceback

        logger.error(traceback.format_exc())

        if "Law with this Node id already exists" in str(e):
            load_results["exists"].append(file_paths)
        else:
            load_results["error"].append(file_paths)

        return load_results, 0


# DEPRECATED: Legacy load_laws function for backward compatibility
# Use initiate_law_loading_task for new Celery-based loading
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
    """
    DEPRECATED: Legacy synchronous law loading function.
    Use initiate_law_loading_task.apply_async() for new Celery-based loading.
    """
    logger.warning(
        "load_laws() is deprecated. Use initiate_law_loading_task for Celery-based loading."
    )

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

    # Run SQL to (re)create the JSONB and vector indexes (node_id index is managed by Django model)
    recreate_indexes(node_id=False, jsonb=True, hnsw=reset_hnsw)

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
    otto_status.laws_last_refreshed = now()
    otto_status.save()


def get_law_loading_status():
    """
    Helper function to get the current law loading status.
    Returns a dict with status information calculated from actual law statuses.
    """
    try:
        otto_status = OttoStatus.objects.singleton()
        laws_status = otto_status.laws_status or {}

        if not laws_status:
            return {"status": "idle", "message": "No law loading in progress"}

        # Get the individual law statuses
        laws = laws_status.get("laws", {})

        # Calculate counts from actual law statuses
        total_laws = len(laws)
        completed_count = 0
        failed_count = 0
        empty_count = 0
        cancelled_count = 0
        processing_count = 0
        queued_count = 0
        failed_law_ids = []
        empty_law_ids = []
        cancelled_law_ids = []
        completed_law_ids = []
        processing_laws = []
        current_law = None

        for law_id, law_status in laws.items():
            status = law_status.get("status", "unknown")
            error = law_status.get("error")

            if status == "complete":
                completed_count += 1
                completed_law_ids.append(law_id)
            elif status == "cancelled":
                cancelled_count += 1
                cancelled_law_ids.append(law_id)
            elif status == "failed":
                if error == "Empty law file":
                    empty_count += 1
                    empty_law_ids.append(law_id)
                else:
                    failed_count += 1
                    failed_law_ids.append({"law_id": law_id, "error": error})
            elif status == "processing":
                processing_count += 1
                details = law_status.get("details", "")
                progress = law_status.get("progress", "")
                processing_laws.append(
                    {"law_id": law_id, "progress": progress, "details": details}
                )
                # If this law is currently processing, it could be our "current" law
                if not current_law:
                    current_law = (
                        f"{law_id} ({progress}: {details})" if details else law_id
                    )
            elif status == "queued":
                queued_count += 1

        # Calculate progress as completed / total (not including failed in progress)
        processed = completed_count
        progress_percent = (completed_count / total_laws * 100) if total_laws > 0 else 0

        # Determine overall status
        overall_status = laws_status.get("status", "unknown")

        # If no laws are processing or queued, and we have some completed/failed, mark as completed
        if (
            processing_count == 0
            and queued_count == 0
            and (completed_count > 0 or failed_count > 0)
        ):
            if overall_status not in ["completed", "failed"]:
                overall_status = "completed"

        return {
            "status": overall_status,
            "total_laws": total_laws,
            "processed": processed,  # Only count completed laws as processed
            "failed": failed_count,
            "empty": empty_count,
            "cancelled": cancelled_count,
            "processing": processing_count,
            "queued": queued_count,
            "progress_percent": round(progress_percent, 1),
            "started_at": laws_status.get("started_at"),
            "completed_at": laws_status.get("completed_at"),
            "cancelled_at": laws_status.get("cancelled_at"),
            "failed_law_ids": failed_law_ids,  # Now includes error details
            "empty_law_ids": empty_law_ids,
            "cancelled_law_ids": cancelled_law_ids,
            "completed_law_ids": completed_law_ids,
            "processing_laws": processing_laws,  # Individual progress details
            "current_law": current_law,
            "laws": laws,
        }
    except Exception as e:
        logger.error(f"Error getting law loading status: {e}")
        return {"status": "error", "message": str(e)}


@shared_task
def retry_failed_laws_task():
    """
    Convenience task to retry failed laws from the last run.
    """
    return initiate_law_loading_task.apply_async(kwargs={"retry_failed": True})


@shared_task
def cancel_law_loading_task():
    """
    Cancel all running law loading tasks.
    """
    try:
        from celery import current_app

        otto_status = OttoStatus.objects.singleton()
        laws_status = otto_status.laws_status or {}

        if not laws_status:
            return {"status": "error", "message": "No law loading process to cancel"}

        task_ids = laws_status.get("task_ids", [])
        cancelled_count = 0
        failed_cancellations = []

        logger.info(f"Attempting to cancel {len(task_ids)} law loading tasks")

        # Cancel all queued/running tasks
        for task_id in task_ids:
            try:
                current_app.control.revoke(task_id, terminate=True)
                cancelled_count += 1
                logger.info(f"Cancelled task: {task_id}")
            except Exception as e:
                failed_cancellations.append(f"{task_id}: {str(e)}")
                logger.warning(f"Failed to cancel task {task_id}: {e}")

        # Update status to indicate cancellation
        laws_status["status"] = "cancelled"
        laws_status["cancelled_at"] = now().isoformat()
        laws_status["cancelled_task_count"] = cancelled_count
        laws_status["failed_cancellations"] = failed_cancellations

        # Mark all non-completed laws as cancelled
        for law_id, law_status in laws_status.get("laws", {}).items():
            if law_status.get("status") in ["queued", "processing"]:
                law_status["status"] = "cancelled"
                law_status["error"] = "Task cancelled by user"
                law_status["completed_at"] = now().isoformat()

        otto_status.laws_status = laws_status
        otto_status.save()

        logger.info(
            f"Cancelled {cancelled_count} tasks, {len(failed_cancellations)} failed"
        )
        return {
            "status": "success",
            "message": f"Cancelled {cancelled_count} tasks",
            "cancelled_count": cancelled_count,
            "failed_count": len(failed_cancellations),
            "failed_cancellations": failed_cancellations,
        }

    except Exception as exc:
        logger.error(f"Error cancelling law loading tasks: {exc}")
        return {"status": "error", "message": str(exc)}


def cancel_law_loading():
    """
    Helper function to cancel law loading (can be called from GUI).
    Returns status information about the cancellation.
    """
    try:
        # Check if there's an active loading process
        otto_status = OttoStatus.objects.singleton()
        laws_status = otto_status.laws_status or {}

        if not laws_status:
            return {"status": "error", "message": "No law loading process to cancel"}

        current_status = laws_status.get("status", "unknown")
        if current_status in ["completed", "failed", "cancelled"]:
            return {
                "status": "error",
                "message": f"Cannot cancel - process is already {current_status}",
            }

        # Queue the cancellation task
        result = cancel_law_loading_task.apply_async()

        return {
            "status": "success",
            "message": "Cancellation initiated",
            "cancellation_task_id": result.id,
        }

    except Exception as e:
        logger.error(f"Error initiating law loading cancellation: {e}")
        return {"status": "error", "message": str(e)}
