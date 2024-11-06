import hashlib
import io
import re
import subprocess
import uuid
from urllib.parse import urljoin

from django.conf import settings
from django.utils import timezone

import filetype
import requests
import tiktoken
from bs4 import BeautifulSoup
from markdownify import markdownify
from structlog import get_logger

from librarian.utils.markdown_splitter import MarkdownSplitter
from otto.models import Cost

logger = get_logger(__name__)


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
        r = requests.get(url, allow_redirects=True)
        content_type = guess_content_type(r.content, r.headers.get("content-type"), url)
        return r.content, content_type

    except Exception as e:
        logger.error(f"Failed to fetch from URL: {e}")
        raise Exception(f"Failed to fetch from URL: {e}")


def generate_hash(content):
    if isinstance(content, str):
        sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    else:
        sha256_hash = hashlib.sha256(content).hexdigest()

    return sha256_hash


def extract_html_metadata(content):
    # Content is the binary data from response.content so convert it to a string
    soup = BeautifulSoup(content.decode("utf-8"), "html.parser")
    title_element = soup.find("title")
    title = title_element.get_text(strip=True) if title_element else None
    time_element = soup.find("time", {"property": "dateModified"})
    modified_at = (
        timezone.datetime.strptime(time_element.get_text(strip=True), "%Y-%m-%d")
        if time_element
        else None
    )
    return {
        "extracted_title": title,
        "extracted_modified_at": modified_at,
    }


def create_nodes(chunks, document):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    document_uuid = document.uuid_hex
    data_source_uuid = document.data_source.uuid_hex

    # Create a document (parent) node
    metadata = {"node_type": "document", "data_source_uuid": data_source_uuid}
    if document.title:
        metadata["title"] = document.title
    if document.url or document.filename:
        metadata["source"] = document.url or document.filename
    document_node = TextNode(text="", id_=document_uuid, metadata=metadata)
    document_node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
        node_id=document_node.node_id
    )

    # Create chunk (child) nodes
    metadata["node_type"] = "chunk"
    child_nodes = create_child_nodes(
        chunks,
        source_node_id=document_node.node_id,
        metadata=metadata,
    )

    # Update node properties
    new_nodes = [document_node] + child_nodes
    exclude_keys = ["page_range", "node_type", "data_source_uuid", "chunk_number"]
    for node in new_nodes:
        node.excluded_llm_metadata_keys = exclude_keys
        node.excluded_embed_metadata_keys = exclude_keys
        # The misspelling of "seperator" corresponds with the LlamaIndex codebase
        node.metadata_seperator = "\n"
        node.metadata_template = "{key}: {value}"
        node.text_template = "# {metadata_str}\ncontent:\n{content}\n\n"

    return new_nodes


def guess_content_type(
    content: str | bytes, content_type: str = None, path: str = ""
) -> str:

    if isinstance(content, bytes) and not content_type:
        if path.endswith(".msg"):
            return "application/vnd.ms-outlook"
        return None  # Unknown binary content

    if "text" in content_type and path.endswith(".md"):
        return "text/markdown"

    if content.startswith("<!DOCTYPE html>") or "<html" in content:
        return "text/html"

    if content.startswith("<?xml") or "<root" in content:
        return "application/xml"

    if content.startswith("{") or content.startswith("["):
        return "application/json"

    return "text/plain"


def get_process_engine_from_type(type):
    if "officedocument.wordprocessingml.document" in type:
        return "WORD"
    elif "officedocument.presentationml.presentation" in type:
        return "POWERPOINT"
    elif "application/vnd.ms-outlook" in type:
        return "OUTLOOK"
    elif "application/pdf" in type:
        return "PDF"
    elif "text/html" in type:
        return "HTML"
    elif "text/markdown" in type:
        return "MARKDOWN"
    else:
        return "TEXT"


