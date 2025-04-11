import email
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from structlog import get_logger

from librarian.models import Document

logger = get_logger(__name__)


def extract_msg(content, root_document_id):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    cwd = Path.cwd()

    document = Document.objects.get(id=root_document_id)
    root_file_path = document.file_path

    directory = f"{cwd}/media/{root_document_id}/email"
    with tempfile.NamedTemporaryFile(suffix=".msg") as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "extract_msg",
                    temp_file_path,
                    "--json",
                    "--extract-embedded",
                    "--out",
                    directory,
                ]
            )
            if os.path.isdir(directory):
                email = {}
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        if file.endswith(".json"):
                            with open(os.path.join(root, file)) as f:
                                data = json.load(f)
                                attachments = data.get("attachments")
                                email_attachments = []
                                for path in attachments:
                                    if os.path.isfile(path):
                                        with open(path, "rb") as f:
                                            name = Path(path).name
                                            nested_file_path = (
                                                f"{root_file_path}/{name}"
                                            )
                                            if not root_file_path:
                                                rel_path = os.path.relpath(
                                                    path, directory
                                                )
                                                nested_file_path = (
                                                    f"{document.name}/{rel_path}"
                                                )
                                            content_type = guess_content_type(
                                                f, path=path
                                            )
                                            process_file(
                                                f,
                                                document.data_source.id,
                                                nested_file_path,
                                                name,
                                                content_type,
                                            )
                                        attachment = Path(path).name
                                        email_attachments.append(attachment)
                                email["attachments"] = ", ".join(email_attachments)
                                email["from"] = data.get("from")
                                email["to"] = data.get("to")
                                email["subject"] = data.get("subject")
                                email["sent_date"] = datetime.strptime(
                                    data.get("date"), "%a, %d %b %Y %H:%M:%S %z"
                                )
                                email["body"] = data.get("body")
            combined_email = f"From: {email.get('from')}\nTo: {email.get('to')}\nSubject: {email.get('subject')}\nDate: {email.get('sent_date')}\nAttachments: {email.get('attachments')}\n\n{email.get('body')}"
            md = combined_email
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed with exit code {e.returncode}")
            logger.error(f"Output: {e.output.decode('utf-8')}")
            md = ""
        except Exception as e:
            logger.error(f"Failed to extract Outlook email: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md
