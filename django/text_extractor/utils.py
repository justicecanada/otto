import math
import os
import textwrap
import uuid
from io import BytesIO

from django.utils.translation import gettext as _

from PIL import Image, ImageSequence
from PIL.Image import Resampling
from structlog import get_logger

from chat.llm import OttoLLM

logger = get_logger(__name__)
default_font = "Helvetica"
img_extensions = (".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp")


def format_merged_file_name(file_names_to_merge, max_length=35):
    # sort files by shortest name to longest name
    file_names_to_merge.sort(key=len)

    joined_file_names = ""
    extra_files = 0
    for file_name in file_names_to_merge:
        if len(joined_file_names) + len(file_name) <= max_length:
            joined_file_names += file_name + "_"
        else:
            extra_files += 1
    joined_file_names = joined_file_names.rstrip("_")
    if len(joined_file_names) == 0:
        merged_file_name = (
            f"Merged_{extra_files}_files" if extra_files > 1 else "Merged_1_file"
        )
    elif extra_files > 0:
        merged_file_name = f"Merged_{joined_file_names}_and_{extra_files}_more"
    else:
        merged_file_name = f"Merged_{joined_file_names}"
    return merged_file_name


def create_toc_pdf(file_names_and_pages):
    """
    Create a table of contents PDF.

    Args:
        file_names_and_pages: List of tuples (file_name, start_page)
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    default_font = "Helvetica"
    max_filename_length = 72  # Character limit to prevent overlap with page numbers

    toc_pdf_bytes = BytesIO()
    c = canvas.Canvas(toc_pdf_bytes, pagesize=A4)
    y_position = 750
    c.setFont(default_font, 12)
    c.drawString(30, y_position, "Table of Contents/Table des matières")
    y_position -= 30

    for file_name, start_page in file_names_and_pages:
        # Truncate filename if too long and add ellipsis
        if len(file_name) > max_filename_length:
            truncated_name = file_name[: max_filename_length - 3] + "..."
        else:
            truncated_name = file_name

        c.drawString(50, y_position, truncated_name)  # Draw the file name
        c.drawRightString(
            550, y_position, str(start_page)
        )  # Draw the page number right-aligned
        y_position -= 20
        if y_position < 50:  # Start a new page if there's no room
            c.showPage()
            c.setFont(default_font, 12)
            y_position = 750

    c.showPage()
    c.save()
    toc_pdf_bytes.seek(0)
    return toc_pdf_bytes


def trim_whitespace(img, margin=10, bg_threshold=230):
    # Convert to grayscale for easier thresholding
    gray = img.convert("L")
    # Create a binary mask to separate background: 0 for background (light), 255 for content (dark)
    mask = gray.point(lambda x: 0 if x > bg_threshold else 255, mode="1")
    bbox = mask.getbbox()  # finds the smallest bbox that has all contents inside

    # Expanding the bbox thats found by a margin on all sides but not crossing the images boundaries
    if bbox:
        left = max(bbox[0] - margin, 0)
        upper = max(bbox[1] - margin, 0)
        right = min(bbox[2] + margin, img.width)
        lower = min(bbox[3] + margin, img.height)
        return img.crop((left, upper, right, lower))
    return img  # No border found


def resize_image_to_a4(img):  # used only when merge is on
    # Fixed A4 dimensions at exactly 100 DPI
    a4_width = 827  # 8.27 inches * 100 DPI
    a4_height = 1169  # 11.69 inches * 100 DPI

    # Trim white borders
    img = trim_whitespace(img)

    # Calculate the scale so that the image fits on the A4 page
    scale = min(a4_width / img.width, a4_height / img.height)
    scale = min(scale, 1.0)

    new_width = int(img.width * scale)
    new_height = int(img.height * scale)

    # Resize the image using LANCZOS (formerly ANTIALIAS)
    resized_img = img.resize((new_width, new_height), Resampling.LANCZOS)

    # Create an A4 background
    background = Image.new("RGB", (a4_width, a4_height), "white")
    offset = (
        (a4_width - new_width) // 2,
        (a4_height - new_height) // 2,
    )
    background.paste(resized_img, offset)

    return background


def dist(p1, p2):
    return math.sqrt((p1.x - p2.x) * (p1.x - p2.x) + (p1.y - p2.y) * (p1.y - p2.y))


def shorten_input_name(input_name):
    base_name, file_extension = os.path.splitext(input_name)
    return str(uuid.uuid4()) + file_extension


def _gpt_extract_page(
    llm: OttoLLM, page_img: Image.Image, page_num: int
) -> tuple[int, str]:
    """Extract text from a single page using GPT Vision (synchronous).

    Args:
        llm: OttoLLM instance
        page_img: PIL Image of the page
        page_num: Page number (for ordering results)

    Returns:
        Tuple of (page_num, extracted_text)
    """
    import base64

    from llama_index.core.llms import ChatMessage, ImageBlock, MessageRole, TextBlock
    from llama_index.core.schema import Document, MediaResource

    # Keep RGB to preserve color information (important for handwriting detection)
    buffer = BytesIO()
    # Save with high quality settings - minimize compression to preserve detail
    page_img.save(buffer, format="PNG", compress_level=6, optimize=False)
    buffer.seek(0)
    img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")

    prompt = (
        "Extract all legible text visible in this image. Return plain text only in reading order. "
        "Do NOT repeat these instructions or add commentary. "
        "If there is no visible text, return '[No text found]'"
    )

    image_document = Document(image_resource=MediaResource(data=img_data))
    # Use detail="high" for better handwriting recognition and text extraction quality
    chat_message = ChatMessage(
        role=MessageRole.USER,
        blocks=[
            TextBlock(text=prompt),
            ImageBlock(image=image_document.image_resource.data, detail="high"),
        ],
    )

    logger.debug("Requesting GPT Vision for page extraction", page_num=page_num)
    response = llm.llm.chat([chat_message])

    text = ""
    if hasattr(response, "message") and hasattr(response.message, "content"):
        text = str(response.message.content or "")
    elif hasattr(response, "content"):
        text = str(response.content or "")
    else:
        text = str(response or "")

    return (page_num, text)


def _process_pages_parallel(
    llm: OttoLLM, page_images: list[tuple[int, Image.Image]], max_concurrency: int = 20
) -> tuple[list[tuple[int, str]], list[int]]:
    """Process multiple pages in parallel using a thread pool.

    Under gevent, ThreadPoolExecutor threads are cooperative greenlets, so
    I/O-bound API calls yield naturally — giving the same parallelism as
    asyncio without any event loop conflicts.

    Args:
        llm: OttoLLM instance
        page_images: List of (page_num, image) tuples
        max_concurrency: Maximum number of concurrent requests (default 20)

    Returns:
        Tuple of (page_results, failed_page_nums)
        - page_results: List of (page_num, text) tuples
        - failed_page_nums: List of page numbers that failed extraction
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    processed_results = []
    failed_page_nums = []

    if not page_images:
        return processed_results, failed_page_nums

    workers = min(max_concurrency, len(page_images))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_page = {
            executor.submit(_gpt_extract_page, llm, page_img, page_num): page_num
            for page_num, page_img in page_images
        }
        for future in as_completed(future_to_page):
            page_num = future_to_page[future]
            try:
                processed_results.append(future.result())
            except Exception as exc:
                logger.warning(
                    "Failed to extract page",
                    page_num=page_num,
                    error=str(exc),
                )
                processed_results.append((page_num, "[Error extracting this page]"))
                failed_page_nums.append(page_num + 1)  # +1 for 1-based page numbers

    return processed_results, failed_page_nums


