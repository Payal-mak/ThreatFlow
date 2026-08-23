from datetime import timezone

from app.schemas.risk_result import RiskResult, RuleEvaluationTrace
from app.services.action_service import (
    create_action,
    prioritize_actions,
    recommended_action,
)


def make_risk_result(
    event_id: str = "event-1",
    score: float = 90,
    level: str = "CRITICAL",
) -> RiskResult:
    return RiskResult(
        event_id=event_id,
        risk_score=score,
        risk_level=level,
        matched_rules=[
            RuleEvaluationTrace(
                rule_id="HIGH_AMOUNT",
                version=1,
                matched=True,
                score=40,
                reason="Amount exceeds threshold",
            )
        ],
        evaluation_trace=[],
        rule_version=1,
    )


def test_low_action():
    action = create_action(make_risk_result("event-low", 10, "LOW"))

    assert action.event_id == "event-low"
    assert action.risk_score == 10
    assert action.risk_level == "LOW"
    assert action.recommended_action == "MONITOR"


def test_medium_action():
    action = create_action(make_risk_result("event-medium", 40, "MEDIUM"))

    assert action.risk_level == "MEDIUM"
    assert action.recommended_action == "REVIEW"


def test_high_action():
    action = create_action(make_risk_result("event-high", 70, "HIGH"))

    assert action.risk_level == "HIGH"
    assert action.recommended_action == "MANUAL_REVIEW"


def test_critical_action():
    action = create_action(make_risk_result("event-critical", 90, "CRITICAL"))

    assert action.risk_level == "CRITICAL"
    assert action.recommended_action == "BLOCK_TRANSACTION_AND_ESCALATE"


def test_priority_ordering():
    actions = [
        create_action(make_risk_result("event-low", 10, "LOW")),
        create_action(make_risk_result("event-critical", 90, "CRITICAL")),
        create_action(make_risk_result("event-high", 70, "HIGH")),
        create_action(make_risk_result("event-medium", 40, "MEDIUM")),
    ]

    ordered = prioritize_actions(actions)

    assert [action.event_id for action in ordered] == [
        "event-critical",
        "event-high",
        "event-medium",
        "event-low",
    ]


def test_equal_score_is_deterministic():
    actions = [
        create_action(make_risk_result("event-b", 80, "CRITICAL")),
        create_action(make_risk_result("event-a", 80, "CRITICAL")),
    ]

    ordered = prioritize_actions(actions)

    assert [action.event_id for action in ordered] == [
        "event-a",
        "event-b",
    ]


def test_multiple_events():
    results = [
        make_risk_result("event-1", 95, "CRITICAL"),
        make_risk_result("event-2", 65, "HIGH"),
        make_risk_result("event-3", 35, "MEDIUM"),
    ]

    actions = [create_action(result) for result in results]

    assert len(actions) == 3
    assert {action.event_id for action in actions} == {
        "event-1",
        "event-2",
        "event-3",
    }


def test_recommended_action_mapping():
    assert recommended_action("LOW") == "MONITOR"
    assert recommended_action("MEDIUM") == "REVIEW"
    assert recommended_action("HIGH") == "MANUAL_REVIEW"
    assert (
        recommended_action("CRITICAL")
        == "BLOCK_TRANSACTION_AND_ESCALATE"
    )


def test_action_has_required_fields():
    action = create_action(make_risk_result())

    assert action.action_id == "action-event-1"
    assert action.event_id == "event-1"
    assert action.priority > 0
    assert action.created_at.tzinfo == timezone.utc
    assert action.explanation
