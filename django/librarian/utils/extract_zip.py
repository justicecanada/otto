import datetime
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from structlog import get_logger

logger = get_logger(__name__)


def process_zip_file(content, data_source_id):
    from librarian.utils.process_engine import guess_content_type, process_file

    binary_stream = BytesIO(content)
    cwd = Path.cwd()
    directory = f"{cwd}/media/{data_source_id}/zip_files"
    with zipfile.ZipFile(file=binary_stream) as archive:
        try:
            info_list = []
            for info in archive.infolist():
                path = archive.extract(info.filename, directory)
                with open(path, "rb") as f:
                    name = Path(path).name
                    content_type = guess_content_type(f, name)
                    process_file(f, data_source_id, name, content_type)
                file_info = f"Filename: {info.filename}\nModified: {datetime.datetime(*info.date_time)}\nNormal size: {info.file_size} bytes\nCompressed size: {info.compress_size} bytes\n--------------------\n"
                info_list.append(file_info)
            md = f"".join(info_list)
        except Exception as e:
            logger.error(f"Failed to extract Zip file: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md