def gpt_extract_text(
    file_name: str, file_content: bytes
) -> tuple[str, float, list[int]]:
    """Use GPT Vision to extract text from images or PDFs.

    Performance notes:
    - Preserves RGB color information (important for handwriting detection)
    - Processes pages in parallel with max 20 concurrent requests for efficiency
    - Uses detail="high" for better text extraction quality
    - Does not build searchable PDF overlay (reserved for future enhancement)

    Args:
        file_name: Name of the input file
        file_content: Binary content of the file

    Returns:
        Tuple of (extracted_text, usd_cost, failed_page_nums)
        - extracted_text: Combined text from all pages (with warning header if failures)
        - usd_cost: Cost of extraction
        - failed_page_nums: List of 1-based page numbers that failed extraction (empty if all succeeded)
    """
    llm = OttoLLM(deployment="gpt-5.1", reasoning_effort="none")
    is_pdf = file_content[:5] == b"%PDF-" or (
        file_name and file_name.lower().endswith(".pdf")
    )

    fitz_module = None
    page_images: list[tuple[int, Image.Image]] = []

    extraction_error = None
    try:
        if is_pdf:
            try:
                import fitz as pymupdf  # PyMuPDF
            except ImportError:
                logger.warning(
                    "PyMuPDF not installed; falling back to image mode for GPT vision"
                )
                is_pdf = False
            else:
                fitz_module = pymupdf

        if is_pdf and fitz_module:
            doc = fitz_module.open(stream=file_content, filetype="pdf")
            try:
                for page_num in range(doc.page_count):
                    page = doc.load_page(page_num)
                    # Use dpi=200 for high quality extraction, especially important for handwriting
                    pix = page.get_pixmap(dpi=200)
                    image_bytes = BytesIO(pix.tobytes("png"))
                    with Image.open(image_bytes) as img:
                        # Keep RGB to preserve color information for better handwriting recognition
                        rgb_img = img.convert("RGB")
                    page_images.append((page_num, rgb_img.copy()))
            finally:
                doc.close()
        else:
            with Image.open(BytesIO(file_content)) as img:
                frames = [frame.copy() for frame in ImageSequence.Iterator(img)]
                if not frames:
                    frames = [img.copy()]
            for idx, frame in enumerate(frames):
                # Keep RGB to preserve color information for better handwriting recognition
                rgb_img = frame.convert("RGB")
                if hasattr(frame, "close"):
                    frame.close()
                page_images.append((idx, rgb_img.copy()))

        # Process all pages in parallel with concurrency limit
        logger.info(
            f"Processing {len(page_images)} pages in parallel",
            num_pages=len(page_images),
        )
        page_results, failed_page_nums = _process_pages_parallel(
            llm, page_images, max_concurrency=20
        )

        # Sort by page number and extract text
        page_results.sort(key=lambda x: x[0])
        page_texts = [text for _, text in page_results]

    except Exception as exc:
        logger.exception("GPT Vision extraction failed")
        extraction_error = exc
        page_texts = []
        failed_page_nums = []

    if not page_texts:
        if extraction_error:
            raise RuntimeError(f"GPT Vision extraction failed: {extraction_error}")
        raise ValueError("No text extracted using GPT Vision")

    # Add warning header if some pages failed
    warning_header = ""
    if failed_page_nums:
        if len(failed_page_nums) == 1:
            warning_header = f"{_('WARNING')}: {_('Page')} {failed_page_nums[0]} {_('failed to extract')}.\n\n"
        else:
            pages_list = ", ".join(str(p) for p in failed_page_nums)
            warning_header = f"{_('WARNING')}: {_('Pages')} {pages_list} {_('failed to extract')}.\n\n"

    combined_text = (
        warning_header
        + "\n\n".join(
            f"--- Page {idx + 1} ---\n{text}" for idx, text in enumerate(page_texts)
        ).strip()
    )
    # try: #keep for later, dont delete
    #     pdf_bytes = _build_pdf_with_text_overlay(
    #         page_images, page_texts, file_name=file_name
    #     )
    # except Exception:
    #     logger.exception(
    #         "Failed to build PDF overlay for GPT output; returning source bytes"
    #     )
    #     pdf_bytes = file_content
    usd_cost = llm.create_costs()
    # return combined_text, pdf_bytes, usd_cost
    return combined_text, usd_cost, failed_page_nums


