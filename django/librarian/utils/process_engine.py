import hashlib
import io
import re
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


def split_markdown(
    markdown_text: str, chunk_size: int = 768, chunk_overlap=100
) -> list:
    from llama_index.core.node_parser import SentenceSplitter

    def close_tags(html_string):
        # Deal with partial page number tags, if any
        # Find the first page tag (either opening or closing)
        first_page_tag = re.search(r"<page_\d+>|</page_\d+>", html_string)
        if first_page_tag:
            # If the first tag is a closing tag, add an opening tag at the beginning
            if first_page_tag.group().startswith("</page_"):
                first_page_num = int(re.search(r"\d+", first_page_tag.group()).group())
                html_string = f"<page_{first_page_num}>\n{html_string}"
            # Close the last opened tag, if not closed
            opening_tags = re.findall(r"<page_\d+>", html_string)
            last_opening_tag = opening_tags[-1] if opening_tags else None
            closing_tags = re.findall(r"</page_\d+>", html_string)
            last_closing_tag = closing_tags[-1] if closing_tags else None
            if last_opening_tag and last_closing_tag:
                last_opening_tag_num = int(re.search(r"\d+", last_opening_tag).group())
                last_closing_tag_num = int(re.search(r"\d+", last_closing_tag).group())
                if last_opening_tag_num != last_closing_tag_num:
                    # Add the closing tag
                    html_string = f"{html_string}</page_{last_opening_tag_num}>"

        soup = BeautifulSoup(html_string, "html.parser")
        return str(soup)

    def get_heading(line):
        # Check if the line is a markdown header, meaning it starts with "# ", "# ", etc.
        # Return the header level if it is a header, otherwise return None
        match = re.match(r"^#{1,6} ", line)
        if match:
            return len(match.group()) - 1, line[match.end() :]
        return None, None

    def set_headings(headings, heading_level, heading_text):
        # Operates on the headings dictionary in place
        headings[heading_level] = heading_text
        # Clear all the headers that are smaller than the current header
        for i in range(heading_level + 1, 7):
            headings[i] = None

    def prepend_headings(headings, text, to_level=7):
        # Prepend the headers to the text
        headings2 = {k: v for k, v in headings.items() if k < to_level}
        if not any(headings2.values()):
            return text.strip()
        headings_str = f'{" > ".join([v for v in headings2.values() if v])}'
        return f"<headings>{headings_str}</headings>\n{text}".strip()

    def get_all_headings(text, existing_headings):
        lines = text.split("\n")
        headings = existing_headings.copy()
        min_level = 7
        for line in lines:
            level, heading_text = get_heading(line)
            if level is not None:
                set_headings(headings, level, heading_text)
                min_level = min(min_level, level)
        return headings, min_level

    def truncate_text_to_tokens(text, max_tokens):
        tokenizer = tiktoken.get_encoding("cl100k_base")

        # Encode the text into tokens
        tokens = tokenizer.encode(text)

        # Truncate the tokens if they exceed the max_tokens limit
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]

        # Decode the tokens back into text
        truncated_text = tokenizer.decode(tokens)
        return truncated_text

    def get_last_table_header(text: str, truncate_tokens: int = None) -> str:
        """
        Returns a string of the last table header found in markdown format.
        This is what a table looks like in the input text:
        | __Table header 1__ | Table header 2 | Table header 3 | Table header 4 |
        | --- | --- | --- | --- |
        | Data row 1, cell 1 | Data row 1, cell 2 | Data row 1, cell 3 | Data row 1, cell 4 |
        | Data row 2, cell 1 | Data row 2, cell 2 | Data row 2, cell 3 | Data row 2, cell 4 |

        Note that the text may or may not contain a table. It may contain table rows,
        but no table headers.
        It may contain text which isn't a table, then a table, then another table, then
        more text. Etc.
        """
        # Remove the overlap since this would be repeated in next chunk anyway.
        if truncate_tokens:
            text = truncate_text_to_tokens(text, truncate_tokens)
        last_table_row = ""
        last_table_header = ""
        lines = text.split("\n")
        for line in lines:
            if line.startswith("| ---") and line.endswith(" |") and last_table_row:
                last_table_header = last_table_row
                last_table_row = ""
            elif line.startswith("| ") and line.endswith(" |"):
                last_table_row = line
        return last_table_header

    def repeat_table_header_if_necessary(text: str, last_table_header: str) -> str:
        """
        Adds the last table header to the text if the text appears to start with
        a continuation of the table.
        """
        if not last_table_header:
            return text
        lines = text.split("\n")
        if len(lines) < 2:
            return text
        first_line_is_table_row = lines[0].startswith("| ") and lines[0].endswith(" |")
        first_line_is_table_header = (
            first_line_is_table_row
            and lines[1].startswith("| ---")
            and lines[1].endswith(" |")
        )
        if (
            first_line_is_table_row
            and not first_line_is_table_header
            and last_table_header
        ):
            return f"{last_table_header}\n{text}"
        return text

    splitter = SentenceSplitter(chunk_overlap=chunk_overlap, chunk_size=chunk_size)
    last_page_number = None
    split_texts = []
    for t in splitter.split_text(markdown_text):
        closed_text = close_tags(t)
        # Edge case: Start and end of chunk are in the middle of a page
        closing_tags = re.findall(r"</page_\d+>", closed_text)
        if last_page_number and not closing_tags:
            # If there was a closing tag on the last chunk, add it to the current chunk
            closed_text = (
                f"<page_{last_page_number}>\n{closed_text}\n</page_{last_page_number}>"
            )
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

    # Add previous headings to chunks under those heading
    current_headings = {i: None for i in range(1, 7)}
    headings_added_texts = []
    last_table_header = None
    for text in stuffed_texts:
        current_headings, min_level = get_all_headings(text, current_headings)
        # Repeat table headers if necessary
        text = repeat_table_header_if_necessary(text, last_table_header)
        last_table_header = get_last_table_header(
            text, truncate_tokens=chunk_size - chunk_overlap
        )
        text = prepend_headings(current_headings, text, to_level=min_level)
        headings_added_texts.append(text)

    return headings_added_texts


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
