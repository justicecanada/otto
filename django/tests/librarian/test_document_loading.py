import os
import re

from django.urls import reverse

import pytest
from openpyxl import Workbook
from structlog import get_logger

from chat.models import Chat
from librarian.models import DataSource, Document, Library
from librarian.utils.process_engine import decode_content, extract_markdown
from otto.models import Cost

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


@pytest.mark.django_db
def test_extract_outlook_msg(client, all_apps_user):
    # library = Library.objects.get_default_library()
    # user = all_apps_user()
    # client.force_login(user)
    # data_source = DataSource.objects.create(library=library)
    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:direct_upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/elephants.msg"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
    document_id = document.id
    assert document is not None
    # Load an Outlook MSG file
    with open(os.path.join(this_dir, "test_files/elephants.msg"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(
            content, "OUTLOOK_MSG", root_document_id=document_id
        )
        assert not "<page_1>" in md
        assert len(md) > 0
        assert len(md_chunks) > 0
        assert "Elephants" in md
        assert "jules.kuehn@justice.gc.ca" in md.lower()


@pytest.mark.django_db
def test_extract_png():
    # Load a PNG file
    with open(os.path.join(this_dir, "test_files/ocr-test.png"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "IMAGE")
        assert len(md) > 0
        assert len(md_chunks) == 1
        assert "Elephant" in md
        assert "Elephant" in md_chunks[0]


@pytest.mark.django_db
def test_extract_zip(client, all_apps_user):

    user = all_apps_user()
    client.force_login(user)

    chat = Chat.objects.create(user=user)
    # Ensure that a data source was created
    data_source = DataSource.objects.filter(chat=chat).first()
    assert data_source is not None
    # Upload a file to the data source
    url = reverse("librarian:direct_upload", kwargs={"data_source_id": data_source.id})
    with open(os.path.join(this_dir, "test_files/example.pdf"), "rb") as f:
        response = client.post(url, {"file": f})
        assert response.status_code == 200
    # Ensure that a document was created
    document = Document.objects.filter(data_source=data_source).first()
    document_id = document.id
    assert document is not None
    # Load a ZIP file
    with open(os.path.join(this_dir, "test_files/example.zip"), "rb") as f:
        content = f.read()
        md, md_chunks = extract_markdown(content, "ZIP", root_document_id=document_id)
        assert len(md) > 0
        assert len(md_chunks) > 0
        assert "example.txt" in md
        assert "example.txt" in md_chunks[0]
        assert "example.docx" in md
        assert "example.docx" in md_chunks[0]
        assert "example.pdf" in md
        assert "example.pdf" in md_chunks[0]
        assert "example.pptx" in md
        assert "example.pptx" in md_chunks[0]


def test_resize_to_azure_requirements():
    import io

    from PIL import Image

    from librarian.utils.process_engine import resize_to_azure_requirements

    def create_image(width, height):
        image = Image.new("RGB", (width, height), color="white")
        with io.BytesIO() as output:
            image.save(output, format="PNG")
            return output.getvalue()

    small_image = create_image(30, 40)
    small_image_2 = create_image(60, 30)
    medium_image = create_image(100, 100)
    large_image = create_image(12000, 1000)
    large_image_2 = create_image(1000, 12000)
    wide_image = create_image(12000, 40)
    tall_image = create_image(40, 12000)

    for image in [
        small_image,
        small_image_2,
        medium_image,
        large_image,
        large_image_2,
        wide_image,
        tall_image,
    ]:
        resized_image = resize_to_azure_requirements(image)
        assert resized_image is not None
        # Check that width and height are both within range (50, 10000)
        image = Image.open(io.BytesIO(resized_image))
        assert 50 <= image.width <= 10000
        assert 50 <= image.height <= 10000


def test_extract_csv():
    # Create a simple, but long CSV content
    csv_content = "Column1,Column2,Column3\n" + "\n".join(
        [f"Row{i}Col1,Row{i}Col2,Row{i}Col3" for i in range(1, 301)]
    )

    md, md_chunks = extract_markdown(csv_content.encode("utf-8"), "CSV")

    # Check that the markdown table is correctly output
    assert len(md) > 0
    assert md.startswith("| Column1 | Column2 | Column3 |")

    assert len(md_chunks) > 1

    # Check that each chunk has the table header repeated
    for chunk in md_chunks:
        assert "| Column1 | Column2 | Column3 |" in chunk.split("\n")[0]
        assert chunk.count("| Column1 | Column2 | Column3 |") == 1


def test_extract_excel():
    # Generate an Excel file with 3 sheets and 300 rows each
    wb = Workbook()
    sheets = ["SheetA", "SheetB", "SheetC"]
    for sheet_name in sheets:
        ws = wb.create_sheet(title=sheet_name)
        ws.append(
            [f"{sheet_name}Column1", f"{sheet_name}Column2", f"{sheet_name}Column3"]
        )
        for i in range(1, 301):
            ws.append(
                [
                    f"{sheet_name}Row{i}Col1",
                    f"{sheet_name}Row{i}Col2",
                    f"{sheet_name}Row{i}Col3",
                ]
            )
    wb.remove(wb["Sheet"])  # Remove the default sheet created by openpyxl
    excel_path = os.path.join(this_dir, "test_files/example.xlsx")
    wb.save(excel_path)

    # Load the generated Excel file
    with open(excel_path, "rb") as f:
        content = f.read()

    md, md_chunks = extract_markdown(content, "EXCEL")
    assert len(md) > 0
    assert len(md_chunks) > 1
    for sheet_name in sheets:
        assert f"# {sheet_name}" in md
        assert (
            f"| {sheet_name}Column1 | {sheet_name}Column2 | {sheet_name}Column3 |" in md
        )

    # Now, in each chunk, if a sheet_name is present, the corresponding h1 should be present
    # AND the table header should be present
    for chunk in md_chunks:
        num_sheets_in_chunk = 0
        for sheet_name in sheets:
            if sheet_name in chunk:
                num_sheets_in_chunk += 1
                assert (
                    f"# {sheet_name}" in chunk
                    or f"<headings>{sheet_name}</headings>" in chunk
                )
                assert (
                    f"| {sheet_name}Column1 | {sheet_name}Column2 | {sheet_name}Column3 |"
                    in chunk
                )
        assert num_sheets_in_chunk > 0

    # Clean up the generated Excel file
    os.remove(excel_path)


def test_decode_content_utf8():
    content = "Hello World".encode("utf-8")
    result = decode_content(content)
    assert result == "Hello World"


def test_decode_content_cp1252():
    # smartquote “ is 0x93 in cp1252
    content = bytes([0x93])
    result = decode_content(content)
    assert result == "“"


def test_decode_content_with_custom_encodings():
    content = "Hello World".encode("utf-16")
    with pytest.raises(Exception):
        decode_content(content, encodings=["utf-8", "ascii"])
