from fastapi.testclient import TestClient

from app.main import app
from app.schemas.risk_result import RiskResult
from app.services.action_service import (
    clear_actions,
    create_action,
    store_action,
)

client = TestClient(app)


def make_risk_result(event_id: str, score: float, level: str) -> RiskResult:
    return RiskResult(
        event_id=event_id,
        risk_score=score,
        risk_level=level,
        matched_rules=[],
        evaluation_trace=[],
        rule_version=1,
    )


def test_get_actions_returns_prioritized_actions():
    clear_actions()

    low = create_action(
        make_risk_result("event-low", 20, "LOW")
    )

    critical = create_action(
        make_risk_result("event-critical", 90, "CRITICAL")
    )

    high = create_action(
        make_risk_result("event-high", 65, "HIGH")
    )

    store_action(low)
    store_action(critical)
    store_action(high)

    response = client.get("/actions")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 3

    assert data[0]["event_id"] == "event-critical"
    assert data[1]["event_id"] == "event-high"
    assert data[2]["event_id"] == "event-low"


def test_get_actions_returns_empty_list():
    clear_actions()

    response = client.get("/actions")

    assert response.status_code == 200
    assert response.json() == []


def test_api_action_contains_required_fields():
    clear_actions()

    action = create_action(
        make_risk_result("event-100", 85, "CRITICAL")
    )

    store_action(action)

    response = client.get("/actions")

    assert response.status_code == 200

    result = response.json()[0]

    required_fields = {
        "action_id",
        "event_id",
        "priority",
        "risk_score",
        "risk_level",
        "recommended_action",
        "created_at",
        "explanation",
    }

    assert required_fields.issubset(result.keys())
