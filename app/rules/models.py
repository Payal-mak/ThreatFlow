"""Pydantic models for the rule DSL: Condition, Rule, RiskLevelBand, RuleSet."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.rules.operators import SUPPORTED_OPERATORS


class Condition(BaseModel):
    """A leaf comparison (field/op/value) or a branch (AND/OR of sub-conditions).

    Exactly one of "leaf" or "branch" shape is valid, enforced below.
    """

    field: str | None = None
    op: str | None = None
    value: Any = None
    AND: list["Condition"] | None = None
    OR: list["Condition"] | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "Condition":
        has_and = self.AND is not None
        has_or = self.OR is not None
        if has_and and has_or:
            raise ValueError("a condition cannot combine both AND and OR at the same level")

        if has_and or has_or:
            if self.field is not None or self.op is not None:
                raise ValueError("AND/OR conditions must not also set 'field' or 'op'")
            branch = self.AND if has_and else self.OR
            if not branch:
                raise ValueError("AND/OR conditions require at least one sub-condition")
            return self

        if self.field is None or self.op is None:
            raise ValueError("a leaf condition requires both 'field' and 'op'")
        if self.op not in SUPPORTED_OPERATORS:
            raise ValueError(
                f"unsupported operator {self.op!r}; supported: {sorted(SUPPORTED_OPERATORS)}"
            )
        return self


class Rule(BaseModel):
    id: str
    version: int = 1
    score: float
    enabled: bool = True
    description: str | None = None
    condition: Condition


class RiskLevelBand(BaseModel):
    level: str
    min: float
    max: float | None = None


def default_risk_levels() -> list[RiskLevelBand]:
    return [
        RiskLevelBand(level="LOW", min=0, max=29),
        RiskLevelBand(level="MEDIUM", min=30, max=59),
        RiskLevelBand(level="HIGH", min=60, max=79),
        RiskLevelBand(level="CRITICAL", min=80, max=None),
    ]


class RuleSet(BaseModel):
    ruleset_version: int | str = 1
    risk_levels: list[RiskLevelBand] = Field(default_factory=default_risk_levels)
    rules: list[Rule]

    @model_validator(mode="after")
    def _validate_unique_rule_ids(self) -> "RuleSet":
        ids = [rule.id for rule in self.rules]
        duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids in configuration: {duplicates}")
        return self
