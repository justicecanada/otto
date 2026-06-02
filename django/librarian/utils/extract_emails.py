import email
import email.header
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from html import unescape as html_unescape
from pathlib import Path

from django.conf import settings

from structlog import get_logger

from otto.utils.common import get_temp_dir

from librarian.models import Document

logger = get_logger(__name__)


def extract_msg(content, root_document_id):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import guess_content_type

    document = Document.objects.get(id=root_document_id)
    root_file_path = document.file_path
    # Use MEDIA_ROOT to ensure shared storage across workers/pods
    directory = os.path.join(settings.MEDIA_ROOT, str(root_document_id), "email")
    os.makedirs(directory, exist_ok=True)
    temp_dir = get_temp_dir()
    with tempfile.NamedTemporaryFile(
        delete=True, suffix=".msg", dir=temp_dir
    ) as temp_file:
        temp_file.write(content)
        temp_file.flush()  # Ensure content is written to disk before subprocess reads it
        temp_file_path = temp_file.name
        try:
            # produce JSON and attachments in the output directory
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "extract_msg",
                    temp_file_path,
                    "--json",
                    "--skip-hidden",
                    "--skip-body-not-found",
                    "--extract-embedded",
                    "--out",
                    directory,
                ],
                check=True,
            )

            email_data = {}
            if os.path.isdir(directory):
                # Read the first JSON output we find
                for root_dir, dirs, files in os.walk(directory):
                    for fname in files:
                        if fname.endswith(".json"):
                            json_path = os.path.join(root_dir, fname)
                            with open(json_path) as jf:
                                data = json.load(jf)

                            attachments = data.get("attachments") or []
                            email_attachments = []
                            message_ids = list(
                                document.messages.values_list("id", flat=True)
                            )
                            for path in attachments:
                                if os.path.isfile(path):
                                    name = Path(path).name
                                    with open(path, "rb") as af:
                                        nested_file_path = f"{root_file_path or document.filename}/{name}"
                                        content_type = guess_content_type(af, path=path)
                                        if message_ids:
                                            for mid in message_ids:
                                                process_file(
                                                    af,
                                                    document.data_source.id,
                                                    nested_file_path,
                                                    name,
                                                    content_type,
                                                    message_id=mid,
                                                    parent_document_id=root_document_id,
                                                )
                                        else:
                                            process_file(
                                                af,
                                                document.data_source.id,
                                                nested_file_path,
                                                name,
                                                content_type,
                                                message_id=None,
                                                parent_document_id=root_document_id,
                                            )
                                    email_attachments.append(name)

                            email_data["attachments"] = ", ".join(email_attachments)
                            email_data["from"] = data.get("from")
                            email_data["to"] = data.get("to")
                            email_data["cc"] = data.get("cc")
                            email_data["bcc"] = data.get("bcc")
                            email_data["subject"] = data.get("subject")
                            try:
                                email_data["sent_date"] = datetime.strptime(
                                    data.get("date"), "%a, %d %b %Y %H:%M:%S %z"
                                )
                            except Exception:
                                email_data["sent_date"] = data.get("date")
                            email_data["body"] = data.get("body")
                            break
            # Ensure email_data exists and populate body if missing
            if "email_data" not in locals():
                email_data = {}

            if not email_data.get("body"):
                email_data["body"] = extract_msg_body(content, directory) or ""

            combined_email = (
                f"From: {email_data.get('from')}\nTo: {email_data.get('to')}\n"
            )
            if email_data.get("cc"):
                combined_email += f"Cc: {email_data.get('cc')}\n"
            if email_data.get("bcc"):
                combined_email += f"Bcc: {email_data.get('bcc')}\n"
            combined_email += (
                f"Subject: {email_data.get('subject')}\n"
                f"Date: {email_data.get('sent_date')}\n"
                f"Attachments: {email_data.get('attachments')}\n\n"
                f"{email_data.get('body')}"
            )
            md = combined_email
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed with exit code {e.returncode}")
            try:
                out = (
                    e.output.decode("utf-8")
                    if isinstance(e.output, (bytes, bytearray))
                    else str(e.output)
                )
            except Exception:
                out = ""
            logger.error(f"Output: {out}")
            md = ""
        except Exception as e:
            logger.error(f"Failed to extract Outlook email: {e}")
            md = ""
        finally:
            # Clean up extracted files to avoid clutter
            try:
                shutil.rmtree(directory, ignore_errors=True)
            except Exception:
                pass
        return md


