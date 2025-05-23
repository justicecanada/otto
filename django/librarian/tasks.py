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


@shared_task(soft_time_limit=ten_minutes)
def process_document(
    document_id, language=None, pdf_method="default", mock_embedding=False
):
    """
    Process a URL and save the content to a document.
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

    llm = OttoLLM(mock_embedding=mock_embedding)
    try:
        with translation.override(language):
            process_document_helper(document, llm, pdf_method)

    except Exception as e:
        document.status = "ERROR"
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            f"Error processing document: {document.name}",
            document_id=document.id,
            error_id=error_id,
            error=full_error,
        )
        document.celery_task_id = None
        if settings.DEBUG:
            document.status_details = full_error + f" ({_('Error ID')}: {error_id})"
        else:
            document.status_details = f"({_('Error ID')}: {error_id})"
        document.save()

    llm.create_costs()


def process_document_helper(document, llm, pdf_method="default"):
    url = document.url
    file = document.saved_file
    if not (url or file):
        raise ValueError("URL or file is required")

    if url:
        logger.info("Processing URL", url=url)
        base_url = (
            urllib.parse.urlparse(url).scheme
            + "://"
            + urllib.parse.urlparse(url).netloc
        )
        if current_task:
            current_task.update_state(
                state="PROCESSING",
                meta={
                    "status_text": _("Fetching URL..."),
                },
            )
        content, content_type = fetch_from_url(url)
        content_type = guess_content_type(content, content_type, document.url)
        document.url_content_type = content_type
    else:
        logger.info("Processing file", file=file)
        base_url = None
        if current_task:
            current_task.update_state(
                state="PROCESSING",
                meta={
                    "status_text": _("Reading file..."),
                },
            )
        content = file.file.read()
        content_type = guess_content_type(content, file.content_type, document.filename)

    if current_task:
        current_task.update_state(
            state="PROCESSING",
            meta={
                "status_text": _("Extracting text..."),
            },
        )
    process_engine = get_process_engine_from_type(content_type)
    if process_engine == "HTML":
        extracted_metadata = extract_html_metadata(content)
        for key, value in extracted_metadata.items():
            setattr(document, key, value)

    document.extracted_text, chunks = extract_markdown(
        content,
        process_engine,
        pdf_method=pdf_method,
        base_url=base_url,
        selector=document.selector,
        root_document_id=document.id,
    )

    if document.content_type in ["application/x-zip-compressed", "application/zip"]:
        # Delete the document; we've already extracted the contents
        document.delete()
        return

    if current_task:
        current_task.update_state(
            state="PROCESSING",
            meta={
                "status_text": _("Adding to library..."),
            },
        )
    nodes = create_nodes(chunks, document)

    document.num_chunks = len(nodes)
    if document.content_type == "application/pdf":
        document.pdf_extraction_method = pdf_method
    document.save()

    library_uuid = document.data_source.library.uuid_hex
    vector_store_index = llm.get_index(library_uuid)
    # Delete existing nodes
    document_uuid = document.uuid_hex
    vector_store_index.delete_ref_doc(document_uuid, delete_from_docstore=True)
    # Insert new nodes in batches
    batch_size = 16
    for i in range(0, len(nodes), batch_size):
        if i > 0:
            percent_complete = i / len(nodes) * 100
            if current_task:
                current_task.update_state(
                    state="PROCESSING",
                    meta={
                        "status_text": f"{_('Adding to library...')} ({int(percent_complete)}% {_('done')})",
                    },
                )
        # Exponential backoff retry
        for j in range(3, 12):
            try:
                vector_store_index.insert_nodes(nodes[i : i + batch_size])
                break
            except Exception as e:
                logger.error(f"Error inserting nodes: {e}")
                logger.debug("Retrying...")
                time.sleep(2**j)

    # Done!
    document.status = "SUCCESS"
    document.fetched_at = datetime.now()
    document.celery_task_id = None
    document.save()


@shared_task(soft_time_limit=ten_minutes)
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
