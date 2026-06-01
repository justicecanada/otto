import os
import shutil
import sys

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import now

from celery import shared_task
from llama_index.core.schema import (
    Document,
    MetadataMode,
    NodeRelationship,
    RelatedNodeInfo,
    TextNode,
)
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import Cost, OttoStatus
from otto.priorities import LOWEST
from otto.utils.common import display_cad_cost

from chat.llm import OttoLLM

from .loading_utils import (
    CONSTITUTION_FILE_PATHS,
    SAMPLE_LAW_IDS,
    _build_law_file_cache,
    _download_repo,
    _get_all_eng_law_ids,
    _get_en_fr_law_file_paths,
    drop_indexes,
    drop_legacy_compound_indexes,
    get_sha_256_hash,
    law_xml_to_nodes,
    recreate_indexes,
    vacuum_analyze_laws_table,
    wait_for_indexes_and_prewarm,
)
from .models import JobStatus, Law, LawLoadingStatus

logger = get_logger(__name__)
twenty_minutes = 20 * 60  # 20 minutes in seconds


def _get_laws_temp_dir():
    """Get or create the temp directory for laws processing."""
    temp_dir = os.path.join(settings.MEDIA_ROOT, "tmp_laws")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def is_cancelled(current_task_id):
    try:
        job_status = JobStatus.objects.singleton()
        # Cancelled if status is 'cancelled' or if celery_task_id does not match
        return (
            job_status.status == "cancelled"
            or job_status.celery_task_id != current_task_id
        )
    except Exception as e:
        # Fail-safe: if we can't verify job status, assume cancelled to avoid
        # doing real work on potentially stale/cancelled jobs.
        logger.warning(f"Could not check job cancellation status: {e}")
        return True


class CancelledError(Exception):
    """Raised when a task cancellation is detected."""

    pass


def check_cancel(task_id):
    if is_cancelled(task_id):
        raise CancelledError()


