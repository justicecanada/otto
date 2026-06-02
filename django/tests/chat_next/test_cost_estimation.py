"""Tests for cost estimation functions added in the tools-cost-warning branch.

Covers:
- _calculate_cost_for_units (utils)
- _get_model_id (utils)
- estimate_translation_cost
- estimate_qa_search_cost / _get_total_chunks_for_scope
- estimate_get_document_text_cost
- estimate_document_processing_cost
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared mocks
# ---------------------------------------------------------------------------


def _make_chat(model_id="gpt-4.1"):
    """Return a minimal mock chat object with settings.chat_model set."""
    chat = MagicMock()
    chat.settings.chat_model = model_id
    return chat


# ---------------------------------------------------------------------------
# _calculate_cost_for_units
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCalculateCostForUnits:
    """Unit tests for the _calculate_cost_for_units helper."""

    def test_returns_correct_decimal_for_known_cost_type(self):
        from chat_next._tools.utils import _calculate_cost_for_units

        # translate-file: unit_cost=15, unit_quantity=1_000_000
        # 1_000_000 chars → 15 / 1_000_000 * 1_000_000 = 15 USD (before CAD conversion)
        result = _calculate_cost_for_units("translate-file", 1_000_000)
        assert result == Decimal("15")

    def test_returns_zero_for_unknown_cost_type(self):
        from chat_next._tools.utils import _calculate_cost_for_units

        result = _calculate_cost_for_units("nonexistent-cost-type-xyz", 500)
        assert result == Decimal("0")

    def test_zero_units_returns_zero(self):
        from chat_next._tools.utils import _calculate_cost_for_units

        result = _calculate_cost_for_units("translate-file", 0)
        assert result == Decimal("0")

    def test_fractional_units_handled(self):
        from chat_next._tools.utils import _calculate_cost_for_units

        # 500_000 chars → 15 * 500_000 / 1_000_000 = 7.5
        result = _calculate_cost_for_units("translate-file", 500_000)
        assert result == Decimal("7.5")


# ---------------------------------------------------------------------------
# _get_model_id
# ---------------------------------------------------------------------------


class TestGetModelId:
    """Unit tests for the _get_model_id helper."""

    def test_returns_model_id_from_chat_settings(self):
        from chat_next._tools.utils import _get_model_id

        chat = _make_chat("gpt-4.1")
        assert _get_model_id(chat) == "gpt-4.1"

    def test_returns_none_when_chat_is_none(self):
        from chat_next._tools.utils import _get_model_id

        assert _get_model_id(None) is None

    def test_returns_none_when_settings_missing(self):
        from chat_next._tools.utils import _get_model_id

        chat = MagicMock(spec=[])  # no .settings attribute
        assert _get_model_id(chat) is None


# ---------------------------------------------------------------------------
# estimate_translation_cost
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateTranslationCost:
    """Tests for estimate_translation_cost."""

    def test_returns_none_when_no_document_ids(self):
        from chat_next._tools.translation import estimate_translation_cost

        result = estimate_translation_cost({})
        assert result is None

    def test_returns_none_for_nonexistent_document(self):
        from chat_next._tools.translation import estimate_translation_cost

        result = estimate_translation_cost({"document_ids": [999999]})
        assert result is None

    def test_returns_cost_string_from_extracted_text(self, all_apps_user):
        from chat_next._tools.translation import estimate_translation_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Test lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        # 1_000_000 chars → 15 USD * exchange_rate (1.38 default) = 20.70 CAD
        doc = Document.objects.create(
            data_source=ds,
            filename="test.docx",
            extracted_text="x" * 1_000_000,
        )

        result = estimate_translation_cost({"document_ids": [doc.id]})

        assert result is not None
        # Result should be a numeric string with 2 decimal places
        float_val = float(result)
        assert float_val > 0

    def test_uses_num_chunks_fallback_when_no_extracted_text(self, all_apps_user):
        from chat_next._tools.translation import estimate_translation_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Fallback lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="chunked.pdf",
            num_chunks=100,
            extracted_text=None,
        )

        result = estimate_translation_cost({"document_ids": [doc.id]})
        assert result is not None
        assert float(result) > 0

    def test_skips_document_with_no_text_or_chunks(self, all_apps_user):
        from chat_next._tools.translation import estimate_translation_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Empty lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="empty.pdf",
            extracted_text=None,
            num_chunks=0,
        )

        result = estimate_translation_cost({"document_ids": [doc.id]})
        assert result is None


# ---------------------------------------------------------------------------
# _get_total_chunks_for_scope
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetTotalChunksForScope:
    """Tests for the QA search scope helper."""

    def test_returns_chunks_for_document(self, all_apps_user):
        from chat_next._tools.qa_libraries import _get_total_chunks_for_scope

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Chunk lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(data_source=ds, filename="doc.pdf", num_chunks=42)

        result = _get_total_chunks_for_scope({"document_id": doc.id})
        assert result == 42

    def test_returns_none_for_nonexistent_document(self):
        from chat_next._tools.qa_libraries import _get_total_chunks_for_scope

        result = _get_total_chunks_for_scope({"document_id": 999999})
        assert result is None

    def test_returns_sum_for_data_source(self, all_apps_user):
        from chat_next._tools.qa_libraries import _get_total_chunks_for_scope

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="DS lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        Document.objects.create(
            data_source=ds, filename="a.pdf", num_chunks=10, is_container=False
        )
        Document.objects.create(
            data_source=ds, filename="b.pdf", num_chunks=20, is_container=False
        )

        result = _get_total_chunks_for_scope({"data_source_id": ds.id})
        assert result == 30

    def test_returns_sum_for_library(self, all_apps_user):
        from chat_next._tools.qa_libraries import _get_total_chunks_for_scope

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Lib sum", created_by=user)
        ds1 = DataSource.objects.create(library=library)
        ds2 = DataSource.objects.create(library=library)
        Document.objects.create(
            data_source=ds1, filename="c.pdf", num_chunks=15, is_container=False
        )
        Document.objects.create(
            data_source=ds2, filename="d.pdf", num_chunks=25, is_container=False
        )

        result = _get_total_chunks_for_scope({"library_id": library.id})
        assert result == 40

    def test_returns_none_for_empty_arguments(self):
        from chat_next._tools.qa_libraries import _get_total_chunks_for_scope

        result = _get_total_chunks_for_scope({})
        assert result is None


# ---------------------------------------------------------------------------
# estimate_qa_search_cost
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateQaSearchCost:
    """Tests for estimate_qa_search_cost."""

    def test_returns_none_when_no_query(self):
        from chat_next._tools.qa_libraries import estimate_qa_search_cost

        chat = _make_chat("gpt-4.1")
        result = estimate_qa_search_cost({"query": ""}, chat=chat)
        assert result is None

    def test_returns_none_when_no_model(self):
        from chat_next._tools.qa_libraries import estimate_qa_search_cost

        result = estimate_qa_search_cost({"query": "test"}, chat=None)
        assert result is None

    def test_returns_cost_string_for_valid_query(self):
        from chat_next._tools.qa_libraries import estimate_qa_search_cost

        chat = _make_chat("gpt-4.1")
        result = estimate_qa_search_cost(
            {"query": "some question", "top_k": 5}, chat=chat
        )
        assert result is not None
        assert float(result) > 0

    def test_caps_chunk_count_at_top_k(self, all_apps_user):
        """With fewer available chunks than top_k, cost should be based on actual chunks."""
        from chat_next._tools.qa_libraries import estimate_qa_search_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Small lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds, filename="small.pdf", num_chunks=2, is_container=False
        )

        chat = _make_chat("gpt-4.1")
        result_small = estimate_qa_search_cost(
            {"query": "q", "top_k": 5, "document_id": doc.id}, chat=chat
        )
        result_uncapped = estimate_qa_search_cost({"query": "q", "top_k": 5}, chat=chat)

        # Small doc (2 chunks) should cost less than uncapped (5 chunks)
        assert result_small is not None
        assert result_uncapped is not None
        assert float(result_small) < float(result_uncapped)

    def test_large_top_k_is_not_artificially_capped_at_ten(self):
        from chat_next._tools.qa_libraries import estimate_qa_search_cost

        chat = _make_chat("gpt-4.1")
        result_10 = estimate_qa_search_cost({"query": "q", "top_k": 10}, chat=chat)
        result_200 = estimate_qa_search_cost({"query": "q", "top_k": 200}, chat=chat)

        assert result_10 is not None
        assert result_200 is not None
        assert float(result_200) > float(result_10)


# ---------------------------------------------------------------------------
# estimate_get_document_text_cost
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateGetDocumentTextCost:
    """Tests for estimate_get_document_text_cost."""

    def test_returns_none_when_no_document_id(self):
        from chat_next._tools.qa_libraries import estimate_get_document_text_cost

        chat = _make_chat("gpt-4.1")
        result = estimate_get_document_text_cost({}, chat=chat)
        assert result is None

    def test_returns_none_for_nonexistent_document(self):
        from chat_next._tools.qa_libraries import estimate_get_document_text_cost

        chat = _make_chat("gpt-4.1")
        result = estimate_get_document_text_cost({"document_id": 999999}, chat=chat)
        assert result is None

    def test_returns_cost_from_extracted_text(self, all_apps_user):
        from chat_next._tools.qa_libraries import estimate_get_document_text_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Text lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="full.txt",
            extracted_text="word " * 10_000,  # 50_000 chars
        )

        chat = _make_chat("gpt-4.1")
        result = estimate_get_document_text_cost({"document_id": doc.id}, chat=chat)
        assert result is not None
        assert float(result) > 0

    def test_respects_end_char_range(self, all_apps_user):
        from chat_next._tools.qa_libraries import estimate_get_document_text_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Range lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="ranged.txt",
            extracted_text="a" * 40_000,
        )

        chat = _make_chat("gpt-4.1")
        full_cost = estimate_get_document_text_cost({"document_id": doc.id}, chat=chat)
        partial_cost = estimate_get_document_text_cost(
            {"document_id": doc.id, "start_char": 0, "end_char": 4_000}, chat=chat
        )

        assert full_cost is not None
        assert partial_cost is not None
        assert float(partial_cost) < float(full_cost)

    def test_uses_num_chunks_fallback(self, all_apps_user):
        from chat_next._tools.qa_libraries import estimate_get_document_text_cost

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Chunk fallback lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="chunks.pdf",
            extracted_text=None,
            num_chunks=50,
        )

        chat = _make_chat("gpt-4.1")
        result = estimate_get_document_text_cost({"document_id": doc.id}, chat=chat)
        assert result is not None
        assert float(result) > 0


# ---------------------------------------------------------------------------
# estimate_document_processing_cost
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstimateDocumentProcessingCost:
    """Tests for estimate_document_processing_cost."""

    def test_returns_none_when_no_document_ids(self):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        chat = _make_chat("gpt-4.1")
        result = estimate_document_processing_cost({}, chat=chat)
        assert result is None

    def test_returns_none_when_chat_has_no_model(self):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        result = estimate_document_processing_cost({"document_ids": [1]}, chat=None)
        assert result is None

    def test_returns_cost_for_document_with_extracted_text(self, all_apps_user):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Proc lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="process.pdf",
            extracted_text="content " * 5_000,  # 40_000 chars
        )

        chat = _make_chat("gpt-4.1")
        result = estimate_document_processing_cost(
            {
                "document_ids": [doc.id],
                "prompt": "Summarize this document",
            },
            chat=chat,
        )
        assert result is not None
        assert float(result) > 0

    def test_skips_nonexistent_documents(self, all_apps_user):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Skip lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="real.pdf",
            extracted_text="text " * 1_000,
        )

        chat = _make_chat("gpt-4.1")
        result = estimate_document_processing_cost(
            {"document_ids": [doc.id, 999999]},
            chat=chat,
        )
        # Should still return a result for the real document
        assert result is not None
        assert float(result) > 0

    def test_prompt_tokens_added_per_document(self, all_apps_user):
        """A longer prompt should produce a higher cost estimate (more tokens per doc)."""
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Prompt cost lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        docs = [
            Document.objects.create(
                data_source=ds,
                filename=f"d{i}.pdf",
                extracted_text="a" * 4_000,
            )
            for i in range(3)
        ]
        doc_ids = [d.id for d in docs]

        chat = _make_chat("gpt-4.1")
        cost_no_prompt = estimate_document_processing_cost(
            {"document_ids": doc_ids}, chat=chat
        )
        cost_with_prompt = estimate_document_processing_cost(
            {"document_ids": doc_ids, "prompt": "x" * 4_000}, chat=chat
        )

        assert cost_no_prompt is not None
        assert cost_with_prompt is not None
        assert float(cost_with_prompt) > float(cost_no_prompt)

    def test_explicit_model_override_is_used_without_chat(self, all_apps_user):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Explicit model lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="process.pdf",
            extracted_text="content " * 5_000,
        )

        result = estimate_document_processing_cost(
            {
                "document_ids": [doc.id],
                "prompt": "Summarize this document",
                "llm_model": "gpt-5-mini",
            },
            chat=None,
        )

        assert result is not None
        assert float(result) > 0

    def test_truncate_chars_reduces_document_processing_cost(self, all_apps_user):
        from chat_next._tools.document_processing import (
            estimate_document_processing_cost,
        )

        from librarian.models import DataSource, Document, Library

        user = all_apps_user()
        library = Library.objects.create(name="Truncate cost lib", created_by=user)
        ds = DataSource.objects.create(library=library)
        doc = Document.objects.create(
            data_source=ds,
            filename="process.pdf",
            extracted_text="content " * 10000,
        )

        chat = _make_chat("gpt-5.4-mini")
        full_cost = estimate_document_processing_cost(
            {
                "document_ids": [doc.id],
                "prompt": "Extract title and date",
            },
            chat=chat,
        )
        truncated_cost = estimate_document_processing_cost(
            {
                "document_ids": [doc.id],
                "prompt": "Extract title and date",
                "truncate_chars": 1200,
            },
            chat=chat,
        )

        assert full_cost is not None
        assert truncated_cost is not None
        assert float(truncated_cost) < float(full_cost)
