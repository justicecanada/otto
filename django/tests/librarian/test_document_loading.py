import os
import re

from django.conf import settings

import pytest

from librarian.utils.process_engine import (
    extract_markdown,
    get_process_engine_from_type,
    guess_content_type,
)
from otto.models import Cost

skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)

"""
Primarily we want to test the functions in librarian/utils/process_engine.py

Specifically the functions in extract_markdown:

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
"""

this_dir = os.path.dirname(os.path.abspath(__file__))


def check_page_numbers_for_example(md, md_chunks):
    assert len(md) > 0
    assert len(md_chunks) > 0
    # Check that <page_1> etc. for pages 1-4 are included exactly once
    # along with their closing tags
    for i in range(1, 5):
        assert md.count(f"<page_{i}>") == 1
        assert md.count(f"</page_{i}>") == 1
    # The same should be true of markdown chunks, except they may not have all pages
    for chunk in md_chunks:
        # Get page numbers in the chunk
        pages = re.findall(r"<page_(\d+)>", chunk)
        assert len(pages) > 0
        for i in pages:
            assert chunk.count(f"<page_{i}>") == 1
            assert chunk.count(f"</page_{i}>") == 1
    assert "<page_5>" not in md
    # Check the first chunk
    assert "<page_1>" in md_chunks[0]
    assert "Paragraph page 1" in md_chunks[0]
    # Check the last chunk
    assert "<page_4>" in md_chunks[-1]
    assert "Paragraph page 1" not in md_chunks[-1]
    # The <page_1> tag should come before the text "Paragraph page 1", and </page_1> after
    assert md.index("<page_1>") < md.index("Paragraph page 1")
    assert md.index("</page_1>") > md.index("Paragraph page 1")
    # Same for page 2
    assert md.index("<page_2>") < md.index("Paragraph page 2")
    assert md.index("</page_2>") > md.index("Paragraph page 2")


def test_extract_pdf():
    # Load a PDF file in "fast" mode (pypdfium)
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "PDF", fast=True)
        check_page_numbers_for_example(md, md_chunks)

    # Load a PDF file in "fast" mode (pypdfium) with a chunk size of 256
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "PDF", fast=True, chunk_size=256)
        check_page_numbers_for_example(md, md_chunks)


@pytest.mark.django_db
def test_extract_pdf_azure():
    # Load a PDF file in "slow" mode (Azure Form Recognizer)
    cost_count = Cost.objects.count()
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "PDF", fast=False)
        check_page_numbers_for_example(md, md_chunks)
    assert Cost.objects.count() == cost_count + 1
    for chunk in md_chunks:
        print("CHUNK_____")
        print(chunk)
        print()


def test_extract_pptx():
    # Load a PPTX file
    with open(os.path.join(this_dir, "test_files/example.pptx"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "POWERPOINT")
        # The powerpoint has the same slide numbers etc. as the PDF
        check_page_numbers_for_example(md, md_chunks)


def test_extract_docx():
    # Load a DOCX file
    with open(os.path.join(this_dir, "test_files/example.docx"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "WORD")
        # This doesn't have page numbers, but it should still have md and md_chunks
        assert len(md) > 0
        assert len(md_chunks) > 0
        assert "<page_1>" not in md
        assert "Paragraph page 1" in md
        assert "<page_1>" not in md_chunks[0]
        print(md_chunks)
        assert "Paragraph page 1" in md_chunks[0]


# HTML extraction is tested elsewhere


def test_extract_text():
    # Load a text file
    with open(os.path.join(this_dir, "test_files/example.txt"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "TEXT")
        # This doesn't have page numbers, but it should still have md and md_chunks
        assert len(md) > 0
        assert len(md_chunks) > 0
        assert "<page_1>" not in md
        assert "Paragraph page 1" in md
        assert "<page_1>" not in md_chunks[0]
        assert "Paragraph page 1" in md_chunks[0]


def test_detect_content_type():
    for filename, process_engine in [
        ("example.docx", "WORD"),
        ("example.pptx", "POWERPOINT"),
        ("example.pdf", "PDF"),
        ("example.txt", "TEXT"),
        ("example.html", "HTML"),
    ]:
        """
                Test this code:


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
        """
        with open(os.path.join(this_dir, f"test_files/{filename}"), "rb") as f:
            content = f.read()
            guessed_type = guess_content_type(content)
            process_engine = get_process_engine_from_type(guessed_type)
            assert process_engine == process_engine
