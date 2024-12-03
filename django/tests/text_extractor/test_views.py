import uuid
from unittest import mock
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

import pytest

from otto.models import Cost
from otto.secure_models import AccessKey
from text_extractor.models import OutputFile, UserRequest
from text_extractor.views import *

pytest_plugins = ("pytest_asyncio",)
skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)


@pytest.mark.django_db
def test_index_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("text_extractor:index"))
    assert response.status_code == 200
    # assert "extensions" in response.context


# @pytest.mark.django_db
# def test_submit_document_view(client, all_apps_user, process_ocr_document_mock):
#     mock_delay, mock_async_result = process_ocr_document_mock
#     user = all_apps_user()
#     client.force_login(user)
#     file = SimpleUploadedFile(
#         "file.pdf", b"file_content", content_type="application/pdf"
#     )
#     response = client.post(
#         reverse("text_extractor:submit_document"), {"file_upload": [file]}
#     )
#     assert response.status_code == 200
#     assert "output_files" in response.context
#     assert len(response.context["output_files"]) > 0
#     assert mock_delay.called
@pytest.mark.django_db
@pytest.mark.parametrize(
    "merged, files",
    [
        (
            False,
            [
                ("file1.pdf", b"file_content1", "application/pdf"),
                ("file2.pdf", b"file_content2", "application/pdf"),
            ],
        ),
        (
            True,
            [
                ("file1.pdf", b"file_content1", "application/pdf"),
                ("file2.pdf", b"file_content2", "application/pdf"),
            ],
        ),
        (
            False,
            [
                ("file1.txt", b"file_content1", "text/plain"),
                ("file2.txt", b"file_content2", "text/plain"),
            ],
        ),
        (
            True,
            [
                ("file1.txt", b"file_content1", "text/plain"),
                ("file2.txt", b"file_content2", "text/plain"),
            ],
        ),
    ],
)
def test_submit_document_view(
    client, all_apps_user, process_ocr_document_mock, merged, files
):
    mock_delay, mock_async_result = process_ocr_document_mock
    user = all_apps_user()
    client.force_login(user)

    uploaded_files = [
        SimpleUploadedFile(file_name, file_content, content_type=content_type)
        for file_name, file_content, content_type in files
    ]

    response = client.post(
        reverse("text_extractor:submit_document"),
        {"file_upload": uploaded_files, "merged": merged},
    )

    assert response.status_code == 200
    assert "output_files" in response.context
    assert len(response.context["output_files"]) > 0
    assert mock_delay.called


import uuid
from unittest import mock

from django.urls import reverse

import pytest


@pytest.mark.django_db
def test_poll_tasks(client, all_apps_user, output_file):
    user = all_apps_user()
    client.force_login(user)

    # Create a mock UserRequest object
    user_request_id = uuid.uuid4()
    user_request = mock.MagicMock()
    user_request.id = user_request_id
    user_request.status = "PENDING"

    # Mock the output_file object
    output_file.celery_task_ids = [str(uuid.uuid4()) for _ in range(3)]
    output_file.status = "PENDING"
    output_file.pdf_file = None
    output_file.txt_file = None
    output_file.usd_cost = 10.0

    # Mock the process_ocr_document.AsyncResult method
    async_result_mock = mock.MagicMock()
    async_result_mock.status = "SUCCESS"

    # Mock the add_extracted_files function
    add_extracted_files_mock = mock.MagicMock(return_value=output_file)

    # Mock the display_cad_cost function
    display_cad_cost_mock = mock.MagicMock(return_value="$13.80")

    # Mock the file_size_to_string function
    file_size_to_string_mock = mock.MagicMock(side_effect=lambda size: f"{size} bytes")

    with (
        mock.patch(
            "text_extractor.models.UserRequest.objects.get", return_value=user_request
        ),
        mock.patch(
            "text_extractor.models.OutputFile.objects.filter",
            return_value=[output_file],
        ),
        mock.patch(
            "text_extractor.tasks.process_ocr_document.AsyncResult",
            return_value=async_result_mock,
        ),
        mock.patch(
            "text_extractor.views.add_extracted_files", add_extracted_files_mock
        ),
        mock.patch("text_extractor.views.display_cad_cost", display_cad_cost_mock),
        mock.patch(
            "text_extractor.views.file_size_to_string", file_size_to_string_mock
        ),
    ):

        response = client.get(
            reverse("text_extractor:poll_tasks", args=[str(user_request.id)])
        )
        response_data = response.json()
        assert response_data["status"] == "SUCCESS"

        # print("User Request Status:", user_request.status)
        # print("Output File Status:", output_file.status)
        # print("Output File USD Cost:", output_file.usd_cost)
        # print("Output File TXT Size:", output_file.txt_size)
        # print("Output File PDF Size:", output_file.pdf_size)
        # assert response.status_code == 200
        # assert user_request.status == "SUCCESS"
        # assert output_file.status == "SUCCESS"
        # assert output_file.usd_cost == "$13.80"
        # assert output_file.txt_size == " bytes"  # Adjust based on actual size
        # assert output_file.pdf_size == " bytes"  # Adjust based on actual size


# @pytest.mark.django_db
# def test_poll_tasks_view(client, all_apps_user, process_ocr_document_mock):
#     mock_delay, mock_async_result = process_ocr_document_mock
#     user = all_apps_user()
#     client.force_login(user)

#     # Create a mock UserRequest object
#     user_request_id = uuid.uuid4()
#     user_request = mock.MagicMock()
#     user_request.id = user_request_id
#     user_request.status = "SUCCESS"

#     with mock.patch(
#         "text_extractor.models.UserRequest.objects.get", return_value=user_request
#     ):
#         response = client.get(
#             reverse("text_extractor:poll_tasks", args=[str(user_request.id)])
#         )
#         assert response.status_code == 200
#         assert user_request.status == "SUCCESS"


def test_download_document(client, all_apps_user, output_file):
    user = all_apps_user()
    client.force_login(user)

    with mock.patch(
        "text_extractor.models.OutputFile.objects.get", return_value=output_file
    ):

        # Test downloading PDF file
        response = client.get(
            reverse(
                "text_extractor:download_document", args=[str(output_file.id), "pdf"]
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/octet-stream"
        assert (
            response["Content-Disposition"]
            == 'attachment; filename="test_document.pdf"'
        )
        assert response.content == b"PDF content"

        # Test downloading TXT file
        response = client.get(
            reverse(
                "text_extractor:download_document", args=[str(output_file.id), "txt"]
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/octet-stream"
        assert (
            response["Content-Disposition"]
            == 'attachment; filename="test_document.txt"'
        )
        assert response.content == b"TXT content"
