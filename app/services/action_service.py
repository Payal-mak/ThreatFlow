"""Action generation and deterministic prioritization service."""

from datetime import datetime, timezone
from typing import Iterable

from app.schemas.action import Action
from app.schemas.risk_result import RiskResult


RISK_LEVEL_PRIORITY = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}

RECOMMENDED_ACTIONS = {
    "CRITICAL": "BLOCK_TRANSACTION_AND_ESCALATE",
    "HIGH": "MANUAL_REVIEW",
    "MEDIUM": "REVIEW",
    "LOW": "MONITOR",
}

_ACTIONS: list[Action] = []


def calculate_priority(risk_level: str, risk_score: float) -> int:
    """Calculate a deterministic priority from risk level and score.

    Higher risk produces a higher priority.
    The risk score is used as the fine-grained priority within
    the same risk level.
    """
    level_weight = RISK_LEVEL_PRIORITY.get(risk_level.upper(), 0)
    return level_weight * 100 + int(risk_score)


def recommended_action(risk_level: str) -> str:
    """Return the recommended action for a risk level."""
    return RECOMMENDED_ACTIONS.get(
        risk_level.upper(),
        "MONITOR",
    )


def create_action(risk_result: RiskResult) -> Action:
    """Convert a RiskResult into a deterministic Action."""
    risk_level = risk_result.risk_level.upper()

    priority = calculate_priority(
        risk_level=risk_level,
        risk_score=risk_result.risk_score,
    )

    return Action(
        action_id=f"action-{risk_result.event_id}",
        event_id=risk_result.event_id,
        priority=priority,
        risk_score=risk_result.risk_score,
        risk_level=risk_level,
        recommended_action=recommended_action(risk_level),
        created_at=datetime.now(timezone.utc),
        explanation=_build_explanation(risk_result),
    )


def prioritize_actions(actions: Iterable[Action]) -> list[Action]:
    """Sort actions deterministically by priority, score, and event ID."""
    return sorted(
        actions,
        key=lambda action: (
            -action.priority,
            -action.risk_score,
            action.event_id,
        ),
    )


def _build_explanation(risk_result: RiskResult) -> str:
    """Build a concise explanation from upstream rule evaluation data."""
    matched = risk_result.matched_rules

    if not matched:
        return (
            f"Risk score {risk_result.risk_score} with no matching rules."
        )

    rule_ids = ", ".join(rule.rule_id for rule in matched)

    return (
        f"Risk score {risk_result.risk_score} "
        f"({risk_result.risk_level}) based on matched rules: {rule_ids}."
    )


def store_action(action: Action) -> Action:
    """Store an action for retrieval through the actions API."""

    # Replace an existing action for the same event to keep the operation idempotent.
    global _ACTIONS

    _ACTIONS = [
        existing
        for existing in _ACTIONS
        if existing.event_id != action.event_id
    ]

    _ACTIONS.append(action)
    return action


def get_actions() -> list[Action]:
    """Return all currently stored actions in deterministic priority order."""

    return prioritize_actions(_ACTIONS)


def clear_actions() -> None:
    """Clear stored actions. Primarily useful for tests."""

    _ACTIONS.clear()
