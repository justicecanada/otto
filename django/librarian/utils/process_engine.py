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
from structlog import get_logger

from otto.models import Cost

logger = get_logger(__name__)


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
    exclude_keys = ["page_range", "node_type", "data_source_uuid", "chunk_number"]
    child_nodes = create_child_nodes(
        chunks,
        source_node_id=document_node.node_id,
        metadata=metadata,
    )

    # Update node properties
    new_nodes = [document_node] + child_nodes
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


def extract_markdown(
    content, process_engine, fast=False, base_url=None, chunk_size=768, selector=None
):
    if process_engine == "PDF" and fast:
        md, md_chunks = fast_pdf_to_text(content, chunk_size)
        if len(md) < 10:
            # Fallback to Azure Document AI (fka Form Recognizer) if the fast method fails
            # since that probably means it needs OCR
            md, md_chunks = pdf_to_markdown(content, chunk_size)
    elif process_engine == "PDF":
        md, md_chunks = pdf_to_markdown(content, chunk_size)
    elif process_engine == "WORD":
        md, md_chunks = docx_to_markdown(content, chunk_size)
    elif process_engine == "POWERPOINT":
        md, md_chunks = pptx_to_markdown(content, chunk_size)
    elif process_engine == "HTML":
        md, md_chunks = html_to_markdown(
            content.decode("utf-8"), chunk_size, base_url, selector
        )
    elif process_engine == "TEXT":
        md, md_chunks = text_to_markdown(content.decode("utf-8"), chunk_size)

    # Sometimes HTML to markdown will result in zero chunks, even though there is text
    if not md_chunks:
        md_chunks = [md]
    return md, md_chunks


def pdf_to_markdown(content, chunk_size=768):
    html = _pdf_to_html_using_azure(content)
    md, nodes = _convert_html_to_markdown(html, chunk_size)
    return md, nodes


def fast_pdf_to_text(content, chunk_size=768):
    # Note: This method is faster than using Azure Form Recognizer
    # Expected it to work well for more generic scenarios but not for scanned PDFs, images, and handwritten text
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    text = ""
    for i, page in enumerate(pdf):
        text += f"<page_{i+1}>\n"
        text += page.get_textpage().get_text_range() + "\n"
        text += f"</page_{i+1}>\n"

    # We don't split the text into chunks here because it's done in create_child_nodes()
    return text, [text]


def html_to_markdown(content, chunk_size=768, base_url=None, selector=None):
    md, nodes = _convert_html_to_markdown(content, chunk_size, base_url, selector)
    return md, nodes


def text_to_markdown(content, chunk_size=768):
    # We don't split the text into chunks here because it's done in create_child_nodes()
    return content, [content]


def docx_to_markdown(content, chunk_size=768):
    import mammoth

    with io.BytesIO(content) as docx_file:
        result = mammoth.convert_to_html(docx_file)
    html = result.value
    md, nodes = _convert_html_to_markdown(html, chunk_size)
    return md, nodes


def pptx_to_markdown(content, chunk_size=768):
    import pptx

    pptx_file = io.BytesIO(content)
    prs = pptx.Presentation(pptx_file)

    # extract text from each slide
    html = ""
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    html += f"<p>{run.text}</p>"
        for note in slide.notes_slide.notes_text_frame.paragraphs:
            for run in note.runs:
                html += f"<p>{run.text}</p>"

    md, nodes = _convert_html_to_markdown(html, chunk_size)
    return md, nodes


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


def create_child_nodes(text_strings, source_node_id, metadata=None):
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode

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

    splitter = SentenceSplitter(chunk_overlap=100, chunk_size=768)

    # Create TextNode objects
    nodes = []
    split_texts = []
    for i, text in enumerate(text_strings):
        split_texts += [close_tags(t) for t in splitter.split_text(text)]

    # Now all the chunks are at most 768 tokens long, but many are much shorter
    # We want to make them a uniform size, so we'll stuff them into the previous chunk
    # (making sure the previous chunk doesn't exceed 768 tokens)
    stuffed_texts = []
    current_text = ""
    for text in split_texts:
        if token_count(f"{current_text} {text}") > 768:
            stuffed_texts.append(current_text)
            current_text = text
        else:
            current_text += " " + text

    # Append the last stuffed text if it's not empty
    if current_text:
        stuffed_texts.append(current_text)

    for i, text in enumerate(stuffed_texts):

        node = TextNode(text=text, id_=str(uuid.uuid4()))

        node.metadata = dict(metadata, chunk_number=i)
        node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(
            node_id=source_node_id
        )
        nodes.append(node)

    # Handle the case when there's only one or zero elements
    if len(text_strings) < 2:
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


