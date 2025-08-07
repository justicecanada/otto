from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import InMemoryUploadedFile

import pytest

from text_extractor.tasks import process_ocr_document
from text_extractor.utils import create_searchable_pdf


@pytest.mark.django_db
def test_process_ocr_document_image(mock_image_file3, all_apps_user):
    file_name, file_content = mock_image_file3
    # Mock the current_task object
    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    # Create an OutputFile to save results to
    from otto.secure_models import AccessKey
    from text_extractor.models import OutputFile, UserRequest

    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key, name="test")
    output_file = OutputFile.objects.create(
        access_key=access_key, user_request=user_request, file_name="test_image"
    )

    with (mock.patch("text_extractor.tasks.current_task", current_task_mock),):

        result = process_ocr_document(
            file_content, file_name, str(output_file.id), str(user.id)
        )

        # Assertions
        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert type(result["cost"]) == Decimal
        assert result["cost"] >= 0
        assert result["input_name"] == "temp_image"
        assert result["error"] is False

        # Check that files were saved to the database
        output_file.refresh_from_db()
        assert output_file.pdf_file is not None
        assert output_file.txt_file is not None
        assert output_file.celery_task_ids == []

        # Check file contents
        with output_file.txt_file.open("r") as f:
            assert f.read() == "RIF drawing"


@pytest.mark.django_db
def test_process_ocr_document_pdf(mock_pdf_file3, all_apps_user):
    file_name, file_content = mock_pdf_file3

    # Create an OutputFile to save results to
    from otto.secure_models import AccessKey
    from text_extractor.models import OutputFile, UserRequest

    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key, name="test")
    output_file = OutputFile.objects.create(
        access_key=access_key, user_request=user_request, file_name="test_pdf"
    )

    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    with (mock.patch("text_extractor.tasks.current_task", current_task_mock),):

        result = process_ocr_document(
            file_content, file_name, str(output_file.id), str(user.id)
        )

        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert type(result["cost"]) == Decimal
        assert result["cost"] >= 0
        assert result["input_name"] == "temp_file1"
        assert result["error"] is False

        # Check that files were saved to the database
        output_file.refresh_from_db()
        assert output_file.pdf_file is not None
        assert output_file.txt_file is not None
        assert output_file.celery_task_ids == []

        # Check file contents
        with output_file.txt_file.open("r") as f:
            assert f.read() == "Page 1\nPage 2\nPage 3"
