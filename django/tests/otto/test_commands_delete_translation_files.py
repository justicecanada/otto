from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

import pytest
from translate.models import InputFile, OutputFile, UserRequest

from otto.secure_models import AccessKey

pytestmark = pytest.mark.django_db


def _grant_translate_creates(access_key):
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)


@patch("django.conf.settings.AZURE_STORAGE")
def test_handle_no_files(mock_storage):
    mock_storage.list_all.return_value = []

    call_command("delete_translation_files")

    mock_storage.list_all.assert_called_once()
    # Assert delete was not called since no files
    mock_storage.delete.assert_not_called()


@patch("django.conf.settings.AZURE_STORAGE")
def test_handle_with_translation_files(mock_storage):
    input_file = f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/test.txt"
    output_file = f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/test.txt"
    other_file = "other/test.txt"
    mock_storage.list_all.return_value = [input_file, output_file, other_file]

    call_command("delete_translation_files")

    mock_storage.list_all.assert_called_once()

    # Assert delete was called for translation files only
    mock_storage.delete.assert_any_call(input_file)
    mock_storage.delete.assert_any_call(output_file)
    assert mock_storage.delete.call_count == 2


@patch("django.conf.settings.AZURE_STORAGE")
def test_handle_list_error(mock_storage):
    mock_storage.list_all.side_effect = Exception("Connection error")

    call_command("delete_translation_files")

    mock_storage.list_all.assert_called_once()

    # Assert delete was not called due to error
    mock_storage.delete.assert_not_called()


@patch("django.conf.settings.AZURE_STORAGE")
def test_handle_delete_error(mock_storage):
    input_file = f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/test.txt"
    output_file = f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/test.txt"
    mock_storage.list_all.return_value = [input_file, output_file]
    mock_storage.delete.side_effect = Exception("Delete error")

    call_command("delete_translation_files")

    # Assert delete was called for files even with error
    mock_storage.list_all.assert_called_once()
    mock_storage.delete.assert_any_call(input_file)
    mock_storage.delete.assert_any_call(output_file)


def test_handle_deletes_old_translate_requests(all_apps_user):
    user = all_apps_user()
    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    old_request = UserRequest.objects.create(
        access_key=access_key,
        name="Old translation",
        source_lang="en",
        target_lang="fr",
    )
    new_request = UserRequest.objects.create(
        access_key=access_key,
        name="New translation",
        source_lang="en",
        target_lang="fr",
    )

    InputFile.objects.create(
        access_key=access_key,
        file=ContentFile(b"old-input", name="old.txt"),
        original_filename="old.txt",
        content_type="text/plain",
        user_request=old_request,
    )
    OutputFile.objects.create(
        access_key=access_key,
        file=ContentFile(b"old-output", name="old_fr.txt"),
        file_name="old_fr.txt",
        user_request=old_request,
        celery_task_ids=[],
    )

    old_request.created_at = timezone.now() - timezone.timedelta(hours=25)
    old_request.save(access_key=access_key)
    new_request.created_at = timezone.now() - timezone.timedelta(hours=1)
    new_request.save(access_key=access_key)

    call_command("delete_translation_files")

    assert not UserRequest.objects.filter(
        access_key=access_key, id=old_request.id
    ).exists()
    assert UserRequest.objects.filter(access_key=access_key, id=new_request.id).exists()
    assert not OutputFile.objects.filter(
        access_key=access_key, user_request_id=old_request.id
    ).exists()
