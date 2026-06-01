"""
Code Interpreter utilities for the OpenAI Responses API.

Covers:
- Extracting CI outputs, file citations, and container IDs from API responses
- Downloading files from OpenAI containers and sandbox URLs
- Replacing temporary URLs in message text with permanent Django file URLs
"""

import os
import re

from django.conf import settings

from openai.types.responses import Response
from structlog import get_logger

logger = get_logger(__name__)


# ============================================================================
# Response extraction helpers
# ============================================================================


def extract_file_citations(response: Response) -> list:
    """
    Extract container file citations from a Response object.

    Code interpreter can create files (images, CSVs, etc.) that are stored in the
    container. These appear as annotations in the response message content.

    Args:
        response: The completed Response object

    Returns:
        List of file citation dicts with file_id, container_id, and filename
    """
    if not response or not response.output:
        return []

    file_citations = []
    for output_item in response.output:
        # Check for content with annotations
        if hasattr(output_item, "content"):
            for content in output_item.content or []:
                if hasattr(content, "annotations"):
                    for ann in content.annotations or []:
                        if (
                            hasattr(ann, "type")
                            and ann.type == "container_file_citation"
                        ):
                            filename = getattr(ann, "filename", "")
                            file_citations.append(
                                {
                                    "type": "container_file_citation",
                                    "file_id": getattr(ann, "file_id", None),
                                    "container_id": getattr(ann, "container_id", None),
                                    "filename": filename,
                                }
                            )

    if file_citations:
        logger.info(
            "File citations found in response",
            file_count=len(file_citations),
            filenames=[f.get("filename") for f in file_citations],
        )

    return file_citations


def extract_code_interpreter_outputs(response: Response) -> list:
    """
    Extract code interpreter outputs (images, logs) from a Response object.

    Code interpreter tool calls can produce image outputs (e.g., matplotlib plots)
    and log outputs. These are in the response.output as code_interpreter_call items.

    Args:
        response: The completed Response object

    Returns:
        List of output dicts with type 'image_url' or 'logs'
    """
    if not response or not response.output:
        return []

    outputs = []

    logger.info("Inspecting response items for CI outputs", count=len(response.output))

    for i, output_item in enumerate(response.output):
        item_type = getattr(output_item, "type", None)
        logger.info("Response item", index=i, type=item_type)

        if item_type == "code_interpreter_call":
            item_outputs = getattr(output_item, "outputs", None)
            logger.info(
                "CI item outputs", count=len(item_outputs) if item_outputs else 0
            )

            if item_outputs:
                for out in item_outputs:
                    out_type = getattr(out, "type", None)
                    logger.info("CI output", type=out_type)
                    if out_type == "image":
                        url = getattr(out, "url", None)
                        if not url and hasattr(out, "image"):
                            image_obj = getattr(out, "image", None)
                            url = getattr(image_obj, "url", None)

                        if url:
                            outputs.append({"type": "image_url", "url": url})
                            logger.info(
                                "Code interpreter image output found",
                                url_prefix=url[:80] if url else None,
                            )
                    elif out_type == "logs":
                        logs = getattr(out, "logs", None)
                        if logs:
                            outputs.append({"type": "logs", "logs": logs})

    return outputs


def extract_container_id(response: Response) -> str:
    """
    Extract the container_id from code_interpreter_call items in a Response.

    The container_id is needed to download files that the model referenced
    in sandbox: URLs but didn't include in file annotations.

    Args:
        response: The completed Response object

    Returns:
        The container_id string, or None if not found
    """
    if not response or not response.output:
        return None

    for output_item in response.output:
        item_type = getattr(output_item, "type", None)
        if item_type == "code_interpreter_call":
            container_id = getattr(output_item, "container_id", None)
            if container_id:
                logger.info(
                    "Found container_id on code_interpreter_call",
                    container_id=container_id,
                )
                return container_id

            status = getattr(output_item, "status", None)
            if status and hasattr(status, "container_id"):
                container_id = getattr(status, "container_id", None)
                if container_id:
                    logger.info(
                        "Found container_id in status", container_id=container_id
                    )
                    return container_id

    return None


