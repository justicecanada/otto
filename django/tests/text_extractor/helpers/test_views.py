from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

import pytest

from otto.models import Cost
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


# permission issues in the code below
# @pytest.mark.django_db
# def test_poll_tasks_view(client, all_apps_user, process_ocr_document_mock):
#     mock_delay, mock_async_result = process_ocr_document_mock
#     user = all_apps_user()
#     client.force_login(user)
#     output_file = OutputFile.objects.create(
#         access_key=AccessKey(user=user),
#         pdf_file=None,
#         txt_file=None,
#         file_name="test_file",
#         user_request=UserRequest.objects.create(
#             access_key=AccessKey(user=user), merged=False, name="testuser"
#         ),
#         celery_task_ids=["mock_task_id"],
#         status="PENDING",
#     )
#     response = client.get(
#         reverse("text_extractor:poll_tasks", args=[output_file.user_request.id])
#     )
#     assert response.status_code == 200
#     output_file.refresh_from_db()
#     assert output_file.status == "SUCCESS"
#     assert mock_async_result.called


# @pytest.mark.django_db
# def test_download_document_view(client, user, access_key, user_request):
#     client.force_login(user)
#     output_file = OutputFile.objects.create(
#         access_key=access_key,
#         pdf_file=SimpleUploadedFile(
#             "file.pdf", b"file_content", content_type="application/pdf"
#         ),
#         txt_file=SimpleUploadedFile(
#             "file.txt", b"file_content", content_type="text/plain"
#         ),
#         file_name="test_file",
#         user_request=user_request,
#         celery_task_ids=["task_id_1"],
#         status="SUCCESS",
#     )
#     response = client.get(
#         reverse("text_extractor:download_document", args=[output_file.id, "pdf"])
#     )
#     assert response.status_code == 200
#     assert response["Content-Disposition"] == 'attachment; filename="test_file.pdf"'
#     assert response.content == b"file_content"

#     response = client.get(
#         reverse("text_extractor:download_document", args=[output_file.id, "txt"])
#     )
#     assert response.status_code == 200
#     assert response["Content-Disposition"] == 'attachment; filename="test_file.txt"'
#     assert response.content == b"file_content"
# ----------------------------------------------------------------------------------


# old code:
# # These tests are failing on GitHub and DevOps due to authorization issues
# @skip_on_github_actions
# @skip_on_devops_pipeline
# def test_merged_document_submission(client, all_apps_user, mock_pdf_file):
#     user = all_apps_user()
#     client.force_login(user)
#     num_costs = Cost.objects.count()

#     response = client.get(reverse("text_extractor:index"))
#     assert response.status_code == 200

#     # Prepare the data for the POST request
#     data = {"file_upload": mock_pdf_file, "merge_docs_checkbox": "on"}

#     # Make a POST request to the submit_document view
#     response = client.post(reverse("text_extractor:submit_document"), data)

#     # Check if the response status code is 200
#     assert response.status_code == 200

#     # Check that the response text does not contain "error"
#     assert "error" not in response.content.decode().lower()

#     # Check if the response contains the expected context data
#     assert "ocr_docs" in response.context
#     assert "user_request_id" in response.context

#     # TODO: Why is the TOC page being OCR'd? This incurs cost but is not needed.
#     # assert Cost.objects.count() == num_costs + 1

#     last_cost = Cost.objects.order_by("id").last()
#     assert last_cost.feature == "text_extractor"
#     assert last_cost.count == 3  # 3 pages


# # These tests are failing on GitHub and DevOps due to authorization issues
# @skip_on_github_actions
# @skip_on_devops_pipeline
# def test_document_submission_and_download(client, all_apps_user, mock_pdf_file):
#     user = all_apps_user()
#     client.force_login(user)
#     num_costs = Cost.objects.count()

#     response = client.get(reverse("text_extractor:index"))
#     assert response.status_code == 200

#     # Prepare the data for the POST request
#     data = {"file_upload": mock_pdf_file}

#     # Make a POST request to the submit_document view
#     response = client.post(reverse("text_extractor:submit_document"), data)

#     # Check if the response status code is 200
#     assert response.status_code == 200

#     # Check that the response text does not contain "error"
#     assert "error" not in response.content.decode().lower()

#     # Check if the response contains the expected context data
#     assert "ocr_docs" in response.context
#     assert "user_request_id" in response.context

#     assert Cost.objects.count() == num_costs + 1

#     last_cost = Cost.objects.order_by("id").last()
#     assert last_cost.feature == "text_extractor"
#     assert last_cost.count == 3  # 3 pages

#     # Test the download view: "download_document/<str:file_id>/<str:user_request_id>",
#     user_request_id = response.context["user_request_id"]
#     docs = response.context["ocr_docs"]
#     assert len(docs) == 1

#     # There should be two properties on the doc: "pdf" and "txt". Each has an ID.
#     pdf_file_id = docs[0]["pdf"]["file"].id
#     txt_file_id = docs[0]["txt"]["file"].id

#     # Make a GET request to the download_document view
#     response = client.get(
#         reverse("text_extractor:download_document", args=[pdf_file_id, user_request_id])
#     )
#     # Validate these properties:
#     #    response = HttpResponse(file.read(), content_type="application/octet-stream")
#     #    response["Content-Disposition"] = (
#     #        f'attachment; filename="{output_file.file_name}"'
#     #    )
#     assert response.status_code == 200
#     assert response["Content-Type"] == "application/octet-stream"
#     assert "attachment" in response["Content-Disposition"].lower()
#     # Same for Text file
#     response = client.get(
#         reverse("text_extractor:download_document", args=[txt_file_id, user_request_id])
#     )
#     assert response.status_code == 200
#     assert response["Content-Type"] == "application/octet-stream"
#     assert "attachment" in response["Content-Disposition"].lower()

#     # Try a random File ID; this should return the error message ("error" in the response)
#     import uuid

#     random_id = uuid.uuid4()
#     response = client.get(
#         reverse("text_extractor:download_document", args=[random_id, user_request_id])
#     )
#     assert "error" in response.content.decode().lower()
