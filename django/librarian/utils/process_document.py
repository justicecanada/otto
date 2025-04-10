from pathlib import Path

from structlog import get_logger

logger = get_logger(__name__)


def process_file(file, data_source_id, nested_file_path, name, content_type):
    from librarian.models import Document, SavedFile
    from librarian.utils.process_engine import generate_hash

    # Check if the file is already stored on the server
    file_hash = generate_hash(file.read())
    logger.info(f"Generated hash for {file_hash}")
    file_exists = SavedFile.objects.filter(sha256_hash=file_hash).exists()
    if file_exists:
        file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
        logger.info(f"Found existing SavedFile for {name}", saved_file_id=file_obj.id)
    else:
        file_obj = SavedFile.objects.create(
            content_type=content_type,
        )
        file_obj.file.save(name, file)
        file_obj.generate_hash()

    existing_document = Document.objects.filter(
        data_source_id=data_source_id,
        filename=name,
        file__sha256_hash=file_obj.sha256_hash,
    ).first()

    # Skip if filename and hash are the same, but reprocess if ERROR status
    if existing_document:
        if existing_document.status == "ERROR":
            existing_document.process()
        return

    document = Document.objects.create(
        data_source_id=data_source_id,
        file=file_obj,
        filename=name,
        filepath=nested_file_path,
    )
    document.process()
