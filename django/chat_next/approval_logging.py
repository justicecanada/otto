from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from django.utils import timezone

from chat_next._tools.approval import (
    APPROVAL_SOURCE_MANUAL,
    parse_function_call_arguments,
)
from chat_next._tools.base import get_tool_display_name
from chat_next.models import (
    EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED,
    EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED,
    EXTERNAL_TOOL_APPROVAL_DECISION_DENIED,
    EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS,
    EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO,
    ExternalToolApprovalLog,
)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _get_external_tool(tool_name: str):
    if not tool_name:
        return None

    from chat_next.tools import TOOL_REGISTRY

    tool = TOOL_REGISTRY.get(tool_name)
    if not tool or not getattr(tool, "is_external_tool", False):
        return None
    return tool


def _get_tool_call_id(function_call: dict) -> str:
    return str(
        (function_call or {}).get("call_id") or (function_call or {}).get("id") or ""
    )


def _stringify_query(raw_arguments, parsed_arguments: dict) -> str:
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return "{}"
        try:
            normalized = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(normalized, dict):
            return json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return stripped

    if isinstance(raw_arguments, dict):
        return json.dumps(raw_arguments, ensure_ascii=False, sort_keys=True)

    if parsed_arguments:
        return json.dumps(parsed_arguments, ensure_ascii=False, sort_keys=True)

    return "{}"


def _is_pii_flagged(
    *, function_call: dict | None, step_details: dict | None, arguments: dict
) -> bool:
    for value in (
        (step_details or {}).get("pii_flagged"),
        ((step_details or {}).get("risk_review") or {}).get("pii_flagged"),
        (function_call or {}).get("pii_flagged"),
        ((function_call or {}).get("risk_review") or {}).get("pii_flagged"),
        arguments.get("pii_flagged"),
        arguments.get("contains_pii"),
    ):
        if value is True:
            return True
    return False


def _get_risk_review(*, function_call: dict | None, step_details: dict | None) -> dict:
    risk_review = (step_details or {}).get("risk_review") or {}
    if not risk_review:
        risk_review = (function_call or {}).get("risk_review") or {}
    return risk_review if isinstance(risk_review, dict) else {}


def _get_pii_entity_categories(
    *, function_call: dict | None, step_details: dict | None
) -> list[str]:
    categories = []
    risk_review = _get_risk_review(
        function_call=function_call,
        step_details=step_details,
    )

    for category in risk_review.get("pii_entity_categories") or []:
        category_text = str(category).strip()
        if category_text and category_text not in categories:
            categories.append(category_text)

    return sorted(categories)


def _get_pii_flag_source(
    *, function_call: dict | None, step_details: dict | None, arguments: dict
) -> str:
    risk_review = _get_risk_review(
        function_call=function_call,
        step_details=step_details,
    )

    if risk_review.get("pii_flagged"):
        review_sources = set(risk_review.get("review_sources") or [])
        if "azure_language" in review_sources:
            return EXTERNAL_TOOL_APPROVAL_PII_SOURCE_AZURE_LANGUAGE_API
        return EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LOCAL_CHECKS

    if _is_pii_flagged(
        function_call=function_call,
        step_details=step_details,
        arguments=arguments,
    ):
        return EXTERNAL_TOOL_APPROVAL_PII_SOURCE_LLM

    return EXTERNAL_TOOL_APPROVAL_PII_SOURCE_NO


def _get_existing_external_tool_approval_log(
    *, message_id_snapshot: int | None, tool_call_id: str, decision: str
):
    records = ExternalToolApprovalLog.objects.filter(
        message_id_snapshot=message_id_snapshot,
        tool_call_id=tool_call_id,
    )

    if decision == EXTERNAL_TOOL_APPROVAL_DECISION_PENDING:
        return (
            records.filter(decision=decision).order_by("-created_at", "-id").first()
            or records.order_by("-created_at", "-id").first()
        )

    return (
        records.exclude(decision=EXTERNAL_TOOL_APPROVAL_DECISION_PENDING)
        .order_by("-decided_at", "-created_at", "-id")
        .first()
        or records.order_by("-created_at", "-id").first()
    )


