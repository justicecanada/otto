import base64
import email
import email.header
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from io import BytesIO
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
                    "--skip-hidden",
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
                                        name = Path(path).name
                                        with open(path, "rb") as f:
                                            nested_file_path = f"{root_file_path or document.filename}/{name}"
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
                                        email_attachments.append(name)
                                email["attachments"] = ", ".join(email_attachments)
                                email["from"] = data.get("from")
                                email["to"] = data.get("to")
                                email["cc"] = data.get("cc")
                                email["bcc"] = data.get("bcc")
                                email["subject"] = data.get("subject")
                                email["sent_date"] = datetime.strptime(
                                    data.get("date"), "%a, %d %b %Y %H:%M:%S %z"
                                )
                                email["body"] = data.get("body")
            combined_email = f"From: {email.get('from')}\n" f"To: {email.get('to')}\n"
            if email.get("cc"):
                combined_email += f"Cc: {email.get('cc')}\n"
            if email.get("bcc"):
                combined_email += f"Bcc: {email.get('bcc')}\n"
            combined_email += (
                f"Subject: {email.get('subject')}\n"
                f"Date: {email.get('sent_date')}\n"
                f"Attachments: {email.get('attachments')}\n\n"
                f"{email.get('body')}"
            )
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


def extract_eml(content, root_document_id):
    from librarian.utils.process_document import process_file

    document = Document.objects.get(id=root_document_id)
    root_file_path = document.file_path

    msg = email.message_from_bytes(content)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(part.get_content_charset())
                break
    else:
        body = msg.get_payload(decode=True).decode(msg.get_content_charset())
    subject, encoding = email.header.decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding if encoding else "utf-8")
    from_ = msg["From"]
    to = msg["To"]
    cc = msg["Cc"]
    bcc = msg["Bcc"]
    sent_date = msg["Date"]
    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename()
            if filename:
                attachments.append(filename)
                payload = part.get_payload(decode=True)
                content_type = part.get_content_type()
                with tempfile.NamedTemporaryFile() as temp_file:
                    temp_file.write(payload)
                    temp_file_path = temp_file.name
                    with open(temp_file_path, "r+b") as f:
                        nested_file_path = (
                            f"{root_file_path or document.filename}/{filename}"
                        )
                        process_file(
                            f,
                            document.data_source.id,
                            nested_file_path,
                            filename,
                            content_type,
                        )

    combined_email = f"From: {from_}\nTo: {to}\n"
    if cc:
        combined_email += f"Cc: {cc}\n"
    if bcc:
        combined_email += f"Bcc: {bcc}\n"
    combined_email += (
        f"Subject: {subject}\n"
        f"Date: {sent_date}\n"
        f"Attachments: {', '.join(attachments)}\n\n"
        f"{body}"
    )
    md = combined_email
    return md
