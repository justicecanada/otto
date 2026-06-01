import codecs
import csv
import hashlib
import io
import re
import subprocess
import tempfile
import uuid
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

import filetype
import requests
import tiktoken
from bs4 import BeautifulSoup
from markdownify import markdownify
from structlog import get_logger

from otto.models import Cost
from otto.utils.common import get_temp_dir, normalize_content_ingestion_url

from librarian.utils.extract_emails import extract_eml, extract_msg
from librarian.utils.extract_zip import process_zip_file
from librarian.utils.markdown_splitter import MarkdownSplitter
from librarian.utils.office import (
    LEGACY_WORD_MIME_TYPES,
    WORDPROCESSINGML_DOCUMENT_MIME,
    convert_legacy_word_to_docx,
)

logger = get_logger(__name__)

# Maximum length for content_type to prevent DB overflow errors
# SavedFile.content_type is max_length=255, but we use a conservative limit
MAX_CONTENT_TYPE_LENGTH = 250

# Threshold for when to force OCR on PDFs, in characters
# If the text extracted from a PDF using non-OCR method is less than this threshold,
# we fallback to Azure Document Intelligence to perform OCR.
FORCE_OCR_THRESHOLD = 1000

# Ordered fallback encodings for plain-text extraction.
# Keep cp1252 last so UTF-16 with BOM is decoded correctly first.
DEFAULT_TEXT_ENCODINGS = ["utf-8-sig", "utf-16", "cp1252"]


def sanitize_content_type(content_type: str) -> str:
    """
    Sanitize a content type string to prevent DB overflow and parsing errors.

    - Strips parameters (e.g., "; charset=utf-8")
    - Validates format (must contain "/")
    - Truncates to MAX_CONTENT_TYPE_LENGTH
    - Returns empty string for invalid input
    """
    if not content_type or not isinstance(content_type, str):
        return ""

    # Strip any parameters after the main type/subtype (e.g., "text/html; charset=utf-8")
    # Only keep the part before the first semicolon
    sanitized = content_type.split(";")[0].strip().lower()

    # Additional validation: content type should be in format "type/subtype"
    # Remove any garbage that might cause issues
    if "/" not in sanitized:
        return ""

    # Truncate if too long (defensive measure)
    # Do this after validation to ensure the truncated result is still valid
    if len(sanitized) > MAX_CONTENT_TYPE_LENGTH:
        logger.warning(
            "Content type truncated",
            original_length=len(sanitized),
            content_type_preview=sanitized[:50],
        )
        # Ensure truncation doesn't break the type/subtype format
        truncated = sanitized[:MAX_CONTENT_TYPE_LENGTH]
        # If truncation removes the slash, the content type is invalid
        if "/" not in truncated:
            return ""
        sanitized = truncated

    return sanitized


def is_mostly_empty(md):
    # Remove <page_x> and </page_x> tags
    md = re.sub(r"</?page_\d+>", "", md)
    # Remove newline characters
    md = md.replace("\n", "").replace("\r", "")
    return len(md) < FORCE_OCR_THRESHOLD


def markdownify_wrapper(text):
    """Wrapper to allow options to be passed to markdownify"""
    return markdownify(
        text,
        heading_style="ATX",
        bullets="*",
        strong_em_symbol="_",
        escape_misc=False,
    )


def fetch_from_url(url):
    try:
        normalized_url = normalize_content_ingestion_url(url)
        if normalized_url != url:
            logger.info(
                "Fetching content using canonical content-ingestion URL",
                requested_url=url,
                fetched_url=normalized_url,
            )

        r = requests.get(normalized_url, allow_redirects=True)
        r.raise_for_status()
        content_type = guess_content_type(
            r.content, r.headers.get("content-type"), normalized_url
        )

        return r.content, content_type

    except Exception as e:
        logger.error(f"Failed to fetch from URL: {e}")
        raise Exception(f"Failed to fetch from URL: {e}")


def generate_hash(file_obj, block_size=65536):
    hasher = hashlib.sha256()
    # Always seek to the beginning before reading
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    if hasattr(file_obj, "chunks"):
        for chunk in file_obj.chunks(block_size):
            hasher.update(chunk)
    else:
        while True:
            chunk = file_obj.read(block_size)
            if not chunk:
                break
            hasher.update(chunk)
    # Always seek back to the beginning after reading
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)
    return hasher.hexdigest()


def extract_html_metadata(content):
    # Content is the binary data from response.content so convert it to a string
    soup = BeautifulSoup(decode_content(content), "html.parser")
    title_element = soup.find("title")
    title = title_element.get_text(strip=True) if title_element else None
    time_element = soup.find("time", {"property": "dateModified"})
    modified_at = (
        timezone.datetime.strptime(
            time_element.get_text(strip=True).strip("\ufeff"), "%Y-%m-%d"
        )
        if time_element
        else None
    )
    return {
        "extracted_title": title,
        "extracted_modified_at": modified_at,
    }