@shared_task(bind=True, queue=settings.HEAVY_QUEUE)
def compute_hashes_and_spawn(
    self,
    laws_root,
    eng_law_ids,
    reset,
    force_update,
    mock_embedding,
    debug,
    force_download,
    parent_task_id,
):
    """
    Heavy task: Handle all CPU-intensive and blocking operations:
    - Drop indexes if reset (blocking DB operation)
    - Compute SHA256 hashes for all law files (CPU-intensive)
    - Update LawLoadingStatus with hash comparison results
    - Spawn individual law processing tasks

    This runs on heavy worker to avoid blocking the light worker.
    """
    try:
        bind_contextvars(feature="laws_load", user_id=None, cost_group_id=None)
        check_cancel(parent_task_id)

        job_status = JobStatus.objects.singleton()

        # Drop indexes on full reset - blocking DB operation, safe on heavy worker
        if reset:
            logger.info("Dropping indexes for full reset")
            drop_indexes()

        check_cancel(parent_task_id)
        job_status.status = "generating_hashes"
        job_status.save()

        # Get existing laws to identify which are new vs updates
        existing_laws = Law.objects.filter(eng_law_id__in=eng_law_ids).only(
            "id", "eng_law_id"
        )
        existing_law_ids = set(existing_laws.values_list("eng_law_id", flat=True))

        # Build file path cache to avoid repeated os.path.exists() calls (saves 4 calls per law)
        logger.info("Building law file cache for fast lookups...")
        file_cache = _build_law_file_cache(laws_root)

        # Compute hashes for all laws (CPU-intensive)
        hash_results = {}
        total_laws = len(eng_law_ids)
        last_update = 0
        # Update frequency: more frequent for small sets, less for large (max every 10 laws)
        update_interval = min(max(total_laws // 20, 5), 10)

        for idx, law_id in enumerate(eng_law_ids, 1):
            check_cancel(parent_task_id)

            # Update progress periodically to show user progress
            if idx == 1 or idx == total_laws or idx - last_update >= update_interval:
                job_status.status = f"gen_hashes ({idx}/{total_laws})"
                job_status.save(update_fields=["status"])
                last_update = idx

            file_paths = _get_en_fr_law_file_paths(laws_root, law_id, cache=file_cache)
            if not file_paths:
                hash_results[law_id] = None  # Mark as error
                continue

            en_path, fr_path = file_paths

            # CPU-intensive hash computation
            new_en_hash = get_sha_256_hash(en_path)
            new_fr_hash = get_sha_256_hash(fr_path)

            hash_results[law_id] = (new_en_hash, new_fr_hash)

        # Reset status to standard value after loop
        job_status.status = "generating_hashes"
        job_status.save(update_fields=["status"])

        check_cancel(parent_task_id)

        # Update existing law statuses with hash results - fetch with select_related to avoid N+1
        law_statuses = LawLoadingStatus.objects.filter(
            finished_at__isnull=True
        ).select_related("law")

        statuses_to_update = []
        for law_status in law_statuses:
            check_cancel(parent_task_id)

            eng_law_id = law_status.eng_law_id
            hash_info = hash_results.get(eng_law_id)

            if hash_info is None:
                # Error - couldn't find files
                law_status.status = "error"
                law_status.error_message = "Could not find EN and FR XML files."
                law_status.finished_at = now()
                statuses_to_update.append(law_status)
                continue

            new_en_hash, new_fr_hash = hash_info
            law_status.sha_256_hash_en = new_en_hash
            law_status.sha_256_hash_fr = new_fr_hash

            if law_status.law:
                # Existing law - check if update needed
                existing_en_hash = law_status.law.sha_256_hash_en
                existing_fr_hash = law_status.law.sha_256_hash_fr

                if existing_en_hash is None or existing_fr_hash is None:
                    logger.info(
                        f"NULL hashes found for existing law {eng_law_id} - assuming needs update"
                    )
                    law_status.status = "pending_update"
                    law_status.details = "NULL hashes - assuming needs update"
                elif (
                    existing_en_hash == new_en_hash and existing_fr_hash == new_fr_hash
                ):
                    if force_update:
                        law_status.status = "pending_update"
                        law_status.details = "No changes detected - forced update"
                    else:
                        law_status.status = "finished_nochange"
                        law_status.details = "No changes detected"
                        law_status.started_at = now()
                        law_status.finished_at = now()
                else:
                    law_status.status = "pending_update"
                    law_status.details = "Changes detected - update"
            else:
                # New law
                law_status.status = "pending_new"
                law_status.details = "New law"

            statuses_to_update.append(law_status)

        # Bulk update all statuses
        if statuses_to_update:
            LawLoadingStatus.objects.bulk_update(
                statuses_to_update,
                fields=[
                    "status",
                    "details",
                    "sha_256_hash_en",
                    "sha_256_hash_fr",
                    "error_message",
                    "finished_at",
                    "started_at",
                ],
                batch_size=500,
            )

        check_cancel(parent_task_id)

        # Create status entries for new laws not yet in database
        new_law_ids = set(hash_results.keys()) - set(existing_law_ids)
        new_law_statuses = []

        for law_id in new_law_ids:
            hash_info = hash_results.get(law_id)
            if hash_info is None:
                LawLoadingStatus.objects.create(
                    eng_law_id=law_id,
                    status="error",
                    error_message="Could not find EN and FR XML files.",
                    finished_at=now(),
                )
                continue

            new_en_hash, new_fr_hash = hash_info
            new_law_statuses.append(
                LawLoadingStatus(
                    eng_law_id=law_id,
                    status="pending_new",
                    details="New law",
                    sha_256_hash_en=new_en_hash,
                    sha_256_hash_fr=new_fr_hash,
                )
            )

        if new_law_statuses:
            LawLoadingStatus.objects.bulk_create(new_law_statuses)

        check_cancel(parent_task_id)
        job_status.status = "loading_laws"
        job_status.save()

        # Spawn individual law processing tasks and collect IDs for bulk revocation
        spawned_task_ids = []
        laws_to_process = LawLoadingStatus.objects.filter(finished_at__isnull=True)
        for law_status in laws_to_process:
            check_cancel(parent_task_id)
            result = parse_law_xml.apply_async(
                kwargs={
                    "law_status_id": law_status.id,
                    "laws_root": laws_root,
                    "mock_embedding": mock_embedding,
                    "debug": debug,
                    "parent_task_id": parent_task_id,
                    "force_download": force_download,
                    "reset": reset,
                },
                priority=LOWEST,
            )
            try:
                spawned_task_ids.append(str(result.id))
            except Exception:
                pass
            result.backend = None  # Prevent BlockingSwitchOutError in gevent

        # Store spawned task IDs so cancel() can revoke them in bulk
        try:
            job_status.refresh_from_db()
            opts = job_status.options or {}
            opts["spawned_task_ids"] = spawned_task_ids
            job_status.options = opts
            job_status.save(update_fields=["options"])
        except Exception as e:
            logger.warning(f"Could not store spawned task IDs: {e}")

        return {"ok": True, "laws_to_process": laws_to_process.count()}

    except CancelledError:
        logger.info("Job was cancelled in compute_hashes_and_spawn")
        try:
            job_status = JobStatus.objects.singleton()
            job_status.status = "cancelled"
            job_status.error_message = "Job was cancelled by user."
            job_status.finished_at = now()
            job_status.save()
        except Exception as save_error:
            logger.error(f"Could not save job_status due to error: {save_error}")
        raise
    except Exception as exc:
        logger.error(f"Error in compute_hashes_and_spawn: {exc}", exc_info=True)
        try:
            job_status = JobStatus.objects.singleton()
            job_status.error_message = str(exc)
            job_status.status = "error"
            job_status.finished_at = now()
            job_status.save()
        except Exception:
            pass
        raise


@shared_task(bind=True, max_retries=10, queue=settings.LIGHT_QUEUE)
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
    """
    Light task: Set up JobStatus, download/extract repo (cooperative I/O),
    then chain to heavy worker for CPU-intensive hash computation and task spawning.

    With gevent monkey patching, file/network I/O is cooperative and won't block worker.
    """
    try:
        bind_contextvars(feature="laws_load", user_id=None, cost_group_id=None)
        job_status = JobStatus.objects.singleton()
        # Cancel existing job
        job_status.cancel()
        LawLoadingStatus.objects.all().delete()

        # Update job status to "started"
        job_status.status = "started"
        job_status.started_at = now()
        job_status.finished_at = None
        job_status.error_message = None
        job_status.celery_task_id = self.request.id
        job_status.options = {
            "small": small,
            "full": full,
            "const_only": const_only,
            "reset": reset,
            "force_download": force_download,
            "mock_embedding": mock_embedding,
            "force_update": force_update,
            "eng_law_ids": eng_law_ids or [],
            "skip_purge": skip_purge,
        }
        job_status.save()
        current_task_id = self.request.id

        # Determine laws XML root directory
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
            check_cancel(current_task_id)

            # With monkey patching, shutil.rmtree is cooperative I/O
            if force_download and os.path.exists(media_laws_dir):
                logger.info("Deleting existing laws directory")
                shutil.rmtree(media_laws_dir)

            check_cancel(current_task_id)

            # Download repo if needed (cooperative I/O with monkey patching)
            if not os.path.exists(media_laws_dir):
                job_status.status = "downloading"
                job_status.save()
                _download_repo()

            laws_root = media_laws_dir

        check_cancel(current_task_id)

        # Determine which laws to process
        law_ids_to_load = []
        if eng_law_ids:
            law_ids_to_load = list(eng_law_ids)
        elif full:
            law_ids_to_load = _get_all_eng_law_ids(laws_root)
        elif small:
            law_ids_to_load = [
                "SOR-2010-203",
                "S-14.3",
            ]
        elif const_only:
            law_ids_to_load = []
        else:
            law_ids_to_load = SAMPLE_LAW_IDS

        if not small and not eng_law_ids:
            law_ids_to_load.append("Constitution 2020")

        eng_law_ids = law_ids_to_load

        check_cancel(current_task_id)

        # Reset or purge as needed
        if reset:
            job_status.status = "resetting"
            job_status.save()
            logger.info("Resetting Law model and indexes")
            Law.reset()
        elif not skip_purge:
            job_status.status = "purging"
            job_status.save()
            logger.info("Deleting missing Law objects")
            Law.objects.purge(keep_ids=eng_law_ids)

        check_cancel(current_task_id)
        job_status.status = "checking_existing"
        job_status.save()

        # Check for existing laws and create LawLoadingStatus entries
        # Process in chunks to avoid large IN clause and reduce memory usage
        chunk_size = 500
        total_law_ids = len(eng_law_ids)

        for i in range(0, total_law_ids, chunk_size):
            chunk = eng_law_ids[i : i + chunk_size]
            existing_laws = Law.objects.filter(eng_law_id__in=chunk).only(
                "id", "eng_law_id"
            )

            status_entries = [
                LawLoadingStatus(
                    law_id=law.id, eng_law_id=law.eng_law_id, status="pending"
                )
                for law in existing_laws
            ]

            if status_entries:
                LawLoadingStatus.objects.bulk_create(status_entries, batch_size=500)

            # Check for cancellation periodically
            if i % 2000 == 0:
                check_cancel(current_task_id)

        check_cancel(current_task_id)

        # Update status before chaining so users see progress immediately
        job_status.status = "generating_hashes"
        job_status.save()

        # Chain to heavy worker for CPU-intensive hash computation and task spawning
        _res = compute_hashes_and_spawn.apply_async(
            kwargs={
                "laws_root": laws_root,
                "eng_law_ids": eng_law_ids,
                "reset": reset,
                "force_update": force_update,
                "mock_embedding": mock_embedding,
                "debug": debug,
                "force_download": force_download,
                "parent_task_id": current_task_id,
            },
            priority=LOWEST,
        )
        _res.backend = None  # Prevent BlockingSwitchOutError in gevent

        return {"ok": True, "chained": True}

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
        logger.error(f"Error in update_laws: {exc}", exc_info=True)
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


@shared_task(bind=True, soft_time_limit=600, queue=settings.HEAVY_QUEUE)
def parse_law_xml(
    self,
    law_status_id,
    laws_root,
    mock_embedding,
    debug,
    parent_task_id,
    force_download,
    reset,
):
    """
    Heavy task: Parse XML files for a single law and create nodes.
    Chains to insert_law_chunks upon success.
    """
    try:
        law_status = LawLoadingStatus.objects.get(id=law_status_id)
    except LawLoadingStatus.DoesNotExist:
        logger.error("LawLoadingStatus not found", law_status_id=law_status_id)
        return

    # Fast exit: if cancel() already marked this entry, skip all work.
    if law_status.status == "cancelled" or law_status.finished_at is not None:
        return

    nodes_file_path = None
    cleanup_file = False  # Only cleanup on error, not success
    try:
        bind_contextvars(feature="laws_load", user_id=None, cost_group_id=None)
        check_cancel(parent_task_id)

        law_status.started_at = now()
        law_status.status = "parsing_xml"
        eng_law_id = law_status.eng_law_id
        logger.info(f"Processing law: {eng_law_id}")

        # Get file paths for the law
        file_paths = _get_en_fr_law_file_paths(laws_root, eng_law_id)
        if not file_paths:
            raise ValueError(f"Could not find EN and FR XML files for {eng_law_id}")

        law_status.save()

        document_en = None
        document_fr = None
        nodes_en = None
        nodes_fr = None

        # Create nodes for the English and French XML files
        for k, file_path in enumerate(file_paths):
            check_cancel(parent_task_id)
            logger.info(f"Processing file: {file_path}")
            # Create nodes from XML
            node_dict = law_xml_to_nodes(file_path)

            check_cancel(parent_task_id)
            if not node_dict["nodes"]:
                law_status.status = "empty"
                law_status.finished_at = now()
                if law_status.law:
                    law = law_status.law
                    law_status.law = None
                    law_status.status = "deleted"
                    law_status.details = "Existing law deleted due to now being empty"
                    law.delete()
                law_status.save()
                _check_and_finalize(parent_task_id, force_download, reset)
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
                f"{doc_metadata['short_title'] or ''}"
                f"{': ' if doc_metadata['short_title'] and doc_metadata['long_title'] else ''}"
                f"{doc_metadata['long_title'] or ''} "
                f"({doc_metadata['consolidated_number'] or doc_metadata['instrument_number'] or doc_metadata['bill_number']})"
            )

            doc_id = f"{node_dict['id']}_{node_dict['lang']}"
            document = Document(
                doc_id=doc_id,
                text=doc_metadata["display_metadata"],
                metadata=doc_metadata,
                excluded_llm_metadata_keys=exclude_keys,
                excluded_embed_metadata_keys=exclude_keys,
                metadata_template="{value}",
                text_template="{metadata_str}",
            )

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

        check_cancel(parent_task_id)

        # Serialize nodes to JSON for passing to next task
        # We'll store them temporarily in the law_status details field as JSON
        import json

        nodes_data = {
            "document_en": {
                "doc_id": document_en.doc_id,
                "text": document_en.text,
                "metadata": document_en.metadata,
                "excluded_llm_metadata_keys": document_en.excluded_llm_metadata_keys,
                "excluded_embed_metadata_keys": document_en.excluded_embed_metadata_keys,
            },
            "document_fr": {
                "doc_id": document_fr.doc_id,
                "text": document_fr.text,
                "metadata": document_fr.metadata,
                "excluded_llm_metadata_keys": document_fr.excluded_llm_metadata_keys,
                "excluded_embed_metadata_keys": document_fr.excluded_embed_metadata_keys,
            },
            "nodes_en": [
                {
                    "id_": node.id_,
                    "text": node.text,
                    "metadata": node.metadata,
                    "excluded_llm_metadata_keys": node.excluded_llm_metadata_keys,
                    "excluded_embed_metadata_keys": node.excluded_embed_metadata_keys,
                    "relationships": {
                        rel_type.value: {"node_id": rel_info.node_id}
                        for rel_type, rel_info in node.relationships.items()
                    },
                }
                for node in nodes_en
            ],
            "nodes_fr": [
                {
                    "id_": node.id_,
                    "text": node.text,
                    "metadata": node.metadata,
                    "excluded_llm_metadata_keys": node.excluded_llm_metadata_keys,
                    "excluded_embed_metadata_keys": node.excluded_embed_metadata_keys,
                    "relationships": {
                        rel_type.value: {"node_id": rel_info.node_id}
                        for rel_type, rel_info in node.relationships.items()
                    },
                }
                for node in nodes_fr
            ],
        }

        # Save to file in MEDIA_ROOT-based temp directory (deterministic name per law)
        # Using a stable filename avoids tmp-cleaner races across workers or restarts.
        temp_dir = _get_laws_temp_dir()
        safe_law_id = eng_law_id.replace("/", "_").replace(" ", "_").replace("..", ".")
        nodes_file_path = os.path.join(temp_dir, f"law_{safe_law_id}.json")
        # Ensure directory exists and write atomically via temp then rename
        os.makedirs(temp_dir, exist_ok=True)
        tmp_path = nodes_file_path + ".part"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(nodes_data, f)
        os.replace(tmp_path, nodes_file_path)

        logger.info(f"Parsed law {eng_law_id}, chaining to insert_law_chunks")

        law_status.status = "parsed"
        law_status.save()

        # Chain to insert task on light queue
        _res = insert_law_chunks.apply_async(
            kwargs={
                "law_status_id": law_status_id,
                "nodes_file_path": nodes_file_path,
                "mock_embedding": mock_embedding,
                "debug": debug,
                "parent_task_id": parent_task_id,
                "force_download": force_download,
                "reset": reset,
            },
            priority=LOWEST,
        )
        _res.backend = None  # Prevent BlockingSwitchOutError in gevent

        return {"ok": True, "law_id": eng_law_id}

    except CancelledError:
        logger.info("Job was cancelled in parse_law_xml.")
        cleanup_file = True  # Clean up on cancellation
        law_status.status = "cancelled"
        law_status.finished_at = now()
        law_status.error_message = "Job was cancelled by user."
        law_status.save()
        return
    except Exception as e:
        logger.error(f"Error in parse_law_xml: {e}", exc_info=True)
        cleanup_file = True  # Clean up on error
        try:
            law_status.status = "error"
            law_status.error_message = str(e)
            law_status.finished_at = now()
            law_status.law = None
            law_status.save()
            # Still check if we should finalize
            _check_and_finalize(parent_task_id, force_download, reset)
        except Exception as save_error:
            logger.error(f"Could not save law_status due to error: {save_error}")
        raise e
    finally:
        # Clean up temp file only on error/cancellation, not on success
        # On success, the file will be cleaned up by insert_law_chunks
        if cleanup_file and nodes_file_path and os.path.exists(nodes_file_path):
            os.unlink(nodes_file_path)


@shared_task(bind=True, soft_time_limit=twenty_minutes, queue=settings.EMBED_QUEUE)
def insert_law_chunks(
    self,
    law_status_id,
    nodes_file_path,
    mock_embedding,
    debug,
    parent_task_id,
    force_download,
    reset,
    start_index=0,
):
    """
    Light task: Create Law object and insert nodes into vector database.
    Very similar to finalize_document_light for librarian.
    """
    import json
    import os

    from librarian.utils.batch_embedding import (
        BatchEmbeddingProgress,
        create_cost_tracking_wrapper,
        insert_nodes_with_checkpointing,
    )

    # Track if task was requeued to avoid deleting temp file needed by requeued task
    task_was_requeued = False

    try:
        law_status = LawLoadingStatus.objects.get(id=law_status_id)
    except LawLoadingStatus.DoesNotExist:
        logger.error("LawLoadingStatus not found", law_status_id=law_status_id)
        # Clean up temp file
        if os.path.exists(nodes_file_path):
            os.unlink(nodes_file_path)
        return

    # Fast exit: if cancel() already marked this entry, skip all work.
    if law_status.status == "cancelled" or law_status.finished_at is not None:
        # Clean up temp file since we won't process it
        if os.path.exists(nodes_file_path):
            os.unlink(nodes_file_path)
        return

    law = None
    try:
        bind_contextvars(feature="laws_load", user_id=None, cost_group_id=None)
        check_cancel(parent_task_id)

        # Only set creating_law_object status if the Law doesn't exist yet.
        # start_index can be 0 even on a requeue (e.g. when the first batch
        # hits a 429 rate-limit and gets requeued before any nodes are inserted),
        # so law_status.law_id is the reliable indicator.
        if not law_status.law_id:
            law_status.status = "creating_law_object"
            law_status.save()

        # Load nodes from temp file (retry once if transient missing)
        if not os.path.exists(nodes_file_path):
            logger.warning(
                "Temp nodes file missing on first attempt", path=nodes_file_path
            )
            # Brief sleep to allow possible delayed write (edge case)
            import time as _t

            _t.sleep(0.25)
        if not os.path.exists(nodes_file_path):
            law_status.status = "error"
            law_status.error_message = f"Missing temp nodes file: {nodes_file_path}"
            law_status.finished_at = now()
            law_status.save()
            _check_and_finalize(parent_task_id, force_download, reset)
            return
        with open(nodes_file_path, "r", encoding="utf-8") as f:
            nodes_data = json.load(f)

        # Keep file for potential requeueing - will be cleaned up in finally block
        # (removed early deletion that was preventing requeued tasks from requeueing again)

        # Reconstruct Document objects
        document_en = Document(
            doc_id=nodes_data["document_en"]["doc_id"],
            text=nodes_data["document_en"]["text"],
            metadata=nodes_data["document_en"]["metadata"],
            excluded_llm_metadata_keys=nodes_data["document_en"][
                "excluded_llm_metadata_keys"
            ],
            excluded_embed_metadata_keys=nodes_data["document_en"][
                "excluded_embed_metadata_keys"
            ],
            metadata_template="{value}",
            text_template="{metadata_str}",
        )

        document_fr = Document(
            doc_id=nodes_data["document_fr"]["doc_id"],
            text=nodes_data["document_fr"]["text"],
            metadata=nodes_data["document_fr"]["metadata"],
            excluded_llm_metadata_keys=nodes_data["document_fr"][
                "excluded_llm_metadata_keys"
            ],
            excluded_embed_metadata_keys=nodes_data["document_fr"][
                "excluded_embed_metadata_keys"
            ],
            metadata_template="{value}",
            text_template="{metadata_str}",
        )

        # Reconstruct Node objects
        nodes_en = []
        for node_data in nodes_data["nodes_en"]:
            node = TextNode(
                id_=node_data["id_"],
                text=node_data["text"],
                metadata=node_data["metadata"],
                excluded_llm_metadata_keys=node_data["excluded_llm_metadata_keys"],
                excluded_embed_metadata_keys=node_data["excluded_embed_metadata_keys"],
            )
            # Reconstruct relationships
            for rel_type_str, rel_info in node_data["relationships"].items():
                rel_type = NodeRelationship(rel_type_str)
                node.relationships[rel_type] = RelatedNodeInfo(
                    node_id=rel_info["node_id"]
                )
            nodes_en.append(node)

        nodes_fr = []
        for node_data in nodes_data["nodes_fr"]:
            node = TextNode(
                id_=node_data["id_"],
                text=node_data["text"],
                metadata=node_data["metadata"],
                excluded_llm_metadata_keys=node_data["excluded_llm_metadata_keys"],
                excluded_embed_metadata_keys=node_data["excluded_embed_metadata_keys"],
            )
            # Reconstruct relationships
            for rel_type_str, rel_info in node_data["relationships"].items():
                rel_type = NodeRelationship(rel_type_str)
                node.relationships[rel_type] = RelatedNodeInfo(
                    node_id=rel_info["node_id"]
                )
            nodes_fr.append(node)

        if debug:
            # In debug mode, just create the law object without embedding
            law = Law.objects.create_or_update_from_documents(
                law_status,
                document_en,
                document_fr,
            )
            law_status.status = "finished_debug"
            law_status.details = "Debug mode - no embedding"
            law_status.finished_at = now()
            law_status.save()
            _check_and_finalize(parent_task_id, force_download, reset)
            return {"ok": True, "law_status_id": law_status_id, "debug": True}

        if law_status.law_id:
            # Requeue: Law object already created in a prior run
            law_status.refresh_from_db(fields=["law"])
            law = law_status.law
        else:
            # Create/update the Law object (no embedding yet)
            law = Law.objects.create_or_update_from_documents(
                law_status,
                document_en,
                document_fr,
            )

        bind_contextvars(feature="laws_load", law_id=law.id if law else None)
        logger.debug(f"Adding to database: {document_en.metadata['display_metadata']}")

        law_status.status = "embedding_nodes"
        law_status.save()

        # Setup LLM for embedding
        llm = OttoLLM(
            mock_embedding=mock_embedding,
            priority=settings.DEFAULT_LAWS_LLM_PRIORITY,
        )

        # Get vector store index
        idx = llm.get_index("laws_lois__", hnsw=False)

        # Delete existing vectors if updating
        if law_status.details and "update" in law_status.details.lower():
            try:
                from librarian.tasks import delete_documents_from_vector_store

                delete_documents_from_vector_store(
                    [law.node_id_en, law.node_id_fr], "laws_lois__"
                )
            except Exception as e:
                logger.error(
                    f"Error deleting nodes from vector store for law {law.eng_law_id}: {e}"
                )

        # Prepare all nodes for embedding
        nodes = []
        nodes.append(document_en)
        nodes.extend(nodes_en)
        nodes.append(document_fr)
        nodes.extend(nodes_fr)

        logger.debug(
            f"Embedding & inserting nodes into vector store (batch size={settings.EMBEDDING_BATCH_SIZE} nodes)..."
        )

        # Filter out bad nodes gracefully
        valid_nodes = []
        for node in nodes:
            try:
                if hasattr(node, "text") and node.text and node.text.strip():
                    valid_nodes.append(node)
                else:
                    logger.warning(
                        f"Skipping node with missing or empty text: {getattr(node, 'doc_id', 'unknown')}"
                    )
            except Exception as e:
                logger.warning(f"Skipping node due to error: {e}")
        nodes = valid_nodes

        # Setup progress tracking
        progress_tracker = BatchEmbeddingProgress(
            f"law_{law.eng_law_id}_embedding_progress"
        )

        # Create wrappers for the embedding utility
        def check_cancel_wrapper():
            check_cancel(parent_task_id)

        # Strip any previously appended status text from requeued runs
        # e.g. "New law Adding to library... (192/1539 - waiting)" -> "New law"
        raw_details = law_status.details or ""
        original_details = raw_details.split("Adding to library")[0].strip()

        def update_status(text):
            law_status.details = f"{original_details} {text}".strip()
            law_status.save(update_fields=["details"])

        def requeue_task(
            next_index,
            *,
            countdown_seconds: float = 0,
            requeue_reason: str | None = None,
        ):
            nonlocal task_was_requeued
            task_was_requeued = True
            current_priority = self.request.delivery_info.get("priority", LOWEST)
            apply_async_kwargs = {
                "kwargs": {
                    "law_status_id": law_status_id,
                    "nodes_file_path": nodes_file_path,
                    "mock_embedding": mock_embedding,
                    "debug": debug,
                    "parent_task_id": parent_task_id,
                    "force_download": force_download,
                    "reset": reset,
                    "start_index": next_index,
                },
                "priority": current_priority,
            }
            if countdown_seconds > 0:
                apply_async_kwargs["countdown"] = countdown_seconds

            logger.info(
                "Requeueing law embedding continuation",
                law_status_id=law_status_id,
                next_index=next_index,
                priority=apply_async_kwargs["priority"],
                countdown_seconds=countdown_seconds,
                requeue_reason=requeue_reason,
            )

            res = insert_law_chunks.apply_async(**apply_async_kwargs)
            task_id = res.id
            res.backend = None  # Prevent BlockingSwitchOutError in gevent
            return task_id

        wrapped_index = create_cost_tracking_wrapper(idx, llm)

        # Use the batch embedding utility
        result = insert_nodes_with_checkpointing(
            nodes=nodes,
            vector_store_index=wrapped_index,
            progress_tracker=progress_tracker,
            check_cancel_fn=check_cancel_wrapper,
            update_status_fn=update_status,
            requeue_fn=requeue_task,
            log_batch_fn=None,  # No logging for laws
            start_index=start_index,
        )

        # Handle cancellation
        if not result.get("ok"):
            if result.get("error") == "cancelled":
                raise CancelledError()
            raise Exception(f"Failed to insert nodes: {result.get('error')}")

        # Handle requeue — the continuation task will finish this law
        if result.get("requeued"):
            return {"ok": True, "law_status_id": law_status_id, "requeued": True}

        # Clear progress
        progress_tracker.clear()

        # Only set hashes after successful vector store operations
        law.sha_256_hash_en = law_status.sha_256_hash_en
        law.sha_256_hash_fr = law_status.sha_256_hash_fr
        law.save()

        # Calculate and save cost (sum of all costs for this law)
        from django.db.models import Sum

        cost_sum = Cost.objects.filter(law=law).aggregate(Sum("usd_cost"))[
            "usd_cost__sum"
        ]
        cost = float(cost_sum) if cost_sum else 0.0
        logger.debug(f"Cost: {display_cad_cost(cost)}")
        law_status.cost = cost

        # Set finished status based on current pending status
        if "update" in (law_status.details or "").lower():
            law_status.status = "finished_update"
            law_status.details = "Law updated successfully"
        elif "new" in (law_status.details or "").lower():
            law_status.status = "finished_new"
            law_status.details = "New law added successfully"
        else:
            law_status.status = "finished"
            law_status.details = "Law processed successfully"

        law_status.finished_at = now()
        law_status.save()

        # Check if all laws are done and trigger finalization if so
        _check_and_finalize(parent_task_id, force_download, reset)

        return {"ok": True, "law_status_id": law_status_id}

    except CancelledError:
        logger.info("Job was cancelled in insert_law_chunks.")
        law_status.status = "cancelled"
        law_status.finished_at = now()
        law_status.error_message = "Job was cancelled by user."
        law_status.save()
        # Clean up law object if it was created
        if law and law.pk:
            try:
                law.delete()
            except Exception as e:
                logger.error(f"Failed to clean up law object: {e}")
        return
    except Exception as e:
        logger.error(f"Error in insert_law_chunks: {e}", exc_info=True)
        try:
            law_status.status = "error"
            law_status.error_message = str(e)
            law_status.finished_at = now()
            law_status.save()
            # Clean up law object if it was created
            if law and law.pk:
                try:
                    law.delete()
                except Exception as cleanup_error:
                    logger.error(f"Failed to clean up law object: {cleanup_error}")
            # Still check if we should finalize
            _check_and_finalize(parent_task_id, force_download, reset)
        except Exception as save_error:
            logger.error(f"Could not save law_status due to error: {save_error}")
        raise e
    finally:
        # Clean up temp file if it still exists
        # Do NOT delete if task was requeued - the requeued task needs this file
        # Delete on: successful completion, errors, or cancellation (when task_was_requeued=False)
        if not task_was_requeued and os.path.exists(nodes_file_path):
            try:
                os.unlink(nodes_file_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file: {e}")


def _check_and_finalize(parent_task_id, force_download, reset):
    """
    Check if all laws are complete (success or error).
    If so, trigger finalization task.
    Uses database-level locking to prevent race conditions.
    """
    from django.db import transaction

    try:
        check_cancel(parent_task_id)

        # Use select_for_update to lock the JobStatus row
        with transaction.atomic():
            job_status = JobStatus.objects.select_for_update().get()

            # Check if job is still active
            if job_status.celery_task_id != parent_task_id:
                logger.info("Job was superseded, skipping finalization check")
                return

            # Check if already finalizing or finished
            if job_status.status in [
                "rebuilding_indexes",
                "finished",
                "cancelled",
                "error",
            ]:
                return

            # Count incomplete laws
            incomplete_count = LawLoadingStatus.objects.filter(
                finished_at__isnull=True
            ).count()

            if incomplete_count == 0:
                # All laws are done! Trigger finalization
                logger.info("All laws complete, triggering finalization")
                job_status.status = "rebuilding_indexes"
                job_status.save()

                # Trigger finalization task
                _res = finalize_law_loading.apply_async(
                    kwargs={
                        "parent_task_id": parent_task_id,
                        "force_download": force_download,
                        "reset": reset,
                    },
                    priority=LOWEST,
                )
                _res.backend = None  # Prevent BlockingSwitchOutError in gevent
    except CancelledError:
        logger.info("Job was cancelled during finalization check")
    except Exception as e:
        logger.error(f"Error checking finalization: {e}", exc_info=True)


@shared_task(bind=True, queue=settings.LIGHT_QUEUE)
def finalize_law_loading(self, parent_task_id, force_download, reset):
    """
    Light task: Finalize the law loading process by rebuilding indexes and updating timestamps.
    Only recreates indexes on full reset to avoid performance impact during incremental updates.
    """
    try:
        check_cancel(parent_task_id)

        logger.info("Finalizing law loading process...")

        # Only recreate indexes if doing full reset
        # Incremental updates work fine with existing indexes - deleted vectors are marked as deleted
        # For periodic maintenance, use a separate REINDEX task
        if reset and not any("pytest" in arg for arg in sys.argv):
            logger.info("Full reset detected - recreating indexes...")
            recreate_indexes()

            # Wait for indexes to complete building, then pre-warm them
            # This runs cooperatively with gevent - time.sleep() is monkey-patched
            logger.info("Waiting for index build completion to pre-warm...")
            prewarm_result = wait_for_indexes_and_prewarm()
            logger.info("Pre-warm result", **prewarm_result)
        elif not any("pytest" in arg for arg in sys.argv):
            # Incremental path: keep it fast and predictable — ANALYZE only with a sensible timeout.
            DEFAULT_ANALYZE_TIMEOUT_SECONDS = 300
            logger.info(
                "Incremental update - refreshing table statistics (ANALYZE only)...",
                timeout_seconds=DEFAULT_ANALYZE_TIMEOUT_SECONDS,
            )
            # Update status while ANALYZE runs so UI reflects stage
            job_status = JobStatus.objects.singleton()
            job_status.status = "updating_stats"
            job_status.save(update_fields=["status"])
            # Drop any legacy compound indexes that may still exist (not recreated by reset)
            drop_legacy_compound_indexes()
            vacuum_analyze_laws_table(
                analyze_only=True,
                timeout_seconds=DEFAULT_ANALYZE_TIMEOUT_SECONDS,
            )

        # Update final status
        if force_download:
            otto_status = OttoStatus.objects.singleton()
            otto_status.laws_last_refreshed = now()
            otto_status.save()

        # Save any skipped placeholder texts collected during parsing
        try:
            from .loading_utils import save_skipped_texts

            path = save_skipped_texts()
            logger.info("Saved skipped texts after load", path=path)
        except Exception as e:
            logger.warning("Failed to save skipped texts", error=str(e))

        job_status = JobStatus.objects.singleton()
        job_status.status = "finished"
        job_status.finished_at = now()
        job_status.save()

        logger.info("Law loading finalization complete")
        return {"ok": True}

    except CancelledError:
        logger.info("Job was cancelled during finalization")
        try:
            job_status = JobStatus.objects.singleton()
            job_status.status = "cancelled"
            job_status.error_message = "Job was cancelled by user."
            job_status.finished_at = now()
            job_status.save()
        except Exception as save_error:
            logger.error(f"Could not save job_status due to error: {save_error}")
    except Exception as exc:
        logger.error(f"Error in finalize_law_loading: {exc}", exc_info=True)
        try:
            job_status = JobStatus.objects.singleton()
            job_status.error_message = str(exc)
            job_status.status = "error"
            job_status.finished_at = now()
            job_status.save()
        except Exception:
            pass
        raise exc


@shared_task
def delete_old_law_searches():
    """Delete LawSearch objects older than 30 days."""
    from datetime import timedelta

    from django.apps import apps

    try:
        LawSearch = apps.get_model("laws", "LawSearch")
        cutoff_date = timezone.now() - timedelta(days=30)

        deleted_count, _ = LawSearch.objects.filter(created_at__lt=cutoff_date).delete()

        logger.info(f"Deleted {deleted_count} old law searches")
        return f"Deleted {deleted_count} old law searches"

    except Exception as e:
        logger.exception(f"Error deleting old law searches: {e}")
        raise
