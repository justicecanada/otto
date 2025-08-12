import io
import math
import os
import tempfile
import time
import traceback
import uuid
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.shortcuts import render
from django.utils.translation import gettext as _

from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFPageCountError
from PIL import Image, ImageDraw, ImageFont, ImageSequence
from PIL.Image import Resampling
from pypdf import PdfReader, PdfWriter
from reportlab.lib import pagesizes
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from structlog import get_logger

from librarian.utils.process_engine import resize_to_azure_requirements

logger = get_logger(__name__)

from otto.models import Cost

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


def create_toc_pdf(file_names, start_pages):
    default_font = "Helvetica"
    toc_pdf_bytes = BytesIO()
    c = canvas.Canvas(toc_pdf_bytes, pagesize=A4)
    y_position = 750
    c.setFont(default_font, 12)
    c.drawString(30, y_position, "Table of Contents/Table des matières")
    y_position -= 30

    for index, file in enumerate(file_names, start=1):
        file_name = file.name  # Adjust based on your file object properties
        c.drawString(50, y_position, file_name)  # Draw the file name
        c.drawRightString(
            550, y_position, str(start_pages[file_name])
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


def get_page_count(file):
    extension = os.path.splitext(file.name)[1].lower()
    if extension == ".pdf":
        reader = PdfReader(file)
        return len(reader.pages)
    elif extension in img_extensions:
        # For image files, consider each file as one page
        return 1
    else:
        raise ValueError(f"Unsupported file type: {extension}")


def calculate_start_pages(files):
    start_pages = {}
    current_page = 2  # Start numbering from 2, bec 1 is table of contents

    for file in files:
        file_name = file.name  # Adjust based on your file object properties
        start_pages[file_name] = current_page
        current_page += get_page_count(file)

    return start_pages


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


def resize_image_to_a4(
    img, enlarge_size=None, header_text=None
):  # used only when merge is on

    dpi = 300  # for A4 size in pixels
    # Minimum readable DPI
    min_dpi = 150

    a4_width = int(8.27 * dpi)  # 8.27 inches is 210mm
    a4_height = int(11.69 * dpi)  # 11.69 inches is 297mm
    min_width = int(img.width * (min_dpi / dpi))
    min_height = int(img.height * (min_dpi / dpi))

    # Trim white borders
    img = trim_whitespace(img)

    # Calculate the scale so that the image is at least 150 DPI on the A4 page
    scale = min(a4_width / img.width, a4_height / img.height)
    min_scale = max(min_width / img.width, min_height / img.height)

    if enlarge_size == "enlarged":
        scale = min(a4_width / img.width, a4_height / img.height) * 0.85
    else:
        # Downsample, but not below minimum readable DPI
        scale = max(min_scale, 1.0)  # Don't upscale if already small

    new_width = int(img.width * scale)
    new_height = int(img.height * scale)

    # Resize the image using LANCZOS (formerly ANTIALIAS)

    resized_img = img.resize((new_width, new_height), Resampling.LANCZOS)

    # Create an A4 background
    background = Image.new("RGB", (a4_width, a4_height), "white")
    # offset = ((a4_width - new_width) // 2, (a4_height - new_width) // 2)
    offset = (
        (a4_width - new_width) // 2,
        (a4_height - new_height) // 2,
    )
    background.paste(resized_img, offset)

    # Add header if provided
    if header_text:
        draw = ImageDraw.Draw(background)
        font = ImageFont.load_default(size=48)
        header_text = f"Filename: {header_text}"
        # Position header at top with some margin
        draw.text((30, 30), header_text, fill="black", font=font)

    return background


def dist(p1, p2):
    return math.sqrt((p1.x - p2.x) * (p1.x - p2.x) + (p1.y - p2.y) * (p1.y - p2.y))


def create_searchable_pdf(input_file):
    # Reset the file pointer to the beginning
    input_file.seek(0)
    file_content = input_file.read()

    # Create a temporary file and write the contents of the uploaded file to it
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        temp.write(file_content)
        temp_path = temp.name
    try:
        input_file.name.lower().endswith(
            img_extensions
        ) or input_file.name.lower().endswith(".pdf")
    except AttributeError:
        logger.exception(
            _("AttributeError: input_file does not have a file extension.")
        )
        return {
            "error": True,
            "message": _(
                "Error: Your file's extension is not supported, please upload images or pdf files"
            ),
        }

    if input_file.name.lower().endswith(".pdf"):
        try:
            image_pages = convert_from_path(
                temp_path, dpi=100
            )  # Adjust DPI as needed for compression

            rgb_pages = [page.convert("RGB") for page in image_pages]

            # Save the compressed images to a new temporary file
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".pdf"
            ) as temp_compressed:
                start_time = time.perf_counter()
                rgb_pages[0].save(
                    temp_compressed,
                    "PDF",
                    resolution=50,
                    save_all=True,
                    append_images=rgb_pages[1:],
                )
                elapsed_time = time.perf_counter() - start_time
                # print(f"---------Multi-page save took {elapsed_time:.2f} seconds")
                logger.info(f"Multi-page save took {elapsed_time:.2f} seconds")
                temp_path = temp_compressed.name
        except PDFPageCountError as e:
            error_id = str(uuid.uuid4())[:7]
            logger.exception(
                _(
                    "PDFPageCountError while processing {input_file.name} in {error_id}: {e}"
                )
            )
            return {
                "error": True,
                "message": _(
                    "Error: The file '{input_file.name}' is not a valid PDF or is corrupted."
                ),
                "error_id": error_id,
            }
        except Exception as e:
            error_id = str(uuid.uuid4())[:7]
            logger.exception(_("Error converting pdfs into images in {error_id}: {e}"))
            # Fallback for other exceptions
            return {
                "error": True,
                "full_error": e,
                "message": _(
                    "Error ID:%(error_id)s - Error occurred while converting pdfs into images"
                )
                % {"error_id": error_id},
                "error_id": error_id,
            }

    elif input_file.name.lower().endswith(img_extensions):
        try:
            with Image.open(temp_path) as img:
                image_pages_original = ImageSequence.Iterator(img)
                image_pages = [
                    resize_to_azure_requirements(image)
                    for image in image_pages_original
                ]

        except Exception as e:
            error_id = str(uuid.uuid4())[:7]
            logger.exception(
                _("Error processing image {input_file.name} in {error_id}: {e}")
            )
            return {
                "error": True,
                "message": _(
                    "Error ID: %(error_id)s -The file '{input_file.name}' cannot be processed."
                )
                % {"error_id": error_id},
                "error_id": error_id,
            }

        # Save the resized images to a new temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_resized:
            for page in image_pages:
                page.save(temp_resized, "PDF")
            temp_path = temp_resized.name

    try:
        # Running OCR using Azure Form Recognizer Read API------
        document_analysis_client = DocumentAnalysisClient(
            endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
            headers={"x-ms-useragent": "searchable-pdf-blog/1.0.0"},
        )
        with open(temp_path, "rb") as f:
            poller = document_analysis_client.begin_analyze_document(
                "prebuilt-read", document=f
            )
        start_time_ocr = time.perf_counter()
        ocr_results = poller.result()
        elapsed_time_ocr = time.perf_counter() - start_time_ocr
        logger.info(f"OCR polling took {elapsed_time_ocr:.2f} seconds")

    except Exception as e:
        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            _("Error running Azure's document intelligence API on in {error_id}: {e}")
        )
        return {
            "error": True,
            "message": _(
                "Error ID: %(error_id)s - Azure's document intelligence API failed to process the file."
            )
            % {"error_id": error_id},
            "error_id": error_id,
        }

    num_pages = len(ocr_results.pages)
    logger.debug(
        _("Azure Form Recognizer finished OCR text for {len(ocr_results.pages)} pages.")
    )
    start_time_text = time.perf_counter()
    all_text = "\n".join(
        line.content for page in ocr_results.pages for line in page.lines
    )
    elapsed_time_text = time.perf_counter() - start_time_text
    logger.info(f"Creating txt file took {elapsed_time_text:.2f} seconds")

    start_time_overlay = time.perf_counter()
    try:
        # Generate OCR overlay layer
        output = PdfWriter()

        for page_id, page in enumerate(ocr_results.pages):
            ocr_overlay = io.BytesIO()
            # Calculate overlay PDF page size
            if image_pages[page_id].height > image_pages[page_id].width:
                page_scale = float(image_pages[page_id].height) / pagesizes.letter[1]
            else:
                page_scale = float(image_pages[page_id].width) / pagesizes.letter[1]

            page_width = float(image_pages[page_id].width) / page_scale
            page_height = float(image_pages[page_id].height) / page_scale

            scale = (page_width / page.width + page_height / page.height) / 2.0
            pdf_canvas = canvas.Canvas(ocr_overlay, pagesize=(page_width, page_height))

            # Add image into PDF page
            pdf_canvas.drawInlineImage(
                image_pages[page_id],
                0,
                0,
                width=page_width,
                height=page_height,
                preserveAspectRatio=True,
            )

            text = pdf_canvas.beginText()
            # Set text rendering mode to invisible
            text.setTextRenderMode(3)

            for word in page.words:
                # Calculate optimal font size
                desired_text_width = (
                    max(
                        dist(word.polygon[0], word.polygon[1]),
                        dist(word.polygon[3], word.polygon[2]),
                    )
                    * scale
                )
                desired_text_height = (
                    max(
                        dist(word.polygon[1], word.polygon[2]),
                        dist(word.polygon[0], word.polygon[3]),
                    )
                    * scale
                )
                font_size = desired_text_height
                actual_text_width = pdf_canvas.stringWidth(
                    word.content, default_font, font_size
                )

                # Calculate text rotation angle
                text_angle = math.atan2(
                    (
                        word.polygon[1].y
                        - word.polygon[0].y
                        + word.polygon[2].y
                        - word.polygon[3].y
                    )
                    / 2.0,
                    (
                        word.polygon[1].x
                        - word.polygon[0].x
                        + word.polygon[2].x
                        - word.polygon[3].x
                    )
                    / 2.0,
                )
                text.setFont(default_font, font_size)
                text.setTextTransform(
                    math.cos(text_angle),
                    -math.sin(text_angle),
                    math.sin(text_angle),
                    math.cos(text_angle),
                    word.polygon[3].x * scale,
                    page_height - word.polygon[3].y * scale,
                )
                text.setHorizScale(desired_text_width / actual_text_width * 100)
                text.textOut(word.content + " ")

            pdf_canvas.drawText(text)
            pdf_canvas.save()

            # Move to the beginning of the buffer
            ocr_overlay.seek(0)

            # Create a new PDF page
            new_pdf_page = PdfReader(ocr_overlay)  # changed
            output.add_page(new_pdf_page.pages[0])
    except Exception as e:
        error_id = str(uuid.uuid4())[:7]
        logger.exception(
            _(f"Error creating PDF overlay after OCR with ErrorID- {error_id}: {e}")
        )
        return {
            "error": True,
            "message": _(
                "Error ID: %(error_id)s - Failed to create PDF overlay after OCR."
            )
            % {"error_id": error_id},
            "error_id": error_id,
        }
    elapsed_time_overlay = time.perf_counter() - start_time_overlay
    logger.info(f"Creating PDF overlay took {elapsed_time_overlay:.2f} seconds")

    cost = Cost.objects.new(cost_type="doc-ai-read", count=num_pages)
    # return output, all_text, cost.usd_cost
    return {
        "error": False,
        "output": output,
        "all_text": all_text,
        "cost": cost.usd_cost,
    }


def shorten_input_name(input_name):
    base_name, file_extension = os.path.splitext(input_name)
    return str(uuid.uuid4()) + file_extension