def _wrap_text_lines(text: str, wrap_width: int = 90) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=wrap_width))
    return lines or [""]


# dont delete,keep for later
# def _build_pdf_with_text_overlay(
#     page_images: list[Image.Image], page_texts: list[str], file_name: str | None
# ) -> bytes:
#     if not page_images:
#         raise ValueError("No images available to build PDF overlay")

#     writer = PdfWriter()
#     for idx, page_img in enumerate(page_images):
#         page_text = page_texts[idx] if idx < len(page_texts) else ""
#         width, height = page_img.size
#         ocr_overlay = BytesIO()
#         pdf_canvas = canvas.Canvas(ocr_overlay, pagesize=(width, height))
#         pdf_canvas.drawInlineImage(page_img, 0, 0, width=width, height=height)

#         def draw_header():
#             if file_name:
#                 header_text = f"Filename: {file_name}"
#                 pdf_canvas.setFont(default_font, 10)
#                 pdf_canvas.drawString(36, height - 24, header_text)

#         text_obj = pdf_canvas.beginText()
#         text_obj.setTextRenderMode(3)  # invisible text overlay
#         text_obj.setFont(default_font, 10)
#         margin_x = 36
#         margin_y = 36
#         cursor_y = height - margin_y

#         draw_header()
#         for line in _wrap_text_lines(page_text):
#             if cursor_y < margin_y:
#                 pdf_canvas.drawText(text_obj)
#                 pdf_canvas.showPage()
#                 pdf_canvas.drawInlineImage(page_img, 0, 0, width=width, height=height)
#                 draw_header()
#                 text_obj = pdf_canvas.beginText()
#                 text_obj.setTextRenderMode(3)
#                 text_obj.setFont(default_font, 10)
#                 cursor_y = height - margin_y

#             text_obj.setTextOrigin(margin_x, cursor_y)
#             text_obj.textLine(line)
#             cursor_y -= 12

#         pdf_canvas.drawText(text_obj)
#         pdf_canvas.save()
#         ocr_overlay.seek(0)
#         new_pdf_page = PdfReader(ocr_overlay)
#         writer.add_page(new_pdf_page.pages[0])
#         if hasattr(page_img, "close"):
#             page_img.close()

#     buffer = BytesIO()
#     writer.write(buffer)
#     buffer.seek(0)
#     return buffer.getvalue()
