import os
import shutil
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings

from structlog import get_logger

from librarian.utils.cancel_check import check_cancel

logger = get_logger(__name__)


def process_zip_file(content, root_document_id, task_id=None, document_id=None):
    from librarian.models import Document

    # Check cancellation at the start (with DB check for long-running ZIP operations)
    check_cancel(task_id, document_id, check_db=True)

    binary_stream = BytesIO(content)
    # Document needed here to access root nested file path and data source id
    # Nested file path is used to keep track of the root file path for other archive file types (e.g .msg, .eml) that are unzipped and trigger their own processing
    document = Document.objects.get(id=root_document_id)
    root_file_path = document.file_path
    # Use MEDIA_ROOT to ensure shared storage across workers/pods
    directory = os.path.join(settings.MEDIA_ROOT, str(root_document_id), "zip")
    os.makedirs(directory, exist_ok=True)

    with ZipFile(file=binary_stream, mode="r") as archive:
        try:
            archive.extractall(directory)
            check_cancel(task_id, document_id, check_db=True)  # Check after extraction

            file_info = extract_nested_zips(
                directory, level=1, task_id=task_id, document_id=document_id
            )
            check_cancel(
                task_id, document_id, check_db=True
            )  # Check after nested zip extraction

            # Gather all messages associated via M2M to the root document
            message_ids = list(document.messages.values_list("id", flat=True))

            # Ensure document has a data_source before processing children
            if not document.data_source:
                raise ValueError(f"Document {root_document_id} has no data_source")

            process_directory(
                directory,
                document.data_source.id,
                document.name,
                root_file_path,
                message_ids,
                root_document_id,
                task_id=task_id,
                document_id=document_id,
            )
            file_info.insert(
                0,
                format_file_info(document.filename, root_file_path, archive.namelist()),
            )
            md = "\n".join(file_info)
        except Exception as e:
            import traceback

            from librarian.utils.cancel_check import CancelledError

            # Let cancellation errors propagate so document status is set correctly
            if isinstance(e, CancelledError):
                shutil.rmtree(directory, ignore_errors=True)
                raise

            logger.error(
                "Failed to extract Zip file",
                document_id=root_document_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


def extract_nested_zips(
    path: str, level: int = 0, task_id: str = None, document_id: int = None
) -> list[str]:
    fileinfo = []
    for root, dirs, files in os.walk(path):
        for file in files:
            # Check cancellation (no DB check in tight loop for performance)
            check_cancel(task_id, document_id, check_db=False)
            file_name = os.path.join(root, file)
            if file_name.endswith(".zip"):
                current_directory = file_name[:-4]
                if not os.path.exists(current_directory):
                    os.makedirs(current_directory)
                with ZipFile(file_name) as zipObj:
                    zipObj.extractall(current_directory)
                    fileinfo.append(
                        format_file_info(file_name, path, zipObj.namelist(), level)
                    )
                os.remove(file_name)
                fileinfo += extract_nested_zips(
                    current_directory, level + 1, task_id, document_id
                )
    return fileinfo


def process_directory(
    directory,
    data_source_id,
    root_document_name,
    root_file_path,
    message_ids,
    root_document_id,
    task_id=None,
    document_id=None,
):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    for root, dirs, files in os.walk(directory):
        for file in files:
            # Check cancellation (no DB check in tight loop for performance)
            check_cancel(task_id, document_id, check_db=False)
            path = os.path.join(root, file)
            with open(path, "rb") as f:
                name = Path(path).name
                content_type = guess_content_type(f, path=path)
                nested_file_path = f"{root_file_path}/{name}"
                if not root_file_path:
                    rel_path = os.path.relpath(path, directory)
                    nested_file_path = f"{root_document_name}/{rel_path}"
                # Associate each extracted child with all root document messages
                # Also set parent_document to establish the container relationship
                if message_ids:
                    for mid in message_ids:
                        process_file(
                            f,
                            data_source_id,
                            nested_file_path,
                            name,
                            content_type,
                            message_id=mid,
                            parent_document_id=root_document_id,
                            task_id=task_id,
                        )
                else:
                    process_file(
                        f,
                        data_source_id,
                        nested_file_path,
                        name,
                        content_type,
                        message_id=None,
                        parent_document_id=root_document_id,
                        task_id=task_id,
                    )


def format_file_info(
    file_name: str, path: str, namelist: list[str], level: int = 0
) -> str:
    def _indent(text: str) -> str:
        return " " * 2 * level + text

    # Handle None or empty path - use filename directly
    if path:
        relative_path = os.path.relpath(file_name, path)
    else:
        relative_path = file_name or "archive"

    # Use a fallback if relative_path is empty/whitespace
    if not relative_path or not relative_path.strip():
        relative_path = "archive"

    out_str = ""
    out_str = _indent(relative_path) + "\n"
    initial_out_str = out_str  # Save to check if files were added
    level += 1
    for file in namelist:
        if file.endswith(".zip"):
            continue
        out_str += _indent(file) + "\n"

    # If no files were added (empty zip or only nested zips), add a note
    if out_str == initial_out_str:
        out_str += _indent("(no files)") + "\n"

    return out_str[:-1]  # Remove the last newline character
