import os
import shutil
import sys
from contextlib import contextmanager

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import now

from celery import shared_task
from llama_index.core.schema import (
    Document,
    MetadataMode,
    NodeRelationship,
    RelatedNodeInfo,
)
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.models import Cost, OttoStatus
from otto.utils.common import display_cad_cost

from .loading_utils import (
    CONSTITUTION_FILE_PATHS,
    SAMPLE_LAW_IDS,
    _download_repo,
    _get_all_eng_law_ids,
    _get_en_fr_law_file_paths,
    get_sha_256_hash,
    law_xml_to_nodes,
    recreate_indexes,
)
from .models import JobStatus, Law, LawLoadingStatus

logger = get_logger(__name__)


def is_cancelled(current_task_id):
    try:
        job_status = JobStatus.objects.singleton()
        # Cancelled if status is 'cancelled' or if celery_task_id does not match
        return (
            job_status.status == "cancelled"
            or job_status.celery_task_id != current_task_id
        )
    except Exception as e:
        # If we can't check the job status due to async context issues, assume not cancelled
        logger.warning(f"Could not check job cancellation status: {e}")
        return False


class CancelledError(Exception):
    """Raised when a task cancellation is detected."""

    pass


def check_cancel(task_id):
    if is_cancelled(task_id):
        raise CancelledError()


@contextmanager
def cancellation_guard(task_id):
    check_cancel(task_id)
    yield


