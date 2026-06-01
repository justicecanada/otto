import io
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from translate.models import InputFile, OutputFile, UserRequest
from translate.tasks import translate_document_task
from translate.utils import build_source_blob_name, build_target_blob_name

from otto.secure_models import AccessKey


def _grant_translate_creates(access_key):
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)


class _FakeAzureStorage:
    def __init__(self):
        self.files = {}
        self.deleted_paths = []

    def save(self, path, content):
        self.files[path] = content.read()
        return path

    def open(self, path, mode="rb"):
        return io.BytesIO(self.files[path])

    def delete(self, path):
        self.deleted_paths.append(path)
        self.files.pop(path, None)


@pytest.mark.django_db
def test_translate_document_task_uses_shared_container_temp_paths(
    all_apps_user, monkeypatch, settings
):
    user = all_apps_user()
    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request",
        source_lang="en",
        target_lang="fr",
    )
    input_file = InputFile.objects.create(
        access_key=access_key,
        file=SimpleUploadedFile(
            "Factum and Court Analysis.docx",
            b"hello world",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        original_filename="Factum and Court Analysis.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        user_request=user_request,
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="Factum and Court Analysis.docx",
        user_request=user_request,
        celery_task_ids=["celery-task-id"],
    )

    settings.AZURE_AI_SERVICES_ENDPOINT = "https://example.cognitiveservices.azure.com/"
    settings.AZURE_AI_SERVICES_KEY = "translation-key"
    settings.AZURE_ACCOUNT_NAME = "account123"
    settings.AZURE_CONTAINER = "shared-translation-container"
    settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT = "temp/translation/in"
    settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT = "temp/translation/out"

    fake_storage = _FakeAzureStorage()
    settings.AZURE_STORAGE = fake_storage

    current_states = []
    monkeypatch.setattr(
        "translate.tasks.current_task",
        SimpleNamespace(
            update_state=lambda **kwargs: current_states.append(kwargs["state"])
        ),
    )

    file_bytes = b"hello world"
    expected_source_blob_name = build_source_blob_name(
        input_file.original_filename, "en", file_bytes
    )
    expected_target_blob_name = build_target_blob_name(
        input_file.original_filename, "fr", file_bytes
    )

    captured = {}

    class _FakePoller:
        details = SimpleNamespace(total_characters_charged=321)

        def result(self):
            return [
                SimpleNamespace(
                    status="Succeeded",
                    error=None,
                    characters_charged=321,
                )
            ]

    class _FakeDocumentTranslationClient:
        def __init__(self, endpoint, credential):
            captured["endpoint"] = endpoint
            captured["credential"] = credential

        def begin_translation(
            self,
            source_url,
            target_url,
            target_language,
            storage_type=None,
            category_id=None,
        ):
            captured["source_url"] = source_url
            captured["target_url"] = target_url
            captured["target_language"] = target_language
            captured["storage_type"] = storage_type
            captured["category_id"] = category_id
            target_blob_path = target_url.split(f"/{settings.AZURE_CONTAINER}/", 1)[1]
            fake_storage.files[target_blob_path] = b"bonjour le monde"
            return _FakePoller()

    monkeypatch.setattr(
        "translate.tasks.DocumentTranslationClient",
        _FakeDocumentTranslationClient,
    )

    created_costs = []

    def _fake_create_cost(**kwargs):
        created_costs.append(kwargs)
        return SimpleNamespace(usd_cost=0.42)

    monkeypatch.setattr("translate.tasks.Cost.objects.new", _fake_create_cost)

    translate_document_task(
        input_file_id=str(input_file.id),
        output_file_id=str(output_file.id),
        user_id=str(user.id),
        target_lang="fr",
        custom_translator_id="custom-category-id",
    )

    refreshed_output = OutputFile.objects.get(access_key=access_key, id=output_file.id)
    refreshed_output.file.open("rb")
    translated_bytes = refreshed_output.file.read()

    assert captured["endpoint"] == settings.AZURE_AI_SERVICES_ENDPOINT
    assert "/translate-source/" not in captured["source_url"]
    assert "/translate-target/" not in captured["target_url"]
    assert "?" not in captured["source_url"]
    assert "?" not in captured["target_url"]
    assert captured["source_url"].startswith(
        "https://account123.blob.core.windows.net/"
        "shared-translation-container/temp/translation/in/"
    )
    assert captured["target_url"].startswith(
        "https://account123.blob.core.windows.net/"
        "shared-translation-container/temp/translation/out/"
    )
    assert expected_source_blob_name in captured["source_url"]
    assert expected_target_blob_name in captured["target_url"]
    assert captured["target_language"] == "fr-ca"
    assert captured["storage_type"] == "File"
    assert captured["category_id"] == "custom-category-id"
    assert current_states == ["IDLING", "UPLOADING", "TRANSLATING", "FINISHED"]
    assert created_costs == [{"cost_type": "translate-custom", "count": 321}]
    assert refreshed_output.file_name == "Factum and Court Analysis_fr.docx"
    assert refreshed_output.usd_cost == pytest.approx(0.42)
    assert translated_bytes == b"bonjour le monde"
    assert any(
        path.startswith("temp/translation/in/") for path in fake_storage.deleted_paths
    )
    assert any(
        path.startswith("temp/translation/out/") for path in fake_storage.deleted_paths
    )
