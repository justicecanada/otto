from django.core.files.base import ContentFile

from structlog import get_logger

logger = get_logger(__name__)


def save_content_to_saved_file(file, filename=None, content_type=""):
    """Persist file-like content to SavedFile with hash-based deduplication."""
    from librarian.models import SavedFile
    from librarian.utils.process_engine import generate_hash, sanitize_content_type

    if file is None:
        raise ValueError("file is required to create a SavedFile")

    if isinstance(file, (bytes, bytearray)):
        file_obj = ContentFile(file)
    else:
        file_obj = file

    resolved_filename = filename or getattr(file_obj, "name", None) or "uploaded-file"
    sanitized_content_type = sanitize_content_type(content_type)

    file_hash = generate_hash(file_obj)
    logger.info(
        "Generated SavedFile hash", sha256=file_hash, filename=resolved_filename
    )

    saved_file = SavedFile.objects.filter(sha256_hash=file_hash).first()
    if saved_file:
        logger.info(
            "Found existing SavedFile for upload",
            saved_file_id=saved_file.id,
            filename=resolved_filename,
        )
        return saved_file, resolved_filename, sanitized_content_type

    saved_file = SavedFile.objects.create(content_type=sanitized_content_type)
    saved_file.file.save(resolved_filename, file_obj)
    saved_file.generate_hash()
    return saved_file, resolved_filename, sanitized_content_type


def process_file(
    file,
    data_source_id,
    nested_file_path,
    name,
    content_type,
    message_id=None,
    parent_document_id=None,
    task_id=None,
):
    """
    Slightly duplicated from chat/views.py (which handles JS file uploads in chat)
    TODO: Consider refactoring chat/views.py to use this function

    nested_file_path should be the path within an archive (e.g. "archive.zip/file.txt")
    or None for regular uploads. Should not be a media path like "files/2025/10/14/file.txt"

    parent_document_id should be set when extracting from containers (ZIP, MSG, EML) to establish
    the parent-child relationship.

    task_id should be passed when called from ZIP extraction to enable cancellation checks.
    """
    from librarian.models import Document
    from librarian.utils.cancel_check import check_cancel

    # Check cancellation BEFORE queuing child tasks (critical for ZIP extraction)
    if parent_document_id:
        check_cancel(task_id, parent_document_id, check_db=True)

    file_obj, resolved_name, content_type = save_content_to_saved_file(
        file,
        filename=name,
        content_type=content_type,
    )

    # Check for existing document by data_source, filename, hash, AND parent
    # This ensures files extracted from different containers are treated as separate documents
    existing_document = Document.objects.filter(
        data_source_id=data_source_id,
        filename=resolved_name,
        saved_file__sha256_hash=file_obj.sha256_hash,
        parent_document_id=parent_document_id,
    ).first()

    # Skip if filename, hash, and parent are the same, but reprocess if ERROR status
    if existing_document:
        if message_id:
            # Associate via M2M for all messages
            existing_document.messages.add(message_id)
        if existing_document.status == "ERROR":
            existing_document.process()
        return

    # Only set file_path if it's actually a nested path (not a media path)
    # Nested paths should be like "archive.zip/file.txt", not "files/2025/10/14/file.txt"
    file_path_to_set = (
        nested_file_path
        if nested_file_path and not nested_file_path.startswith("files/")
        else None
    )

    document = Document.objects.create(
        data_source_id=data_source_id,
        saved_file=file_obj,
        filename=resolved_name,
        file_path=file_path_to_set,
        parent_document_id=parent_document_id,
    )
    # Associate with M2M as well when created with a message
    if message_id:
        document.messages.add(message_id)
    document.process()
