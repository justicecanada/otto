from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import InMemoryUploadedFile

import pytest

from text_extractor.tasks import process_ocr_document
from text_extractor.utils import create_searchable_pdf


@pytest.mark.django_db
def test_process_ocr_document_image(mock_image_file3):
    file_name, file_content = mock_image_file3
    merged = False
    idx = 0
    # Mock the current_task object
    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    with (mock.patch("text_extractor.tasks.current_task", current_task_mock),):

        result = process_ocr_document(file_content, file_name, merged, idx)

        # Assertions
        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert result[1] == "RIF drawing"
        assert type(result[2]) == Decimal
        assert result[2] >= 0
        assert result[3] == "temp_image"

        # Check the PDF content
        pdf_bytes = BytesIO(result[0])
        assert pdf_bytes.getvalue() != b""


@pytest.mark.django_db
def test_process_ocr_document_pdf(mock_pdf_file3):
    file_name, file_content = mock_pdf_file3
    merged = False
    idx = 0

    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    with (mock.patch("text_extractor.tasks.current_task", current_task_mock),):

        result = process_ocr_document(file_content, file_name, merged, idx)

        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert result[1] == "Page 1\nPage 2\nPage 3"
        assert type(result[2]) == Decimal
        assert result[2] >= 0
        assert result[3] == "temp_file1"

        # Check the PDF content and ensure it's not empty
        pdf_bytes = BytesIO(result[0])
        assert pdf_bytes.getvalue() != b""


# @pytest.mark.parametrize(
#     "merged, idx, expected_merged_flag",
#     [
#         (False, 0, False),
#         (True, 0, False),
#         (True, 1, True),
#     ],
# )
# def test_process_ocr_document_merged_flag(merged, idx, expected_merged_flag, mock_image_file):
#     file_content = b"%PDF-1.4 test pdf content"
#     file_name = "test.pdf"

#     # Mock the create_searchable_pdf function
#     ocr_file_mock = mock.MagicMock()
#     txt_file_mock = "test.txt"
#     cost_mock = 8.6
#     create_searchable_pdf = mock.MagicMock(
#         return_value=(ocr_file_mock, txt_file_mock, cost_mock)
#     )

#     # Mock the current_task object
#     current_task_mock = mock.MagicMock()
#     current_task_mock.update_state = mock.MagicMock()

#     with (
#         mock.patch("text_extractor.utils.create_searchable_pdf", create_searchable_pdf),
#         mock.patch("text_extractor.tasks.current_task", current_task_mock),
#     ):

#         result = process_ocr_document(file_content, file_name, merged, idx)

#         # Assertions
#         current_task_mock.update_state.assert_called_once_with(state="PROCESSING")
#         create_searchable_pdf.assert_called_once_with(mock.ANY, expected_merged_flag)
#         assert result[1] == txt_file_mock
#         assert result[2] == cost_mock
#         assert result[3] == "test"

#         # Check the PDF content
#         pdf_bytes = BytesIO(result[0])
#         ocr_file_mock.write.assert_called_once_with(pdf_bytes)
