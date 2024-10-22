import hashlib
import io
import re
import uuid
from urllib.parse import urljoin

from django.conf import settings
from django.utils import timezone

import filetype
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from structlog import get_logger

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
        content_type = r.headers.get("content-type")
        if content_type is None:
            content_type = guess_content_type(r.content)
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


def guess_content_type(content):
    # Check if the content is binary using filetype.guess
    detected_type = filetype.guess(content)
    if detected_type is not None:
        return detected_type.mime

    if isinstance(content, bytes):
        return None  # Unknown

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
    elif "application/pdf" in type:
        return "PDF"
    elif "text/html" in type:
        return "HTML"
    else:
        return "TEXT"


def split_markdown(text_strings, chunk_size=768):
    if type(text_strings) == str:
        text_strings = [text_strings]

    from llama_index.core.node_parser import SentenceSplitter

    def close_tags(html_string):
        # Deal with partial page number tags, if any
        # Find the first page tag (either opening or closing)
        page_tag = re.search(r"<page_\d+>|</page_\d+>", html_string)
        if page_tag:
            # If it's not an opening tag, add an opening tag at the beginning
            if not page_tag.group().startswith("<page_"):
                first_page_num = int(re.search(r"\d+", page_tag.group()).group())
                html_string = f"<page_{first_page_num}>\n{html_string}"
            # Close the last opened tag, if not closed
            opening_tags = re.findall(r"<page_\d+>", html_string)
            last_opening_tag = opening_tags[-1] if opening_tags else None
            closing_tags = re.findall(r"</page_\d+>", html_string)
            last_closing_tag = closing_tags[-1] if closing_tags else None
            # Check if last opening tag is not the same page number as the last closing tag
            if last_opening_tag and last_closing_tag:
                last_opening_tag_num = int(re.search(r"\d+", last_opening_tag).group())
                last_closing_tag_num = int(re.search(r"\d+", last_closing_tag).group())
                if last_opening_tag_num != last_closing_tag_num:
                    # Add the closing tag
                    html_string = f"{html_string}</page_{last_opening_tag_num}>"

        soup = BeautifulSoup(html_string, "html.parser")
        return str(soup)

    splitter = SentenceSplitter(chunk_overlap=100, chunk_size=chunk_size)

    split_texts = []
    for i, text in enumerate(text_strings):
        last_page_number = None
        for t in splitter.split_text(text):
            closed_text = close_tags(t)
            # Edge case: Start and end of chunk are in the middle of a page
            closing_tags = re.findall(r"</page_\d+>", closed_text)
            if last_page_number and not closing_tags:
                # If there was a closing tag on the last chunk, add it to the current chunk
                closed_text = f"<page_{last_page_number}>\n{closed_text}\n</page_{last_page_number}>"
            elif closing_tags:
                last_page_number = int(re.search(r"\d+", closing_tags[-1]).group())
            split_texts.append(closed_text)

    # Now all the chunks are at most `chunk_size` tokens long, but some may be shorter
    # We want to make them a uniform size, so we'll stuff them into the previous chunk
    # (making sure the previous chunk doesn't exceed `chunk_size` tokens)
    stuffed_texts = []
    current_text = ""
    for text in split_texts:
        if token_count(f"{current_text}\n{text}") > chunk_size:
            stuffed_texts.append(current_text)
            current_text = text
        else:
            current_text += f"\n{text}"

    # Append the last stuffed text if it's not empty
    if current_text:
        stuffed_texts.append(current_text)

    return stuffed_texts


def extract_markdown(
    content, process_engine, fast=False, base_url=None, chunk_size=768, selector=None
):
    if process_engine == "PDF" and fast:
        md = fast_pdf_to_text(content)
        if len(md) < 10:
            # Fallback to Azure Document AI (fka Form Recognizer) if the fast method fails
            # since that probably means it needs OCR
            md = pdf_to_markdown(content)
    elif process_engine == "PDF":
        md = pdf_to_markdown(content)
    elif process_engine == "WORD":
        md = docx_to_markdown(content)
    elif process_engine == "POWERPOINT":
        md = pptx_to_markdown(content)
    elif process_engine == "HTML":
        md = html_to_markdown(content.decode("utf-8"), base_url, selector)
    elif process_engine == "TEXT":
        md = content.decode("utf-8")

    # Divide the markdown into chunks
    return md, split_markdown(md, chunk_size)


def pdf_to_markdown(content):
    html = _pdf_to_html_using_azure(content)
    return _convert_html_to_markdown(html)


def fast_pdf_to_text(content):
    # Note: This method is faster than using Azure Form Recognizer
    # Expected it to work well for more generic scenarios but not for scanned PDFs, images, and handwritten text
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    text = ""
    for i, page in enumerate(pdf):
        text += f"<page_{i+1}>\n"
        text += page.get_textpage().get_text_range() + "\n"
        text += f"</page_{i+1}>\n"

    return text


def html_to_markdown(content, base_url=None, selector=None):
    return _convert_html_to_markdown(content, base_url, selector)


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


def document_summary(content):
    from langchain.chains.summarize import load_summarize_chain
    from langchain.schema import Document
    from langchain_openai import AzureChatOpenAI

    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=settings.DEFAULT_CHAT_MODEL,
        model=settings.DEFAULT_CHAT_MODEL,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0.1,
    )
    content = content[:5000]
    chain = load_summarize_chain(llm, chain_type="stuff")
    doc = Document(page_content=content, metadata={"source": "userinput"})
    summary = chain.run([doc])
    return summary


def document_title(content):
    from langchain.schema import HumanMessage
    from langchain_openai import AzureChatOpenAI

    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=settings.DEFAULT_CHAT_MODEL,
        model=settings.DEFAULT_CHAT_MODEL,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0.1,
    )
    prompt = "Generate a short title fewer than 50 characters."
    content = content[:5000]
    title = llm([HumanMessage(content=content), HumanMessage(content=prompt)]).content[
        :254
    ]
    # Remove any double quotes wrapping the title, if any
    title = re.sub(r'^"|"$', "", title)
    return title


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
    import tiktoken

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

    text = _remove_ignored_tags(str(soup))

    markdown = markdownify_wrapper(text).strip()
    return markdown


def _pdf_to_html_using_azure(content):
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