def extract_unique_container_ids(response: Response) -> set:
    """
    Extract all unique container_ids from code_interpreter_call items in a Response.

    This is used to determine the actual number of Code Interpreter sessions used,
    since Azure bills per session ($0.0363/session) not per tool call.

    Args:
        response: The completed Response object

    Returns:
        Set of unique container_id strings found in the response
    """
    if not response or not response.output:
        return set()

    container_ids = set()
    for output_item in response.output:
        item_type = getattr(output_item, "type", None)
        if item_type == "code_interpreter_call":
            container_id = getattr(output_item, "container_id", None)
            if container_id:
                container_ids.add(container_id)
                continue

            status = getattr(output_item, "status", None)
            if status and hasattr(status, "container_id"):
                container_id = getattr(status, "container_id", None)
                if container_id:
                    container_ids.add(container_id)

    if container_ids:
        logger.info(
            "Code interpreter sessions found",
            unique_sessions=len(container_ids),
            container_ids=list(container_ids),
        )

    return container_ids


# ============================================================================
# File download helpers
# ============================================================================


def download_container_files(file_citations: list, message) -> list:
    """
    Download files from OpenAI container and save them as ChatFile objects.

    Args:
        file_citations: List of file citation dicts with keys:
                       - file_id: The container file ID
                       - container_id: The container ID
                       - filename: The original filename
        message: The Message object to attach files to

    Returns:
        List of created ChatFile objects
    """
    import hashlib

    from django.core.files.base import ContentFile

    from openai import AzureOpenAI

    from librarian.models import SavedFile

    from chat_next.models import ChatFile

    if not file_citations:
        return []

    created_files = []

    def save_file_content(content_bytes: bytes, filename: str) -> "ChatFile":
        """Helper to save file content and create ChatFile."""
        file_hash = hashlib.sha256(content_bytes).hexdigest()

        clean_filename = filename
        if clean_filename.startswith("cfile_"):
            parts = clean_filename.rsplit(".", 1)
            if len(parts) == 2:
                clean_filename = f"generated_file.{parts[1]}"

        saved_file = SavedFile.objects.filter(sha256_hash=file_hash).first()
        if not saved_file:
            saved_file = SavedFile.objects.create(sha256_hash=file_hash)
            saved_file.file.save(clean_filename, ContentFile(content_bytes))

        return ChatFile.objects.create(
            message=message,
            filename=clean_filename,
            saved_file=saved_file,
        )

    try:
        api_version = settings.AZURE_AI_SERVICES_VERSION
        if not api_version or api_version in ("v1", "v1/"):
            api_version = "2025-03-01-preview"
        client = AzureOpenAI(
            api_key=settings.AZURE_AI_SERVICES_KEY,
            azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
            api_version=api_version,
        )

        for citation in file_citations:
            file_id = citation.get("file_id")
            container_id = citation.get("container_id")
            filename = citation.get("filename", "generated_file")

            if not file_id or not container_id:
                logger.warning(
                    "Skipping file citation with missing file_id or container_id",
                    citation=citation,
                )
                continue

            try:
                file_content = client.containers.files.content.retrieve(
                    file_id=file_id,
                    container_id=container_id,
                )
                content_bytes = file_content.read()
                chat_file = save_file_content(content_bytes, filename)
                created_files.append(chat_file)
                logger.info(
                    "Downloaded container file",
                    filename=filename,
                    file_id=file_id,
                    chat_file_id=chat_file.id,
                )
            except Exception as e:
                logger.error(
                    "Failed to download container file",
                    filename=filename,
                    file_id=file_id,
                    error=str(e),
                )

    except Exception as e:
        logger.error(
            "Failed to initialize Azure OpenAI client for container files",
            error=str(e),
        )

    return created_files


