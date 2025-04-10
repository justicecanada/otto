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
    data_source_id = document.data_source.id
    root_nested_file_path = document.file.nested_file_path
    directory = f"{cwd}/media/{data_source_id}/zip"

    with ZipFile(file=binary_stream, mode="r") as archive:
        try:
            archive.extractall(directory)
            file_info = extract_nested_zips(directory)
            process_directory(
                directory, document.data_source.id, document.name, root_nested_file_path
            )
            root_level_files = ", ".join(archive.namelist())
            file_info.insert(0, (f"Files: {root_level_files}\n"))
            md = "".join(file_info)
        except Exception as e:
            logger.error(f"Failed to extract Zip file: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


def extract_nested_zips(path):
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
                        format_file_info(file_name, path, zipObj.namelist())
                    )
                os.remove(file_name)
                extract_nested_zips(current_directory)
    return fileinfo


def process_directory(
    directory, data_source_id, root_document_name, root_nested_file_path
):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    for root, dirs, files in os.walk(directory):
        for file in files:
            path = os.path.join(root, file)
            with open(path, "rb") as f:
                name = Path(path).name
                content_type = guess_content_type(f, path=path)
                nested_file_path = f"{root_nested_file_path}/{name}"
                if not root_nested_file_path:
                    rel_path = os.path.relpath(path, directory)
                    nested_file_path = f"{root_document_name}/{rel_path}"
                process_file(f, data_source_id, nested_file_path, name, content_type)


def format_file_info(file_name, path, namelist) -> str:
    relative_path = os.path.relpath(file_name, path)
    files = ", ".join(namelist)
    return f"Filename: {relative_path} - Files: {files}\n"
