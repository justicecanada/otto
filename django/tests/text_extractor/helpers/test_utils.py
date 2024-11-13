from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile

import pytest

from text_extractor.models import OutputFile
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


@pytest.mark.django_db
@patch("text_extractor.utils.process_ocr_document.AsyncResult")
@patch("text_extractor.utils.ContentFile")
@patch("text_extractor.models.OutputFile.save")
def test_add_extracted_files_single_task_id(
    mock_save, mock_content_file, mock_async_result
):
    # Setup
    mock_result = MagicMock()
    mock_result.get.return_value = (
        b"pdf_content",
        "txt_content",
        10.0,
        "input_name.pdf",
    )
    mock_async_result.return_value = mock_result

    output_file = MagicMock()
    output_file.celery_task_ids = ["task_id_1"]
    access_key = MagicMock()

    # Call the function
    add_extracted_files(output_file, access_key)

    # Assertions
    mock_async_result.assert_called_once_with("task_id_1")
    mock_result.get.assert_called_once()
    mock_content_file.assert_any_call(b"pdf_content", name="input_name.pdf")
    mock_content_file.assert_any_call(
        "txt_content".encode("utf-8"), name="input_name.txt"
    )
    output_file.save.assert_called_once_with(access_key=access_key)
    assert output_file.usd_cost == 10.0
    assert output_file.celery_task_ids == []


@pytest.mark.django_db
@patch("text_extractor.utils.process_ocr_document.AsyncResult")
@patch("text_extractor.utils.ContentFile")
@patch("text_extractor.models.OutputFile.save")
def test_add_extracted_files_multiple_task_ids(
    mock_save, mock_content_file, mock_async_result
):
    # Setup
    mock_result_1 = MagicMock()
    mock_result_1.get.return_value = (
        b"pdf_content_1",
        "txt_content_1",
        5.0,
        "input_name_1.pdf",
    )
    mock_result_2 = MagicMock()
    mock_result_2.get.return_value = (
        b"pdf_content_2",
        "txt_content_2",
        7.0,
        "input_name_2.pdf",
    )
    mock_async_result.side_effect = [mock_result_1, mock_result_2]

    output_file = MagicMock()
    output_file.celery_task_ids = ["task_id_1", "task_id_2"]
    access_key = MagicMock()

    # Call the function
    add_extracted_files(output_file, access_key)

    # Assertions
    assert mock_async_result.call_count == 2
    assert mock_result_1.get.call_count == 1
    assert mock_result_2.get.call_count == 1
    assert mock_content_file.call_count == 2  # Called twice for PDF and TXT
    output_file.save.assert_called_with(access_key=access_key)
    assert output_file.usd_cost == 12.0
    assert output_file.celery_task_ids == []


@pytest.mark.django_db
@patch("text_extractor.utils.process_ocr_document.AsyncResult")
@patch("text_extractor.utils.ContentFile")
@patch("text_extractor.models.OutputFile.save")
def test_add_extracted_files_task_failure(
    mock_save, mock_content_file, mock_async_result
):
    # Setup
    mock_result = MagicMock()
    mock_result.get.side_effect = Exception("Task failed")
    mock_async_result.return_value = mock_result

    output_file = MagicMock()
    output_file.celery_task_ids = ["task_id_1"]
    access_key = MagicMock()

    # Call the function
    with pytest.raises(Exception):
        add_extracted_files(output_file, access_key)

    # Assertions
    mock_async_result.assert_called_once_with("task_id_1")
    mock_result.get.assert_called_once()
    output_file.save.assert_called_with(access_key=access_key)
    assert output_file.status == "FAILURE"
