import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.alert import Alert


@pytest.fixture(autouse=True)
def setup_database():
    """Reset database tables before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "threatflow-ingestion"


def test_ingest_source_a_valid(client):
    payload = {
        "alert_id": "A-1001",
        "external_alert_id": "A-1001",
        "account_id": "C-101",
        "transaction_id": "TX-5001",
        "event_timestamp": "2026-08-23T09:10:00Z",
        "amount": 15000.0,
        "currency": "INR",
        "location": "Chennai",
        "device_id": "DEV-22",
        "risk_level": "HIGH",
        "merchant": "Amazon",
    }
    response = client.post("/alerts/source-a", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["alert_id"] == "A-1001"
    assert data["source"] == "SOURCE_A"
    assert data["account_id"] == "C-101"
    assert data["received_at"] is not None
    assert data["received_timestamp"] is not None


def test_ingest_source_b_valid(client):
    payload = {
        "alert_id": "B-2001",
        "account_id": "C-102",
        "transaction_id": "TX-5002",
        "event_timestamp": "2026-08-23T09:12:00Z",
        "amount": 5000.0,
        "location": "Singapore",
        "risk_level": "MEDIUM",
    }
    response = client.post("/alerts/source-b", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["source"] == "SOURCE_B"
    assert data["location"] == "Singapore"


def test_ingest_source_c_valid(client):
    payload = {
        "alert_id": "C-3001",
        "account_id": "C-103",
        "transaction_id": "TX-5003",
        "event_timestamp": "2026-08-23T09:15:00Z",
        "amount": 25000.0,
        "location": "Mumbai",
        "risk_level": "CRITICAL",
    }
    response = client.post("/alerts/source-c", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["source"] == "SOURCE_C"
    assert data["risk_level"] == "CRITICAL"


def test_ingest_missing_required_field(client):
    # Missing account_id and event_timestamp
    payload = {
        "alert_id": "A-1001",
        "amount": 1000.0,
    }
    response = client.post("/alerts/source-a", json=payload)
    assert response.status_code == 422


def test_ingest_unknown_source(client):
    payload = {
        "alert_id": "X-9001",
        "source": "SOURCE_X",
        "account_id": "C-101",
        "event_timestamp": "2026-08-23T09:10:00Z",
    }
    response = client.post("/api/alerts", json=payload)
    assert response.status_code == 422


def test_ingest_invalid_risk_level(client):
    payload = {
        "alert_id": "A-1001",
        "account_id": "C-101",
        "event_timestamp": "2026-08-23T09:10:00Z",
        "risk_level": "INVALID_RISK",
    }
    response = client.post("/alerts/source-a", json=payload)
    assert response.status_code == 422


def test_ingest_negative_amount(client):
    payload = {
        "alert_id": "A-1001",
        "account_id": "C-101",
        "event_timestamp": "2026-08-23T09:10:00Z",
        "amount": -500.0,
    }
    response = client.post("/alerts/source-a", json=payload)
    assert response.status_code == 422


def test_ingest_incomplete_evidence_accepted(client):
    # Incomplete alert: customer_id, location, device_id, risk_level, amount are None
    payload = {
        "alert_id": "A-1004",
        "source": "SOURCE_A",
        "transaction_id": "TX-1004",
        "account_id": "C-104",
        "event_timestamp": "2026-08-23T09:20:00Z",
        "customer_id": None,
        "amount": None,
        "location": None,
        "device_id": None,
        "risk_level": None,
    }
    response = client.post("/api/alerts", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    alert = data["alert"]
    assert alert["account_id"] == "C-104"
    assert alert["amount"] is None
    assert alert["location"] is None
    assert alert["risk_level"] is None


def test_timestamp_handling_received_at_generated(client):
    payload = {
        "alert_id": "A-1001",
        "account_id": "C-101",
        "transaction_id": "TX-5001",
        "event_timestamp": "2026-08-23T09:10:05Z",
    }
    response = client.post("/alerts/source-a", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["event_timestamp"].startswith("2026-08-23T09:10:05")
    assert "received_at" in data and data["received_at"] is not None
    assert "received_timestamp" in data and data["received_timestamp"] is not None


def test_duplicate_alerts_preserved(client):
    payload = {
        "alert_id": "B-2001",
        "account_id": "C-101",
        "transaction_id": "TX-5001",
        "event_timestamp": "2026-08-23T10:00:05Z",
        "amount": 15000.0,
    }
    # Ingest same alert twice
    res1 = client.post("/alerts/source-b", json=payload)
    res2 = client.post("/alerts/source-b", json=payload)

    assert res1.status_code == 201
    assert res2.status_code == 201

    # Verify both exist in stored alerts
    get_res = client.get("/alerts")
    assert get_res.status_code == 200
    alerts = get_res.json()
    assert len(alerts) == 2
    assert alerts[0]["alert_id"] == "B-2001"
    assert alerts[1]["alert_id"] == "B-2001"
    assert alerts[0]["id"] != alerts[1]["id"]


def test_out_of_order_preservation(client):
    # Alert 1: Newer timestamp (10:00:15) delivered first
    p1 = {
        "alert_id": "C-3001",
        "source": "SOURCE_C",
        "account_id": "C-101",
        "transaction_id": "TX-5001",
        "event_timestamp": "2026-08-23T10:00:15Z",
    }
    # Alert 2: Older timestamp (09:10:05) delivered second
    p2 = {
        "alert_id": "A-1001",
        "source": "SOURCE_A",
        "account_id": "C-101",
        "transaction_id": "TX-5001",
        "event_timestamp": "2026-08-23T09:10:05Z",
    }

    res1 = client.post("/api/alerts", json=p1)
    res2 = client.post("/api/alerts", json=p2)

    assert res1.status_code == 202
    assert res2.status_code == 202

    # Verify arrival order is preserved (C-3001 first, A-1001 second)
    get_res = client.get("/alerts")
    alerts = get_res.json()
    assert len(alerts) == 2
    assert alerts[0]["alert_id"] == "C-3001"
    assert alerts[1]["alert_id"] == "A-1001"


def test_mock_replay_endpoint(client):
    response = client.post("/alerts/replay", json={"scenario": "conflict"})
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["scenario"] == "conflict"
    assert data["count"] == 3
    assert len(data["alerts"]) == 3

    # Check GET /alerts
    get_res = client.get("/alerts")
    assert get_res.status_code == 200
    assert len(get_res.json()) == 3
