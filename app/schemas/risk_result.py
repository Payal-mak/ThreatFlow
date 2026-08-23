"""Output contract of the rule engine, consumed by the actions/audit layer."""

from pydantic import BaseModel, Field


class RuleEvaluationTrace(BaseModel):
    """Explainability record for a single rule's evaluation against one event."""

    rule_id: str
    version: int
    matched: bool
    score: float
    reason: str
    missing_fields: list[str] = Field(default_factory=list)


class RiskResult(BaseModel):
    event_id: str
    risk_score: float
    risk_level: str
    matched_rules: list[RuleEvaluationTrace]
    evaluation_trace: list[RuleEvaluationTrace]
    rule_version: int | str
