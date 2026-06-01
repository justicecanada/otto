import os
import tempfile
from uuid import uuid4

from django.conf import settings

import pytest
from azure.ai.translation.document import DocumentTranslationClient
from azure.core.credentials import AzureKeyCredential

"""
Integration test for Azure Document Translation via Azure AI Services.

This test validates that the Azure AI Services endpoint and credentials are correctly configured.
It creates a real temporary file, uploads it to Azure Blob Storage, and submits a translation
job to the Document Translation service.

To run: pytest tests/chat/test_translate_azure_integration.py -v -s

Note: This test requires:
- AZURE_AI_SERVICES_KEY to be set in .env
- AZURE_AI_SERVICES_ENDPOINT to be set in .env
- Azure Blob Storage account access configured
- The otto container to be accessible
"""

"""
Live Azure Document Translation integration tests.

These are **skipped by default**. To run locally, set:
    RUN_LIVE_AZURE_TESTS=1
and ensure you are **not** on GitHub Actions (GITHUB_ACTIONS != "true").

Rationale: GitHub-hosted runners come from untrusted IPs; we only run live
cloud calls from developer workstations.
"""

RUN_LIVE_AZURE_TESTS = os.environ.get("RUN_LIVE_AZURE_TESTS") == "1"
ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
SKIP_REASON = "Live Azure translation tests are skipped unless RUN_LIVE_AZURE_TESTS=1 and not on GitHub Actions"


def get_ai_services_translation_endpoint() -> str | None:
    return os.environ.get("AZURE_AI_SERVICES_ENDPOINT")


@pytest.mark.skipif(not RUN_LIVE_AZURE_TESTS or ON_GITHUB, reason=SKIP_REASON)
@pytest.mark.django_db
def test_azure_translation_client_initialization():
    """Test that the DocumentTranslationClient can be initialized with configured credentials."""
    # Verify that the settings have the required values
    endpoint = get_ai_services_translation_endpoint()
    assert endpoint, "AI Services translation endpoint not set"
    assert settings.AZURE_AI_SERVICES_KEY, "AZURE_AI_SERVICES_KEY not set"

    # Attempt to create the client - this will fail if endpoint/key are wrong
    try:
        client = DocumentTranslationClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(settings.AZURE_AI_SERVICES_KEY),
        )
        assert client is not None
    except Exception as e:
        pytest.fail(f"Failed to initialize DocumentTranslationClient: {e}")


@pytest.mark.skipif(not RUN_LIVE_AZURE_TESTS or ON_GITHUB, reason=SKIP_REASON)
@pytest.mark.django_db
def test_azure_blob_storage_access():
    """Test that files can be uploaded to Azure Blob Storage."""
    # Create a temporary test file
    with tempfile.NamedTemporaryFile(
        delete=False, mode="w", suffix=".txt"
    ) as temp_file:
        temp_file.write("Test content for translation.")
        file_path = temp_file.name

    try:
        # Verify storage is configured
        assert settings.AZURE_STORAGE is not None, "AZURE_STORAGE not configured"
        assert settings.AZURE_ACCOUNT_NAME, "AZURE_ACCOUNT_NAME not set"
        assert settings.AZURE_CONTAINER, "AZURE_CONTAINER not set"

        # Test file upload
        test_path = f"test-integration/{os.path.basename(file_path)}"
        with open(file_path, "rb") as f:
            settings.AZURE_STORAGE.save(test_path, f)

        # Verify file exists in storage
        assert settings.AZURE_STORAGE.exists(test_path), (
            f"File {test_path} not found in storage"
        )

        # Clean up
        settings.AZURE_STORAGE.delete(test_path)

    finally:
        os.remove(file_path)


@pytest.mark.skipif(not RUN_LIVE_AZURE_TESTS or ON_GITHUB, reason=SKIP_REASON)
@pytest.mark.django_db
def test_translation_job_submission():
    """Test submitting a real translation job to Azure Document Translation service.

    This test mimics the translate_file task in tasks.py to validate the full workflow.
    """
    # Create a temporary test file with simple English text
    with tempfile.NamedTemporaryFile(
        delete=False, mode="w", suffix=".txt", encoding="utf-8"
    ) as temp_file:
        temp_file.write("Hello, this is a test file for translation to French.")
        file_path = temp_file.name

    azure_storage = settings.AZURE_STORAGE
    file_name = os.path.basename(file_path)
    input_file_name = file_name.replace(" ", "_")
    file_extension = os.path.splitext(input_file_name)[1]
    file_name_without_extension = os.path.splitext(input_file_name)[0]
    target_language = "fr-ca"
    output_file_name = (
        f"{file_name_without_extension}_{target_language.upper()}{file_extension}"
    )

    file_uuid = uuid4()
    input_file_path = (
        f"test-integration/translation-input/{file_uuid}/{input_file_name}"
    )
    output_file_path = (
        f"test-integration/translation-output/{file_uuid}/{output_file_name}"
    )

    try:
        # Upload the file to Azure Blob Storage (matches tasks.py)
        with open(file_path, "rb") as f:
            azure_storage.save(input_file_path, f)

        # Build the source and target blob URLs (matches tasks.py)
        source_url = (
            f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/"
            f"{settings.AZURE_CONTAINER}/{input_file_path}"
        )
        target_url = (
            f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/"
            f"{settings.AZURE_CONTAINER}/{output_file_path}"
        )

        # Create translation client and submit job (matches tasks.py)
        endpoint = get_ai_services_translation_endpoint()
        assert endpoint, "AI Services translation endpoint not set"
        translation_client = DocumentTranslationClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(settings.AZURE_AI_SERVICES_KEY),
        )

        poller = translation_client.begin_translation(
            source_url,
            target_url,
            target_language,
            storage_type="File",
        )

        # Wait for completion (matches tasks.py)
        result = poller.result()

        # Validate documents all succeeded (matches tasks.py)
        documents = list(result)
        assert documents, "No documents returned from translation result"

        for document in documents:
            if document.status == "Succeeded":
                # Verify we can read the translated file (matches tasks.py approach)
                assert azure_storage.exists(output_file_path), (
                    f"Translated output blob not found: {output_file_path}"
                )
                with azure_storage.open(output_file_path) as f:
                    content = f.read()
                    assert content, "Translated file is empty"
                    assert len(content) > 0, "Translated file has no content"
            else:
                pytest.fail(f"Translation failed: {document.error.message}")

    finally:
        # Clean up: remove test files from storage
        try:
            if azure_storage.exists(input_file_path):
                azure_storage.delete(input_file_path)
            if azure_storage.exists(output_file_path):
                azure_storage.delete(output_file_path)
        except Exception:
            pass  # Ignore cleanup errors

        os.remove(file_path)