def _compute_page_boundaries(text: str) -> list[tuple[int, int, int]]:
    """
    Parse <page_N> tags and return sorted list of (tag_start, tag_end_of_closing, page_num).
    Used to determine which page a given character offset falls on.
    Returns empty list if no page tags found.
    """
    boundaries = []
    for match in re.finditer(r"<page_(\d+)>", text):
        page_num = int(match.group(1))
        boundaries.append((match.start(), page_num))
    return sorted(boundaries, key=lambda x: x[0])


def _get_page_for_offset(page_boundaries, char_offset):
    """Given sorted page_boundaries list, find which page contains char_offset."""
    page = None
    for tag_start, page_num in page_boundaries:
        if tag_start > char_offset:
            break
        page = page_num
    return page


def _compute_chunk_positions(chunks, extracted_text):
    """
    Compute (start_char, end_char, start_page) for each chunk by finding it
    in the extracted text. Returns list of dicts with position info.
    Chunks are sequential substrings with possible overlap.
    """
    if not extracted_text:
        return [{"start_char": None, "end_char": None, "start_page": None}] * len(
            chunks
        )

    page_boundaries = _compute_page_boundaries(extracted_text)
    positions = []
    search_start = 0

    for chunk_text in chunks:
        pos = extracted_text.find(chunk_text, search_start)
        if pos < 0:
            # Fallback: search from beginning (shouldn't happen with sequential chunks)
            pos = extracted_text.find(chunk_text)
        if pos >= 0:
            end_pos = pos + len(chunk_text)
            page = (
                _get_page_for_offset(page_boundaries, pos) if page_boundaries else None
            )
            positions.append(
                {"start_char": pos, "end_char": end_pos, "start_page": page}
            )
            search_start = pos + 1  # Advance past start to handle overlapping chunks
        else:
            positions.append({"start_char": None, "end_char": None, "start_page": None})

    return positions


def create_nodes(chunks, document):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    document_uuid = document.uuid_hex
    data_source_uuid = document.data_source.uuid_hex

    # Create a document (parent) node
    metadata = {"node_type": "document", "data_source_uuid": data_source_uuid}
    metadata["doc_id"] = document.id
    if document.title:
        metadata["title"] = document.title
    source = document.file_path or document.url or document.filename
    if source:
        metadata["source"] = source
    document_node = TextNode(text="", id_=document_uuid, metadata=metadata)
    document_node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
        node_id=document_node.node_id
    )

    # Compute character positions and page numbers for each chunk
    chunk_positions = _compute_chunk_positions(chunks, document.extracted_text)

    # Create chunk (child) nodes
    metadata["node_type"] = "chunk"
    child_nodes = create_child_nodes(
        chunks,
        source_node_id=document_node.node_id,
        metadata=metadata,
        chunk_positions=chunk_positions,
    )

    # Update node properties
    new_nodes = [document_node] + child_nodes
    exclude_keys = [
        "page_range",
        "node_type",
        "data_source_uuid",
        "chunk_number",
        "doc_id",
        "start_char",
        "end_char",
        "start_page",
    ]
    for node in new_nodes:
        node.excluded_llm_metadata_keys = exclude_keys
        node.excluded_embed_metadata_keys = exclude_keys
        node.metadata_separator = "\n"
        node.metadata_template = "{key}: {value}"
        node.text_template = "# {metadata_str}\ncontent:\n{content}\n\n"

    return new_nodes


def guess_content_type(
    content: str | bytes, content_type: str = "", path: str = ""
) -> str:
    """
    Guess the content type of the given content.

    Args:
        content: The file content (bytes or string)
        content_type: Optional hint for content type (e.g., from HTTP headers)
        path: Optional file path to help guess based on extension

    Returns:
        A sanitized content type string (e.g., "text/html", not "text/html; charset=utf-8")
    """
    # Sanitize the input content_type first (strip parameters like charset)
    content_type = sanitize_content_type(content_type)

    # Normalize the path to lowercase for consistent extension checking
    path = path.lower() if path else ""

    # We consider these content types to be reliable and do not need further guessing
    trusted_content_types = [
        "application/pdf",
        "application/xml",
        "application/vnd.ms-outlook",
        "application/eml",
        "application/zip",
        "application/x-zip-compressed",
        "text/html",
        "text/markdown",
        "text/csv",
        "application/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "officedocument.presentationml.presentation",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/bmp",
        "image/tiff",
        "image/tif",
        "image/heif",
        "image/heic",
    ]

    if content_type in trusted_content_types:
        return content_type

    if hasattr(content, "read"):
        content = content.read()

    if isinstance(content, bytes):
        # Explicitly handle Outlook emails
        if path.endswith(".msg"):
            return "application/vnd.ms-outlook"

        if path.endswith(".zip"):
            return "application/zip"

        if path.endswith(".docx"):
            return WORDPROCESSINGML_DOCUMENT_MIME

        if path.endswith(".doc") or path.endswith(".dot"):
            return "application/msword"

        if path.endswith(".eml"):
            return "application/eml"
        # Use filetype library to guess the content type.
        kind = filetype.guess(content)
        if kind and not path.endswith(".md"):
            return kind.mime

        # Fallback to manual checks if filetype library fails
        try:
            content = content.decode("utf-8", errors="ignore")
        except UnicodeDecodeError:
            return content_type or "application/octet-stream"

    if isinstance(content, str):
        if "text" in content_type and path.endswith(".md"):
            return "text/markdown"

        if content.startswith("<!DOCTYPE html>") or "<html" in content:
            return "text/html"

        if content.startswith("<?xml") or "<root" in content:
            return "application/xml"

        if content.startswith("{") or content.startswith("["):
            return "application/json"

    return content_type or "text/plain"


