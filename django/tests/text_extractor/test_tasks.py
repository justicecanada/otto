from decimal import Decimal
from unittest import mock

from django.core.exceptions import SynchronousOnlyOperation
from django.core.files.base import ContentFile

import pytest

from otto.secure_models import AccessKey

from text_extractor.models import InputFile, OutputFile, UserRequest
from text_extractor.tasks import (
    _run_db_operation,
    process_document_merge,
    process_ocr_document,
)


def test_run_db_operation_retries_with_async_unsafe():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise SynchronousOnlyOperation(
                "You cannot call this from an async context - use a thread or sync_to_async."
            )
        return "ok"

    assert _run_db_operation(flaky) == "ok"
    assert calls["count"] >= 2


def test_run_db_operation_propagates_non_async_error():
    class ExpectedError(Exception):
        pass

    def broken():
        raise ExpectedError("boom")

    with pytest.raises(ExpectedError, match="boom"):
        _run_db_operation(broken)


@pytest.mark.django_db
def test_process_ocr_document_image(mock_image_file3, all_apps_user):
    file_name, file_content = mock_image_file3

    # Mock the current_task object
    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    # Create an OutputFile to save results to
    from django.core.files.base import ContentFile

    from otto.secure_models import AccessKey

    from text_extractor.models import InputFile, OutputFile, UserRequest

    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key, name="test")
    output_file = OutputFile.objects.create(
        access_key=access_key, user_request=user_request, file_name="test_image"
    )
    input_file = InputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
        original_filename="temp_image.png",
        file=ContentFile(file_content, name="temp_image.png"),
        content_type="image/png",
    )

    with (
        mock.patch("text_extractor.tasks.current_task", current_task_mock),
    ):
        result = process_ocr_document(
            str(input_file.id), str(output_file.id), str(user.id)
        )

        # Assertions
        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert type(result["cost"]) is Decimal
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

    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key, name="test")
    output_file = OutputFile.objects.create(
        access_key=access_key, user_request=user_request, file_name="test_pdf"
    )
    input_file = InputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
        original_filename="temp_file1.pdf",
        file=ContentFile(file_content, name="temp_file1.pdf"),
        content_type="application/pdf",
    )

    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    with (
        mock.patch("text_extractor.tasks.current_task", current_task_mock),
    ):
        result = process_ocr_document(
            str(input_file.id), str(output_file.id), str(user.id)
        )

        current_task_mock.update_state.assert_called_once_with(state="PROCESSING")

        assert type(result["cost"]) is Decimal
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


@pytest.mark.django_db
def test_process_document_merge(
    monkeypatch,
    all_apps_user,
    mock_pdf_file3,
    mock_image_file3,
    mock_image_file4,
    DummyResult,
):
    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key)
    file_name_1, file_content_1 = mock_pdf_file3
    file_name_2, file_content_2 = mock_image_file3
    file_name_3, file_content_3 = mock_image_file4

    # Patch current_task in the task module so update_state doesn't touch Celery backend
    class DummyCurrentTask:
        def __init__(self):
            # give it a non-empty id just in case something reads it
            self.request = type("Req", (), {"id": "dummy-main-task-id"})()

        def update_state(self, *args, **kwargs):
            return None  # no-op

    monkeypatch.setattr(
        "text_extractor.tasks.current_task",
        DummyCurrentTask(),
        raising=False,
    )

    input_file_1 = InputFile.objects.create(
        access_key=access_key,
        file=ContentFile(file_content_1, name="temp_file.pdf"),
        original_filename="temp_file.pdf",
        content_type="application/pdf",
        user_request=user_request,
    )

    input_file_2 = InputFile.objects.create(
        access_key=access_key,
        file=ContentFile(file_content_2, name="temp_image.png"),
        original_filename="temp_image.png",
        content_type="image/png",
        user_request=user_request,
    )
    input_file_3 = InputFile.objects.create(
        access_key=access_key,
        file=ContentFile(file_content_3, name="tiny_image.jpg"),
        original_filename="tiny_image.jpg",
        content_type="image/jpeg",
        user_request=user_request,
    )

    output_file = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
    )

    # Stub the chained OCR task so we don't hit Celery/Azure at all
    def fake_apply_async(*args, **kwargs):
        result = DummyResult()
        result.id = "dummy-ocr-task-id"
        return result

    monkeypatch.setattr(
        "text_extractor.tasks.process_ocr_document.apply_async",
        fake_apply_async,
    )

    # Call the merge task synchronously
    result = process_document_merge(
        input_file_ids=[
            str(input_file_1.id),
            str(input_file_2.id),
            str(input_file_3.id),
        ],
        output_file_id=str(output_file.id),
        user_id=user.id,
        cost_group_id=None,
    )

    # Basic result shape
    assert result["error"] is False
    assert result["output_file_id"] == output_file.id
    assert result["chained_to"] == "dummy-ocr-task-id"
    assert "merged" in result["message"].lower()

    # OutputFile should now be updated with the chained task id
    output_file.refresh_from_db()
    assert output_file.celery_task_ids == ["dummy-ocr-task-id"]


