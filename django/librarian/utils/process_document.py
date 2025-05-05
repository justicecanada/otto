from structlog import get_logger

logger = get_logger(__name__)


def process_file(file, data_source_id, nested_file_path, name, content_type):
    """
    Slightly duplicated from chat/views.py (which handles JS file uploads in chat)
    TODO: Consider refactoring chat/views.py to use this function
    """
    from librarian.models import Document, SavedFile

    file_obj = SavedFile.objects.create(content_type=content_type)
    file_obj.file.save(name, file)

    document = Document.objects.create(
        data_source_id=data_source_id,
        saved_file=file_obj,
        filename=name,
        file_path=nested_file_path,
    )
    document.process()
