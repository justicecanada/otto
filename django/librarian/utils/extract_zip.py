import os
import shutil
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from structlog import get_logger

logger = get_logger(__name__)


def process_zip_file(content, data_source_id):
    binary_stream = BytesIO(content)
    cwd = Path.cwd()
    directory = f"{cwd}/media/zip/{data_source_id}"
    with ZipFile(file=binary_stream, mode="r") as archive:
        try:
            archive.extractall(directory)
            unzip(directory)
            process(directory, data_source_id)
            md = ""
        except Exception as e:
            logger.error(f"Failed to extract Zip file: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


def unzip(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            file_name = os.path.join(root, file)
            if file_name.endswith(".zip"):
                current_directory = file_name[:-4]
                if not os.path.exists(current_directory):
                    os.makedirs(current_directory)
                with ZipFile(file_name) as zipObj:
                    zipObj.extractall(current_directory)
                os.remove(file_name)
                unzip(current_directory)


def process(directory, data_source_id):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    for root, dirs, files in os.walk(directory):
        for file in files:
            path = os.path.join(root, file)
            with open(path, "rb") as f:
                name = Path(path).name
                content_type = guess_content_type(file, name)
                # print(f"Relpath: {os.path.relpath(path, directory)}")
                process_file(f, data_source_id, name, content_type)
