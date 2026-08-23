"""Safe, recursive evaluation of a Condition tree against an event mapping.

Missing or null fields never raise — they are recorded as "unavailable" and
the condition is treated as not matched, since a rule can't be positively
confirmed without the evidence it depends on.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.rules.models import Condition
from app.rules.operators import apply_operator


@dataclass
class ConditionEvalResult:
    matched: bool
    missing_fields: list[str] = field(default_factory=list)
    detail: str = ""


def _merge_missing(results: list[ConditionEvalResult]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for result in results:
        for name in result.missing_fields:
            if name not in seen:
                seen.add(name)
                merged.append(name)
    return merged


def evaluate_condition(condition: Condition, event: Mapping[str, Any]) -> ConditionEvalResult:
    if condition.AND is not None:
        sub_results = [evaluate_condition(sub, event) for sub in condition.AND]
        matched = all(r.matched for r in sub_results)
        detail = "AND(" + ", ".join(r.detail for r in sub_results) + ")"
        return ConditionEvalResult(matched, _merge_missing(sub_results), detail)

    if condition.OR is not None:
        sub_results = [evaluate_condition(sub, event) for sub in condition.OR]
        matched = any(r.matched for r in sub_results)
        detail = "OR(" + ", ".join(r.detail for r in sub_results) + ")"
        return ConditionEvalResult(matched, _merge_missing(sub_results), detail)

    field_name = condition.field
    op = condition.op
    rule_value = condition.value

    if field_name not in event or event.get(field_name) is None:
        return ConditionEvalResult(
            matched=False,
            missing_fields=[field_name],
            detail=f"field '{field_name}' unavailable",
        )

    field_value = event[field_name]
    try:
        matched = apply_operator(op, field_value, rule_value)
    except TypeError as exc:
        return ConditionEvalResult(
            matched=False,
            missing_fields=[field_name],
            detail=f"field '{field_name}' incompatible for op '{op}': {exc}",
        )

    return ConditionEvalResult(
        matched=matched,
        missing_fields=[],
        detail=f"{field_name} {field_value!r} {op} {rule_value!r} -> {matched}",
    )
