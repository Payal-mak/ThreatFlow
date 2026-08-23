"""Comparison operators supported by the rule DSL.

No eval() and no arbitrary code execution: every operator is an explicit,
whitelisted function.
"""

from typing import Any

NUMERIC_OPERATORS = {">", ">=", "<", "<="}
EQUALITY_OPERATORS = {"==", "!="}
MEMBERSHIP_OPERATORS = {"IN", "CONTAINS"}
SUPPORTED_OPERATORS = NUMERIC_OPERATORS | EQUALITY_OPERATORS | MEMBERSHIP_OPERATORS


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def apply_operator(op: str, field_value: Any, rule_value: Any) -> bool:
    """Apply a whitelisted operator. Raises TypeError on incompatible operands
    so the caller can turn that into a safe "unavailable" trace entry instead
    of letting a bad comparison crash the engine.
    """
    if op in NUMERIC_OPERATORS:
        if not _is_number(field_value) or not _is_number(rule_value):
            raise TypeError(
                f"operator '{op}' requires numeric operands, got "
                f"{type(field_value).__name__!r} and {type(rule_value).__name__!r}"
            )
        if op == ">":
            return field_value > rule_value
        if op == ">=":
            return field_value >= rule_value
        if op == "<":
            return field_value < rule_value
        return field_value <= rule_value  # "<="

    if op == "==":
        return field_value == rule_value
    if op == "!=":
        return field_value != rule_value

    if op == "IN":
        if not isinstance(rule_value, (list, tuple, set)):
            raise TypeError("operator 'IN' requires the rule value to be a list")
        return field_value in rule_value

    if op == "CONTAINS":
        if not isinstance(field_value, (list, tuple, set, str)):
            raise TypeError(
                f"operator 'CONTAINS' requires a collection field value, got "
                f"{type(field_value).__name__!r}"
            )
        return rule_value in field_value

    raise ValueError(f"unsupported operator: {op!r}")
