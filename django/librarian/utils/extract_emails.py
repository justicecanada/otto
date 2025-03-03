import email
import json
import os
import shutil
import subprocess
import sys
import tempfile
import weakref
from datetime import datetime
from email import policy
from email.iterators import typed_subpart_iterator
from email.parser import BytesParser
from pathlib import Path

from django.urls import reverse

import requests
from structlog import get_logger

from librarian.models import Document, SavedFile

logger = get_logger(__name__)


def clean_up(filepath):
    shutil.rmtree(filepath, ignore_errors=True)
    print(f"Removed directory: {filepath}")


def extract_msg(content, data_source_id):
    cwd = Path.cwd()
    directory = f"{cwd}/media/extracted_emails"
    print(f"Data Source ID: {data_source_id}")
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
            file_list = []
            if os.path.isdir(directory):
                email = {}
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        if file.endswith(".json"):
                            with open(os.path.join(root, file)) as f:
                                data = json.load(f)
                                attachments = data.get("attachments")
                                email_attachments = []
                                for attachment in attachments:
                                    if os.path.isfile(attachment):
                                        # Upload a file to the data source
                                        # url = reverse(
                                        #     "librarian:upload",
                                        #     kwargs={"data_source_id": data_source_id},
                                        # )
                                        # url = f"/librarian/modal/upload/to/{data_source_id}/"
                                        with open(attachment, "rb") as f:
                                            name = Path(attachment).name
                                            suffix = Path(attachment).suffix
                                            # content_type = suffix
                                            if suffix == ".msg":
                                                content_type = (
                                                    "application/vnd.ms-outlook"
                                                )
                                            else:
                                                content_type = suffix
                                            process_file(
                                                f, data_source_id, name, content_type
                                            )
                                            # print(attachment)
                                            # response = requests.post(url, {"file": f})
                                            # print(response.status_code)
                                            # print(response.request)
                                        email_attachments.append(attachment)
                                email["attachments"] = email_attachments
                                email["from"] = data.get("from")
                                email["to"] = data.get("to")
                                email["subject"] = data.get("subject")
                                email["sent_date"] = datetime.strptime(
                                    data.get("date"), "%a, %d %b %Y %H:%M:%S %z"
                                )
                                email["body"] = data.get("body")
                            file_list.append(os.path.join(root, file))
            compose_email = f"From: {email.get('from')}\nTo: {email.get('to')}\nSubject: {email.get('subject')}\nDate: {email.get('sent_date')}\n\n{email.get('body')}"
            md = compose_email
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed with exit code {e.returncode}")
            logger.error(f"Output: {e.output.decode('utf-8')}")
            md = ""
        except Exception as e:
            logger.error(f"Failed to extract Outlook email: {e}")
            md = ""
        shutil.rmtree(directory, ignore_errors=True)
        return md


def process_file(file, data_source_id, name, content_type):
    from librarian.utils.process_engine import generate_hash

    file_hash = generate_hash(file.read())
    print(f"File hash: {file_hash}")
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
    print(f"Document ID: {document.id}")
    print(f"Document Name: {document.filename}")
    response = requests.get(
        url=f"http://localhost:8000/librarian/modal/upload/to/{data_source_id}/"
    )
    document.process()


def extract(filepath, callback=None):
    print(f"Processing email: {filepath}")
    cwd = Path.cwd()
    directory = f"{cwd}/media/extracted_emails"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "extract_msg",
            filepath,
            "--json",
            "--extract-embedded",
            "--out",
            directory,
        ]
    )
    file_list = []
    if os.path.isdir(directory):
        emails = []
        for root, dirs, files in os.walk(directory):
            email = {}
            for file in files:
                if file.endswith(".msg"):
                    filepath = os.path.join(root, file)
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "extract_msg",
                            filepath,
                            "--json",
                            "--extract-embedded",
                            "--out",
                            directory,
                        ]
                    )
                if file.endswith(".json"):
                    with open(os.path.join(root, file)) as f:
                        data = json.load(f)
                        attachments = data.get("attachments")
                        email_attachments = []
                        for attachment in attachments:
                            if os.path.isfile(attachment):
                                email_attachments.append(attachment)
                        email["attachments"] = email_attachments
                        email["from"] = data.get("from")
                        email["to"] = data.get("to")
                        email["subject"] = data.get("subject")
                        email["sent_date"] = datetime.strptime(
                            data.get("date"), "%a, %d %b %Y %H:%M:%S %z"
                        )
                        email["body"] = data.get("body")

                        emails.append(email)
                    file_list.append(os.path.join(root, file))
    else:
        print("Directory does not exist")

    return {"files": file_list, "directory": directory, "emails": emails}