def download_sandbox_files(text: str, container_id: str, message) -> list:
    """
    Extract sandbox: URLs from text, download the files from the container,
    and attach them to the message.

    Sometimes the model returns sandbox:/mnt/data/filename.png links without
    proper file annotations. This function:
    1. Extracts all sandbox: paths from the text
    2. Lists files in the container
    3. Downloads files that match the sandbox paths
    4. Creates ChatFile objects for them

    Args:
        text: The message text containing sandbox: URLs
        container_id: The container ID to download files from
        message: The Message object to attach files to

    Returns:
        List of created ChatFile objects
    """
    import hashlib

    from django.core.files.base import ContentFile

    from openai import AzureOpenAI

    from librarian.models import SavedFile

    from chat_next.models import ChatFile

    if not text or "sandbox:" not in text or not container_id:
        return []

    sandbox_paths = re.findall(r"sandbox:([^\s\)\"\']+)", text)
    if not sandbox_paths:
        return []

    existing_files = set(f.filename for f in message.files.all())

    needed_files = {}
    for path in sandbox_paths:
        filename = path.split("/")[-1]
        if filename and filename not in existing_files:
            needed_files[filename] = path

    if not needed_files:
        return []

    logger.info(
        "Extracting sandbox files not in annotations",
        needed_files=list(needed_files.keys()),
        container_id=container_id,
    )

    created_files = []

    def save_file_content(content_bytes: bytes, filename: str) -> "ChatFile":
        """Helper to save file content and create ChatFile."""
        file_hash = hashlib.sha256(content_bytes).hexdigest()

        saved_file = SavedFile.objects.filter(sha256_hash=file_hash).first()
        if not saved_file:
            saved_file = SavedFile.objects.create(sha256_hash=file_hash)
            saved_file.file.save(filename, ContentFile(content_bytes))

        return ChatFile.objects.create(
            message=message,
            filename=filename,
            saved_file=saved_file,
        )

    try:
        api_version = settings.AZURE_AI_SERVICES_VERSION
        if not api_version or api_version in ("v1", "v1/"):
            api_version = "2025-03-01-preview"
        client = AzureOpenAI(
            api_key=settings.AZURE_AI_SERVICES_KEY,
            azure_endpoint=settings.AZURE_AI_SERVICES_ENDPOINT,
            api_version=api_version,
        )

        try:
            container_files = client.containers.files.list(container_id=container_id)
            file_id_map = {}
            for cf in container_files:
                cf_filename = getattr(cf, "filename", "") or getattr(cf, "name", "")
                cf_id = getattr(cf, "id", None) or getattr(cf, "file_id", None)
                if cf_filename and cf_id:
                    simple_name = cf_filename.split("/")[-1]
                    file_id_map[simple_name] = cf_id
                    file_id_map[cf_filename] = cf_id

            logger.info(
                "Container files listed",
                container_id=container_id,
                file_count=len(file_id_map),
            )
        except Exception as e:
            logger.error(
                "Failed to list container files",
                container_id=container_id,
                error=str(e),
            )
            return []

        downloaded_file_ids = set()
        for filename, path in needed_files.items():
            file_id = file_id_map.get(filename)
            if not file_id:
                file_id = file_id_map.get(path.lstrip("/"))
            if not file_id:
                stem = os.path.splitext(filename)[0].lower()
                ext = os.path.splitext(filename)[1].lower()
                for cf_name, cf_id in file_id_map.items():
                    cf_simple = cf_name.split("/")[-1]
                    cf_stem = os.path.splitext(cf_simple)[0].lower()
                    cf_ext = os.path.splitext(cf_simple)[1].lower()
                    if cf_ext != ext:
                        continue
                    if stem in cf_stem or cf_stem in stem:
                        file_id = cf_id
                        logger.info(
                            "Fuzzy matched sandbox file",
                            requested=filename,
                            matched=cf_simple,
                        )
                        break
            if not file_id:
                logger.warning(
                    "File not found in container",
                    filename=filename,
                    path=path,
                    available_files=list(file_id_map.keys()),
                )
                continue

            downloaded_file_ids.add(file_id)
            try:
                file_content = client.containers.files.content.retrieve(
                    file_id=file_id,
                    container_id=container_id,
                )
                content_bytes = file_content.read()
                chat_file = save_file_content(content_bytes, filename)
                created_files.append(chat_file)
                logger.info(
                    "Downloaded sandbox file",
                    filename=filename,
                    file_id=file_id,
                    chat_file_id=chat_file.id,
                )
            except Exception as e:
                logger.error(
                    "Failed to download sandbox file",
                    filename=filename,
                    file_id=file_id,
                    error=str(e),
                )

        # Fallback: if no files were downloaded, grab any un-downloaded container files
        if not created_files and file_id_map:
            unique_container_files = {}
            for cf_name, cf_id in file_id_map.items():
                if (
                    cf_id not in downloaded_file_ids
                    and cf_id not in unique_container_files
                ):
                    simple = cf_name.split("/")[-1]
                    if simple not in existing_files:
                        unique_container_files[cf_id] = simple

            for cf_id, cf_simple in unique_container_files.items():
                try:
                    file_content = client.containers.files.content.retrieve(
                        file_id=cf_id,
                        container_id=container_id,
                    )
                    content_bytes = file_content.read()
                    chat_file = save_file_content(content_bytes, cf_simple)
                    created_files.append(chat_file)
                    logger.info(
                        "Downloaded unmatched container file (fallback)",
                        filename=cf_simple,
                        file_id=cf_id,
                        chat_file_id=chat_file.id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to download fallback container file",
                        filename=cf_simple,
                        file_id=cf_id,
                        error=str(e),
                    )

    except Exception as e:
        logger.error(
            "Failed to initialize Azure OpenAI client for sandbox files",
            error=str(e),
        )

    return created_files