def _upsert_external_tool_approval_log(
    *,
    message,
    user,
    function_call: dict,
    decision: str,
    approval_source: str,
    approval_request_id: str = "",
    displayed_at=None,
    decided_at=None,
    step_details: dict | None = None,
):
    tool_name = (
        (function_call or {}).get("name") or (step_details or {}).get("name") or ""
    )
    tool = _get_external_tool(tool_name)
    if not tool:
        return None

    tool_call_id = _get_tool_call_id(function_call)
    if not tool_call_id:
        return None

    arguments = parse_function_call_arguments(function_call or {})
    raw_arguments = (function_call or {}).get("arguments", arguments)
    query = _stringify_query(raw_arguments, arguments)
    pii_flagged = _is_pii_flagged(
        function_call=function_call,
        step_details=step_details,
        arguments=arguments,
    )
    pii_flag_source = _get_pii_flag_source(
        function_call=function_call,
        step_details=step_details,
        arguments=arguments,
    )
    pii_entity_categories = _get_pii_entity_categories(
        function_call=function_call,
        step_details=step_details,
    )
    displayed_at = _parse_datetime(displayed_at)
    decided_at = _parse_datetime(decided_at)
    message_id_snapshot = getattr(message, "id", None)

    record = _get_existing_external_tool_approval_log(
        message_id_snapshot=message_id_snapshot,
        tool_call_id=tool_call_id,
        decision=decision,
    )

    if record is None:
        record = ExternalToolApprovalLog(
            user=user,
            message=message,
            message_id_snapshot=message_id_snapshot,
            tool_call_id=tool_call_id,
            approval_request_id=approval_request_id or "",
            tool_name=tool_name,
            tool_label=str(tool.display_name or get_tool_display_name(tool_name)),
            external_service_name=str(tool.external_service_name or ""),
            query=query,
            tool_arguments=arguments,
            decision=decision,
            approval_source=approval_source or "",
            pii_flagged=pii_flagged,
            pii_flag_source=pii_flag_source,
            pii_entity_categories=pii_entity_categories,
            displayed_at=displayed_at,
            decided_at=decided_at,
        )
        record.save()
        return record

    if (
        displayed_at is None
        and decision
        in {
            EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED,
            EXTERNAL_TOOL_APPROVAL_DECISION_DENIED,
            EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED,
        }
        and record.displayed_at is not None
    ):
        displayed_at = record.displayed_at

    fields_to_update = []
    if user and record.user_id != getattr(user, "id", None):
        record.user = user
        fields_to_update.append("user")
    if message and record.message_id != getattr(message, "id", None):
        record.message = message
        fields_to_update.append("message")
    if record.message_id_snapshot != message_id_snapshot:
        record.message_id_snapshot = message_id_snapshot
        fields_to_update.append("message_id_snapshot")
    if record.approval_request_id != (approval_request_id or ""):
        record.approval_request_id = approval_request_id or ""
        fields_to_update.append("approval_request_id")
    if record.tool_name != tool_name:
        record.tool_name = tool_name
        fields_to_update.append("tool_name")

    tool_label = str(tool.display_name or get_tool_display_name(tool_name))
    if record.tool_label != tool_label:
        record.tool_label = tool_label
        fields_to_update.append("tool_label")

    external_service_name = str(tool.external_service_name or "")
    if record.external_service_name != external_service_name:
        record.external_service_name = external_service_name
        fields_to_update.append("external_service_name")

    if record.query != query:
        record.query = query
        fields_to_update.append("query")
    if record.tool_arguments != arguments:
        record.tool_arguments = arguments
        fields_to_update.append("tool_arguments")
    if record.decision != decision and not (
        decision == EXTERNAL_TOOL_APPROVAL_DECISION_PENDING
        and record.decision != EXTERNAL_TOOL_APPROVAL_DECISION_PENDING
    ):
        record.decision = decision
        fields_to_update.append("decision")
    if record.approval_source != (approval_source or ""):
        record.approval_source = approval_source or ""
        fields_to_update.append("approval_source")
    if record.pii_flagged != pii_flagged:
        record.pii_flagged = pii_flagged
        fields_to_update.append("pii_flagged")
    if record.pii_flag_source != pii_flag_source:
        record.pii_flag_source = pii_flag_source
        fields_to_update.append("pii_flag_source")
    if pii_entity_categories:
        if record.pii_entity_categories != pii_entity_categories:
            record.pii_entity_categories = pii_entity_categories
            fields_to_update.append("pii_entity_categories")
    elif not pii_flagged and record.pii_entity_categories:
        record.pii_entity_categories = []
        fields_to_update.append("pii_entity_categories")
    if displayed_at and record.displayed_at is None:
        record.displayed_at = displayed_at
        fields_to_update.append("displayed_at")
    if decided_at and record.decided_at != decided_at:
        record.decided_at = decided_at
        fields_to_update.append("decided_at")

    if fields_to_update:
        record.save(update_fields=[*fields_to_update, "updated_at"])

    return record