def extract_eml(content, root_document_id):
    from librarian.utils.process_document import process_file
    from librarian.utils.process_engine import sanitize_content_type

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
    message_ids = list(document.messages.values_list("id", flat=True))
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename()
            if filename:
                attachments.append(filename)
                payload = part.get_payload(decode=True)
                # Sanitize content type from email part to prevent DB overflow
                content_type = sanitize_content_type(part.get_content_type())
                temp_dir = get_temp_dir()
                with tempfile.NamedTemporaryFile(dir=temp_dir) as temp_file:
                    temp_file.write(payload)
                    temp_file.flush()  # Ensure content is written to disk before re-opening
                    temp_file_path = temp_file.name
                    with open(temp_file_path, "r+b") as f:
                        nested_file_path = (
                            f"{root_file_path or document.filename}/{filename}"
                        )
                        if message_ids:
                            for mid in message_ids:
                                process_file(
                                    f,
                                    document.data_source.id,
                                    nested_file_path,
                                    filename,
                                    content_type,
                                    message_id=mid,
                                    parent_document_id=root_document_id,
                                )
                        else:
                            process_file(
                                f,
                                document.data_source.id,
                                nested_file_path,
                                filename,
                                content_type,
                                message_id=None,
                                parent_document_id=root_document_id,
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


def extract_msg_body(content, directory):
    captured_body = ""
    temp_dir = get_temp_dir()
    with tempfile.NamedTemporaryFile(
        delete=True, suffix=".msg", dir=temp_dir
    ) as temp_file:
        temp_file.write(content)
        temp_file.flush()  # Ensure content is written to disk before subprocess reads it
        temp_file_path = temp_file.name
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "extract_msg",
                    temp_file_path,
                    "--html",
                    "--out",
                    directory,
                ]
            )
            captured_body = find_and_parse_html(directory)
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed with exit code {e.returncode}")
            try:
                out = (
                    e.output.decode("utf-8")
                    if isinstance(e.output, (bytes, bytearray))
                    else str(e.output)
                )
            except Exception:
                out = ""
            logger.error(f"Output: {out}")
        except Exception as e:
            logger.error(f"Failed to extract Outlook email: {e}")
    return captured_body


def parse_message_html(html_content: str) -> str:
    """Return the plain-text message body from a message HTML string.

    Removes injected header blocks (e.g. <div id="injectedHeader"> containing
    From/To/Cc/Subject lines) and strips HTML tags while preserving paragraph breaks.
    """
    if not html_content:
        return ""

    # Remove <style> blocks
    try:
        without_style = re.sub(
            r"<style.*?>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE
        )
    except Exception:
        without_style = html_content

    # Remove the injected header block if present
    without_header = re.sub(
        r"<div[^>]+id=[\"']injectedHeader[\"'][^>]*>.*?</div>",
        "",
        without_style,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Extract body content if present
    m = re.search(
        r"<body[^>]*>(.*)</body>", without_header, flags=re.DOTALL | re.IGNORECASE
    )
    content = m.group(1) if m else without_header

    # Replace common block-level tags with newlines to preserve paragraphs
    block_tags = [
        r"</p>",
        r"<br\s*/?>",
        r"</div>",
        r"</tr>",
        r"</li>",
        r"</ul>",
        r"</ol>",
    ]
    for t in block_tags:
        content = re.sub(t, "\n", content, flags=re.IGNORECASE)

    # Remove any remaining tags
    text = re.sub(r"<[^>]+>", "", content)

    # Unescape HTML entities
    text = html_unescape(text)

    # Normalize whitespace and collapse multiple blank lines
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned_lines = []
    prev_blank = False
    for ln in lines:
        if not ln:
            if not prev_blank:
                cleaned_lines.append("")
            prev_blank = True
        else:
            cleaned_lines.append(ln)
            prev_blank = False

    final = "\n".join(cleaned_lines).strip()
    return final


def find_and_parse_html(directory: str) -> str:
    """Walk `directory`, find the first .html file, parse it with parse_message_html and return the text.

    Returns an empty string if no .html file is found or on error.
    """
    if not directory:
        return ""
    try:
        for root, dirs, files in os.walk(directory):
            for fname in files:
                if fname.lower().endswith(".html"):
                    path = os.path.join(root, fname)
                    try:
                        html_content = robust_read_text(path)
                        return parse_message_html(html_content)
                    except Exception as e:
                        logger.debug(f"Failed to read/parse {path}: {e}")
                        continue
    except Exception as e:
        logger.debug(f"Error walking directory {directory}: {e}")
    return ""


def robust_read_text(path: str) -> str:
    """Read file as bytes and try several decodings to avoid replacement characters.

    Tries utf-8, utf-8-sig, cp1252 (Windows-1252), then latin-1. If utf-8 produced
    the Unicode replacement character, prefer cp1252 decoding which often maps
    Windows smart quotes and dashes correctly.
    """
    with open(path, "rb") as bf:
        data = bf.read()

    # Try utf-8 first
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                text = data.decode("latin-1", errors="replace")

    # If utf-8 produced replacement characters, try cp1252 which often fixes smart quotes
    if "\ufffd" in text:
        try:
            text = data.decode("cp1252")
        except Exception:
            pass

    return text