@shared_task(bind=True, max_retries=10)
def update_laws(
    self,
    small=False,
    full=True,
    const_only=False,
    reset=False,
    force_download=True,
    mock_embedding=False,
    debug=False,
    force_update=False,
    eng_law_ids=None,
    skip_purge=False,
):
    try:
        bind_contextvars(feature="laws_load")
        job_status = JobStatus.objects.singleton()
        # Cancel existing job
        job_status.cancel()
        LawLoadingStatus.objects.all().delete()

        # Update job status to "in_progress"
        job_status.status = "started"
        job_status.started_at = now()
        job_status.finished_at = None
        job_status.error_message = None
        job_status.celery_task_id = self.request.id
        job_status.save()
        current_task_id = self.request.id

        # Determine laws XML root directory, and download if necessary
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
            with cancellation_guard(current_task_id):
                if force_download and os.path.exists(media_laws_dir):
                    shutil.rmtree(media_laws_dir)
            with cancellation_guard(current_task_id):
                if not os.path.exists(media_laws_dir):
                    job_status.status = "downloading"
                    job_status.save()
                    _download_repo()
            laws_root = media_laws_dir

        with cancellation_guard(current_task_id):
            # Determine which laws to process
            law_ids_to_load = []
            if eng_law_ids:
                # List of laws was explicitly provided in call to function
                law_ids_to_load = list(eng_law_ids)
            elif full:
                law_ids_to_load = _get_all_eng_law_ids(laws_root)
            elif small:
                law_ids_to_load = [
                    "SOR-2010-203",  # Certain Ships Remission Order, 2010 (5kb)
                    "S-14.3",  # An Act to grant access to records of the Special Committee on the Defence of Canada Regulations (5kb)
                ]
            elif const_only:
                law_ids_to_load = []
            else:
                # Subset of legislation, for testing
                law_ids_to_load = SAMPLE_LAW_IDS
            if not small and not eng_law_ids:
                law_ids_to_load.append("Constitution 2020")
            eng_law_ids = law_ids_to_load

        with cancellation_guard(current_task_id):
            if reset:
                job_status.status = "resetting"
                job_status.save()
                logger.info("Resetting Law model and indexes")
                Law.reset()
                # Recreate the table
                OttoLLM().get_retriever("laws_lois__", hnsw=True).retrieve("?")

            elif not skip_purge:
                job_status.status = "purging"
                job_status.save()
                logger.info("Deleting missing Law objects")
                Law.objects.purge(keep_ids=eng_law_ids)

        job_status.status = "checking_existing"
        job_status.save()

        with cancellation_guard(current_task_id):
            # Check for existing laws in the database
            existing_law_statuses = []  # Ensure always defined
            existing_laws = Law.objects.filter(eng_law_id__in=eng_law_ids)
            if existing_laws.exists():
                # Create LawLoadingStatus for each
                existing_law_statuses = LawLoadingStatus.objects.bulk_create(
                    [
                        LawLoadingStatus(
                            law=law, eng_law_id=law.eng_law_id, status="pending"
                        )
                        for law in existing_laws
                    ]
                )

        # Check the hashes of existing laws to see if they need updates
        for law_status in existing_law_statuses:
            with cancellation_guard(current_task_id):
                file_paths = _get_en_fr_law_file_paths(laws_root, law_status.eng_law_id)
                if not file_paths:
                    law_status.status = "error"
                    law_status.error_message = f"Could not find EN and FR XML files."
                    law_status.finished_at = now()
                    law_status.save()
                    continue
                en_path, fr_path = file_paths
                new_en_hash = get_sha_256_hash(en_path)
                new_fr_hash = get_sha_256_hash(fr_path)

                if law_status.law:
                    # Get existing hashes
                    existing_en_hash = law_status.law.sha_256_hash_en
                    existing_fr_hash = law_status.law.sha_256_hash_fr

                    # If existing hashes are NULL (from older laws loaded before hash tracking),
                    # we must assume they need updating since we can't compare
                    if existing_en_hash is None or existing_fr_hash is None:
                        logger.info(
                            f"NULL hashes found for existing law {law_status.eng_law_id} - assuming needs update"
                        )
                        law_status.status = "pending_update"
                        law_status.details = "NULL hashes - assuming needs update"

                    # Existing law with valid hashes, check if they match
                    elif (
                        existing_en_hash == new_en_hash
                        and existing_fr_hash == new_fr_hash
                    ):
                        # No update needed
                        if force_update:
                            law_status.status = "pending_update"
                            law_status.details = "No changes detected - forced update"
                        else:
                            law_status.status = "finished_nochange"
                            law_status.details = "No changes detected"
                            law_status.started_at = now()
                            law_status.finished_at = now()

                    # If existing hashes do NOT match, we need to update
                    else:
                        law_status.status = "pending_update"
                        law_status.details = "Changes detected - update"

                law_status.sha_256_hash_en = new_en_hash
                law_status.sha_256_hash_fr = new_fr_hash
                law_status.save()

        job_status.status = "generating_hashes"
        job_status.save()

        new_laws = set(eng_law_ids) - set(
            existing_laws.values_list("eng_law_id", flat=True)
        )
        if new_laws:
            new_law_statuses = []

            for law_id in new_laws:
                with cancellation_guard(current_task_id):
                    # Get the hashes
                    file_paths = _get_en_fr_law_file_paths(laws_root, law_id)
                    if not file_paths:
                        law_status.status = "error"
                        law_status.error_message = (
                            f"Could not find EN and FR XML files."
                        )
                        law_status.finished_at = now()
                        law_status.save()
                        continue
                    en_path, fr_path = file_paths
                    new_en_hash = get_sha_256_hash(en_path)
                    new_fr_hash = get_sha_256_hash(fr_path)

                    new_law_statuses.append(
                        LawLoadingStatus(
                            eng_law_id=law_id,
                            status="pending_new",
                            details="New law",
                            sha_256_hash_en=new_en_hash,
                            sha_256_hash_fr=new_fr_hash,
                        )
                    )

            new_law_statuses = LawLoadingStatus.objects.bulk_create(new_law_statuses)

        job_status.status = "loading_laws"
        job_status.save()
        for law_status in LawLoadingStatus.objects.filter(finished_at__isnull=True):
            with cancellation_guard(current_task_id):
                try:
                    process_law_status(
                        law_status, laws_root, mock_embedding, debug, current_task_id
                    )
                except CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        f"Error processing law {law_status.eng_law_id}: {exc}",
                        exc_info=True,
                    )
                    # Update status to indicate failure
                    try:
                        law_status.status = "error"
                        law_status.error_message = str(exc)
                        law_status.finished_at = now()
                        law_status.save()
                    except Exception as save_error:
                        logger.error(
                            f"Could not save law_status due to error: {save_error}"
                        )

        # Finalize job status
        job_status.status = "rebuilding_indexes"
        job_status.save()
        finalize_law_loading_task(downloaded=force_download)
        job_status.status = "finished"
        job_status.finished_at = now()
        job_status.save()

    except CancelledError:
        logger.info("Job was cancelled in update_laws.")
        try:
            job_status = JobStatus.objects.singleton()
            job_status.status = "cancelled"
            job_status.error_message = "Job was cancelled by user."
            job_status.finished_at = now()
            job_status.save()
        except Exception as save_error:
            logger.error(f"Could not save job_status due to error: {save_error}")

    except Exception as exc:
        logger.error(f"Error in initiate_law_loading_task: {exc}")
        # Update status to indicate failure
        try:
            job_status = JobStatus.objects.singleton()
            job_status.error_message = str(exc)
            job_status.status = "error"
            job_status.finished_at = now()
            job_status.save()
            # Only retry if not cancelled
            if job_status.status != "cancelled":
                raise self.retry(exc=exc, countdown=60)
        except Exception:
            pass
        raise


