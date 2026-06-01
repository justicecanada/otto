import hashlib
from datetime import datetime, timedelta, timezone

from django.conf import settings

import requests
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    generate_blob_sas,
)
from structlog import get_logger

logger = get_logger(__name__)

SOURCE_CONTAINER = "translate-source"
TARGET_CONTAINER = "translate-target"


def translate_text_azure(text, source_lang, target_lang):
    """Call Azure Translator Text API v3.0 to translate a string."""
    if not text or not text.strip():
        return ""
    url = f"{settings.AZURE_AI_SERVICES_ENDPOINT}translator/text/v3.0/translate"
    params = {"api-version": "3.0", "from": source_lang, "to": target_lang}
    headers = {
        "Ocp-Apim-Subscription-Key": settings.AZURE_AI_SERVICES_KEY,
        "Ocp-Apim-Subscription-Region": settings.AZURE_AI_SERVICES_REGION,
        "Content-Type": "application/json",
    }
    response = requests.post(
        url, params=params, headers=headers, json=[{"Text": text}], timeout=30
    )
    response.raise_for_status()
    return response.json()[0]["translations"][0]["text"]


def _file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()  # noqa: S324 — used as cache key, not security


def build_source_blob_name(original_filename, source_lang, file_bytes):
    """Match C# naming: {source_lang}_{md5hash}_{filename}"""
    return (
        f"{source_lang}_{_file_hash(file_bytes)}_{original_filename.replace(' ', '_')}"
    )


def build_target_blob_name(original_filename, target_lang, file_bytes):
    """Match C# naming: {target_lang}_{md5hash}_{filename}"""
    return (
        f"{target_lang}_{_file_hash(file_bytes)}_{original_filename.replace(' ', '_')}"
    )


def get_blob_service_client():
    return BlobServiceClient(
        account_url=f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net",
        credential=settings.AZURE_ACCOUNT_KEY,
    )


def _blob_sas_url(container, blob_name, permission, expiry_hours=2):
    sas = generate_blob_sas(
        account_name=settings.AZURE_ACCOUNT_NAME,
        container_name=container,
        blob_name=blob_name,
        account_key=settings.AZURE_ACCOUNT_KEY,
        permission=permission,
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )
    return f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{container}/{blob_name}?{sas}"


def generate_source_sas_url(container, blob_name):
    return _blob_sas_url(container, blob_name, BlobSasPermissions(read=True))


def generate_target_sas_url(container, blob_name):
    return _blob_sas_url(
        container, blob_name, BlobSasPermissions(read=True, write=True, create=True)
    )