def get_process_engine_from_type(type):
    if "image" in type:
        return "IMAGE"
    elif type in LEGACY_WORD_MIME_TYPES:
        return "WORD_LEGACY"
    elif "officedocument.wordprocessingml.document" in type:
        return "WORD"
    elif "officedocument.presentationml.presentation" in type:
        return "POWERPOINT"
    elif "application/vnd.ms-outlook" in type:
        return "OUTLOOK_MSG"
    elif "application/zip" in type or "application/x-zip-compressed" in type:
        return "ZIP"
    elif "application/eml" in type:
        return "EML"
    elif "application/pdf" in type:
        return "PDF"
    elif "text/html" in type:
        return "HTML"
    elif "text/markdown" in type:
        return "MARKDOWN"
    elif "text/csv" in type or "application/csv" in type:
        return "CSV"
    elif "spreadsheet" in type:
        return "EXCEL"
    elif _is_unsupported_binary_type(type):
        return "UNSUPPORTED"
    else:
        return "TEXT"


def _is_unsupported_binary_type(content_type: str) -> bool:
    """
    Check if the content type is a known unsupported binary format.
    These are file types we cannot extract text from.
    """
    unsupported_prefixes = [
        "audio/",
        "video/",
        "application/octet-stream",
        "application/x-executable",
        "application/x-sharedlib",
        "application/x-msdownload",
        "application/x-dosexec",
    ]
    for prefix in unsupported_prefixes:
        if content_type.startswith(prefix):
            return True
    return False


class UnsupportedFileTypeError(Exception):
    """Raised when a file type is not supported for text extraction."""

    def __init__(self, content_type: str, filename: str = None):
        self.content_type = content_type
        self.filename = filename
        if filename:
            message = _(
                "The file '{filename}' has an unsupported format ({content_type}). "
                "Supported formats include: PDF, Word (DOC and DOCX), PowerPoint, Excel, images, HTML, "
                "plain text, Markdown, CSV, ZIP archives, and email files (MSG, EML)."
            ).format(filename=filename, content_type=content_type)
        else:
            message = _(
                "This file has an unsupported format ({content_type}). "
                "Supported formats include: PDF, Word (DOC and DOCX), PowerPoint, Excel, images, HTML, "
                "plain text, Markdown, CSV, ZIP archives, and email files (MSG, EML)."
            ).format(content_type=content_type)
        super().__init__(message)


def decode_content(
    content: bytes,
    encodings: list[str] = DEFAULT_TEXT_ENCODINGS,
) -> str:
    """
    Decode content with multiple encodings with fallback.

    Returns:
        Decoded string

    Raises:
        Exception: If content cannot be decoded with any of the provided encodings
    """
    for encoding in encodings:
        try:
            if encoding == "utf-16" and not _has_utf16_bom(content):
                continue
            decoded_content = content.decode(encoding)
            if encoding == "cp1252" and _has_disallowed_control_characters(
                decoded_content
            ):
                logger.debug("Rejected cp1252 decode due to control characters")
                continue
            return decoded_content
        except UnicodeDecodeError as e:
            logger.debug(e)
            continue
    raise Exception(f"Failed to decode content with encodings: {encodings}")


def _has_disallowed_control_characters(text: str) -> bool:
    """Return True when decoded text contains non-whitespace control characters."""
    return bool(re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]", text))


def _has_utf16_bom(content: bytes) -> bool:
    """Return True when content starts with a UTF-16 byte order mark."""
    return content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))


class ExtractionResult:
    def __init__(
        self,
        markdown: str = "",
        chunks: list[str] = [],
        pdf_method: str = "default",
        needs_azure: bool = False,
        azure_model: str = None,
    ):
        self.markdown = markdown
        self.chunks = chunks
        self.pdf_method = pdf_method
        self.needs_azure = needs_azure  # Whether Document Intelligence is needed
        self.azure_model = azure_model

    def __repr__(self):
        return f"ExtractionResult(markdown={self.markdown[:30]}, chunks={len(self.chunks)}, pdf_method={self.pdf_method}, needs_azure={self.needs_azure})"


