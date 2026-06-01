import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Pattern

APPROVAL_SOURCE_CACHE = "cache"
APPROVAL_SOURCE_MANUAL = "manual"
APPROVAL_SOURCE_QUERY_POLICY = "query_policy"
APPROVAL_SOURCE_USER_ALLOWLIST = "user_allowlist"


@dataclass(frozen=True)
class ApprovalDecision:
    auto_approve: bool
    approval_source: str = APPROVAL_SOURCE_MANUAL
    matched_rule: str | None = None


MANUAL_APPROVAL_DECISION = ApprovalDecision(auto_approve=False)


@dataclass(frozen=True)
class ApprovalPolicyContext:
    user: Any
    chat: Any
    tool: Any
    function_call: dict
    arguments: dict


ApprovalValidatorResult = ApprovalDecision | bool | None
ApprovalValidator = Callable[
    [ApprovalPolicyContext],
    ApprovalValidatorResult | Awaitable[ApprovalValidatorResult],
]


def _normalize_validator_result(
    result: ApprovalValidatorResult,
    *,
    approval_source: str,
    rule_name: str | None,
) -> ApprovalDecision:
    if isinstance(result, ApprovalDecision):
        return result
    if result:
        return ApprovalDecision(
            auto_approve=True,
            approval_source=approval_source,
            matched_rule=rule_name,
        )
    return MANUAL_APPROVAL_DECISION


async def _resolve_validator_result(
    validator: ApprovalValidator,
    context: ApprovalPolicyContext,
    *,
    approval_source: str,
    rule_name: str | None,
) -> ApprovalDecision:
    result = validator(context)
    if inspect.isawaitable(result):
        result = await result
    return _normalize_validator_result(
        result,
        approval_source=approval_source,
        rule_name=rule_name,
    )


def _get_argument_value(arguments: dict, field_path: str) -> Any:
    current = arguments
    for part in field_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@dataclass
class ApprovalRule:
    validator: ApprovalValidator
    approval_source: str = APPROVAL_SOURCE_QUERY_POLICY
    name: str | None = None


@dataclass
class ApprovalPolicy:
    rules: list[ApprovalRule] = field(default_factory=list)
    default_decision: ApprovalDecision = MANUAL_APPROVAL_DECISION

    async def evaluate(self, context: ApprovalPolicyContext) -> ApprovalDecision:
        for rule in self.rules:
            decision = await _resolve_validator_result(
                rule.validator,
                context,
                approval_source=rule.approval_source,
                rule_name=rule.name,
            )
            if decision.auto_approve:
                return decision
        return self.default_decision


def exact_argument_match(
    field_path: str,
    *,
    allowed_values: set[Any] | list[Any] | tuple[Any, ...],
    approval_source: str = APPROVAL_SOURCE_QUERY_POLICY,
    rule_name: str | None = None,
) -> ApprovalRule:
    allowed = set(allowed_values)

    def validator(context: ApprovalPolicyContext) -> bool:
        return _get_argument_value(context.arguments, field_path) in allowed

    return ApprovalRule(
        validator=validator,
        approval_source=approval_source,
        name=rule_name,
    )


def regex_argument_match(
    field_path: str,
    pattern: str | Pattern[str],
    *,
    approval_source: str = APPROVAL_SOURCE_QUERY_POLICY,
    rule_name: str | None = None,
    flags: int = 0,
) -> ApprovalRule:
    compiled = re.compile(pattern, flags) if isinstance(pattern, str) else pattern

    def validator(context: ApprovalPolicyContext) -> bool:
        value = _get_argument_value(context.arguments, field_path)
        return isinstance(value, str) and bool(compiled.search(value))

    return ApprovalRule(
        validator=validator,
        approval_source=approval_source,
        name=rule_name,
    )


def custom_approval_rule(
    validator: ApprovalValidator,
    *,
    approval_source: str = APPROVAL_SOURCE_QUERY_POLICY,
    rule_name: str | None = None,
) -> ApprovalRule:
    return ApprovalRule(
        validator=validator,
        approval_source=approval_source,
        name=rule_name,
    )


def parse_function_call_arguments(function_call: dict) -> dict:
    arguments = function_call.get("arguments", {})
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return arguments if isinstance(arguments, dict) else {}


async def evaluate_approval_policy(
    *,
    tool,
    user,
    chat,
    function_call: dict,
) -> ApprovalDecision:
    policy = getattr(tool, "approval_policy", None)
    if not policy:
        return MANUAL_APPROVAL_DECISION

    context = ApprovalPolicyContext(
        user=user,
        chat=chat,
        tool=tool,
        function_call=function_call,
        arguments=parse_function_call_arguments(function_call),
    )
    return await policy.evaluate(context)
