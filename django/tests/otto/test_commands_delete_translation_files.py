from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command


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
