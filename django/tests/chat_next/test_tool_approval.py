from datetime import timedelta
from unittest.mock import Mock

from django.test import override_settings
from django.utils import timezone

import chat_next.tools  # noqa: F401
import pytest
from chat_next._tools.approval import (
    APPROVAL_SOURCE_QUERY_POLICY,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalPolicyContext,
    custom_approval_rule,
    evaluate_approval_policy,
    exact_argument_match,
    regex_argument_match,
)
from chat_next._tools.approval_display import render_tool_approval_input_html
from chat_next._tools.base import TOOL_REGISTRY
from chat_next._tools.risk_review import review_external_tool_call
from chat_next.approval_logging import (
    mark_external_tool_approval_decision,
    sync_external_tool_approval_logs_from_processing_steps,
)
from chat_next.models import (
    EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED,
    EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED,
    EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM,
    Chat,
    ExternalToolApprovalLog,
    Message,
)
from chat_next.utils import format_processing_steps

from otto.models import OttoStatus


@pytest.mark.asyncio
async def test_approval_policy_exact_argument_match_auto_approves():
    policy = ApprovalPolicy(
        rules=[
            exact_argument_match(
                "doc_type",
                allowed_values={"cases", "laws"},
                rule_name="safe_doc_type",
            )
        ]
    )

    decision = await policy.evaluate(
        ApprovalPolicyContext(
            user=None,
            chat=None,
            tool=None,
            function_call={"arguments": '{"doc_type": "cases"}'},
            arguments={"doc_type": "cases"},
        )
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "safe_doc_type"


@pytest.mark.asyncio
async def test_approval_policy_regex_argument_match_auto_approves():
    policy = ApprovalPolicy(
        rules=[
            regex_argument_match(
                "query",
                r"^dataset[s]?$",
                rule_name="dataset_keyword",
            )
        ]
    )

    decision = await policy.evaluate(
        ApprovalPolicyContext(
            user=None,
            chat=None,
            tool=None,
            function_call={"arguments": '{"query": "datasets"}'},
            arguments={"query": "datasets"},
        )
    )

    assert decision.auto_approve is True
    assert decision.matched_rule == "dataset_keyword"


@pytest.mark.asyncio
async def test_approval_policy_custom_validator_can_return_decision():
    async def validator(context):
        if context.arguments.get("contains_pii"):
            return ApprovalDecision(auto_approve=False)
        return ApprovalDecision(
            auto_approve=True,
            approval_source=APPROVAL_SOURCE_QUERY_POLICY,
            matched_rule="classifier_safe",
        )

    policy = ApprovalPolicy(
        rules=[custom_approval_rule(validator, rule_name="classifier_safe")]
    )

    safe_decision = await policy.evaluate(
        ApprovalPolicyContext(
            user=None,
            chat=None,
            tool=None,
            function_call={"arguments": "{}"},
            arguments={"contains_pii": False},
        )
    )
    blocked_decision = await policy.evaluate(
        ApprovalPolicyContext(
            user=None,
            chat=None,
            tool=None,
            function_call={"arguments": "{}"},
            arguments={"contains_pii": True},
        )
    )

    assert safe_decision.auto_approve is True
    assert safe_decision.matched_rule == "classifier_safe"
    assert blocked_decision.auto_approve is False


@pytest.mark.asyncio
async def test_safe_a2aj_case_fetch_is_auto_approved_by_query_policy():
    tool = TOOL_REGISTRY.get("fetch_canadian_case_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "2020 SCC 5", "output_language": "en"}'
        },
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_case_citation_fetch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {
            "query": "v.",
            "search_type": "name",
            "sort_results": "newest_first",
        },
        {
            "query": "R.",
            "search_type": "name",
            "sort_results": "newest_first",
            "dataset": "SCC,ONCA",
        },
        {
            "query": "Canada",
            "search_type": "name",
            "sort_results": "newest_first",
            "size": 10,
        },
        {
            "query": "Attorney General",
            "search_type": "name",
            "sort_results": "newest_first",
            "dataset": "SCC",
        },
    ],
)
async def test_safe_a2aj_case_browse_seed_search_is_auto_approved(arguments):
    tool = TOOL_REGISTRY.get("search_canadian_case_law")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": arguments},
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_case_browse_seed_search"