def should_enable_markdown_chunking(
    process_engine: str, pdf_method: str | None = None
) -> bool:
    """Return whether Markdown-aware chunking should be used for extracted text."""
    if process_engine == "PDF":
        return pdf_method in {"layout", "azure_read", "azure_layout"}

    return process_engine in {
        "WORD",
        "WORD_LEGACY",
        "POWERPOINT",
        "HTML",
        "MARKDOWN",
        "CSV",
        "EXCEL",
    }


def split_markdown_into_chunks(
    markdown: str,
    process_engine: str,
    pdf_method: str | None = None,
    chunk_size: int = 768,
):
    """Split extracted document text into chunks using the same logic as ingestion."""
    if not chunk_size:
        return []

    try:
        md_splitter = MarkdownSplitter(
            chunk_size=chunk_size,
            chunk_overlap=100,
            enable_markdown=should_enable_markdown_chunking(
                process_engine, pdf_method=pdf_method
            ),
        )
        return md_splitter.split_markdown(markdown)
    except Exception as e:
        logger.debug("Error splitting markdown using MarkdownSplitter:")
        logger.error(e)
        from llama_index.core.node_parser import SentenceSplitter

        sentence_splitter = SentenceSplitter(
            chunk_size=chunk_size, chunk_overlap=min(chunk_size // 4, 100)
        )
        return sentence_splitter.split_text(markdown)


def extract_markdown(
    content: str | bytes,
    process_engine: str,
    pdf_method: str = "default",
    base_url: str = None,
    chunk_size: int = 768,
    selector: str = None,
    root_document_id: int = None,
    task_id: str = None,
    document_id: int = None,
    content_type: str = None,
) -> ExtractionResult:
    try:
        if process_engine == "IMAGE":
            content = resize_to_azure_requirements(content)
            return ExtractionResult(
                pdf_method="azure_read", needs_azure=True, azure_model="prebuilt-read"
            )
        elif process_engine == "PDF":
            if pdf_method == "default":
                md = pdf_to_text_pymupdf(content)
                if is_mostly_empty(md):
                    pdf_method = "azure_read"
                    return ExtractionResult(
                        pdf_method="azure_read",
                        needs_azure=True,
                        azure_model="prebuilt-read",
                    )
            elif pdf_method == "layout":
                md = pdf_to_markdown_pymupdf4llm(content)
                if is_mostly_empty(md):
                    pdf_method = "azure_read"
                    return ExtractionResult(
                        pdf_method="azure_read",
                        needs_azure=True,
                        azure_model="prebuilt-read",
                    )
            elif pdf_method == "azure_read":
                return ExtractionResult(
                    pdf_method="azure_read",
                    needs_azure=True,
                    azure_model="prebuilt-read",
                )
            elif pdf_method == "azure_layout":
                return ExtractionResult(
                    pdf_method="azure_layout",
                    needs_azure=True,
                    azure_model="prebuilt-layout",
                )
        elif process_engine == "WORD_LEGACY":
            md = docx_to_markdown(legacy_word_to_docx(content))
        elif process_engine == "WORD":
            md = docx_to_markdown(content)
        elif process_engine == "POWERPOINT":
            md = pptx_to_markdown(content)
        elif process_engine == "HTML":
            md = html_to_markdown(decode_content(content), base_url, selector)
        elif process_engine == "MARKDOWN":
            md = decode_content(content)
        elif process_engine == "OUTLOOK_MSG":
            md = extract_msg(content, root_document_id)
        elif process_engine == "ZIP":
            md = process_zip_file(content, root_document_id, task_id, document_id)
        elif process_engine == "EML":
            md = extract_eml(content, root_document_id)
        elif process_engine == "CSV":
            md = csv_to_markdown(content)
        elif process_engine == "EXCEL":
            md = excel_to_markdown(content)
        elif process_engine == "UNSUPPORTED":
            # Explicitly unsupported binary format (audio, video, etc.)
            raise UnsupportedFileTypeError(content_type=content_type or "unknown")
        else:
            # TEXT fallback - try to decode as text
            try:
                md = decode_content(content)
            except Exception:
                # If decoding fails, it's likely a binary file we can't process
                raise UnsupportedFileTypeError(content_type=content_type or "unknown")

        md = remove_nul_characters(md)

        # Strip leading/trailing whitespace; replace all >2 line breaks with 2 line breaks
        md = re.sub(r"\n{3,}", "\n\n", md.strip())

        md_chunks = split_markdown_into_chunks(
            md,
            process_engine=process_engine,
            pdf_method=pdf_method,
            chunk_size=chunk_size,
        )
        return ExtractionResult(md, md_chunks, pdf_method)

    except Exception as e:
        logger.error(f"Error in extract_markdown: {str(e)}")
        raise


def pdf_to_text_pymupdf(content):
    import pymupdf

    doc = pymupdf.open(stream=content)
    md = ""
    for i, page in enumerate(doc):
        md += f"<page_{i + 1}>\n"
        text = page.get_text().strip()
        md += text
        md += f"\n</page_{i + 1}>\n"
    doc.close()
    return md


def _replace_pymupdf4llm_page_separators(md: str) -> str:
    """Convert pymupdf4llm page separators to our page tag format, with each tag on its own line."""
    if not md.strip():
        return md

    # Split on pymupdf4llm page separators: "--- end of page=N ---"
    page_breaks = list(re.finditer(r"--- end of page=(\d+) ---", md))
    if not page_breaks:
        # No page breaks, wrap everything in <page_1>...</page_1>
        content = md.strip()
        return f"<page_1>\n{content}\n</page_1>\n"

    parts = []
    last_idx = 0
    for i, match in enumerate(page_breaks):
        page_num = int(match.group(1)) + 1  # Convert to 1-based page number
        part = md[last_idx : match.start()].strip()
        parts.append((page_num, part))
        last_idx = match.end()
    # Add last part as next page number
    last_page_num = int(page_breaks[-1].group(1)) + 2
    last_part = md[last_idx:].strip()
    parts.append((last_page_num, last_part))

    # Build output with each tag on its own line
    result = ""
    for page_num, content in parts:
        result += f"<page_{page_num}>\n{content}\n</page_{page_num}>\n"
    return result


def pdf_to_markdown_pymupdf4llm(content):
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.Document(stream=content)

    md = pymupdf4llm.to_markdown(doc, page_separators=True)
    md = _replace_pymupdf4llm_page_separators(md)

    doc.close()
    return md


def html_to_markdown(content, base_url=None, selector=None):
    return _convert_html_to_markdown(content, base_url, selector)


def remove_nul_characters(text):
    """Remove NUL (0x00) characters from the text."""
    return text.replace("\x00", "")


def msg_to_markdown(content):
    temp_dir = get_temp_dir()
    with tempfile.NamedTemporaryFile(suffix=".msg", dir=temp_dir) as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name
        try:
            md = subprocess.check_output(
                ["python", "-m", "extract_msg", "--dump-stdout", temp_file_path]
            ).decode("utf-8")
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed with exit code {e.returncode}")
            logger.error(f"Output: {e.output.decode('utf-8')}")
            md = ""
        except Exception as e:
            logger.error(f"Failed to extract text from Outlook email: {e}")
            md = ""
        return md


def docx_to_markdown(content):
    import mammoth

    with io.BytesIO(content) as docx_file:
        try:
            result = mammoth.convert_to_html(docx_file)
        except Exception as e:
            logger.error(f"Failed to extract text from .docx file: {e}")
            raise Exception(_("Corrupt docx file."))
    html = result.value

    md = _convert_html_to_markdown(html)
    return md


def legacy_word_to_docx(content):
    try:
        return convert_legacy_word_to_docx(content)
    except Exception as e:
        logger.error(f"Failed to convert .doc file to .docx via LibreOffice: {e}")
        raise Exception(_("Could not convert legacy Word (.doc) file.")) from e


def pptx_to_markdown(content):
    import pptx

    with io.BytesIO(content) as ppt_file:
        try:
            prs = pptx.Presentation(ppt_file)
        except Exception as e:
            logger.error(f"Failed to extract text from .pptx file: {e}")
            raise Exception(_("Corrupt pptx file."))

    # extract text from each slide
    all_html = ""
    for i, slide in enumerate(prs.slides):
        html = ""
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                html += "<p>"
                for run in paragraph.runs:
                    html += run.text
                html += "</p>"
        if len(slide.notes_slide.notes_text_frame.paragraphs) > 0:
            html += "<h6>Presenter notes:</h6>"
            for note in slide.notes_slide.notes_text_frame.paragraphs:
                html += "<p>"
                for run in note.runs:
                    html += run.text
                html += "</p>"
        if html:
            all_html += f"<page_{i + 1}>\n{html}\n</page_{i + 1}>\n"

    md = _convert_html_to_markdown(all_html)
    return md


def create_child_nodes(chunks, source_node_id, metadata=None, chunk_positions=None):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    nodes = []
    for i, text in enumerate(chunks):
        node = TextNode(text=text, id_=str(uuid.uuid4()))

        node_meta = dict(metadata, chunk_number=i)
        # Add position metadata if available
        if chunk_positions and i < len(chunk_positions):
            pos = chunk_positions[i]
            if pos.get("start_char") is not None:
                node_meta["start_char"] = pos["start_char"]
                node_meta["end_char"] = pos["end_char"]
            if pos.get("start_page") is not None:
                node_meta["start_page"] = pos["start_page"]
        node.metadata = node_meta
        node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
            node_id=source_node_id
        )
        nodes.append(node)

    # Handle the case when there's only one or zero elements
    if len(chunks) < 2:
        return nodes

    # Set relationships
    for i in range(len(nodes) - 1):
        nodes[i].relationships[NodeRelationship.NEXT] = RelatedNodeInfo(
            node_id=nodes[i + 1].node_id
        )
        nodes[i + 1].relationships[NodeRelationship.PREVIOUS] = RelatedNodeInfo(
            node_id=nodes[i].node_id
        )

    return nodes


