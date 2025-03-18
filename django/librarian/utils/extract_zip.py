import datetime
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from structlog import get_logger

from librarian.models import Document, SavedFile

logger = get_logger(__name__)


def extract_file(content, data_source_id):
    from librarian.utils.process_engine import guess_content_type

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
                    # suffix = Path(path).suffix
                    # if suffix == ".msg":
                    #     content_type = "application/vnd.ms-outlook"
                    # elif suffix == ".eml":
                    #     content_type = "message/rfc822"
                    # elif suffix == ".zip":
                    #     content_type = "application/zip"
                    # else:
                    #     content_type = suffix
                    process_file(f, data_source_id, name, content_type)
                file_info = f"Filename: {info.filename}\nModified: {datetime.datetime(*info.date_time)}\nNormal size: {info.file_size} bytes\nCompressed size: {info.compress_size} bytes\n--------------------\n"
                info_list.append(file_info)
            md = f"Compressed files\n\n".join(info_list)
        except Exception as e:
            logger.error(f"Failed to extract Zip file: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


# TODO: Refactor this function and use for processing all type (i.e. .msg, .eml, .zip) of files
def process_file(file, data_source_id, name, content_type):
    from librarian.utils.process_engine import generate_hash

    file_hash = generate_hash(file.read())
    file_exists = SavedFile.objects.filter(sha256_hash=file_hash).exists()
    if file_exists:
        file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
        logger.info(
            f"Found existing SavedFile for {file.name}", saved_file_id=file_obj.id
        )
        # Check if identical document already exists in the DataSource
        existing_document = Document.objects.filter(
            data_source_id=data_source_id,
            filename=file.name,
            file__sha256_hash=file_hash,
        ).first()
        # Skip if filename and hash are the same, and processing status is SUCCESS
        if existing_document:
            if existing_document.status != "SUCCESS":
                existing_document.process()
            return
    else:
        file_obj = SavedFile.objects.create(content_type=content_type)
        file_obj.file.save(name, file)
        file_obj.generate_hash()

    document = Document.objects.create(
        data_source_id=data_source_id, file=file_obj, filename=name
    )
    document.process()