@pytest.mark.asyncio
async def test_case_browse_seed_search_with_full_text_requires_manual_approval():
    tool = TOOL_REGISTRY.get("search_canadian_case_law")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "v.",
                "search_type": "full_text",
                "sort_results": "newest_first",
            }
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_case_browse_seed_search_with_date_filter_is_auto_approved():
    tool = TOOL_REGISTRY.get("search_canadian_case_law")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "Canada",
                "search_type": "name",
                "sort_results": "newest_first",
                "start_date": "2025-01-01",
            }
        },
    )

    assert decision.auto_approve is True
    assert decision.matched_rule == "a2aj_safe_case_browse_seed_search"


@pytest.mark.asyncio
async def test_case_browse_seed_search_with_invalid_date_filter_requires_manual_approval():
    tool = TOOL_REGISTRY.get("search_canadian_case_law")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "Canada",
                "search_type": "name",
                "sort_results": "newest_first",
                "start_date": "2025/01/01",
            }
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_safe_a2aj_case_fetch_with_full_text_window_is_auto_approved():
    tool = TOOL_REGISTRY.get("fetch_canadian_case_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": '{"citation": "2020 SCC 5", "end_char": -1}'},
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_case_citation_fetch"


@pytest.mark.asyncio
async def test_safe_a2aj_case_fetch_with_continuation_slice_is_auto_approved():
    tool = TOOL_REGISTRY.get("fetch_canadian_case_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "2020 SCC 5", "start_char": 25000, "end_char": 50000}'
        },
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_case_citation_fetch"


@pytest.mark.asyncio
async def test_a2aj_case_fetch_with_inexact_citation_requires_manual_approval():
    tool = TOOL_REGISTRY.get("fetch_canadian_case_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": '{"citation": "SCC 5", "end_char": -1}'},
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_a2aj_case_fetch_with_nine_digit_slice_requires_manual_approval():
    tool = TOOL_REGISTRY.get("fetch_canadian_case_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "2020 SCC 5", "start_char": 123456789, "end_char": -1}'
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_safe_a2aj_legislation_fetch_is_auto_approved_by_query_policy():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "RSC 1985, c C-46", "section": "219"}'
        },
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_legislation_citation_fetch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {
            "query": "Act",
            "search_type": "name",
            "sort_results": "newest_first",
            "dataset": "LEGISLATION-FED",
        },
        {
            "query": "Regulations",
            "search_type": "name",
            "sort_results": "newest_first",
            "dataset": "REGULATIONS-FED",
        },
        {
            "query": "Order",
            "search_type": "name",
            "sort_results": "newest_first",
            "dataset": "REGULATIONS-FED",
            "size": 10,
        },
    ],
)
async def test_safe_a2aj_legislation_browse_seed_search_is_auto_approved(arguments):
    tool = TOOL_REGISTRY.get("search_canadian_legislation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": arguments},
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_legislation_browse_seed_search"


@pytest.mark.asyncio
async def test_legislation_browse_seed_search_with_wrong_dataset_requires_manual_approval():
    tool = TOOL_REGISTRY.get("search_canadian_legislation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "Act",
                "search_type": "name",
                "sort_results": "newest_first",
                "dataset": "REGULATIONS-FED",
            }
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_legislation_browse_seed_search_with_date_filter_is_auto_approved():
    tool = TOOL_REGISTRY.get("search_canadian_legislation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "Regulations",
                "search_type": "name",
                "sort_results": "newest_first",
                "dataset": "REGULATIONS-FED",
                "end_date": "2026-01-01",
            }
        },
    )

    assert decision.auto_approve is True
    assert decision.matched_rule == "a2aj_safe_legislation_browse_seed_search"


@pytest.mark.asyncio
async def test_legislation_browse_seed_search_with_reversed_date_range_requires_manual_approval():
    tool = TOOL_REGISTRY.get("search_canadian_legislation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": {
                "query": "Regulations",
                "search_type": "name",
                "sort_results": "newest_first",
                "dataset": "REGULATIONS-FED",
                "start_date": "2026-05-01",
                "end_date": "2026-01-01",
            }
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_safe_a2aj_legislation_fetch_with_full_text_window_is_auto_approved():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": '{"citation": "RSC 1985, c C-46", "end_char": -1}'},
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_legislation_citation_fetch"


@pytest.mark.asyncio
async def test_safe_a2aj_legislation_fetch_with_continuation_slice_is_auto_approved():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "RSC 1985, c C-46", "start_char": 25000, "end_char": 50000}'
        },
    )

    assert decision.auto_approve is True
    assert decision.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert decision.matched_rule == "a2aj_safe_legislation_citation_fetch"