def token_count(string: str, model: str = "gpt-4") -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding("o200k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens


def _remove_ignored_tags(text):
    # remove any javascript, css, images, svg, and comments from self.text
    text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<img.*?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<svg.*?</svg>", "", text, flags=re.DOTALL)
    # remove any attribute tags that start with javascript:
    text = re.sub(r"<[^>]+javascript:.*?>", "", text, flags=re.DOTALL)
    # remove any empty html tags from self.text
    text = re.sub(r"<[^/>][^>]*>\s*</[^>]+>", "", text, flags=re.DOTALL)

    # remove any header/footer/nav tags and the content within them
    text = re.sub(r"<header.*?</header>", "", text, flags=re.DOTALL)
    text = re.sub(r"<footer.*?</footer>", "", text, flags=re.DOTALL)
    text = re.sub(r"<nav.*?</nav>", "", text, flags=re.DOTALL)

    # Remove all the line breaks, carriage returns, and tabs
    text = re.sub(r"[\n\r\t]", "", text)

    return text


def _convert_html_to_markdown(
    source_html: str, base_url: str = None, selector: str = None
) -> str:
    """Converts HTML to markdown, preserving <page_x> tags in the markdown output."""
    page_open_tags = re.findall(r"<page_\d+>", source_html)
    # When page tags (e.g. "<page_1">) are present, run this step separately for each
    # of the page contents and combine the results
    if page_open_tags:
        combined_md = ""
        for opening_tag in page_open_tags:
            closing_tag = opening_tag.replace("<", "</")
            page_html_contents = re.search(
                f"{opening_tag}(.*){closing_tag}", source_html, re.DOTALL
            ).group(1)
            page_md = _convert_html_to_markdown(page_html_contents, base_url)
            combined_md += f"{opening_tag}\n{page_md}\n{closing_tag}\n"
        return combined_md

    soup = BeautifulSoup(source_html, "html.parser")
    if soup.find("body"):
        soup = soup.find("body")

    if selector:
        selected_html = BeautifulSoup(
            "".join([str(tag) for tag in soup.select(selector)]), "html.parser"
        )
        if selected_html:
            soup = selected_html
        else:
            logger.warning(f"Selector {selector} not found in HTML")

    if not base_url:
        # find all anchor tags
        for anchor in soup.find_all("a"):
            # get the href attribute value
            href = anchor.get("href")
            # convert relative URLs to absolute URLs
            if href and not href.startswith("http"):
                absolute_url = urljoin(base_url, href)
                anchor["href"] = absolute_url

    # Replace <caption> elements with <h6> so that they get capture in breadcrumbs
    for caption in soup.find_all("caption"):
        caption.name = "h6"

    text = _remove_ignored_tags(str(soup))

    markdown = markdownify_wrapper(text).strip()
    return markdown


def csv_to_markdown(content):
    """Convert CSV content to markdown table."""
    try:
        decoded = decode_content(content)
        with io.StringIO(decoded) as csv_file:
            reader = csv.reader(csv_file)
            rows = list(reader)
    except Exception as e:
        logger.error(f"Failed to extract text from CSV file: {e}")
        raise Exception(_("Corrupt CSV file."))

    if not rows:
        return ""

    header = [_csv_cell_to_markdown(cell) for cell in rows[0]]
    table = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows[1:]:
        row_text = [_csv_cell_to_markdown(cell) for cell in row]
        table.append("| " + " | ".join(row_text) + " |")

    md = "\n".join(table)
    return md


def _split_hyperlink_args(args: str) -> list[str]:
    """Split HYPERLINK(...) args on commas/semicolons outside of quoted strings."""
    parts = []
    current = []
    in_quotes = False
    i = 0

    while i < len(args):
        char = args[i]

        if char == '"':
            # Handle escaped quote in CSV/Excel formulas: ""
            if in_quotes and i + 1 < len(args) and args[i + 1] == '"':
                current.append('""')
                i += 2
                continue
            in_quotes = not in_quotes
            current.append(char)
        elif not in_quotes and char in [",", ";"]:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)

        i += 1

    parts.append("".join(current).strip())
    return parts


