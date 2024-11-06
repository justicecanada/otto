import os
import re

from django.conf import settings

import pytest
from structlog import get_logger

from librarian.utils.process_engine import extract_markdown
from otto.models import Cost

skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)

this_dir = os.path.dirname(os.path.abspath(__file__))

logger = get_logger(__name__)


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
        md, md_chunks = extract_markdown(content, "PDF", pdf_method="default")
        check_page_numbers_for_example(md, md_chunks)

    # Load a PDF file in "fast" mode (pypdfium) with a chunk size of 256
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(
            content, "PDF", pdf_method="default", chunk_size=256
        )
        check_page_numbers_for_example(md, md_chunks)


@pytest.mark.django_db
def test_extract_pdf_azure_read():
    # Load a PDF file in "slow" mode (Azure Form Recognizer)
    cost_count = Cost.objects.count()
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "PDF", pdf_method="azure_read")
        check_page_numbers_for_example(md, md_chunks)
    assert Cost.objects.count() == cost_count + 1


@pytest.mark.django_db
def test_extract_pdf_azure_layout():
    # Load a PDF file in "slow" mode (Azure Form Recognizer)
    cost_count = Cost.objects.count()
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "PDF", pdf_method="azure_layout")
        check_page_numbers_for_example(md, md_chunks)
    assert Cost.objects.count() == cost_count + 1


def test_extract_pptx():
    # Load a PPTX file
    with open(os.path.join(this_dir, "test_files/example.pptx"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "POWERPOINT")
        logger.debug(md)
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
        assert "Paragraph page 1" in md_chunks[0]
        # Check that there are "previous headings" breadcrumbs included in later chunks
        # but not the first chunk
        assert not md_chunks[0].startswith("<headings>")
        assert md_chunks[1].startswith("<headings>")
        # Check that the headings in the first chunk are present in the second chunk
        # as breadcrumbs (not headings)
        assert "# Heading level 1, on page 1" in md_chunks[0]
        assert "# Heading level 1, on page 1" not in md_chunks[1]
        assert "Heading level 1, on page 1" in md_chunks[1]
        assert "## Heading level 2, on page 2" in md_chunks[0]
        assert "## Heading level 2, on page 2" not in md_chunks[1]
        assert "Heading level 2, on page 2" in md_chunks[1]


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