@pytest.mark.asyncio
async def test_a2aj_legislation_fetch_with_nine_digit_slice_requires_manual_approval():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "RSC 1985, c C-46", "start_char": 123456789, "end_char": -1}'
        },
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_a2aj_legislation_fetch_with_ambiguous_citation_requires_manual_approval():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={"arguments": '{"citation": "C-46"}'},
    )

    assert decision.auto_approve is False


@pytest.mark.asyncio
async def test_a2aj_legislation_fetch_with_unsafe_section_requires_manual_approval():
    tool = TOOL_REGISTRY.get("fetch_canadian_legislation_by_citation")

    decision = await evaluate_approval_policy(
        tool=tool,
        user=None,
        chat=None,
        function_call={
            "arguments": '{"citation": "RSC 1985, c C-46", "section": "part x"}'
        },
    )

    assert decision.auto_approve is False


def test_review_external_tool_call_flags_sensitive_markers_and_local_pii():
    review = review_external_tool_call(
        function_call={
            "name": "termium_lookup",
            "arguments": '{"query": "cabinet confidence jane.doe@example.com", "index": "ent"}',
        },
        tool=TOOL_REGISTRY.get("termium_lookup"),
    )

    assert review["flagged"] is True
    assert review["pii_flagged"] is True
    assert "heuristic" in review["review_sources"]
    assert "Email" in review["pii_entity_categories"]
    assert "Email" in review["detected_pii_entity_categories"]
    assert "privileged_or_classified" in review["matched_marker_ids"]
    assert not any(
        "privileged" in item.lower() or "cabinet" in item.lower()
        for item in review["summary_items"]
    )
    assert any(
        "personal information" in item.lower() for item in review["summary_items"]
    )


@pytest.mark.django_db
@override_settings(EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENABLED=False)
def test_review_external_tool_call_does_not_flag_phrase_only_privileged_marker_by_default():
    status = OttoStatus.objects.singleton()
    status.external_tool_review_flag_privileged_or_classified = False
    status.save(update_fields=["external_tool_review_flag_privileged_or_classified"])

    review = review_external_tool_call(
        function_call={
            "name": "termium_lookup",
            "arguments": '{"query": "cabinet confidence", "index": "ent"}',
        },
        tool=TOOL_REGISTRY.get("termium_lookup"),
    )

    assert review["flagged"] is False
    assert review["pii_flagged"] is False
    assert review["summary_items"] == []
    assert review["review_sources"] == []
    assert review["matched_marker_ids"] == ["privileged_or_classified"]


def test_review_external_tool_call_reviews_unexpected_value_in_otherwise_safe_field():
    review = review_external_tool_call(
        function_call={
            "name": "termium_lookup",
            "arguments": '{"query": "harmless query", "index": "jane.doe@example.com"}',
        },
        tool=TOOL_REGISTRY.get("termium_lookup"),
    )

    assert review["flagged"] is True
    assert review["pii_flagged"] is True
    assert "Email" in review["pii_entity_categories"]
    assert "Email" in review["detected_pii_entity_categories"]


@override_settings(
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENABLED=True,
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENDPOINT="https://language-review.example/",
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_API_VERSION="2022-05-01",
    AZURE_AI_SERVICES_KEY="test-key",
)
def test_review_external_tool_call_uses_optional_azure_language_review(monkeypatch):
    captured = {}
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": {
            "documents": [
                {
                    "id": "1",
                    "redactedText": "query: Contact ***************",
                    "entities": [
                        {
                            "text": "jane.doe@example.com",
                            "category": "Email",
                            "confidenceScore": 0.99,
                        }
                    ],
                }
            ]
        }
    }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("chat_next._tools.risk_review.requests.post", fake_post)

    review = review_external_tool_call(
        function_call={
            "name": "termium_lookup",
            "arguments": '{"query": "Contact jane.doe@example.com", "index": "eng"}',
        },
        tool=TOOL_REGISTRY.get("termium_lookup"),
    )

    assert captured["url"] == (
        "https://language-review.example/language/:analyze-text?api-version=2022-05-01"
    )
    assert captured["headers"]["Ocp-Apim-Subscription-Key"] == "test-key"
    assert review["azure_language_used"] is True
    assert "azure_language" in review["review_sources"]
    assert "Email" in review["pii_entity_categories"]
    assert "Email" in review["summary_items"]