def _unquote_formula_string(value: str) -> str:
    """Unquote and unescape a formula string argument if wrapped in double quotes."""
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def _csv_cell_to_markdown(cell_value: str) -> str:
    """Convert a CSV cell to markdown text, preserving Excel-style HYPERLINK formulas."""
    value = str(cell_value) if cell_value is not None else ""
    formula_match = re.match(r"^\s*=\s*HYPERLINK\s*\((.*)\)\s*$", value, re.IGNORECASE)
    if not formula_match:
        return value.replace("|", "\\|")

    args = _split_hyperlink_args(formula_match.group(1))
    if not args:
        return value.replace("|", "\\|")

    url = _unquote_formula_string(args[0])
    label = _unquote_formula_string(args[1]) if len(args) > 1 else url

    if not url:
        return value.replace("|", "\\|")

    escaped_label = label.replace("|", "\\|")
    return f"[{escaped_label}]({url})"


def _excel_cell_to_markdown(cell) -> str:
    """Convert an Excel cell to markdown text, preserving hyperlinks."""
    value = str(cell.value) if cell.value is not None else ""
    if cell.hyperlink and cell.hyperlink.target:
        escaped_value = value.replace("|", "\\|")
        return f"[{escaped_value}]({cell.hyperlink.target})"
    return value.replace("|", "\\|")


