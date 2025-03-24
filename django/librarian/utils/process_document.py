import asyncio
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from structlog import get_logger

logger = get_logger(__name__)


def process_file(file, data_source_id, name=None, content_type=None, file_hash=None):
    from librarian.models import Document, SavedFile
    from librarian.utils.process_engine import generate_hash

    logger.info(f"Processing file {file.name}")
    # logger.info(f"Processing file {file.content_type}")
    # print(f"Processing file {file.content_type}")

    # Check if the file is already stored on the server
    file_hash = generate_hash(file.read())
    logger.info(f"Generated hash for {file_hash}")
    file_exists = SavedFile.objects.filter(sha256_hash=file_hash).exists()
    if file_exists:
        file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
        logger.info(f"Found existing SavedFile for {name}", saved_file_id=file_obj.id)
        return
    else:
        file_obj = SavedFile.objects.create(content_type=content_type)
        file_obj.file.save(name, file)
        file_obj.generate_hash()

    document = Document.objects.create(
        data_source_id=data_source_id, file=file_obj, filename=name
    )
    document.process()