@override_settings(
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENABLED=True,
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENDPOINT="https://language-review.example/",
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_API_VERSION="2022-05-01",
    AZURE_AI_SERVICES_KEY="test-key",
)
def test_review_external_tool_call_azure_organization_not_flagged_by_default(
    monkeypatch,
):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": {
            "documents": [
                {
                    "id": "1",
                    "redactedText": "query: Charter AND rights",
                    "entities": [
                        {
                            "text": "SCC",
                            "category": "Organization",
                            "confidenceScore": 0.99,
                        }
                    ],
                }
            ]
        }
    }

    monkeypatch.setattr(
        "chat_next._tools.risk_review.requests.post",
        lambda *args, **kwargs: response,
    )

    review = review_external_tool_call(
        function_call={
            "name": "search_canadian_case_law",
            "arguments": '{"query": "Charter AND rights", "dataset": "SCC", "size": 10}',
        },
        tool=TOOL_REGISTRY.get("search_canadian_case_law"),
    )

    assert review["azure_language_used"] is True
    assert review["flagged"] is True
    assert review["pii_flagged"] is True
    assert review["pii_entity_categories"] == ["Organization"]
    assert review["detected_pii_entity_categories"] == ["Organization"]
    assert review["review_sources"] == ["azure_language"]
    assert review["summary_items"] == ["Organization"]


@pytest.mark.django_db
@override_settings(
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENABLED=True,
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_ENDPOINT="https://language-review.example/",
    EXTERNAL_TOOL_AZURE_LANGUAGE_REVIEW_API_VERSION="2022-05-01",
    AZURE_AI_SERVICES_KEY="test-key",
)
def test_review_external_tool_call_respects_operator_sensitivity_settings(monkeypatch):
    status = OttoStatus.objects.singleton()
    status.external_tool_review_flagged_azure_pii_categories = ["Age"]
    status.external_tool_review_flag_privileged_or_classified = True
    status.save(
        update_fields=[
            "external_tool_review_flagged_azure_pii_categories",
            "external_tool_review_flag_privileged_or_classified",
        ]
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": {
            "documents": [
                {
                    "id": "1",
                    "redactedText": "query: cabinet confidence",
                    "entities": [
                        {
                            "text": "42",
                            "category": "Age",
                            "confidenceScore": 0.99,
                        }
                    ],
                }
            ]
        }
    }

    monkeypatch.setattr(
        "chat_next._tools.risk_review.requests.post",
        lambda *args, **kwargs: response,
    )

    review = review_external_tool_call(
        function_call={
            "name": "termium_lookup",
            "arguments": '{"query": "cabinet confidence", "index": "ent"}',
        },
        tool=TOOL_REGISTRY.get("termium_lookup"),
    )

    assert review["flagged"] is True
    assert review["pii_flagged"] is True
    assert "Age" in review["pii_entity_categories"]
    assert "Age" in review["summary_items"]
    assert any(
        "cabinet-confidence" in item.lower() or "classified information" in item.lower()
        for item in review["summary_items"]
    )
    assert set(review["review_sources"]) == {"azure_language", "heuristic"}


def test_format_processing_steps_exposes_risk_review_metadata_for_widget():
    formatted = format_processing_steps(
        [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "termium_lookup",
                    "call_id": "call_termium_pending",
                    "arguments": '{"query": "cabinet confidence jane.doe@example.com", "index": "ent"}',
                    "approval_request_id": "call_termium_pending",
                    "tool_label": "Termium lookup",
                    "approval_source": "manual",
                    "approval_requires_external_warning": True,
                    "pii_flagged": True,
                    "risk_review": {
                        "flagged": True,
                        "pii_flagged": True,
                        "summary_items": [
                            "Contains terms associated with privileged, Cabinet-confidence, or classified information.",
                            "Local review suggests personal information: Email.",
                        ],
                        "review_sources": ["heuristic"],
                        "pii_entity_categories": ["Email"],
                    },
                },
            }
        ]
    )

    assert len(formatted) == 1
    assert formatted[0]["is_approval_request"] is True
    assert formatted[0]["pii_flagged"] is True
    assert formatted[0]["risk_review"]["flagged"] is True
    assert formatted[0]["risk_review"]["summary_items"][0].startswith(
        "Contains terms associated"
    )