def excel_to_markdown(content):
    """Convert Excel content to markdown tables."""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content))
    except Exception as e:
        logger.error(f"Failed to extract text from Excel file: {e}")
        raise Exception(_("Corrupt Excel file."))

    markdown = ""
    for sheet in workbook.sheetnames:
        markdown += f"# {sheet}\n\n"
        sheet_obj = workbook[sheet]
        rows = list(sheet_obj.iter_rows())
        if not rows:
            continue
        header = [_excel_cell_to_markdown(cell) for cell in rows[0]]
        table = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * len(header)) + " |",
        ]
        for row in rows[1:]:
            row_text = [_excel_cell_to_markdown(cell) for cell in row]
            table.append("| " + " | ".join(row_text) + " |")
        markdown += "\n".join(table) + "\n\n"
    return markdown


def submit_azure_document_ai(
    content: bytes,
    model: str,
    request_searchable_pdf: bool = False,
) -> str:
    """
    Submit content to Azure Document Intelligence and return operation_location URL.
    Does not wait for completion.

    Args:
        content: Binary content to analyze
        model: Either "prebuilt-layout" or "prebuilt-read"

    Returns:
        operation_location URL string
    """
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import (
        AnalyzeOutputOption,
        DocumentContentFormat,
    )
    from azure.core.credentials import AzureKeyCredential

    document_analysis_client = DocumentIntelligenceClient(
        endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
        credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
    )

    analyze_kwargs = {}
    if request_searchable_pdf and model == "prebuilt-read":
        analyze_kwargs["output"] = [AnalyzeOutputOption.PDF]
        analyze_kwargs["output_content_format"] = DocumentContentFormat.MARKDOWN

    poller = document_analysis_client.begin_analyze_document(
        model,
        content,
        **analyze_kwargs,
    )
    # Return the operation location without waiting for result
    return poller._polling_method._initial_response.http_response.headers.get(
        "operation-location"
    )


def get_azure_document_ai_result_pdf(operation_location: str, model: str) -> bytes:
    """Fetch searchable PDF bytes for an Azure Document Intelligence result."""
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    parsed = urlparse(operation_location or "")
    path = parsed.path.rstrip("/")
    marker = "/analyzeResults/"
    if marker not in path:
        raise ValueError("Could not parse Azure Document Intelligence result ID")

    result_id = path.split(marker, 1)[1]
    if not result_id:
        raise ValueError("Missing Azure Document Intelligence result ID")

    document_analysis_client = DocumentIntelligenceClient(
        endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
        credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
    )
    pdf_content = document_analysis_client.get_analyze_result_pdf(
        model_id=model,
        result_id=result_id,
    )
    return b"".join(chunk for chunk in pdf_content)


def poll_azure_document_ai(operation_location: str):
    """
    Poll Azure Document Intelligence operation until complete.
    Returns the result JSON when ready.

    Args:
        operation_location: The operation location URL from submit

    Returns:
        The completed result JSON dict
    """
    import time

    import requests

    # Poll the operation until complete
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_DOCUMENT_INTELLIGENCE_KEY,
    }

    while True:
        response = requests.get(operation_location, headers=headers)
        result_json = response.json()

        status = result_json.get("status")
        if status == "succeeded":
            return result_json
        elif status == "failed":
            error = result_json.get("error", {})
            raise Exception(f"Azure Document Intelligence failed: {error}")
        elif status in ["notStarted", "running"]:
            time.sleep(1)  # Wait 1 second before polling again
        else:
            raise Exception(f"Unknown status: {status}")


