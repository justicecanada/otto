from io import BytesIO
from unittest import mock

from text_extractor.utils import (
    _gpt_extract_page,
    _process_pages_parallel,
    create_toc_pdf,
    dist,
    format_merged_file_name,
    resize_image_to_a4,
)


def test_format_merged_file_name():
    # Test when all file names fit
    file_names_to_merge = ["file1", "file2", "file3"]
    merged_file_name = format_merged_file_name(file_names_to_merge)
    assert merged_file_name == "Merged_file1_file2_file3"

    max_length = 4  # set a max_length so that no file names fit
    merged_file_name = format_merged_file_name(file_names_to_merge, max_length)
    assert merged_file_name == "Merged_3_files"

    max_length = 15  # set a max_length so that only some file names fit
    merged_file_name = format_merged_file_name(file_names_to_merge, max_length)
    assert merged_file_name == "Merged_file1_file2_and_1_more"


def test_resize_image_to_a4(mock_image_file2):
    dpi = 100
    a4_width, a4_height = int(8.27 * dpi), int(11.69 * dpi)

    # Call the function under test
    resized_img = resize_image_to_a4(mock_image_file2)

    # Assert the size of the returned image is A4
    assert resized_img.size == (
        a4_width,
        a4_height,
    ), "The resized image does not match A4 size"

    # Assert the mode of the returned image is "RGB"
    assert resized_img.mode == "RGB", "The mode of the resized image is not RGB"


def test_dist():
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    p1 = Point(0, 0)
    p2 = Point(3, 4)
    assert dist(p1, p2) == 5

    p1 = Point(-3, -4)
    p2 = Point(0, 0)
    assert dist(p1, p2) == 5  # Testing with negative points

    p1 = Point(-3, 4)
    p2 = Point(3, -4)
    assert dist(p1, p2) == 10  # Testing with points in different quadrants

    p1 = Point(-1, -1)
    p2 = Point(-4, -5)
    assert dist(p1, p2) == 5  # Both points negative, distance should still be 5

    p1 = Point(-3, 0)
    p2 = Point(0, 4)
    assert dist(p1, p2) == 5  # One point negative, the other positive


def test_create_toc_pdf():
    # Test creating a TOC with file names and page numbers
    file_names_and_pages = [
        ("file1.pdf", 2),
        ("file2.pdf", 5),
        ("file3.pdf", 10),
    ]

    toc_pdf = create_toc_pdf(file_names_and_pages)

    # Verify it returns a BytesIO object
    assert isinstance(toc_pdf, BytesIO)

    # Verify it contains PDF content
    toc_pdf.seek(0)
    content = toc_pdf.read()
    assert content.startswith(b"%PDF"), "Should be a valid PDF"
    assert len(content) > 0, "PDF should have content"


def test_gpt_extract_page_returns_page_num_and_text():
    """_gpt_extract_page returns (page_num, text) from sync LLM call."""
    from PIL import Image

    fake_response = mock.MagicMock()
    fake_response.message.content = "Hello world"

    fake_llm = mock.MagicMock()
    fake_llm.llm.chat.return_value = fake_response

    img = Image.new("RGB", (100, 100), "white")
    page_num, text = _gpt_extract_page(fake_llm, img, 0)

    assert page_num == 0
    assert text == "Hello world"
    fake_llm.llm.chat.assert_called_once()


def test_process_pages_parallel_collects_results():
    """_process_pages_parallel returns sorted results from thread pool."""
    from PIL import Image

    fake_response = mock.MagicMock()
    fake_response.message.content = "page text"

    fake_llm = mock.MagicMock()
    fake_llm.llm.chat.return_value = fake_response

    images = [(0, Image.new("RGB", (50, 50))), (1, Image.new("RGB", (50, 50)))]
    results, failed = _process_pages_parallel(fake_llm, images, max_concurrency=2)

    assert len(results) == 2
    assert failed == []
    assert fake_llm.llm.chat.call_count == 2


def test_process_pages_parallel_handles_failures():
    """_process_pages_parallel records failures without crashing."""
    from PIL import Image

    fake_llm = mock.MagicMock()
    fake_llm.llm.chat.side_effect = RuntimeError("API error")

    images = [(0, Image.new("RGB", (50, 50)))]
    results, failed = _process_pages_parallel(fake_llm, images, max_concurrency=1)

    assert len(results) == 1
    assert results[0] == (0, "[Error extracting this page]")
    assert failed == [1]  # 1-based page number


def test_process_pages_parallel_handles_empty_input():
    fake_llm = mock.MagicMock()
    results, failed = _process_pages_parallel(fake_llm, [], max_concurrency=2)

    assert results == []
    assert failed == []