def _convert_html_to_markdown(
    source_html: str, chunk_size: int = 768, base_url: str = None, selector: str = None
) -> tuple:
    """
    Converts HTML to markdown. Returns a tuple (full markdown text, list of chunks).
    <page_x> tags are not parsed here, but are preserved in the markdown text.
    """
    page_open_tags = re.findall(r"<page_\d+>", source_html)
    # When page tags (e.g. "<page_1">) are present, run this step separately for each
    # of the page contents and combine the results
    if page_open_tags:
        combined_md = ""
        combined_nodes = []
        for opening_tag in page_open_tags:
            closing_tag = opening_tag.replace("<", "</")
            page_html_contents = re.search(
                f"{opening_tag}(.*){closing_tag}", source_html, re.DOTALL
            ).group(1)
            page_md, page_nodes = _convert_html_to_markdown(
                page_html_contents, chunk_size, base_url
            )
            combined_md += f"{opening_tag}\n{page_md}\n{closing_tag}\n"
            combined_nodes += [
                f"{opening_tag}\n{node}\n{closing_tag}"
                for node in page_nodes
                if len(node)
            ]
        return combined_md, combined_nodes

    from markdownify import markdownify

    def md(text):
        """Wrapper to allow options to be passed to markdownify"""
        return markdownify(text, heading_style="ATX", bullets="*", strong_em_symbol="_")

    model = settings.DEFAULT_CHAT_MODEL

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

    # remove any javascript, css, images, svg, and comments from self.text
    text = re.sub(r"<script.*?</script>", "", str(soup), flags=re.DOTALL)
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

    # Recreate the soup object from the cleaned text
    cleaned_soup = BeautifulSoup(text, "html.parser")

    # Process paragraphs, lists, and tables
    nodes = []

    # Elements that are typically text
    header_node_names = ["h1", "h2", "h3", "h4", "h5", "h6"]
    section_node_names = ["section", "article"]
    text_node_names = ["p", "ul", "ol"]
    table_node_names = ["table"]

    # Accumulate text nodes until the chunk size is reached
    current_node = ""
    header_str = ""
    header_map = {f"h{i}": "" for i in range(1, 7)}
    for node in cleaned_soup.find_all(
        header_node_names + text_node_names + table_node_names
    ):
        # If it's a header, then append the current node and reset
        if node.name in header_node_names:
            nodes.append(md(current_node).strip())

            # Update the header map with the current header
            header_map[node.name] = str(node)

            # Clear all the headers that are smaller than the current header
            for i in range(int(node.name[1]) + 1, 7):
                header_map[f"h{i}"] = ""

            # Create the header string from the header map
            header_str = "".join(header_map.values())
            current_node = header_str

        # If it's a section or article node, then append the current node and reset
        elif node.name in section_node_names:
            nodes.append(md(current_node).strip())
            current_node = ""

        # If it's a text node, then accumulate the text until the chunk size is reached
        elif node.name in text_node_names:
            node_str = str(node)

            # Split the text by chunk size or if the node is a header
            tentative_node = md(f"{current_node}{node_str}").strip()
            if token_count(tentative_node, model) > chunk_size:
                nodes.append(tentative_node)
                # Reset the current node and include the header again for context
                current_node = f"{header_str}{node_str}"
            else:
                current_node += node_str

        # If it's a table node, then split the table by chunk size
        elif node.name in table_node_names:

            # Append the current node to the nodes list and reset the current node
            if current_node:
                nodes.append(md(current_node).strip())
                current_node = ""

            # Find the table caption and append it to the current node
            caption = node.select_one("caption")
            caption_str = str(caption) if caption else ""

            thead = node.select_one("thead")
            thead_str = str(thead) if thead else ""

            # if no thead, iterate through all the rows and find a row that has ONLY th tags as children
            if not thead:
                for row in node.find_all("tr"):
                    if all([child.name == "th" for child in row.children]):
                        thead_str = str(row)
                        break

            # Iterate through all other rows and append them to the data rows list
            data_rows = []
            for row in node.find_all("tr"):
                if str(row) not in thead_str:
                    data_rows.append(row)

            # Split the table by chunk size, preserving the header for each mini table
            current_node = f"{header_str}<p>{caption_str}</p><table>{thead_str}"
            for data_row in data_rows:
                data_str = str(data_row)

                # Split the table by chunk size
                tentative_node = md(f"{current_node}{data_str}</table>").strip()
                if token_count(tentative_node, model) > chunk_size:
                    nodes.append(tentative_node)
                    current_node = f"{header_str}<p>{caption_str}</p><table>{thead_str}"
                else:
                    current_node += data_str

            # Append the last mini table to the nodes list
            nodes.append(md(f"{current_node}</table>").strip())
            current_node = ""
    if current_node:
        nodes.append(md(current_node).strip())

    markdown = md(text).strip()
    # print([node[:100] for node in nodes])
    return markdown, nodes


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

    # Remove any duplicate chunks by looking at the text
    p_chunks = list({chunk.get("text"): chunk for chunk in p_chunks}.values())

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

    # def _fetch_from_file(self, force=False):
    #     # Logic for ChatFile fetch
    #     from chat.models import ChatFile

    #     file_obj = ChatFile.objects.get(id=self.reference)
    #     self._set_process_engine_from_type(file_obj.content_type)
    #     self.source_name = file_obj.name
    #     return file_obj.file.read()

    # Selection of hashing code from my other (incomplete) PR

    # sha256_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    # def generate_hash(self):
    #     if self.file:
    #         with self.file.open("rb") as f:
    #             sha256 = hashlib.sha256(f.read())
    #             self.sha256_hash = sha256.hexdigest()
    #             self.save()

    # self.fetched_from_source_at = timezone.now()
    # """