def process_law_status(law_status, laws_root, mock_embedding, debug, current_task_id):
    try:
        law_status.started_at = now()
        law_status.status = "parsing_xml"
        eng_law_id = law_status.eng_law_id
        logger.info(f"Processing law: {eng_law_id}")

        # Get file paths for the law
        file_paths = _get_en_fr_law_file_paths(laws_root, eng_law_id)
        if not file_paths:
            raise ValueError(f"Could not find EN and FR XML files for {eng_law_id}")

        # Update law status to "processing"
        law_status.save()

        llm = OttoLLM(mock_embedding=mock_embedding)
        document_en = None
        document_fr = None
        nodes_en = None
        nodes_fr = None
        # Create nodes for the English and French XML files
        for k, file_path in enumerate(file_paths):
            with cancellation_guard(current_task_id):
                logger.info(f"Processing file: {file_path}")
                # Create nodes from XML
                node_dict = law_xml_to_nodes(file_path)
            with cancellation_guard(current_task_id):
                if not node_dict["nodes"]:
                    law_status.status = "empty"
                    law_status.finished_at = now()
                    if law_status.law:
                        law = law_status.law
                        law_status.law = None
                        law_status.status = "deleted"
                        law_status.details = (
                            "Existing law deleted due to now being empty"
                        )
                        law_status.save()
                        law.delete()
                    else:
                        law_status.save()
                    return

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

                if file_path in CONSTITUTION_FILE_PATHS:
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
                    nodes[i + 1].relationships[NodeRelationship.PREVIOUS] = (
                        RelatedNodeInfo(node_id=nodes[i].node_id)
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

        with cancellation_guard(current_task_id):
            # Nodes and document should be ready now! Let's add to our Django model
            # This will also handle the creation of LlamaIndex vector tables
            logger.info(
                f"Creating Law object (and embeddings) for document: {document_en.metadata}"
            )

            law_status.status = "embedding_nodes"

            if not debug:
                logger.debug(
                    f"Adding to database: {document_en.metadata['display_metadata']}"
                )

                # This method will update existing Law object if it already exists
                # It includes granular progress updates for embedding
                law = Law.objects.from_docs_and_nodes(
                    law_status,
                    document_en,
                    nodes_en,
                    document_fr,
                    nodes_fr,
                    llm=llm,
                    current_task_id=current_task_id,
                )

                if law is not None:
                    bind_contextvars(law_id=law.id)
                    llm.create_costs()
                    cost_obj = Cost.objects.filter(law=law).order_by("date_incurred")
                    cost = float(cost_obj.last().usd_cost) if cost_obj.exists() else 0.0
                    logger.debug(f"Cost: {display_cad_cost(cost)}")
                    law_status.law = law
                    law_status.cost = cost
                    # Set finished status based on current pending status
                    if "update" in law_status.details.lower():
                        law_status.status = "finished_update"
                        law_status.details = "Law updated successfully"
                    elif "new" in law_status.details.lower():
                        law_status.status = "finished_new"
                        law_status.details = "New law added successfully"
                    law_status.finished_at = now()
                    law_status.save()

    except CancelledError:
        logger.info("Job was cancelled in process_law_status.")
        law_status.status = "cancelled"
        law_status.finished_at = now()
        law_status.error_message = "Job was cancelled by user."
        law_status.save()
        return
    except Exception as e:
        logger.error(f"Error in process_law_status: {e}", exc_info=True)
        try:
            law_status.status = "error"
            law_status.error_message = str(e)
            law_status.finished_at = now()
            law_status.law = None
            law_status.save()
        except Exception as save_error:
            logger.error(f"Could not save law_status due to error: {save_error}")
        raise e


def finalize_law_loading_task(downloaded=False):
    """
    Finalize the law loading process by rebuilding indexes and updating timestamps.
    """
    try:
        logger.info("Finalizing law loading process...")

        # Rebuild vector-specific indexes (node_id index is managed by Django model)
        # Only recreate indexes if not running under pytest
        if not any("pytest" in arg for arg in sys.argv):
            recreate_indexes(node_id=False, jsonb=True, hnsw=False)

        # Update final status
        if downloaded:
            otto_status = OttoStatus.objects.singleton()
            otto_status.laws_last_refreshed = now()
            otto_status.save()
        logger.info("Law loading finalization complete")

    except Exception as exc:
        logger.error(f"Error in finalize_law_loading_task: {exc}")
        raise exc
