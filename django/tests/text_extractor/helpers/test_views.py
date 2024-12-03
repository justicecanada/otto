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


@pytest.mark.django_db
def test_submit_document_view(client, all_apps_user, process_ocr_document_mock):
    mock_delay, mock_async_result = process_ocr_document_mock
    user = all_apps_user()
    client.force_login(user)
    file = SimpleUploadedFile(
        "file.pdf", b"file_content", content_type="application/pdf"
    )
    response = client.post(
        reverse("text_extractor:submit_document"), {"file_upload": [file]}
    )
    assert response.status_code == 200
    assert "output_files" in response.context
    assert len(response.context["output_files"]) > 0
    assert mock_delay.called


@pytest.mark.django_db
def test_poll_tasks_view(client, all_apps_user, process_ocr_document_mock):
    mock_delay, mock_async_result = process_ocr_document_mock
    user = all_apps_user()
    client.force_login(user)

    # Create a mock UserRequest object
    user_request_id = uuid.uuid4()
    user_request = mock.MagicMock()
    user_request.id = user_request_id
    user_request.status = "SUCCESS"

    with mock.patch(
        "text_extractor.models.UserRequest.objects.get", return_value=user_request
    ):
        response = client.get(
            reverse("text_extractor:poll_tasks", args=[str(user_request.id)])
        )
        assert response.status_code == 200
        assert user_request.status == "SUCCESS"


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
