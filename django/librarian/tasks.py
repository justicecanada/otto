import time
import traceback
import urllib.parse
import uuid
from datetime import datetime
from typing import List

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

from celery import current_task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from tqdm import tqdm

from chat.llm import OttoLLM
from librarian.models import Document
from librarian.utils.process_engine import (
    create_nodes,
    extract_html_metadata,
    extract_markdown,
    fetch_from_url,
    get_process_engine_from_type,
    guess_content_type,
)
from otto.models import User

logger = get_logger(__name__)

ten_minutes = 600
one_minute = 60


@shared_task(soft_time_limit=ten_minutes, queue="light")
def process_document(
    document_id, language=None, pdf_method="default", mock_embedding=False
):
    """
    Task for document fetching (URL or file) and initial routing.
    Always runs on the lightworker. Download, then dispatches to processing task
    that routes appropriately based on content/file size.
    """
    if language is None:
        language = translation.get_language()
    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found", document_id=document_id)
        return
    document.status = "PROCESSING"
    document.celery_task_id = current_task.request.id
    document.save()

    content = None
    content_type = None
    base_url = None

    try:
        if document.url:
            # URL fetch: performed only on lightworker
            logger.info("Processing URL", url=document.url)
            base_url = (
                urllib.parse.urlparse(document.url).scheme
                + "://"
                + urllib.parse.urlparse(document.url).netloc
            )
            if current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={"status_text": _("Fetching URL...")},
                )
            content, content_type = fetch_from_url(document.url)
            content_type = guess_content_type(content, content_type, document.url)
            document.url_content_type = content_type
        elif document.saved_file:
            # File fetch
            logger.info("Processing file", file=document.saved_file)
            if current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={"status_text": _("Reading file...")},
                )
            content = document.saved_file.file.read()
            from librarian.utils.process_engine import guess_content_type

            content_type = guess_content_type(
                content, document.saved_file.content_type, document.filename
            )
            base_url = None
        else:
            raise ValueError("URL or file is required")

        process_document_helper.apply_async(
            args=[
                document_id,
                content,
                content_type,
                base_url,
                language,
                pdf_method,
                mock_embedding,
            ],
            queue="light",
        )

    except Exception as e:
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            f"Error fetching document: {getattr(document,'name','(unknown)')}",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        document.status = "ERROR"
        document.celery_task_id = None
        if settings.DEBUG:
            document.status_details = full_error + f" ({_('Error ID')}: {error_id})"
        else:
            document.status_details = f"({_('Error ID')}: {error_id})"
        document.save()


@shared_task(bind=True, soft_time_limit=ten_minutes)
def process_document_helper(
    self,
    document_id,
    content,
    content_type,
    base_url,
    language,
    pdf_method,
    mock_embedding,
    rerouted=False,
):
    """
    Helper Celery task that performs processing after content (file or URL) is available.
    Dynamically routes to heavy or light depending on content size.
    """

    MAX_LIGHT_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    queue = self.request.delivery_info.get("routing_key")
    content_size = len(content) if hasattr(content, "__len__") else 0

    if not rerouted:
        if content_size < MAX_LIGHT_FILE_SIZE and queue != "light":
            logger.info(
                f"process_document_helper: rerouting small content ({content_size} bytes) to lightworker."
            )
            result = process_document_helper.apply_async(
                args=[
                    document_id,
                    content,
                    content_type,
                    base_url,
                    language,
                    pdf_method,
                    mock_embedding,
                ],
                kwargs={"rerouted": True},
                queue="light",
            )
            return result.get(timeout=ten_minutes)
        elif content_size >= MAX_LIGHT_FILE_SIZE and queue != "heavy":
            logger.info(
                f"process_document_helper: rerouting large content ({content_size} bytes) to heavyworker."
            )
            result = process_document_helper.apply_async(
                args=[
                    document_id,
                    content,
                    content_type,
                    base_url,
                    language,
                    pdf_method,
                    mock_embedding,
                ],
                kwargs={"rerouted": True},
                queue="heavy",
            )
            return result.get(timeout=ten_minutes)

    logger.info(
        f"process_document_helper: processing document {document_id} in queue {self.request.delivery_info.get('routing_key')}."
    )
    try:
        document = Document.objects.get(id=document_id)
        llm = OttoLLM(
            mock_embedding=mock_embedding,
            embedding_deployment="text-embedding-3-large-documents",
        )
        if language is None:
            language = translation.get_language()
        with translation.override(language):
            process_engine = get_process_engine_from_type(content_type)
            if process_engine == "HTML":
                extracted_metadata = extract_html_metadata(content)
                for key, value in extracted_metadata.items():
                    setattr(document, key, value)

            extraction_result = extract_markdown(
                content,
                process_engine,
                pdf_method=pdf_method,
                base_url=base_url,
                selector=document.selector,
                root_document_id=document.id,
            )

            document.extracted_text = extraction_result.markdown
            if document.content_type == "application/pdf":
                document.pdf_extraction_method = extraction_result.pdf_method

            if document.content_type in [
                "application/x-zip-compressed",
                "application/zip",
            ]:
                document.delete()
                return

            nodes = create_nodes(extraction_result.chunks, document)
            document.num_chunks = len(nodes)
            document.save()

            library_uuid = document.data_source.library.uuid_hex
            vector_store_index = llm.get_index(library_uuid)
            # Delete existing nodes
            document_uuid = document.uuid_hex
            vector_store_index.delete_ref_doc(document_uuid, delete_from_docstore=True)
            # Insert new nodes in batches
            batch_size = 16
            for i in range(0, len(nodes), batch_size):
                for j in range(3, 12):
                    try:
                        vector_store_index.insert_nodes(nodes[i : i + batch_size])
                        break
                    except Exception as e:
                        logger.error(f"Error inserting nodes: {e}")
                        logger.debug("Retrying...")
                        time.sleep(2**j)

            document.status = "SUCCESS"
            document.fetched_at = datetime.now()
            document.celery_task_id = None
            document.save()
        llm.create_costs()
    except Exception as e:
        error_id = str(uuid.uuid4())[:7]
        full_error = traceback.format_exc()
        logger.error(
            f"Error processing content for document: {document_id}",
            document_id=document_id,
            error_id=error_id,
            error=full_error,
        )
        # Update document status and error code
        document = Document.objects.filter(id=document_id).first()
        if document:
            document.status = "ERROR"
            document.celery_task_id = None
            if settings.DEBUG:
                document.status_details = full_error + f" ({_('Error ID')}: {error_id})"
            else:
                document.status_details = f"({_('Error ID')}: {error_id})"
            document.save()


@shared_task(soft_time_limit=ten_minutes, queue="light")
def delete_documents_from_vector_store(
    document_uuids: List[str], library_uuid: str
) -> None:
    llm = OttoLLM()
    logger.info(f"Deleting documents from vector store:\n{document_uuids}")
    for document_uuid in document_uuids:
        try:
            idx = llm.get_index(library_uuid)
            idx.delete_ref_doc(document_uuid, delete_from_docstore=True)
        except Exception as e:
            logger.error(f"Failed to remove documents from vector store: {e}")
