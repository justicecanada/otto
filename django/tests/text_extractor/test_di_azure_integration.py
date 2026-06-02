"""
Live Azure Document Intelligence integration tests.

These are **skipped by default**. To run locally, set:
    RUN_LIVE_DI_TESTS=1
and ensure you are **not** on GitHub Actions (GITHUB_ACTIONS != "true").

Rationale: GitHub-hosted runners come from untrusted IPs; we only run live
cloud calls from developer workstations.
"""

import os
import tempfile

from django.conf import settings

import pytest
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentContentFormat
from azure.core.credentials import AzureKeyCredential

RUN_LIVE_DI_TESTS = os.environ.get("RUN_LIVE_DI_TESTS") == "1"
ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
SKIP_REASON = (
    "Live DI tests are skipped unless RUN_LIVE_DI_TESTS=1 and not on GitHub Actions"
)


@pytest.mark.skipif(not RUN_LIVE_DI_TESTS or ON_GITHUB, reason=SKIP_REASON)
@pytest.mark.django_db
def test_di_client_initialization():
    assert settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT, (
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT not set"
    )
    assert settings.AZURE_DOCUMENT_INTELLIGENCE_KEY, (
        "AZURE_DOCUMENT_INTELLIGENCE_KEY not set"
    )

    try:
        client = DocumentIntelligenceClient(
            endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )
        assert client is not None
    except Exception as e:
        pytest.fail(f"Failed to initialize DocumentIntelligenceClient: {e}")


@pytest.mark.skipif(not RUN_LIVE_DI_TESTS or ON_GITHUB, reason=SKIP_REASON)
@pytest.mark.django_db
def test_di_basic_analysis():
    """Submit a minimal PDF to prebuilt-read and ensure we get content back."""
    # Build a tiny PDF in-memory
    from reportlab.pdfgen import canvas

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        c = canvas.Canvas(tmp_pdf.name)
        c.drawString(100, 750, "Hello Document Intelligence")
        c.save()
        pdf_path = tmp_pdf.name

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    try:
        client = DocumentIntelligenceClient(
            endpoint=settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )

        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=pdf_bytes,
            output_content_format=DocumentContentFormat.TEXT,
        )
        result = poller.result()

        content = (
            result["content"]
            if isinstance(result, dict)
            else getattr(result, "content", "")
        )
        assert content, "No content returned from DI"
        assert "Hello Document Intelligence" in content
        assert len(result.pages) >= 1

        # Verify page-level content exists
        pages_have_content = any(
            getattr(p, "spans", None)
            or getattr(p, "lines", None)
            or getattr(p, "words", None)
            for p in result.pages
        )
        assert pages_have_content, "No page-level content returned"
    except Exception as e:
        pytest.fail(f"Document Intelligence analysis failed: {e}")
    finally:
        os.unlink(pdf_path)
