"""Integration tests: Nithilan Ingestion → Canonical Alert → Srinivas Correlation/Conflict → Output.

Tests the REAL end-to-end flow through API endpoints and service layer.
No mocking — uses in-memory SQLite for full ORM + API round-trip.

Scenarios tested:
  1.  Single alert reaches the correlation layer.
  2.  Same transaction from multiple sources becomes one correlated event.
  3.  Exact duplicate does not create another logical event.
  4.  Same account + different transaction remains separate.
  5.  Out-of-order alerts are correlated correctly.
  6.  Late alert inside merge window joins the existing event.
  7.  Late alert outside merge window creates a separate event.
  8.  Conflicting location is detected and resolved/preserved.
  9.  Conflicting fraud_type is detected and resolved/preserved.
  10. Incomplete alert with null optional fields is processed without failure.
  11. Three-source event is correlated correctly.
  12. Repeated processing is deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.alert import Alert
from app.models.correlated_alert import CorrelatedAlert
from app.schemas.alert import AlertCreate
from app.services import correlation_service
from app.services.ingestion_service import create_alert


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session() -> Session:
    """Yield a fresh in-memory SQLite session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """FastAPI TestClient wired to the same in-memory DB."""
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ingest_via_api(
    client: TestClient, source: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST an alert to the ingestion endpoint and return the JSON response."""
    resp = client.post(f"/alerts/{source}", json=payload)
    assert resp.status_code == 201, f"Ingestion failed ({resp.status_code}): {resp.text}"
    return resp.json()


def _ingest_via_service(db: Session, source: str, **overrides) -> Alert:
    """Ingest an alert directly via the service layer (bypasses HTTP)."""
    defaults = {
        "external_alert_id": "ext-001",
        "account_id": "acct-123",
        "transaction_id": "txn-abc",
        "amount": 1500.00,
        "location": "New York",
        "event_timestamp": datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc),
        "fraud_type": "fraud",
        "description": "Suspicious activity",
    }
    defaults.update(overrides)
    payload = AlertCreate(**defaults)
    return create_alert(db, source=source, alert_in=payload)


def _correlate_and_fetch(
    db: Session, alert: Alert, window_seconds: int | None = None
) -> tuple[correlation_service.CorrelationResult, CorrelatedAlert | None]:
    """Run correlation on a single alert and fetch the resulting group from DB."""
    result = correlation_service.correlate_alert(db, alert, window_seconds=window_seconds)
    group = correlation_service.get_correlated_by_group(db, result.group_id)
    return result, group


# ---------------------------------------------------------------------------
# 1. Single alert reaches the correlation layer
# ---------------------------------------------------------------------------

class TestIntegration_SingleAlertReachesCorrelation:
    """Ingest one alert via API → correlate → verify group exists with correct snapshot."""

    def test_single_alert_via_api_then_correlate(self, client: TestClient, db_session: Session) -> None:
        payload = {
            "external_alert_id": "int-001",
            "account_id": "INT-ACCT-001",
            "transaction_id": "INT-TXN-001",
            "amount": 2500.00,
            "location": "Mumbai",
            "event_timestamp": "2026-08-23T10:00:00Z",
            "fraud_type": "fraud",
            "description": "Suspicious transfer",
        }

        # Step 1: Ingest via API
        alert_json = _ingest_via_api(client, "source-a", payload)
        assert alert_json["alert_id"] is not None
        assert alert_json["source"] == "SOURCE_A"
        assert alert_json["account_id"] == "INT-ACCT-001"
        assert alert_json["transaction_id"] == "INT-TXN-001"
        assert alert_json["fraud_type"] == "fraud"

        # Step 2: Fetch the Alert ORM object and correlate
        alert = db_session.query(Alert).filter(Alert.alert_id == alert_json["alert_id"]).one()
        result, group = _correlate_and_fetch(db_session, alert)

        # Step 3: Verify correlation output
        assert group is not None
        assert group.account_id == "INT-ACCT-001"
        assert group.transaction_id == "INT-TXN-001"
        assert group.amount == 2500.00
        assert group.location == "Mumbai"
        assert group.fraud_type == "fraud"
        assert group.conflict_status == "no_conflict"
        assert len(group.merged_alert_ids) == 1
        assert alert.id in group.merged_alert_ids

        # Step 4: Verify via Correlation API
        resp = client.get(f"/correlation/groups/{group.group_id}")
        assert resp.status_code == 200
        api_group = resp.json()
        assert api_group["account_id"] == "INT-ACCT-001"
        assert api_group["conflict_status"] == "no_conflict"


# ---------------------------------------------------------------------------
# 2. Same transaction from multiple sources → one correlated event
# ---------------------------------------------------------------------------

class TestIntegration_MultiSourceCorrelation:
    """Two alerts from different sources, same txn_id → one group, 2 merged IDs."""

    def test_two_sources_same_txn_correlate(self, client: TestClient, db_session: Session) -> None:
        ts = "2026-08-23T11:00:00Z"
        base = {
            "account_id": "INT-ACCT-002",
            "transaction_id": "INT-TXN-002",
            "amount": 5000.00,
            "location": "Delhi",
            "event_timestamp": ts,
            "fraud_type": "suspicious",
        }

        # Ingest from source-a
        a_json = _ingest_via_api(client, "source-a", {**base, "external_alert_id": "ms-001a"})
        alert_a = db_session.query(Alert).filter(Alert.alert_id == a_json["alert_id"]).one()
        r_a = correlation_service.correlate_alert(db_session, alert_a)

        # Ingest from source-b (5 seconds later — within merge window)
        b_json = _ingest_via_api(client, "source-b", {
            **base, "external_alert_id": "ms-001b",
            "event_timestamp": "2026-08-23T11:00:05Z",
        })
        alert_b = db_session.query(Alert).filter(Alert.alert_id == b_json["alert_id"]).one()
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        # Both must land in the same group
        assert r_a.group_id == r_b.group_id
        assert len(r_b.merged_alert_ids) == 2

        # Fetch via API and verify
        resp = client.get(f"/correlation/groups/{r_a.group_id}")
        assert resp.status_code == 200
        api_group = resp.json()
        assert len(api_group["merged_alert_ids"]) == 2
        assert alert_a.id in api_group["merged_alert_ids"]
        assert alert_b.id in api_group["merged_alert_ids"]


# ---------------------------------------------------------------------------
# 3. Exact duplicate does not create another logical event
# ---------------------------------------------------------------------------

class TestIntegration_ExactDuplicate:
    """Same source + same external_alert_id → duplicate flag, still one logical event."""

    def test_exact_duplicate_flagged(self, client: TestClient, db_session: Session) -> None:
        base = {
            "account_id": "INT-ACCT-003",
            "transaction_id": "INT-TXN-003",
            "amount": 1000.00,
            "location": "Chennai",
            "event_timestamp": "2026-08-23T12:00:00Z",
            "fraud_type": "fraud",
        }

        # First ingestion
        a_json = _ingest_via_api(client, "source-a", {**base, "external_alert_id": "dup-001"})
        alert_a = db_session.query(Alert).filter(Alert.alert_id == a_json["alert_id"]).one()
        r_a = correlation_service.correlate_alert(db_session, alert_a)
        assert r_a.is_duplicate is False

        # Second ingestion — same source, same external_alert_id
        b_json = _ingest_via_api(client, "source-a", {**base, "external_alert_id": "dup-001"})
        alert_b = db_session.query(Alert).filter(Alert.id == b_json["id"]).one()
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        # Should merge into same group and be flagged as duplicate
        assert r_b.group_id == r_a.group_id
        assert r_b.is_duplicate is True
        assert len(r_b.merged_alert_ids) == 2


# ---------------------------------------------------------------------------
# 4. Same account + different transaction → separate groups
# ---------------------------------------------------------------------------

class TestIntegration_DifferentTransactions:
    """Same account but different txn_id → separate correlation groups."""

    def test_different_txn_ids_separate_groups(self, client: TestClient, db_session: Session) -> None:
        common = {"account_id": "INT-ACCT-004", "amount": 3000.00, "location": "Hyderabad"}

        a_json = _ingest_via_api(client, "source-a", {
            **common, "external_alert_id": "sep-001a",
            "transaction_id": "INT-TXN-004A",
            "event_timestamp": "2026-08-23T13:00:00Z",
        })
        alert_a = db_session.query(Alert).filter(Alert.alert_id == a_json["alert_id"]).one()
        r_a = correlation_service.correlate_alert(db_session, alert_a)

        b_json = _ingest_via_api(client, "source-b", {
            **common, "external_alert_id": "sep-001b",
            "transaction_id": "INT-TXN-004B",
            "event_timestamp": "2026-08-23T13:00:05Z",
        })
        alert_b = db_session.query(Alert).filter(Alert.alert_id == b_json["alert_id"]).one()
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        assert r_a.group_id != r_b.group_id

        # Verify both groups appear in the listing
        resp = client.get("/correlation/groups")
        assert resp.status_code == 200
        groups = resp.json()
        group_ids = {g["group_id"] for g in groups}
        assert r_a.group_id in group_ids
        assert r_b.group_id in group_ids


# ---------------------------------------------------------------------------
# 5. Out-of-order alerts are correlated correctly
# ---------------------------------------------------------------------------

class TestIntegration_OutOfOrder:
    """Alerts arriving in reverse event_timestamp order still merge."""

    def test_reverse_order_correlates(self, client: TestClient, db_session: Session) -> None:
        ts_a = datetime(2026, 8, 23, 14, 0, 0, tzinfo=timezone.utc)
        ts_b = datetime(2026, 8, 23, 14, 2, 0, tzinfo=timezone.utc)

        # B (later event) arrives first
        b = _ingest_via_service(db_session, "source-b",
            external_alert_id="ooo-001b", account_id="INT-ACCT-005",
            transaction_id="INT-TXN-005", amount=4000.00,
            location="Bangalore", event_timestamp=ts_b, fraud_type="fraud")
        r_b = correlation_service.correlate_alert(db_session, b)

        # A (earlier event) arrives second
        a = _ingest_via_service(db_session, "source-a",
            external_alert_id="ooo-001a", account_id="INT-ACCT-005",
            transaction_id="INT-TXN-005", amount=4000.00,
            location="Bangalore", event_timestamp=ts_a, fraud_type="fraud")
        r_a = correlation_service.correlate_alert(db_session, a)

        assert r_a.group_id == r_b.group_id
        assert len(r_a.merged_alert_ids) == 2

        group = correlation_service.get_correlated_by_group(db_session, r_a.group_id)
        assert group is not None
        assert group.account_id == "INT-ACCT-005"


# ---------------------------------------------------------------------------
# 6. Late alert inside merge window joins existing event
# ---------------------------------------------------------------------------

class TestIntegration_LateAlertInsideWindow:
    """Alert arrives after another, event_timestamp within window → joins group."""

    def test_late_inside_window_joins(self, client: TestClient, db_session: Session) -> None:
        window = 60  # seconds
        ts_first = datetime(2026, 8, 23, 15, 0, 0, tzinfo=timezone.utc)
        ts_late = datetime(2026, 8, 23, 15, 0, 45, tzinfo=timezone.utc)  # 45s later

        first = _ingest_via_service(db_session, "source-a",
            external_alert_id="law-001a", account_id="INT-ACCT-006",
            transaction_id="INT-TXN-006", amount=800.00,
            location="Pune", event_timestamp=ts_first, fraud_type="suspicious")
        r_first = correlation_service.correlate_alert(db_session, first, window_seconds=window)

        late = _ingest_via_service(db_session, "source-b",
            external_alert_id="law-001b", account_id="INT-ACCT-006",
            transaction_id="INT-TXN-006", amount=800.00,
            location="Pune", event_timestamp=ts_late, fraud_type="suspicious")
        r_late = correlation_service.correlate_alert(db_session, late, window_seconds=window)

        assert r_late.group_id == r_first.group_id
        assert len(r_late.merged_alert_ids) == 2


# ---------------------------------------------------------------------------
# 7. Late alert outside merge window creates separate event
# ---------------------------------------------------------------------------

class TestIntegration_LateAlertOutsideWindow:
    """Alert arrives after another, event_timestamp outside window → new group."""

    def test_late_outside_window_creates_new(self, client: TestClient, db_session: Session) -> None:
        window = 60  # seconds
        ts_first = datetime(2026, 8, 23, 16, 0, 0, tzinfo=timezone.utc)
        ts_late = datetime(2026, 8, 23, 16, 2, 0, tzinfo=timezone.utc)  # 120s later

        first = _ingest_via_service(db_session, "source-a",
            external_alert_id="low-001a", account_id="INT-ACCT-007",
            transaction_id="INT-TXN-007", amount=700.00,
            location="Kolkata", event_timestamp=ts_first, fraud_type="fraud")
        r_first = correlation_service.correlate_alert(db_session, first, window_seconds=window)

        late = _ingest_via_service(db_session, "source-b",
            external_alert_id="low-001b", account_id="INT-ACCT-007",
            transaction_id="INT-TXN-007", amount=700.00,
            location="Kolkata", event_timestamp=ts_late, fraud_type="fraud")
        r_late = correlation_service.correlate_alert(db_session, late, window_seconds=window)

        assert r_late.group_id != r_first.group_id


# ---------------------------------------------------------------------------
# 8. Conflicting location is detected and resolved/preserved
# ---------------------------------------------------------------------------

class TestIntegration_LocationConflict:
    """Two sources report different locations for same txn → conflict detected."""

    def test_location_conflict_via_full_flow(self, client: TestClient, db_session: Session) -> None:
        ts = datetime(2026, 8, 23, 17, 0, 0, tzinfo=timezone.utc)

        a = _ingest_via_service(db_session, "source-a",
            external_alert_id="lcf-001a", account_id="INT-ACCT-008",
            transaction_id="INT-TXN-008", amount=6000.00,
            location="Delhi", event_timestamp=ts, fraud_type="fraud")
        r_a = correlation_service.correlate_alert(db_session, a)

        b = _ingest_via_service(db_session, "source-b",
            external_alert_id="lcf-001b", account_id="INT-ACCT-008",
            transaction_id="INT-TXN-008", amount=6000.00,
            location="Mumbai", event_timestamp=ts, fraud_type="fraud")
        r_b = correlation_service.correlate_alert(db_session, b)

        # Verify via API
        resp = client.get(f"/correlation/groups/{r_a.group_id}")
        assert resp.status_code == 200
        group = resp.json()

        assert group["conflict_status"] in ("resolved", "unresolved")
        assert len(group["merged_alert_ids"]) == 2

        # Verify the snapshot location exists
        assert group["location"] in ("Delhi", "Mumbai")


# ---------------------------------------------------------------------------
# 9. Conflicting fraud_type is detected and resolved/preserved
# ---------------------------------------------------------------------------

class TestIntegration_FraudTypeConflict:
    """Two sources report different fraud_type for same txn → conflict detected."""

    def test_fraud_type_conflict_via_full_flow(self, client: TestClient, db_session: Session) -> None:
        ts = datetime(2026, 8, 23, 18, 0, 0, tzinfo=timezone.utc)

        a = _ingest_via_service(db_session, "source-a",
            external_alert_id="ftf-001a", account_id="INT-ACCT-009",
            transaction_id="INT-TXN-009", amount=9000.00,
            location="Chennai", event_timestamp=ts, fraud_type="fraud")
        r_a = correlation_service.correlate_alert(db_session, a)

        b = _ingest_via_service(db_session, "source-b",
            external_alert_id="ftf-001b", account_id="INT-ACCT-009",
            transaction_id="INT-TXN-009", amount=9000.00,
            location="Chennai", event_timestamp=ts, fraud_type="suspicious")
        r_b = correlation_service.correlate_alert(db_session, b)

        resp = client.get(f"/correlation/groups/{r_a.group_id}")
        assert resp.status_code == 200
        group = resp.json()

        assert group["conflict_status"] in ("resolved", "unresolved")
        assert group["fraud_type"] in ("fraud", "suspicious")


# ---------------------------------------------------------------------------
# 10. Incomplete alert with null optional fields processed without failure
# ---------------------------------------------------------------------------

class TestIntegration_IncompleteAlert:
    """Alert with only required fields (account_id, event_timestamp) → no crash."""

    def test_minimal_alert_processed(self, client: TestClient, db_session: Session) -> None:
        payload = {
            "external_alert_id": "inc-001",
            "account_id": "INT-ACCT-010",
            "event_timestamp": "2026-08-23T19:00:00Z",
            # All optional fields omitted
        }

        alert_json = _ingest_via_api(client, "source-a", payload)
        assert alert_json["alert_id"] is not None
        assert alert_json["transaction_id"] is None
        assert alert_json["amount"] is None
        assert alert_json["location"] is None
        assert alert_json["fraud_type"] is None
        assert alert_json["description"] is None
        assert alert_json["metadata"] is None
        assert alert_json["merchant"] is None
        assert alert_json["device_id"] is None
        assert alert_json["risk_level"] is None

        # Correlate — should not raise
        alert = db_session.query(Alert).filter(Alert.alert_id == alert_json["alert_id"]).one()
        result, group = _correlate_and_fetch(db_session, alert)

        assert group is not None
        assert group.account_id == "INT-ACCT-010"
        assert group.transaction_id is None
        assert group.amount is None
        assert group.conflict_status == "no_conflict"


# ---------------------------------------------------------------------------
# 11. Three-source event is correlated correctly
# ---------------------------------------------------------------------------

class TestIntegration_ThreeSourceEvent:
    """Three sources report same txn → one group with 3 merged IDs."""

    def test_three_sources_correlate(self, client: TestClient, db_session: Session) -> None:
        ts = datetime(2026, 8, 23, 20, 0, 0, tzinfo=timezone.utc)

        sources = [
            ("source-a", "3src-001a", "Delhi", "fraud"),
            ("source-b", "3src-001b", "Delhi", "fraud"),
            ("source-c", "3src-001c", "Mumbai", "suspicious"),
        ]

        results = []
        for source, ext_id, loc, ftype in sources:
            a = _ingest_via_service(db_session, source,
                external_alert_id=ext_id, account_id="INT-ACCT-011",
                transaction_id="INT-TXN-011", amount=12000.00,
                location=loc, event_timestamp=ts, fraud_type=ftype)
            r = correlation_service.correlate_alert(db_session, a)
            results.append(r)

        # All three share the same group
        assert results[0].group_id == results[1].group_id == results[2].group_id
        assert len(results[2].merged_alert_ids) == 3

        # Verify via API
        resp = client.get(f"/correlation/groups/{results[0].group_id}")
        assert resp.status_code == 200
        group = resp.json()
        assert len(group["merged_alert_ids"]) == 3
        # Location conflict: 2 say Delhi, 1 says Mumbai → resolved to Delhi
        assert group["location"] == "Delhi"
        # Fraud_type conflict: 2 say fraud, 1 says suspicious → resolved to fraud
        assert group["fraud_type"] == "fraud"
        assert group["conflict_status"] == "resolved"


# ---------------------------------------------------------------------------
# 12. Repeated processing is deterministic
# ---------------------------------------------------------------------------

class TestIntegration_Determinism:
    """Same sequence of ingests produces identical correlation results."""

    def test_deterministic_output(self) -> None:
        """Two independent runs produce identical correlation keys and structure."""
        from sqlalchemy.pool import StaticPool

        def _run_pipeline():
            """Run two-alert correlation in an isolated DB and return snapshot."""
            engine = create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=engine)
            TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            db = TestSession()

            ts = datetime(2026, 8, 23, 21, 0, 0, tzinfo=timezone.utc)

            a = _ingest_via_service(db, "source-a",
                external_alert_id="det-001a", account_id="INT-ACCT-012",
                transaction_id="INT-TXN-012", amount=3333.33,
                location="Goa", event_timestamp=ts, fraud_type="fraud",
                description="Beach fraud")
            r_a = correlation_service.correlate_alert(db, a)

            b = _ingest_via_service(db, "source-b",
                external_alert_id="det-001b", account_id="INT-ACCT-012",
                transaction_id="INT-TXN-012", amount=3333.33,
                location="Goa", event_timestamp=ts + timedelta(seconds=2),
                fraud_type="fraud", description="Beach fraud")
            r_b = correlation_service.correlate_alert(db, b)

            group = correlation_service.get_correlated_by_group(db, r_a.group_id)
            return {
                "group_id": r_a.group_id,
                "key": r_a.correlation_key,
                "merged_count": len(r_b.merged_alert_ids),
                "account_id": group.account_id,
                "amount": group.amount,
                "location": group.location,
                "fraud_type": group.fraud_type,
                "conflict_status": group.conflict_status,
            }

        result = _run_pipeline()
        result2 = _run_pipeline()

        assert result["key"] == result2["key"]
        assert result["merged_count"] == result2["merged_count"]
        assert result["account_id"] == result2["account_id"]
        assert result["amount"] == result2["amount"]
        assert result["conflict_status"] == result2["conflict_status"]


# ---------------------------------------------------------------------------
# OUTPUT STRUCTURE: Verify CorrelatedAlertResponse for Payal's Rule Engine
# ---------------------------------------------------------------------------

class TestOutputStructure_ForPayal:
    """Verify that the correlation output contains everything Payal's rule engine needs."""

    def test_correlated_alert_response_has_all_fields(self, client: TestClient, db_session: Session) -> None:
        """Ingest → correlate → fetch via API → verify all fields present."""
        payload = {
            "external_alert_id": "pay-001",
            "account_id": "INT-ACCT-PAYAL",
            "transaction_id": "INT-TXN-PAYAL",
            "amount": 7500.00,
            "location": "Hyderabad",
            "event_timestamp": "2026-08-23T22:00:00Z",
            "fraud_type": "fraud",
            "description": "High-value transfer",
            "merchant": "Acme Corp",
            "device_id": "DEV-999",
            "risk_level": "high",
            "received_timestamp": "2026-08-23T22:01:00Z",
            "metadata": {"channel": "mobile", "ip": "10.0.0.1"},
        }

        # Ingest via API
        alert_json = _ingest_via_api(client, "source-a", payload)

        # Correlate
        alert = db_session.query(Alert).filter(Alert.alert_id == alert_json["alert_id"]).one()
        result, group = _correlate_and_fetch(db_session, alert)

        # Fetch via Correlation API
        resp = client.get(f"/correlation/groups/{group.group_id}")
        assert resp.status_code == 200
        api_group = resp.json()

        # Verify all fields Payal needs
        required_fields = [
            "id", "correlation_key", "group_id", "merged_alert_ids",
            "is_duplicate", "account_id", "transaction_id", "amount",
            "location", "event_timestamp", "merchant", "device_id",
            "risk_level", "fraud_type", "description",
            "created_at", "updated_at", "conflict_status",
        ]
        for field in required_fields:
            assert field in api_group, f"Missing field: {field}"

        # Verify values propagated correctly
        assert api_group["account_id"] == "INT-ACCT-PAYAL"
        assert api_group["transaction_id"] == "INT-TXN-PAYAL"
        assert api_group["amount"] == 7500.00
        assert api_group["location"] == "Hyderabad"
        assert api_group["fraud_type"] == "fraud"
        assert api_group["merchant"] == "Acme Corp"
        assert api_group["device_id"] == "DEV-999"
        assert api_group["risk_level"] == "HIGH"
        assert api_group["conflict_status"] == "no_conflict"
        assert api_group["is_duplicate"] is False
        assert len(api_group["merged_alert_ids"]) == 1

    def test_multiple_groups_listed_for_payal(self, client: TestClient, db_session: Session) -> None:
        """Multiple correlation groups are all returned by the listing endpoint."""
        groups_created = []

        for i in range(3):
            a = _ingest_via_service(db_session, "source-a",
                external_alert_id=f"list-00{i}", account_id=f"INT-ACCT-LIST-{i}",
                transaction_id=f"INT-TXN-LIST-{i}", amount=1000.0 * (i + 1),
                location="Test", event_timestamp=datetime(2026, 8, 23, 23, i, 0, tzinfo=timezone.utc),
                fraud_type="fraud")
            r = correlation_service.correlate_alert(db_session, a)
            groups_created.append(r.group_id)

        resp = client.get("/correlation/groups")
        assert resp.status_code == 200
        groups = resp.json()
        listed_ids = {g["group_id"] for g in groups}
        for gid in groups_created:
            assert gid in listed_ids


# ---------------------------------------------------------------------------
# INTERFACE MISMATCH CHECK: AlertResponse ↔ AlertCreate ↔ CorrelatedAlertResponse
# ---------------------------------------------------------------------------

class TestInterfaceMismatch:
    """Verify field names are consistent across ingestion and correlation layers."""

    def test_alert_create_fields_match_alert_response(self, client: TestClient) -> None:
        """AlertCreate fields are a subset of AlertResponse fields."""
        from app.schemas.alert import AlertCreate, AlertResponse

        create_fields = set(AlertCreate.model_fields.keys())
        response_fields = set(AlertResponse.model_fields.keys())

        # Every field accepted by AlertCreate must be returned by AlertResponse
        missing = create_fields - response_fields
        assert not missing, f"AlertCreate fields missing from AlertResponse: {missing}"

    def test_alert_response_has_alert_id(self, client: TestClient) -> None:
        """AlertResponse includes both 'id' (DB PK) and 'alert_id' (canonical identifier)."""
        from app.schemas.alert import AlertResponse

        assert "alert_id" in AlertResponse.model_fields
        assert "id" in AlertResponse.model_fields

    def test_correlated_alert_response_has_fraud_type(self, client: TestClient) -> None:
        """CorrelatedAlertResponse uses 'fraud_type' (canonical), not 'alert_type'."""
        from app.schemas.correlated_alert import CorrelatedAlertResponse

        assert "fraud_type" in CorrelatedAlertResponse.model_fields
        assert "alert_type" not in CorrelatedAlertResponse.model_fields

    def test_winning_alert_dict_uses_canonical_names(self, db_session: Session) -> None:
        """CorrelationResult.winning_alert dict uses canonical field names."""
        a = _ingest_via_service(db_session, "source-a",
            external_alert_id="wm-001", account_id="INT-ACCT-WM",
            transaction_id="INT-TXN-WM", amount=100.0,
            location="Test", event_timestamp=datetime(2026, 8, 23, tzinfo=timezone.utc),
            fraud_type="fraud")
        result = correlation_service.correlate_alert(db_session, a)

        winning = result.winning_alert
        assert "alert_id" in winning
        assert "fraud_type" in winning
        assert "meta_data" in winning