def replace_sandbox_urls(text: str, message) -> str:
    """
    Replace sandbox:// URLs in message text with actual file URLs.

    The code interpreter generates text like:
    - ![image](sandbox:/mnt/data/file.png)
    - [Download the PNG](sandbox:/mnt/data/file.png)

    This function replaces those with URLs to the actual ChatFile objects
    that were downloaded from the container.

    Args:
        text: The message text potentially containing sandbox:// URLs
        message: The Message object with attached files

    Returns:
        The text with sandbox:// URLs replaced with actual file URLs
    """
    from django.urls import reverse

    if not text or "sandbox:" not in text:
        return text

    chat_files = list(message.files.all())
    if not chat_files:
        return text

    file_url_map = {}
    for chat_file in chat_files:
        if chat_file.saved_file and chat_file.saved_file.file:
            download_url = reverse(
                "chat_next:download_file", kwargs={"file_id": chat_file.id}
            )
            file_url_map[chat_file.filename] = download_url
            file_url_map[f"/mnt/data/{chat_file.filename}"] = download_url

    def replace_url(match):
        full_match = match.group(0)
        path = match.group(1)
        for pattern, url in file_url_map.items():
            if pattern in path or path.endswith(pattern):
                return url
        return full_match

    return re.sub(r"sandbox:([^\s\)\"\']+)", replace_url, text)


def download_code_interpreter_images(outputs: list, message) -> list:
    """
    Download images from code interpreter outputs and attach them to the message.

    Code interpreter can generate images (e.g., matplotlib plots) that are hosted
    temporarily on OpenAI's servers. This function downloads them and stores them
    as ChatFile objects attached to the message.

    Args:
        outputs: List of output dicts with 'type' and 'url' keys for images
        message: The Message object to attach images to

    Returns:
        List of tuples (original_url, new_url) for URL replacement
    """
    import hashlib
    import mimetypes

    from django.core.files.base import ContentFile

    import requests

    from librarian.models import SavedFile

    from chat_next.models import ChatFile

    if not outputs:
        logger.info("download_code_interpreter_images: No outputs to process")
        return []

    logger.info(
        "download_code_interpreter_images: Processing outputs",
        output_count=len(outputs),
        output_types=[o.get("type") for o in outputs],
    )

    url_mappings = []

    for idx, output in enumerate(outputs):
        logger.info(
            "Processing output",
            idx=idx,
            output_type=output.get("type"),
            has_url=bool(output.get("url")),
        )

        if output.get("type") != "image_url":
            continue

        url = output.get("url")
        if not url:
            continue

        try:
            logger.info(
                "Downloading image from URL", url_prefix=url[:100] if url else None
            )
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            content_bytes = response.content

            content_type = response.headers.get("content-type", "image/png")
            extension = mimetypes.guess_extension(content_type) or ".png"
            filename = f"generated_plot_{idx + 1}{extension}"

            file_hash = hashlib.sha256(content_bytes).hexdigest()

            saved_file = SavedFile.objects.filter(sha256_hash=file_hash).first()
            if not saved_file:
                saved_file = SavedFile.objects.create(sha256_hash=file_hash)
                saved_file.file.save(filename, ContentFile(content_bytes))

            chat_file = ChatFile.objects.create(
                message=message,
                filename=filename,
                saved_file=saved_file,
            )

            url_mappings.append((url, saved_file.file.url))

            logger.info(
                "Downloaded code interpreter image",
                filename=filename,
                chat_file_id=chat_file.id,
            )

        except Exception as e:
            logger.error(
                "Failed to download code interpreter image",
                url=url[:80] if url else None,
                error=str(e),
            )

    return url_mappings
