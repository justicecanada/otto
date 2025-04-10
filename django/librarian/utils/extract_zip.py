import os
import shutil
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from django.utils.translation import gettext as _

from structlog import get_logger

logger = get_logger(__name__)


def process_zip_file(content, root_document_id):
    from librarian.models import Document

    binary_stream = BytesIO(content)
    cwd = Path.cwd()
    # Document needed here to access root nested file path and data source id
    # Nested file path is used to keep track of the root file path for other archive file types (e.g .msg, .eml) that are unzipped and trigger their own processing
    document = Document.objects.get(id=root_document_id)
    root_file_path = document.file_path
    directory = f"{cwd}/media/{root_document_id}/zip"

    with ZipFile(file=binary_stream, mode="r") as archive:
        try:
            archive.extractall(directory)
            file_info = extract_nested_zips(directory, level=1)
            process_directory(
                directory, document.data_source.id, document.name, root_file_path
            )
            file_info.insert(
                0,
                format_file_info(document.filename, root_file_path, archive.namelist()),
            )
            md = "\n".join(file_info)
        except Exception as e:
            logger.error(f"Failed to extract Zip file: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


def extract_nested_zips(path: str, level: int = 0) -> list[str]:
    fileinfo = []
    for root, dirs, files in os.walk(path):
        for file in files:
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
                fileinfo += extract_nested_zips(current_directory, level + 1)
    return fileinfo


def process_directory(directory, data_source_id, root_document_name, root_file_path):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    for root, dirs, files in os.walk(directory):
        for file in files:
            path = os.path.join(root, file)
            with open(path, "rb") as f:
                name = Path(path).name
                content_type = guess_content_type(f, path=path)
                nested_file_path = f"{root_file_path}/{name}"
                if not root_file_path:
                    rel_path = os.path.relpath(path, directory)
                    nested_file_path = f"{root_document_name}/{rel_path}"
                process_file(f, data_source_id, nested_file_path, name, content_type)


def format_file_info(
    file_name: str, path: str, namelist: list[str], level: int = 0
) -> str:
    def _indent(text: str) -> str:
        return " " * 2 * level + text

    relative_path = os.path.relpath(file_name, path)
    out_str = ""
    out_str = _indent(relative_path) + "\n"
    level += 1
    for file in namelist:
        if file.endswith(".zip"):
            continue
        out_str += _indent(file) + "\n"
    return out_str[:-1]  # Remove the last newline character
