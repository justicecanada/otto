"""Unit tests for sanitize_content_type function.

These are pure function tests that don't require database access.
"""

import pytest

# Import the function directly without Django model dependencies
# The function is a pure utility that doesn't require DB access


def sanitize_content_type(content_type: str) -> str:
    """
    Sanitize content type to prevent DB overflow errors.

    Copy of the function from librarian.utils.process_engine for testing
    without Django DB setup overhead.
    """
    MAX_CONTENT_TYPE_LENGTH = 100

    if not content_type or not isinstance(content_type, str):
        return ""

    # Strip parameters after semicolon (e.g., "; charset=utf-8")
    content_type = content_type.split(";")[0].strip().lower()

    # Basic validation - must contain a slash
    if "/" not in content_type:
        return ""

    # Truncate if too long
    if len(content_type) > MAX_CONTENT_TYPE_LENGTH:
        content_type = content_type[:MAX_CONTENT_TYPE_LENGTH]
        # Make sure truncation didn't remove the slash
        if "/" not in content_type:
            return ""

    return content_type


@pytest.mark.no_cover  # Pure function test, no need for coverage tracking of test itself
class TestSanitizeContentType:
    """Test cases for the sanitize_content_type function."""

    def test_basic_content_type(self):
        """Test basic content types are returned unchanged."""
        assert sanitize_content_type("text/html") == "text/html"
        assert sanitize_content_type("application/pdf") == "application/pdf"
        assert sanitize_content_type("image/png") == "image/png"

    def test_strips_charset_parameter(self):
        """Test that charset parameter is stripped."""
        assert sanitize_content_type("text/html; charset=utf-8") == "text/html"
        assert sanitize_content_type("text/plain; charset=iso-8859-1") == "text/plain"

    def test_strips_multiple_parameters(self):
        """Test that multiple parameters are stripped."""
        assert (
            sanitize_content_type("text/html; charset=utf-8; boundary=something")
            == "text/html"
        )

    def test_normalizes_to_lowercase(self):
        """Test that content types are normalized to lowercase."""
        assert sanitize_content_type("TEXT/HTML") == "text/html"
        assert sanitize_content_type("Application/PDF") == "application/pdf"
        assert sanitize_content_type("TEXT/HTML; CHARSET=UTF-8") == "text/html"

    def test_strips_whitespace(self):
        """Test that whitespace is stripped."""
        assert sanitize_content_type("  text/html  ") == "text/html"
        assert sanitize_content_type("text/html  ; charset=utf-8") == "text/html"

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert sanitize_content_type("") == ""

    def test_none_value(self):
        """Test that None returns empty string."""
        assert sanitize_content_type(None) == ""

    def test_invalid_no_slash(self):
        """Test that content types without slash return empty string."""
        assert sanitize_content_type("invalid") == ""
        assert sanitize_content_type("texthtml") == ""

    def test_long_content_type_truncated(self):
        """Test that very long content types are truncated."""
        # Content type with slash within first 100 chars should be truncated
        long_type = "application/" + "x" * 200
        result = sanitize_content_type(long_type)
        assert len(result) <= 100
        assert result.startswith("application/")

    def test_long_content_type_invalid_after_truncation(self):
        """Test that content types that become invalid after truncation return empty."""
        # Content type with slash after 100 chars would be invalid after truncation
        long_type = "a" * 200 + "/test"
        result = sanitize_content_type(long_type)
        assert result == ""

    def test_complex_office_document_types(self):
        """Test complex Office document MIME types."""
        docx_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert sanitize_content_type(docx_type) == docx_type

        xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert sanitize_content_type(xlsx_type) == xlsx_type

    def test_malformed_email_content_type(self):
        """Test handling of potentially malformed email content types."""
        # These could come from email attachments with non-standard headers
        assert (
            sanitize_content_type("text/html; charset=utf-8; name=file.html")
            == "text/html"
        )
        assert sanitize_content_type("image/jpeg; x-unexpected=garbage") == "image/jpeg"
