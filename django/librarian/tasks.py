import time
import urllib.parse
from datetime import datetime

from celery import current_task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from tqdm import tqdm

from librarian.models import Document
from librarian.utils.process_engine import (
    create_nodes,
    extract_html_metadata,
    extract_markdown,
    fetch_from_url,
    get_process_engine_from_type,
)
from librarian.utils.vector_store_helpers import connect_to_vector_store

logger = get_logger(__name__)

ten_minutes = 600


@shared_task(soft_time_limit=ten_minutes)
def process_document(document_id):
    """
    Process a URL and save the content to a document.
    """
    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document not found", document_id=document_id)
        return
    document.status = "PROCESSING"
    document.celery_task_id = current_task.request.id
    document.save()

    try:
        url = document.url
        file = document.file
        if not (url or file):
            raise ValueError("URL or file is required")

        if url:
            logger.info("Processing URL", url=url)
            base_url = (
                urllib.parse.urlparse(url).scheme
                + "://"
                + urllib.parse.urlparse(url).netloc
            )
            current_task.update_state(
                state="PROCESSING",
                meta={
                    "status_text": "Fetching URL...",
                },
            )
            content, content_type = fetch_from_url(url)
            document.url_content_type = content_type
        else:
            logger.info("Processing file", file=file)
            base_url = None
            current_task.update_state(
                state="PROCESSING",
                meta={
                    "status_text": "Reading file...",
                },
            )
            content = file.file.read()
            content_type = file.content_type

        current_task.update_state(
            state="PROCESSING",
            meta={
                "status_text": "Extracting text...",
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
            fast=True,
            base_url=base_url,
            selector=document.selector,
        )
        num_chunks = len(chunks)
        document.num_chunks = num_chunks
        document.save()

        current_task.update_state(
            state="PROCESSING",
            meta={
                "status_text": "Adding to library...",
            },
        )
        nodes = create_nodes(chunks, document)
        # Delete existing nodes
        document_uuid = document.uuid_hex
        library_uuid = document.data_source.library.uuid_hex
        vector_store_index = connect_to_vector_store(library_uuid)
        vector_store_index.delete_ref_doc(document_uuid, delete_from_docstore=True)
        # Insert new nodes in batches
        batch_size = 16
        for i in tqdm(range(0, len(nodes), batch_size)):
            if i > 0:
                percent_complete = i / len(nodes) * 100
                current_task.update_state(
                    state="PROCESSING",
                    meta={
                        "status_text": f"Adding to library... ({int(percent_complete)}% done)",
                    },
                )
            # Exponential backoff retry
            for j in range(3, 12):
                try:
                    vector_store_index.insert_nodes(nodes[i : i + batch_size])
                    break
                except Exception as e:
                    print(f"Error inserting nodes: {e}")
                    print("Retrying...")
                    time.sleep(2**j)

        # Done!
        document.status = "SUCCESS"
        document.fetched_at = datetime.now()
        document.celery_task_id = None
        document.save()

    except SoftTimeLimitExceeded:
        document.status = "ERROR"
        document.celery_task_id = None
        document.save()
