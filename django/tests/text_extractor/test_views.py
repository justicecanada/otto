import uuid
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest

from otto.secure_models import AccessKey
from text_extractor.models import OutputFile, UserRequest
from text_extractor.views import *

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.django_db
def test_index_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("text_extractor:index"))
    assert response.status_code == 200
    # assert "extensions" in response.context


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


@pytest.mark.django_db(transaction=True)
def test_poll_tasks_view(
    client,
    all_apps_user,
    process_ocr_document_mock,
):
    group, created = Group.objects.get_or_create(name="Otto admin")

    user = all_apps_user()
    client.force_login(user)
    access_key = AccessKey(user=user)
    content_type_user_request = ContentType.objects.get_for_model(UserRequest)

    permission_user_request, created = Permission.objects.get_or_create(
        codename="add_userrequest",
        content_type=content_type_user_request,
        name="Can add user request",
    )
    group.permissions.add(permission_user_request)
    user.groups.add(group)
    user.user_permissions.add(permission_user_request)

    user_request = UserRequest.objects.create(
        access_key=access_key, name="Test Request"
    )
    content_type_output_file = ContentType.objects.get_for_model(OutputFile)
    permission_output_file_add, created = Permission.objects.get_or_create(
        codename="add_outputfile",
        content_type=content_type_output_file,
        name="Can add output file",
    )

    user.user_permissions.add(permission_output_file_add)

    output_file1 = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
        celery_task_ids=[str(uuid.uuid4())],
        pdf_file=ContentFile(b"PDF content", name="test1.pdf"),
        txt_file=ContentFile(b"TXT content", name="test1.txt"),
        usd_cost=10.0,
    )
    output_file2 = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
        celery_task_ids=[str(uuid.uuid4())],
        pdf_file=ContentFile(b"PDF content", name="test2.pdf"),
        txt_file=ContentFile(b"TXT content", name="test2.txt"),
        usd_cost=0.0,
    )

    def async_result_side_effect(task_id):
        if task_id == output_file1.celery_task_ids[0]:
            return mock.MagicMock(status="SUCCESS")
        elif task_id == output_file2.celery_task_ids[0]:
            return mock.MagicMock(status="FAILURE")
        return mock.MagicMock(status="PENDING")

    add_extracted_files_mock = mock.MagicMock(
        side_effect=lambda output_file, access_key: output_file
    )

    def display_cad_cost_side_effect(usd_cost):
        if usd_cost == 10.0:
            return 13.80
        elif usd_cost == 0.0:
            return 0.00
        return 0.00

    display_cad_cost_mock = mock.MagicMock(side_effect=display_cad_cost_side_effect)

    # Mock the file_size_to_string function
    file_size_to_string_mock = mock.MagicMock(side_effect=lambda size: f"{size} bytes")

    with (
        mock.patch(
            "text_extractor.tasks.process_ocr_document.AsyncResult",
            side_effect=async_result_side_effect,
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

        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=utf-8"
        assert "output_files" in response.context

        output_files_res = response.context["output_files"]
        assert len(output_files_res) == 2
        #  the status and cost based on celery_task_ids
        for output_file_res in output_files_res:
            if output_file_res.celery_task_ids == output_file1.celery_task_ids:
                assert output_file_res.status == "SUCCESS"
                assert output_file_res.cost == 13.8
            elif output_file_res.celery_task_ids == output_file2.celery_task_ids:
                assert output_file_res.status == "FAILURE"
                assert output_file_res.cost == 0.0
        # Verify that celery has finished processing all tasks
        for output_file_res in output_files_res:
            assert output_file_res.status not in ["PENDING", "PROCESSING"]


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


@pytest.mark.django_db(transaction=True)
def test_download_all_zip(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    group, created = Group.objects.get_or_create(name="Otto admin")
    access_key = AccessKey(user=user)
    content_type_user_request = ContentType.objects.get_for_model(UserRequest)
    content_type_output_file = ContentType.objects.get_for_model(OutputFile)

    # Grant permissions
    permission_user_request, created = Permission.objects.get_or_create(
        codename="add_userrequest",
        content_type=content_type_user_request,
        name="Can add user request",
    )
    permission_output_file_add, created = Permission.objects.get_or_create(
        codename="add_outputfile",
        content_type=content_type_output_file,
        name="Can add output file",
    )
    group.permissions.add(permission_user_request)
    group.permissions.add(permission_output_file_add)
    user.groups.add(group)
    user.user_permissions.add(permission_user_request)
    user.user_permissions.add(permission_output_file_add)

    user_request = UserRequest.objects.create(
        access_key=access_key, name="Test Request"
    )

    # Create real OutputFile objects
    output_files = []
    for i in range(2):
        output_file = OutputFile.objects.create(
            access_key=access_key,
            user_request=user_request,
            celery_task_ids=[str(uuid.uuid4())],
            file_name=f"file_{i}",
            pdf_file=ContentFile(b"%PDF-1.4 test pdf content", name=f"file_{i}.pdf"),
            txt_file=ContentFile(b"test txt content", name=f"file_{i}.txt"),
            usd_cost=0.0,
        )
        output_files.append(output_file)

    url = reverse("text_extractor:download_all_zip", args=[str(user_request.id)])
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"

    zip_buffer = io.BytesIO(response.content)
    with zipfile.ZipFile(zip_buffer, "r") as zip_file:
        namelist = zip_file.namelist()
        expected_files = []
        for output_file in output_files:
            expected_files.append(f"{output_file.file_name}.pdf")
            expected_files.append(f"{output_file.file_name}.txt")
        assert set(namelist) == set(expected_files)
