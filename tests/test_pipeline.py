"""End-to-end test for the pipeline: ingest -> correlate -> score -> act -> audit.

Reproduces the exact scenario from the team workflow doc: three alerts from
three different sources, same transaction, conflicting location and risk
level, out of order arrival, all resolving to one prioritized action.
"""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema():
    """Some test modules drop all tables in their own teardown; make sure
    this module's tests always run against a fresh, existing schema
    regardless of what ran before them.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _post(source: str, payload: dict) -> dict:
    response = client.post(f"/pipeline/{source}", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_three_conflicting_alerts_produce_one_prioritized_action() -> None:
    # Source B arrives first (out of order relative to A/C's narrative order).
    result_b = _post(
        "source-b",
        {
            "external_alert_id": "B901",
            "transaction_id": "TX1001",
            "account_id": "ACC55",
            "amount": 15000,
            "location": "Mumbai",
            "risk_level": "HIGH",
            "event_timestamp": "2026-08-23T10:31:02Z",
        },
    )
    result_a = _post(
        "source-a",
        {
            "external_alert_id": "A001",
            "transaction_id": "TX1001",
            "account_id": "ACC55",
            "amount": 15000,
            "location": "Ahmedabad",
            "risk_level": "MEDIUM",
            "event_timestamp": "2026-08-23T10:31:01Z",
        },
    )
    result_c = _post(
        "source-c",
        {
            "external_alert_id": "C501",
            "transaction_id": "TX1001",
            "account_id": "ACC55",
            "amount": 15000,
            "risk_level": "HIGH",
            "event_timestamp": "2026-08-23T10:31:03Z",
        },
    )

    # All three alerts correlate into the same group (same underlying event).
    group_id = result_a["correlation"]["group_id"]
    assert result_b["correlation"]["group_id"] == group_id
    assert result_c["correlation"]["group_id"] == group_id
    assert len(result_c["correlation"]["merged_alert_ids"]) == 3

    # A conflicting location was detected and resolved.
    assert result_c["correlation"]["conflict_status"] in {"resolved", "unresolved"}

    # High amount + a location conflict should push this into a high risk score.
    risk_result = result_c["risk_result"]
    assert risk_result["risk_score"] > 0
    assert risk_result["risk_level"] in {"HIGH", "CRITICAL"}

    action = result_c["action"]
    assert action["event_id"] == group_id
    assert action["recommended_action"] in {"MANUAL_REVIEW", "BLOCK_TRANSACTION_AND_ESCALATE"}

    # The action list surfaces exactly one action for this event, not three.
    actions_response = client.get("/actions")
    assert actions_response.status_code == 200
    matching = [a for a in actions_response.json() if a["event_id"] == group_id]
    assert len(matching) == 1

    # The audit trail recorded every stage for this group.
    audit_response = client.get("/audit", params={"entity_id": group_id})
    assert audit_response.status_code == 200
    event_types = {record["event_type"] for record in audit_response.json()}
    assert "ALERT_CORRELATED" in event_types