def test_render_tool_approval_input_html_formats_termium_lookup_as_definition_list():
    rendered = render_tool_approval_input_html(
        "termium_lookup",
        '{"query": "cabinet confidence", "index": "ent", "lang": "fra"}',
    )

    assert "reasoning-approval-fields" in rendered
    assert "Search term" in rendered
    assert "cabinet confidence" in rendered
    assert "Search mode" not in rendered
    assert "Exact English term" not in rendered
    assert "Interface language" not in rendered
    assert "French" not in rendered
    assert "```json" not in rendered


def test_render_tool_approval_input_html_hides_safe_a2aj_search_fields():
    rendered = render_tool_approval_input_html(
        "search_canadian_case_law",
        '{"query": "Charter AND rights", "search_type": "name", "dataset": "SCC,ONCA", "sort_results": "newest_first", "size": 10}',
    )

    assert "Charter AND rights" in rendered
    assert "Title / name" not in rendered
    assert "SCC" not in rendered
    assert "Newest first" not in rendered
    assert "Result count" not in rendered


def test_render_tool_approval_input_html_shows_unexpected_value_in_safe_enum_field():
    rendered = render_tool_approval_input_html(
        "termium_lookup",
        '{"query": "cabinet confidence", "index": "jane.doe@example.com", "lang": "fra"}',
    )

    assert "Search term" in rendered
    assert "Search mode" in rendered
    assert "jane.doe@example.com" in rendered
    assert "Interface language" not in rendered


def test_render_tool_approval_input_html_shows_unexpected_fields_via_generic_fallback():
    rendered = render_tool_approval_input_html(
        "list_canadian_legal_datasets",
        '{"doc_type": "both", "bool_field": "this could also technically be anything"}',
    )

    assert "Bool field" in rendered
    assert "this could also technically be anything" in rendered
    assert "Coverage" not in rendered


def test_format_processing_steps_adds_approval_input_html_for_parallel_waiting_calls():
    formatted = format_processing_steps(
        [
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "search_canadian_case_law",
                    "call_id": "call_case_search_pending_1",
                    "arguments": '{"query": "constructive dismissal", "dataset": "SCC,ONCA"}',
                    "tool_label": "Search Canadian case law",
                },
            },
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "fetch_canadian_case_by_citation",
                    "call_id": "call_case_fetch_pending_2",
                    "arguments": '{"citation": "2020 SCC 5", "start_char": 25000, "end_char": 50000}',
                    "approval_request_id": "call_case_fetch_pending_2",
                    "tool_label": "Fetch Canadian case by citation",
                },
            },
        ]
    )

    assert len(formatted) == 2
    assert all("approval_input_html" in step for step in formatted)
    assert formatted[0].get("is_approval_request") is None
    assert formatted[1]["is_approval_request"] is True
    assert "constructive dismissal" in formatted[0]["approval_input_html"]
    assert "SCC" not in formatted[0]["approval_input_html"]
    assert "Citation" in formatted[1]["approval_input_html"]
    assert "2020 SCC 5" in formatted[1]["approval_input_html"]
    assert "Requested text" not in formatted[1]["approval_input_html"]


