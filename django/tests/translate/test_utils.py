from datetime import datetime, timezone

from azure.storage.blob import BlobSasPermissions
from translate import utils


def test_translate_text_azure_empty_text_returns_empty_without_api_call(monkeypatch):
    called = False

    def _fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("translate.utils.requests.post", _fake_post)

    assert utils.translate_text_azure("   ", "en", "fr") == ""
    assert called is False


def test_translate_text_azure_success_calls_api_and_returns_translation(
    monkeypatch, settings
):
    settings.AZURE_AI_SERVICES_ENDPOINT = "https://example.cognitiveservices.azure.com/"
    settings.AZURE_AI_SERVICES_KEY = "test-key"
    settings.AZURE_AI_SERVICES_REGION = "canadacentral"

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            captured["raise_for_status_called"] = True

        def json(self):
            return [{"translations": [{"text": "Bonjour"}]}]

    def _fake_post(url, params, headers, json, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("translate.utils.requests.post", _fake_post)

    result = utils.translate_text_azure("Hello", "en", "fr")

    assert result == "Bonjour"
    assert (
        captured["url"]
        == "https://example.cognitiveservices.azure.com/translator/text/v3.0/translate"
    )
    assert captured["params"] == {"api-version": "3.0", "from": "en", "to": "fr"}
    assert captured["headers"] == {
        "Ocp-Apim-Subscription-Key": "test-key",
        "Ocp-Apim-Subscription-Region": "canadacentral",
        "Content-Type": "application/json",
    }
    assert captured["json"] == [{"Text": "Hello"}]
    assert captured["raise_for_status_called"] is True


def test_file_hash_matches_md5_value():
    assert utils._file_hash(b"abc") == "900150983cd24fb0d6963f7d28e17f72"


def test_build_source_blob_name_includes_lang_hash_and_sanitized_filename():
    blob_name = utils.build_source_blob_name("my file.txt", "en", b"abc")

    assert blob_name == "en_900150983cd24fb0d6963f7d28e17f72_my_file.txt"


def test_build_target_blob_name_includes_lang_hash_and_sanitized_filename():
    blob_name = utils.build_target_blob_name("my file.txt", "fr", b"abc")

    assert blob_name == "fr_900150983cd24fb0d6963f7d28e17f72_my_file.txt"


def test_get_blob_service_client_uses_account_settings(monkeypatch, settings):
    settings.AZURE_ACCOUNT_NAME = "account123"
    settings.AZURE_ACCOUNT_KEY = "secret-key"

    captured = {}

    class _FakeBlobServiceClient:
        def __init__(self, account_url, credential):
            captured["account_url"] = account_url
            captured["credential"] = credential

    monkeypatch.setattr("translate.utils.BlobServiceClient", _FakeBlobServiceClient)

    client = utils.get_blob_service_client()

    assert isinstance(client, _FakeBlobServiceClient)
    assert captured["account_url"] == "https://account123.blob.core.windows.net"
    assert captured["credential"] == "secret-key"


def test_blob_sas_url_builds_expected_url_and_calls_generator(monkeypatch, settings):
    settings.AZURE_ACCOUNT_NAME = "account123"
    settings.AZURE_ACCOUNT_KEY = "secret-key"

    captured = {}

    def _fake_generate_blob_sas(
        account_name,
        container_name,
        blob_name,
        account_key,
        permission,
        expiry,
    ):
        captured["account_name"] = account_name
        captured["container_name"] = container_name
        captured["blob_name"] = blob_name
        captured["account_key"] = account_key
        captured["permission"] = permission
        captured["expiry"] = expiry
        return "sig=abc"

    monkeypatch.setattr("translate.utils.generate_blob_sas", _fake_generate_blob_sas)

    permission = BlobSasPermissions(read=True)
    url = utils._blob_sas_url("translate-source", "doc.txt", permission, expiry_hours=4)

    assert (
        url
        == "https://account123.blob.core.windows.net/translate-source/doc.txt?sig=abc"
    )
    assert captured["account_name"] == "account123"
    assert captured["container_name"] == "translate-source"
    assert captured["blob_name"] == "doc.txt"
    assert captured["account_key"] == "secret-key"
    assert captured["permission"] == permission
    assert isinstance(captured["expiry"], datetime)
    assert captured["expiry"].tzinfo == timezone.utc


def test_generate_source_sas_url_uses_read_permission(monkeypatch):
    captured = {}

    def _fake_blob_sas_url(container, blob_name, permission, expiry_hours=2):
        captured["container"] = container
        captured["blob_name"] = blob_name
        captured["permission"] = permission
        captured["expiry_hours"] = expiry_hours
        return "source-url"

    monkeypatch.setattr("translate.utils._blob_sas_url", _fake_blob_sas_url)

    result = utils.generate_source_sas_url("translate-source", "doc.txt")

    assert result == "source-url"
    assert captured["container"] == "translate-source"
    assert captured["blob_name"] == "doc.txt"
    assert captured["permission"].read is True
    assert captured["permission"].write is False
    assert captured["permission"].create is False
    assert captured["expiry_hours"] == 2


def test_generate_target_sas_url_uses_read_write_create_permissions(monkeypatch):
    captured = {}

    def _fake_blob_sas_url(container, blob_name, permission, expiry_hours=2):
        captured["container"] = container
        captured["blob_name"] = blob_name
        captured["permission"] = permission
        captured["expiry_hours"] = expiry_hours
        return "target-url"

    monkeypatch.setattr("translate.utils._blob_sas_url", _fake_blob_sas_url)

    result = utils.generate_target_sas_url("translate-target", "out.txt")

    assert result == "target-url"
    assert captured["container"] == "translate-target"
    assert captured["blob_name"] == "out.txt"
    assert captured["permission"].read is True
    assert captured["permission"].write is True
    assert captured["permission"].create is True
    assert captured["expiry_hours"] == 2
