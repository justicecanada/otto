import io
import math
import os
import tempfile
import uuid
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.shortcuts import render

from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from pdf2image import convert_from_path
from PIL import Image, ImageSequence
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
    default_font = "Times-Roman"
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


def resize_image_to_a4(img, dpi=300):  # used only when merge is on
    a4_width = int(8.27 * dpi)  # 8.27 inches is 210mm
    a4_height = int(11.69 * dpi)  # 11.69 inches is 297mm

    scale = min(a4_width / img.width, a4_height / img.height)

    if scale < 1.0:
        # Image is bigger than A4 in at least one dimension; scale it down
        new_width = int(img.width * scale)
        new_height = int(img.height * scale)
    else:
        # Image already fits A4 or is smaller; keep its original size
        new_width = img.width
        new_height = img.height

    # Resize the image using LANCZOS (formerly ANTIALIAS)

    resized_img = img.resize((new_width, new_height), Resampling.LANCZOS)

    # Create an A4 background
    background = Image.new("RGB", (a4_width, a4_height), "white")
    offset = ((a4_width - new_width) // 2, (a4_height - new_height) // 2)
    background.paste(resized_img, offset)
    return background


def dist(p1, p2):
    return math.sqrt((p1.x - p2.x) * (p1.x - p2.x) + (p1.y - p2.y) * (p1.y - p2.y))


def create_searchable_pdf(input_file, add_header, merged=False):
    # Reset the file pointer to the beginning
    input_file.seek(0)
    file_content = input_file.read()

    # Create a temporary file and write the contents of the uploaded file to it
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        temp.write(file_content)
        temp_path = temp.name

    if input_file.name.lower().endswith(".pdf"):
        image_pages = convert_from_path(
            temp_path, dpi=100
        )  # Adjust DPI as needed for compression

        # Save the compressed images to a new temporary file
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf"
        ) as temp_compressed:
            for page in image_pages:
                page.save(
                    temp_compressed, "PDF", resolution=50
                )  # Adjust resolution as needed
            temp_path = temp_compressed.name

    elif input_file.name.lower().endswith(img_extensions):
        if merged:
            with Image.open(temp_path) as img:
                image_pages_original = ImageSequence.Iterator(img)
                image_pages = [
                    resize_image_to_a4(image) for image in image_pages_original
                ]
        else:
            with Image.open(temp_path) as img:
                image_pages_original = ImageSequence.Iterator(img)
                image_pages = [
                    resize_to_azure_requirements(image)
                    for image in image_pages_original
                ]

        # Save the resized images to a new temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_resized:
            for page in image_pages:
                page.save(temp_resized, "PDF")
            temp_path = temp_resized.name

    else:

        raise ValueError(
            f"Unsupported file type:{input_file}. Supported extensions: .pdf or {img_extensions}"
        )

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

    ocr_results = poller.result()

    num_pages = len(ocr_results.pages)
    logger.debug(
        f"Azure Form Recognizer finished OCR text for {len(ocr_results.pages)} pages."
    )
    all_text = []
    for page in ocr_results.pages:
        for line in page.lines:
            all_text.append(line.content)

    all_text = "\n".join(all_text)

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

        # add header
        if add_header:
            header_text = f"Filename: {str(input_file)}"
            pdf_canvas.setFont(default_font, 10)
            pdf_canvas.drawString(30, page_height - 30, header_text)

        pdf_canvas.drawText(text)
        pdf_canvas.save()

        # Move to the beginning of the buffer
        ocr_overlay.seek(0)

        # Create a new PDF page
        new_pdf_page = PdfReader(ocr_overlay)  # changed
        output.add_page(new_pdf_page.pages[0])

    cost = Cost.objects.new(cost_type="doc-ai-read", count=num_pages)
    return output, all_text, cost.usd_cost


def shorten_input_name(input_name):
    base_name, file_extension = os.path.splitext(input_name)
    return str(uuid.uuid4()) + file_extension


def add_extracted_files(output_file, access_key):
    from .tasks import process_ocr_document

    # Update the OutputFile objects with the generated PDF and TXT files
    # Set the celery task IDs to [] when finished

    total_cost = 0

    if len(output_file.celery_task_ids) == 1:
        # Single file processing/ not merged
        task_id = output_file.celery_task_ids[0]
        result = process_ocr_document.AsyncResult(task_id).get()
        # pdf_bytes_content, txt_file_content, cost, input_name = result.get()
        if result.get("error"):
            # Handle the error case
            logger.error(
                f"Task {task_id} failed with error: {result['message']} (Error ID: {result['error_id']})"
            )
            output_file.status = "FAILURE"
            output_file.error_message = result["message"]
            output_file.save(access_key=access_key)
            return  # Exit early since the task failed

        # Handle the success case
        pdf_bytes_content = result["pdf_bytes"]
        txt_file_content = result["txt_file"]
        cost = result["cost"]
        input_name = result["input_name"]
        # double check if input name has extension, maybe this is already done
        output_name = shorten_input_name(input_name)

        output_file.pdf_file = ContentFile(pdf_bytes_content, name=f"{output_name}.pdf")
        output_file.txt_file = ContentFile(
            txt_file_content.encode("utf-8"),
            name=shorten_input_name(f"{output_name}.txt"),
        )

        total_cost += cost

    else:
        merged_pdf_writer = PdfWriter()
        merged_text_content = ""

        for task_id in output_file.celery_task_ids:
            result = process_ocr_document.AsyncResult(task_id).get()
            if result.get("error"):
                # Handle the error case for individual tasks
                logger.error(
                    f"Task {task_id} failed with error: {result['message']} (Error ID: {result['error_id']})"
                )
                output_file.status = "FAILURE"
                output_file.save(access_key=access_key)
                continue  # Skip this task and continue with others
            pdf_bytes_content = result["pdf_bytes"]
            txt_file_content = result["txt_file"]
            cost = result["cost"]
            # try:
            #     pdf_bytes_content, txt_file_content, cost, input_name = result.get()
            # except Exception as e:
            #     output_file.status = "FAILURE"
            #     output_file.save(access_key=access_key)
            #     logger.error(f"Task {task_id} failed with error: {e}")
            #     continue  # Skip this task and continue with others

            # Accumulate total cost
            total_cost += cost

            try:
                pdf_reader = PdfReader(BytesIO(pdf_bytes_content))
                for page in pdf_reader.pages:
                    merged_pdf_writer.add_page(page)
            except Exception as e:
                logger.error(f"Failed to parse PDF from task {task_id}: {e}")
                continue  # Skip this PDF and continue with others

            # Accumulate text content
            merged_text_content += txt_file_content + "\n"

        # Write merged PDF to BytesIO
        merged_pdf_bytes_io = BytesIO()
        try:
            merged_pdf_writer.write(merged_pdf_bytes_io)
        except Exception as e:
            logger.error(f"Failed to write merged PDF: {e}")
            output_file.status = "FAILURE"
            output_file.save(access_key=access_key)
            raise e
        merged_pdf_bytes_io.seek(0)  # Reset pointer to the start

        # Assign merged PDF and text content to output_file
        merged_pdf_content = merged_pdf_bytes_io.read()
        output_file.pdf_file = ContentFile(
            merged_pdf_content, name=shorten_input_name("merged_output.pdf")
        )
        output_file.txt_file = ContentFile(
            merged_text_content.encode("utf-8"),
            name=shorten_input_name("merged_output.txt"),
        )

    # Clear the task IDs and update cost
    output_file.celery_task_ids = []
    output_file.usd_cost = total_cost
    output_file.save(access_key=access_key)

    return output_file