def parse_azure_layout_result(result_json: dict) -> str:
    """
    Parse Azure Document Intelligence layout result into HTML.

    Args:
        result_json: The result JSON from Azure Document Intelligence

    Returns:
        HTML string with layout information
    """
    from shapely.geometry import Polygon

    analyze_result = result_json.get("analyzeResult", {})

    num_pages = len(analyze_result.get("pages", []))
    Cost.objects.new(cost_type="doc-ai-prebuilt", count=num_pages)

    # Extract table bounding regions for intersection checking
    table_bounding_regions = []
    for table in analyze_result.get("tables", []):
        for cell in table.get("cells", []):
            if cell.get("boundingRegions"):
                table_bounding_regions.append(cell["boundingRegions"][0])

    table_chunks = []
    for table in analyze_result.get("tables", []):
        if not table.get("boundingRegions"):
            continue
        page_number = table["boundingRegions"][0]["pageNumber"]

        # Generate table HTML
        table_html = "<table>"
        for row in range(table["rowCount"]):
            table_html += "<tr>"
            for col in range(table["columnCount"]):
                cell = next(
                    (
                        c
                        for c in table.get("cells", [])
                        if c["rowIndex"] == row and c["columnIndex"] == col
                    ),
                    None,
                )
                table_html += f"<td>{cell.get('content', '') if cell else ''}</td>"
            table_html += "</tr>"
        table_html += "</table>"

        polygon = table["boundingRegions"][0]["polygon"]
        points = [(polygon[i], polygon[i + 1]) for i in range(0, len(polygon), 2)]
        table_polygon = Polygon(points)

        table_chunks.append(
            {
                "page_number": page_number,
                "x": table_polygon.bounds[0],
                "y": table_polygon.bounds[1],
                "text": table_html,
            }
        )

    p_chunks = []
    for para in analyze_result.get("paragraphs", []):
        if not para.get("boundingRegions"):
            continue

        para_page = para["boundingRegions"][0]["pageNumber"]
        polygon = para["boundingRegions"][0]["polygon"]
        points = [(polygon[i], polygon[i + 1]) for i in range(0, len(polygon), 2)]
        para_polygon = Polygon(points)

        # Check intersection with tables
        intersects_table = any(
            para_page == br["pageNumber"]
            and para_polygon.intersects(
                Polygon(
                    [
                        (br["polygon"][i], br["polygon"][i + 1])
                        for i in range(0, len(br["polygon"]), 2)
                    ]
                )
            )
            for br in table_bounding_regions
        )

        if intersects_table:
            continue

        # Skip checkbox/selection markers
        content = para.get("content", "")
        if any(word in content for word in [":selected:", ":checked:", ":unchecked:"]):
            continue

        p_chunks.append(
            {
                "page_number": para_page,
                "x": para_polygon.bounds[0],
                "y": para_polygon.bounds[1],
                "text": f"<p>{content}</p>",
            }
        )

    chunks = table_chunks + p_chunks

    # Sort chunks by page number, then by y coordinate, then by x coordinate
    chunks = sorted(
        chunks, key=lambda item: (item.get("page_number"), item.get("y"), item.get("x"))
    )
    html = ""
    cur_page = None
    for idx, chunk in enumerate(chunks, 1):
        page_start_tag = f"\n<page_{chunk.get('page_number')}>\n"
        page_end_tag = f"\n</page_{chunk.get('page_number')}>\n"
        prev_end_tag = f"\n</page_{cur_page}>\n" if cur_page is not None else ""
        if chunk.get("page_number") != cur_page:
            if cur_page is not None:
                html += prev_end_tag
            cur_page = chunk.get("page_number")
            html += page_start_tag
        html += chunk.get("text")

    if cur_page is not None and chunks:
        html += page_end_tag

    return html


def parse_azure_read_result(result_json: dict) -> str:
    """
    Parse Azure Document Intelligence read result into text.

    Args:
        result_json: The result JSON from Azure Document Intelligence

    Returns:
        Text string with page tags
    """
    analyze_result = result_json.get("analyzeResult", {})
    pages = analyze_result.get("pages", [])

    num_pages = len(pages)
    Cost.objects.new(cost_type="doc-ai-read", count=num_pages)

    text = ""
    for page in pages:
        page_num = page["pageNumber"]
        text += f"\n<page_{page_num}>\n"
        for line in page.get("lines", []):
            text += line.get("content", "") + "\n"
        text = text.strip() + f"\n</page_{page_num}>\n"

    return text.strip()


def resize_to_azure_requirements(content):
    from PIL import Image

    if isinstance(content, Image.Image):
        image = content
    else:
        with io.BytesIO(content) as image_file:
            image = Image.open(image_file)
            image.load()

    width, height = image.size
    if width < 50 or height < 50:
        # Resize to at least 50 pixels
        if width <= height:
            new_width = 50
            new_height = int(height * (50 / width))
        else:
            new_height = 50
            new_width = int(width * (50 / height))
    elif width > 10000 or height > 10000:
        # Resize to max 10000 pixels
        if width >= height:
            new_width = 10000
            new_height = int(height * (10000 / width))
        else:
            new_height = 10000
            new_width = int(width * (10000 / height))
    else:
        if isinstance(content, Image.Image):
            new_width, new_height = width, height
        else:
            return content
    # Edge case: insanely wide or tall images. Don't maintain proportions.
    new_width = min(new_width, 10000)
    new_height = min(new_height, 10000)
    new_width = max(new_width, 50)
    new_height = max(new_height, 50)
    image = image.resize((new_width, new_height))

    if isinstance(content, Image.Image):
        return image
    else:
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            content = output.getvalue()
            return content
