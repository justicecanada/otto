import pytest

from text_extractor.utils import *


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
    # DPI can be adjusted if needed
    dpi = 150
    a4_width, a4_height = int(8.27 * dpi), int(11.69 * dpi)

    # Call the function under test
    resized_img = resize_image_to_a4(mock_image_file2, dpi=dpi)

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


def test_get_page_count_pdf(mock_pdf_file):
    page_count = get_page_count(mock_pdf_file)
    assert page_count == 3, "The page count should be 3"


def test_get_page_count_image(mock_image_file):
    page_count = get_page_count(mock_image_file)
    assert page_count == 1


def test_get_page_count_unsupported(mock_unsupported_file):
    with pytest.raises(ValueError):
        get_page_count(mock_unsupported_file)


def test_calculate_start_pages_empty():
    assert calculate_start_pages([]) == {}, "Should return an empty dict for no files"


def test_calculate_start_pages_single_file(mock_pdf_file):
    files = [mock_pdf_file]
    expected = {"temp_file1.pdf": 2}
    assert (
        calculate_start_pages(files) == expected
    ), "Incorrect start page for a single file"


def test_calculate_start_pages_multiple_files(
    mock_pdf_file, mock_pdf_file2, mock_image_file
):
    expected = {"temp_file1.pdf": 2, "temp_file2.pdf": 5, "temp_image.jpg": 15}
    with mock_pdf_file, mock_pdf_file2, mock_image_file:
        files = [mock_pdf_file, mock_pdf_file2, mock_image_file]
        result = calculate_start_pages(files)
        assert result == expected, f"Expected {expected}, got {result}"