def sync_external_tool_approval_logs_from_processing_steps(
    *,
    message,
    user,
    raw_processing_steps: Iterable[dict] | None,
    pending_local_tool: dict | None = None,
    recorded_at=None,
):
    recorded_at = _parse_datetime(recorded_at) or timezone.now()
    pending_local_tool = pending_local_tool or {}
    pending_request_id = str(pending_local_tool.get("call_id") or "")
    pending_displayed_at = (
        _parse_datetime(pending_local_tool.get("approval_requested_at")) or recorded_at
    )

    for step in raw_processing_steps or []:
        if not isinstance(step, dict) or step.get("type") != "tool_call":
            continue
        if step.get("tool_type") not in {"function", "function_call"}:
            continue

        details = step.get("details") or {}
        function_call = {
            "name": details.get("name"),
            "call_id": details.get("call_id"),
            "arguments": details.get("arguments", {}),
        }
        approval_source = details.get("approval_source") or ""
        if step.get("status") == "waiting_approval":
            _upsert_external_tool_approval_log(
                message=message,
                user=user,
                function_call=function_call,
                decision=EXTERNAL_TOOL_APPROVAL_DECISION_PENDING,
                approval_source=approval_source or APPROVAL_SOURCE_MANUAL,
                approval_request_id=str(
                    details.get("approval_request_id") or pending_request_id
                ),
                displayed_at=pending_displayed_at,
                step_details=details,
            )
            continue

        if approval_source and approval_source != APPROVAL_SOURCE_MANUAL:
            _upsert_external_tool_approval_log(
                message=message,
                user=user,
                function_call=function_call,
                decision=EXTERNAL_TOOL_APPROVAL_DECISION_AUTO_APPROVED,
                approval_source=approval_source,
                approval_request_id=str(
                    details.get("approval_request_id") or pending_request_id
                ),
                decided_at=recorded_at,
                step_details=details,
            )


def mark_external_tool_approval_decision(
    *,
    message,
    user,
    function_calls: Iterable[dict] | None,
    approved: bool,
    decided_at=None,
    approval_request_id: str = "",
    fallback_displayed_at=None,
):
    decided_at = _parse_datetime(decided_at) or timezone.now()
    fallback_displayed_at = _parse_datetime(fallback_displayed_at)
    decision = (
        EXTERNAL_TOOL_APPROVAL_DECISION_APPROVED
        if approved
        else EXTERNAL_TOOL_APPROVAL_DECISION_DENIED
    )

    for function_call in function_calls or []:
        _upsert_external_tool_approval_log(
            message=message,
            user=user,
            function_call=function_call,
            decision=decision,
            approval_source=APPROVAL_SOURCE_MANUAL,
            approval_request_id=approval_request_id,
            displayed_at=fallback_displayed_at,
            decided_at=decided_at,
        )