@pytest.mark.django_db
def test_sync_external_tool_approval_logs_records_pending_and_auto_approved_events(
    all_apps_user,
):
    user = all_apps_user("external-approval-audit")
    chat = Chat.objects.create(user=user, title="Approval audit chat")
    message = Message.objects.create(chat=chat, text="", is_bot=True)
    approval_requested_at = timezone.now()

    sync_external_tool_approval_logs_from_processing_steps(
        message=message,
        user=user,
        raw_processing_steps=[
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "waiting_approval",
                "details": {
                    "name": "termium_lookup",
                    "call_id": "call_termium_pending",
                    "arguments": '{"query": "solicitor-client privilege", "index": "ent"}',
                    "approval_request_id": "call_termium_pending",
                    "approval_source": "manual",
                    "risk_review": {
                        "flagged": True,
                        "pii_flagged": True,
                        "review_sources": ["azure_language"],
                        "pii_entity_categories": ["Person", "Address"],
                    },
                },
            },
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "list_canadian_legal_datasets",
                    "call_id": "call_a2aj_auto",
                    "arguments": '{"doc_type": "both"}',
                    "approval_source": APPROVAL_SOURCE_QUERY_POLICY,
                    "risk_review": {
                        "flagged": True,
                        "pii_flagged": True,
                        "review_sources": ["heuristic"],
                        "pii_entity_categories": ["Organization"],
                    },
                },
            },
            {
                "type": "tool_call",
                "tool_type": "function_call",
                "status": "completed",
                "details": {
                    "name": "rag_search",
                    "call_id": "call_internal_ignored",
                    "arguments": '{"query": "budget"}',
                    "approval_source": APPROVAL_SOURCE_QUERY_POLICY,
                },
            },
        ],
        pending_local_tool={
            "call_id": "call_termium_pending",
            "approval_requested_at": approval_requested_at.isoformat(),
        },
        recorded_at=approval_requested_at,
    )

    pending_log = ExternalToolApprovalLog.objects.get(
        tool_call_id="call_termium_pending"
    )
    auto_log = ExternalToolApprovalLog.objects.get(tool_call_id="call_a2aj_auto")

    assert ExternalToolApprovalLog.objects.count() == 2
    assert pending_log.decision == EXTERNAL_TOOL_APPROVAL_DECISION_PENDING
    assert pending_log.external_service_name == "TERMIUM Plus®"
    assert pending_log.displayed_at == approval_requested_at
    assert pending_log.pii_entity_categories == ["Address", "Person"]
    assert "solicitor-client privilege" in pending_log.query

    assert auto_log.decision == EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED
    assert auto_log.external_service_name == "A2AJ"
    assert auto_log.decided_at == approval_requested_at
    assert auto_log.approval_source == APPROVAL_SOURCE_QUERY_POLICY
    assert auto_log.pii_entity_categories == ["Organization"]


@pytest.mark.django_db
def test_mark_external_tool_approval_decision_creates_decision_log_without_overwriting_pending_log(
    all_apps_user,
):
    user = all_apps_user("external-approval-decision")
    chat = Chat.objects.create(user=user, title="Approval decision chat")
    message = Message.objects.create(chat=chat, text="", is_bot=True)
    displayed_at = timezone.now() - timedelta(seconds=12)

    log = ExternalToolApprovalLog.objects.create(
        user=user,
        message=message,
        tool_call_id="call_termium_pending",
        approval_request_id="call_termium_pending",
        tool_name="termium_lookup",
        tool_label="Termium lookup",
        external_service_name="TERMIUM Plus®",
        query='{"query": "cabinet confidence", "index": "ent"}',
        tool_arguments={"query": "cabinet confidence", "index": "ent"},
        decision=EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
        approval_source="manual",
        displayed_at=displayed_at,
    )

    decided_at = displayed_at + timedelta(seconds=12)
    mark_external_tool_approval_decision(
        message=message,
        user=user,
        function_calls=[
            {
                "type": "function_call",
                "name": "termium_lookup",
                "call_id": "call_termium_pending",
                "arguments": '{"query": "cabinet confidence", "index": "ent"}',
                "risk_review": {
                    "flagged": True,
                    "pii_flagged": True,
                    "review_sources": ["azure_language"],
                    "pii_entity_categories": ["Person"],
                },
            }
        ],
        approved=True,
        decided_at=decided_at,
        approval_request_id="call_termium_pending",
    )

    log.refresh_from_db()
    approved_log = ExternalToolApprovalLog.objects.get(
        message_id_snapshot=message.id,
        tool_call_id="call_termium_pending",
        decision=EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED,
    )

    assert approved_log == log
    assert approved_log.decided_at == decided_at
    assert approved_log.pii_entity_categories == ["Person"]
    assert approved_log.review_latency_seconds == 12


@pytest.mark.django_db
def test_external_tool_approval_log_survives_message_delete(all_apps_user):
    user = all_apps_user("external-approval-delete")
    chat = Chat.objects.create(user=user, title="Approval delete chat")
    message = Message.objects.create(chat=chat, text="", is_bot=True)

    log = ExternalToolApprovalLog.objects.create(
        user=user,
        message=message,
        tool_call_id="call_termium_delete",
        approval_request_id="call_termium_delete",
        tool_name="termium_lookup",
        tool_label="Termium lookup",
        external_service_name="TERMIUM Plus®",
        query='{"query": "cabinet confidence", "index": "ent"}',
        tool_arguments={"query": "cabinet confidence", "index": "ent"},
        decision=EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
        approval_source="manual",
        pii_flagged=True,
        pii_flag_source=EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM,
    )

    log_id = log.id
    message_id = message.id

    message.delete()

    log.refresh_from_db()
    assert log.id == log_id
    assert log.message is None
    assert log.message_reference_id == message_id