@pytest.mark.django_db
def test_process_document_merge_unsupported_file_triggers_error(
    monkeypatch, all_apps_user, DummyResult
):
    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key)

    # Patch current_task so update_state does nothing (no Celery backend access)
    class DummyCurrentTask:
        def __init__(self):
            self.request = type("Req", (), {"id": "dummy-error-task-id"})()

        def update_state(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "text_extractor.tasks.current_task",
        DummyCurrentTask(),
        raising=False,
    )

    # Create a bogus, unsupported file (e.g., .txt)
    bad_bytes = b"not a pdf or image"
    input_file = InputFile.objects.create(
        access_key=access_key,
        file=ContentFile(bad_bytes, name="bad.txt"),
        original_filename="bad.txt",
        content_type="text/plain",
        user_request=user_request,
    )

    output_file = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
    )

    # Also patch chained OCR to avoid it being called if logic ever changes
    def fake_apply_async(*args, **kwargs):
        result = DummyResult()
        result.id = "should-not-be-used"
        return result

    monkeypatch.setattr(
        "text_extractor.tasks.process_ocr_document.apply_async",
        fake_apply_async,
    )

    # Call the task: unsupported file should raise inside try and be caught
    result = process_document_merge(
        input_file_ids=[str(input_file.id)],
        output_file_id=str(output_file.id),
        user_id=user.id,
        cost_group_id=None,
    )

    # We should be in the error path
    assert result["error"] is True
    assert "error_id" in result
    assert "message" in result
    output_file.refresh_from_db()
    assert output_file.error_message  # non-empty
    assert isinstance(output_file.error_message, str)
    assert output_file.celery_task_ids == []


@pytest.mark.django_db
def test_process_ocr_document_user_lookup_failure_sets_error(
    all_apps_user, mock_pdf_file3
):
    _file_name, file_content = mock_pdf_file3

    user = all_apps_user()
    access_key = AccessKey(user=user)
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)

    user_request = UserRequest.objects.create(access_key=access_key, name="test")
    output_file = OutputFile.objects.create(
        access_key=access_key, user_request=user_request, file_name="test_pdf"
    )
    input_file = InputFile.objects.create(
        access_key=access_key,
        user_request=user_request,
        original_filename="temp_file1.pdf",
        file=ContentFile(file_content, name="temp_file1.pdf"),
        content_type="application/pdf",
    )

    fake_user_model = mock.MagicMock()
    fake_user_model.objects.get.side_effect = SynchronousOnlyOperation(
        "You cannot call this from an async context - use a thread or sync_to_async."
    )
    current_task_mock = mock.MagicMock()
    current_task_mock.update_state = mock.MagicMock()

    with (
        mock.patch("text_extractor.tasks.current_task", current_task_mock),
        mock.patch("text_extractor.tasks.get_user_model", return_value=fake_user_model),
        mock.patch(
            "otto.utils.common.generate_ai_error_summary",
            return_value="Friendly OCR error",
        ),
    ):
        result = process_ocr_document(
            str(input_file.id), str(output_file.id), str(user.id), ai_model="gpt-5.1"
        )

    assert result["error"] is True
    output_file.refresh_from_db()
    assert output_file.error_message == "Friendly OCR error"
    assert output_file.celery_task_ids == []
