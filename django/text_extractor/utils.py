import io
import math
import os
import tempfile
import time
import uuid
from io import BytesIO

from django.conf import settings
from django.utils.translation import gettext as _

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeOutputOption,
    DocumentContentFormat,
)
from azure.ai.vision.imageanalysis import ImageAnalysisClient
from azure.ai.vision.imageanalysis.models import VisualFeatures
from azure.core.credentials import AzureKeyCredential
from PIL import Image
from PIL.Image import Resampling
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from structlog import get_logger

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


def create_searchable_pdf(input_file, file_type):
    document_analysis_client = None
    vision_client = None
    try:
        # Prepare file bytes for Azure analysis to avoid serialization of file objects
        if hasattr(input_file, "read"):
            input_file.seek(0)
            body_data = input_file.read()
            # check if it is image or pdf
            if file_type == "application/pdf":
                document_analysis_client = DocumentIntelligenceClient(
                    endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
                    credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
                    headers={"x-ms-useragent": "searchable-pdf-blog/1.0.0"},
                )
                # Call Azure Form Recognizer with raw bytes
                poller = document_analysis_client.begin_analyze_document(
                    model_id="prebuilt-read",
                    body=body_data,
                    output=[AnalyzeOutputOption.PDF],
                    output_content_format=DocumentContentFormat.MARKDOWN,
                )
                start_time_ocr = time.perf_counter()
                ocr_results = poller.result()
                elapsed_time_ocr = time.perf_counter() - start_time_ocr
                logger.info(f"OCR polling took {elapsed_time_ocr:.2f} seconds")

            elif file_type in img_extensions or file_type.startswith("image/"):
                vision_client = ImageAnalysisClient(
                    endpoint=settings.AZURE_COGNITIVE_SERVICE_ENDPOINT,
                    credential=AzureKeyCredential(settings.AZURE_COGNITIVE_SERVICE_KEY),
                )
                # Extract text (OCR) from an image stream. This will be a synchronously (blocking) call.
                ocr_results = vision_client.analyze(
                    image_data=body_data, visual_features=[VisualFeatures.READ]
                )

    except Exception as e:
        error_id = str(uuid.uuid4())[:7]
        message = e.message if hasattr(e, "message") else str(e)
        logger.exception(
            _("Error running Azure's document intelligence API on in {error_id}: {e}")
        )
        return {
            "error": True,
            "message": _(f"Error ID: {error_id} - {message}"),
            "error_id": error_id,
        }

    if document_analysis_client:
        page_count = len(ocr_results.pages)
        logger.debug(
            _("Azure Form Recognizer finished OCR text. Number of pages:")
            + str(page_count)
        )
        cost = Cost.objects.new(cost_type="doc-ai-read", count=page_count)
        all_text = ocr_results["content"]
    else:
        # For Vision OCR, use number of images (assume 1 image per call)
        cost = Cost.objects.new(cost_type="vision-ocr", count=1)
        all_text = []
        for block in ocr_results["readResult"]["blocks"]:
            for line in block["lines"]:
                for word in line["words"]:
                    all_text.append(word["text"])

    # Get the OCR'd PDF from Azure
    print(all_text)
    pdf_content = None
    if document_analysis_client:
        start_time_pdf = time.perf_counter()
        pdf_content = document_analysis_client.get_analyze_result_pdf(
            model_id="prebuilt-read", result_id=poller.details["operation_id"]
        )
        elapsed_time_pdf = time.perf_counter() - start_time_pdf
        logger.info(f"Creating PDF file took {elapsed_time_pdf:.2f} seconds")

    return {
        "error": False,
        "pdf_content": pdf_content,
        "all_text": all_text,
        "cost": cost.usd_cost,
    }


def shorten_input_name(input_name):
    base_name, file_extension = os.path.splitext(input_name)
    return str(uuid.uuid4()) + file_extension