def extract_markdown(
    content,
    process_engine,
    pdf_method="default",
    base_url=None,
    chunk_size=768,
    selector=None,
):
    enable_markdown = True
    if process_engine == "PDF":
        if pdf_method == "default":
            enable_markdown = False
            md = pdf_to_text_pdfium(content)
            if len(md) < 10:
                # Fallback to Azure Document Intelligence Read API to OCR
                md = pdf_to_text_azure_read(content)
        elif pdf_method == "azure_layout":
            md = pdf_to_markdown_azure_layout(content)
        elif pdf_method == "azure_read":
            enable_markdown = False
            md = pdf_to_text_azure_read(content)
    elif process_engine == "WORD":
        md = docx_to_markdown(content)
    elif process_engine == "POWERPOINT":
        md = pptx_to_markdown(content)
    elif process_engine == "HTML":
        md = html_to_markdown(content.decode("utf-8"), base_url, selector)
    elif process_engine == "MARKDOWN":
        md = content.decode("utf-8")
    elif process_engine == "OUTLOOK":
        enable_markdown = False
        md = msg_to_markdown(content)
    elif process_engine == "TEXT":
        enable_markdown = False
        md = content.decode("utf-8")

    # Strip leading/trailing whitespace; replace all >2 line breaks with 2 line breaks
    md = re.sub(r"\n{3,}", "\n\n", md.strip())

    # Divide the markdown into chunks
    try:
        md_splitter = MarkdownSplitter(
            chunk_size=chunk_size, chunk_overlap=0, enable_markdown=enable_markdown
        )
        md_chunks = md_splitter.split_markdown(md)
    except Exception as e:
        logger.debug("Error splitting markdown using MarkdownSplitter:")
        logger.error(e)
        # Fallback to simpler method
        from llama_index.core.node_parser import SentenceSplitter

        sentence_splitter = SentenceSplitter(
            chunk_size=chunk_size, chunk_overlap=min(chunk_size // 4, 100)
        )
        md_chunks = sentence_splitter.split_text(md)
    return md, md_chunks


def pdf_to_markdown_azure_layout(content):
    html = _pdf_to_html_azure_layout(content)
    return _convert_html_to_markdown(html)


def pdf_to_text_pdfium(content):
    # Fast and cheap, but no OCR or layout analysis
    import pypdfium2 as pdfium

    text = ""
    pdf = pdfium.PdfDocument(content)
    for i, page in enumerate(pdf):
        text_page = page.get_textpage()
        text += f"<page_{i+1}>\n"
        text += text_page.get_text_range() + "\n"
        text += f"</page_{i+1}>\n"
        # PyPDFium does not cleanup its resources automatically. Ensures memory freed.
        text_page.close()
    pdf.close()

    return text


def html_to_markdown(content, base_url=None, selector=None):
    return _convert_html_to_markdown(content, base_url, selector)


def msg_to_markdown(content):
    # Get the text using extract_msg command line, e.g.
    # python -m extract_msg --dump-stdout temporary_file.msg --html
    # We know we have a bytes object, so we can write it to a temporary file
    with open("temporary_file.msg", "wb") as f:
        f.write(content)
    try:
        md = subprocess.check_output(
            ["python", "-m", "extract_msg", "--dump-stdout", "temporary_file.msg"]
        ).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to extract text from Outlook email: {e}")
        md = ""
    finally:
        subprocess.run(["rm", "temporary_file.msg"])
    return md


def docx_to_markdown(content):
    import mammoth

    with io.BytesIO(content) as docx_file:
        result = mammoth.convert_to_html(docx_file)
    html = result.value

    return _convert_html_to_markdown(html)


def pptx_to_markdown(content):
    import pptx

    pptx_file = io.BytesIO(content)
    prs = pptx.Presentation(pptx_file)

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
            html += f"<h6>Presenter notes:</h6>"
            for note in slide.notes_slide.notes_text_frame.paragraphs:
                html += "<p>"
                for run in note.runs:
                    html += run.text
                html += "</p>"
        if html:
            all_html += f"<page_{i+1}>\n{html}\n</page_{i+1}>\n"

    return _convert_html_to_markdown(all_html)


def create_child_nodes(chunks, source_node_id, metadata=None):
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

    nodes = []
    for i, text in enumerate(chunks):

        node = TextNode(text=text, id_=str(uuid.uuid4()))

        node.metadata = dict(metadata, chunk_number=i)
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
    encoding = tiktoken.encoding_for_model(model)
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
        selected_html = soup.select_one(selector)
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


def _pdf_to_html_azure_layout(content):
    from azure.ai.formrecognizer import DocumentAnalysisClient
    from azure.core.credentials import AzureKeyCredential
    from shapely.geometry import Polygon

    # Note: This method handles scanned PDFs, images, and handwritten text but is $$$

    document_analysis_client = DocumentAnalysisClient(
        endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
        credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
    )

    poller = document_analysis_client.begin_analyze_document("prebuilt-layout", content)
    result = poller.result()

    num_pages = len(result.pages)
    cost = Cost.objects.new(cost_type="doc-ai-prebuilt", count=num_pages)

    # Extract table bounding regions
    table_bounding_regions = []
    for table in result.tables:
        for cell in table.cells:
            table_bounding_regions.append(cell.bounding_regions[0])

    table_chunks = []
    for _, table in enumerate(result.tables):
        page_number = table.bounding_regions[0].page_number

        # Generate table HTML syntax
        table_html = "<table>"
        for row in range(table.row_count):
            table_html += "<tr>"
            for col in range(table.column_count):
                try:
                    cell = next(
                        cell
                        for cell in table.cells
                        if cell.row_index == row and cell.column_index == col
                    )
                    table_html += "<td>{}</td>".format(cell.content)
                except StopIteration:
                    table_html += "<td></td>"
            table_html += "</tr>"
        table_html += "</table>"

        chunk = {
            "page_number": page_number,
            "x": table.bounding_regions[0].polygon[0].x,
            "y": table.bounding_regions[0].polygon[0].y,
            "text": table_html,
        }
        table_chunks.append(chunk)

    p_chunks = []
    for paragraph in result.paragraphs:
        paragraph_page_number = paragraph.bounding_regions[0].page_number
        paragraph_polygon = Polygon(
            [(point.x, point.y) for point in paragraph.bounding_regions[0].polygon]
        )

        # Check intersection between paragraph and table cells
        if any(
            paragraph_polygon.intersects(
                Polygon([point.x, point.y] for point in cell.polygon)
            )
            for cell in table_bounding_regions
            if cell.page_number == paragraph_page_number
        ):
            continue

        # If text contains words like :selected:, :checked:, or :unchecked:, then skip it
        if any(
            word in paragraph.content
            for word in [":selected:", ":checked:", ":unchecked:"]
        ):
            continue

        # Create Chunk object and append to chunks list
        chunk = {
            "page_number": paragraph_page_number,
            "x": paragraph_polygon.bounds[0],
            "y": paragraph_polygon.bounds[1],
            "text": "<p>" + paragraph.content + "</p>",
        }
        p_chunks.append(chunk)

    chunks = table_chunks + p_chunks

    # Sort chunks by page number, then by y coordinate, then by x coordinate
    chunks = sorted(
        chunks, key=lambda item: (item.get("page_number"), item.get("y"), item.get("x"))
    )
    html = ""
    cur_page = None
    for _, chunk in enumerate(chunks, 1):
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


def pdf_to_text_azure_read(content: bytes) -> str:

    from azure.ai.formrecognizer import DocumentAnalysisClient
    from azure.core.credentials import AzureKeyCredential

    document_analysis_client = DocumentAnalysisClient(
        endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
        credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
    )

    poller = document_analysis_client.begin_analyze_document("prebuilt-read", content)
    result = poller.result()

    num_pages = len(result.pages)
    cost = Cost.objects.new(cost_type="doc-ai-read", count=num_pages)

    p_chunks = []
    for page in result.pages:
        for line in page.lines:
            chunk = {
                "page_number": page.page_number,
                "text": line.content + "\n",
            }
            p_chunks.append(chunk)

    text = ""
    cur_page = None
    for _, chunk in enumerate(p_chunks, 1):
        page_start_tag = f"\n<page_{chunk.get('page_number')}>\n"
        page_end_tag = f"\n</page_{chunk.get('page_number')}>\n"
        prev_end_tag = f"\n</page_{cur_page}>\n" if cur_page is not None else ""
        if chunk.get("page_number") != cur_page:
            if cur_page is not None:
                text = text.strip() + prev_end_tag
            cur_page = chunk.get("page_number")
            text = text.strip() + page_start_tag
        text += chunk.get("text")

    if cur_page is not None and p_chunks:
        text = text.strip() + page_end_tag

    return text
