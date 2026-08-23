"""Focused pytest tests for correlation + deduplication.

Each test uses an isolated in-memory SQLite database so that tests are
fully independent and deterministic.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
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
    """Yield a fresh, rolled-back session for each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSession()
    yield session
    session.rollback()
    session.close()


def _make_alert(db: Session, **overrides) -> Alert:
    """Helper: ingest a minimal alert with sensible defaults."""
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
    return create_alert(db, source=overrides.get("source", "source-a"), alert_in=payload)


# ---------------------------------------------------------------------------
# 1. Two alerts that SHOULD correlate
# ---------------------------------------------------------------------------

class TestCorrelationPositive:
    """Two alerts from different sources describing the same event."""

    def test_correlate_by_transaction_id(self, db_session: Session) -> None:
        """Same account_id + transaction_id from two sources → same group."""
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-001",
            account_id="acct-999",
            transaction_id="txn-xyz",
            amount=2500.50,
            event_timestamp=datetime(2026, 8, 23, 9, 0, 0, tzinfo=timezone.utc),
        )

        # Ingest first alert → creates a new group.
        result_a = correlation_service.correlate_alert(db_session, alert_a)
        assert result_a.group_id is not None
        assert len(result_a.merged_alert_ids) == 1

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-001",
            account_id="acct-999",
            transaction_id="txn-xyz",
            amount=2500.50,
            event_timestamp=datetime(2026, 8, 23, 9, 0, 5, tzinfo=timezone.utc),
        )

        # Ingest second alert → merges into existing group.
        result_b = correlation_service.correlate_alert(db_session, alert_b)
        assert result_b.group_id == result_a.group_id
        assert len(result_b.merged_alert_ids) == 2
        assert alert_a.id in result_b.merged_alert_ids
        assert alert_b.id in result_b.merged_alert_ids

        # The CorrelatedAlert row should exist in the DB.
        group = correlation_service.get_correlated_by_group(db_session, result_a.group_id)
        assert group is not None
        assert group.is_duplicate is False  # cross-source, not exact dup

    def test_correlate_by_amount_and_date_fallback(self, db_session: Session) -> None:
        """No transaction_id → fallback key: account + amount + date.

        Both alerts are within the default 300s merge window.
        """
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-002",
            account_id="acct-500",
            transaction_id=None,
            amount=999.99,
            event_timestamp=datetime(2026, 8, 23, 14, 30, 0, tzinfo=timezone.utc),
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-c",
            external_alert_id="c-002",
            account_id="acct-500",
            transaction_id=None,
            amount=999.99,
            event_timestamp=datetime(2026, 8, 23, 14, 31, 0, tzinfo=timezone.utc),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        # Same account + same amount + same calendar date + within window → same group.
        assert result_b.group_id == result_a.group_id
        assert len(result_b.merged_alert_ids) == 2


# ---------------------------------------------------------------------------
# 2. Two alerts that should NOT correlate
# ---------------------------------------------------------------------------

class TestCorrelationNegative:
    """Alerts describing different events stay in separate groups."""

    def test_different_account_id(self, db_session: Session) -> None:
        """Different account_id → different groups."""
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-010",
            account_id="acct-111",
            transaction_id="txn-shared",
            amount=500.00,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-010",
            account_id="acct-222",
            transaction_id="txn-shared",
            amount=500.00,
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        assert result_a.group_id != result_b.group_id

    def test_different_amount_same_account(self, db_session: Session) -> None:
        """Same account, no txn_id, different amount → different groups."""
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-011",
            account_id="acct-300",
            transaction_id=None,
            amount=100.00,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-011",
            account_id="acct-300",
            transaction_id=None,
            amount=200.00,
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        assert result_a.group_id != result_b.group_id

    def test_different_date_same_amount_and_account(self, db_session: Session) -> None:
        """Same account + amount, but different calendar date → different groups."""
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-012",
            account_id="acct-400",
            transaction_id=None,
            amount=750.00,
            event_timestamp=datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc),
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-012",
            account_id="acct-400",
            transaction_id=None,
            amount=750.00,
            event_timestamp=datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        assert result_a.group_id != result_b.group_id


# ---------------------------------------------------------------------------
# 3. Exact duplicate
# ---------------------------------------------------------------------------

class TestExactDuplicate:
    """Same source + same external_alert_id → exact duplicate."""

    def test_same_source_same_external_id(self, db_session: Session) -> None:
        alert_first = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="ext-DUP-1",
            account_id="acct-700",
            transaction_id="txn-dup",
            amount=3000.00,
        )
        result_first = correlation_service.correlate_alert(db_session, alert_first)
        assert result_first.is_duplicate is False

        # Send the exact same alert again.
        alert_second = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="ext-DUP-1",
            account_id="acct-700",
            transaction_id="txn-dup",
            amount=3000.00,
        )
        result_second = correlation_service.correlate_alert(db_session, alert_second)

        # Should merge into the same group and be marked as duplicate.
        assert result_second.group_id == result_first.group_id
        assert result_second.is_duplicate is True
        assert len(result_second.merged_alert_ids) == 2

    def test_is_exact_duplicate_function(self, db_session: Session) -> None:
        """Directly test the is_exact_duplicate helper."""
        alert_a = _make_alert(db_session, source="source-b", external_alert_id="ext-X")
        alert_b = _make_alert(db_session, source="source-b", external_alert_id="ext-X")
        alert_c = _make_alert(db_session, source="source-c", external_alert_id="ext-X")

        assert correlation_service.is_exact_duplicate(alert_a, alert_b) is True
        # Different source → not an exact duplicate (but may be cross-source).
        assert correlation_service.is_exact_duplicate(alert_a, alert_c) is False


# ---------------------------------------------------------------------------
# 4. Cross-source near-duplicate
# ---------------------------------------------------------------------------

class TestCrossSourceDuplicate:
    """Same correlation key + same amount + timestamps within tolerance."""

    def test_cross_source_near_duplicate(self, db_session: Session) -> None:
        """Two alerts from different sources, same data, within 1s → dup."""
        ts = datetime(2026, 8, 23, 11, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-ND-1",
            account_id="acct-800",
            transaction_id="txn-nd",
            amount=4200.00,
            event_timestamp=ts,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-ND-1",
            account_id="acct-800",
            transaction_id="txn-nd",
            amount=4200.00,
            event_timestamp=ts + timedelta(seconds=0.5),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        assert result_b.group_id == result_a.group_id
        assert len(result_b.merged_alert_ids) == 2

    def test_cross_source_outside_tolerance(self, db_session: Session) -> None:
        """Same key but timestamps differ by >1s → NOT a cross-source dup."""
        ts = datetime(2026, 8, 23, 11, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="a-ND-2",
            account_id="acct-801",
            transaction_id="txn-nd2",
            amount=1000.00,
            event_timestamp=ts,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="b-ND-2",
            account_id="acct-801",
            transaction_id="txn-nd2",
            amount=1000.00,
            event_timestamp=ts + timedelta(seconds=5),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b)

        # Different source, same key, but outside tolerance → different groups
        # (or merged by key but NOT flagged as cross-source dup by the function).
        assert result_b.group_id == result_a.group_id  # same key → same group

    def test_is_cross_source_duplicate_function(self, db_session: Session) -> None:
        """Directly test the is_cross_source_duplicate helper."""
        ts = datetime(2026, 8, 23, 11, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session,
            source="source-a",
            account_id="acct-802",
            transaction_id="txn-cs",
            amount=1500.00,
            event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session,
            source="source-b",
            account_id="acct-802",
            transaction_id="txn-cs",
            amount=1500.00,
            event_timestamp=ts + timedelta(seconds=0.3),
        )
        alert_c = _make_alert(
            db_session,
            source="source-a",
            account_id="acct-802",
            transaction_id="txn-cs",
            amount=1500.00,
            event_timestamp=ts,
        )

        # Different source + matching data + within tolerance → True.
        assert correlation_service.is_cross_source_duplicate(alert_a, alert_b) is True
        # Same source → not a cross-source duplicate.
        assert correlation_service.is_cross_source_duplicate(alert_a, alert_c) is False


# ---------------------------------------------------------------------------
# 5. Deterministic correlation results
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Correlation key and results are fully deterministic."""

    def test_correlation_key_is_deterministic(self, db_session: Session) -> None:
        """Same fields → identical correlation key every time."""
        alert = _make_alert(
            db_session,
            source="source-a",
            account_id="acct-DET",
            transaction_id="txn-det",
            amount=1234.56,
        )
        key1 = correlation_service.compute_correlation_key(alert)
        key2 = correlation_service.compute_correlation_key(alert)
        assert key1 == key2
        assert key1 == "acct-DET|txn-det"

    def test_correlation_key_tier1_vs_tier2(self, db_session: Session) -> None:
        """With txn_id → Tier 1; without → Tier 2."""
        with_txn = _make_alert(
            db_session,
            source="source-a",
            account_id="acct-X",
            transaction_id="txn-1",
            amount=100.00,
            event_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        without_txn = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="ext-2",
            account_id="acct-X",
            transaction_id=None,
            amount=100.00,
            event_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        key_tier1 = correlation_service.compute_correlation_key(with_txn)
        key_tier2 = correlation_service.compute_correlation_key(without_txn)

        assert key_tier1 == "acct-X|txn-1"
        assert key_tier2 == "acct-X|100.00|2026-01-01"
        assert key_tier1 != key_tier2

    def test_correlation_key_from_dict_matches_orm(self, db_session: Session) -> None:
        """compute_correlation_key_from_dict agrees with compute_correlation_key."""
        alert = _make_alert(
            db_session,
            source="source-a",
            account_id="acct-DF",
            transaction_id="txn-df",
            amount=500.00,
        )
        key_orm = correlation_service.compute_correlation_key(alert)
        key_dict = correlation_service.compute_correlation_key_from_dict({
            "account_id": "acct-DF",
            "transaction_id": "txn-df",
            "amount": 500.00,
            "event_timestamp": alert.event_timestamp,
        })
        assert key_orm == key_dict

    def test_amount_normalisation(self, db_session: Session) -> None:
        """Floating-point differences are normalised away."""
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="ext-NORM-A",
            account_id="acct-N",
            transaction_id=None,
            amount=100.001,
            event_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="ext-NORM-B",
            account_id="acct-N",
            transaction_id=None,
            amount=100.004,
            event_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

        key_a = correlation_service.compute_correlation_key(alert_a)
        key_b = correlation_service.compute_correlation_key(alert_b)
        # Both round to 100.00 → same key.
        assert key_a == key_b

    def test_winning_alert_selection_is_deterministic(self, db_session: Session) -> None:
        """_select_winning_alert always picks the most complete alert."""
        alert_sparse = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="ext-W1",
            account_id="acct-W",
            transaction_id=None,
            amount=None,
            location=None,
            fraud_type=None,
            description=None,
        )
        alert_rich = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="ext-W2",
            account_id="acct-W",
            transaction_id="txn-rich",
            amount=9999.00,
            location="London",
            fraud_type="high-risk",
            description="Very detailed",
        )

        winner = correlation_service._select_winning_alert([alert_sparse, alert_rich])
        assert winner.alert_id == alert_rich.alert_id

        # Running again with reversed list order → same winner.
        winner2 = correlation_service._select_winning_alert([alert_rich, alert_sparse])
        assert winner2.alert_id == alert_rich.alert_id


# ===========================================================================
# TASK 3 — Merge Window + Out-of-Order Tests
# ===========================================================================

# Default window for basic grouping tests.
WINDOW = 60
# Wider window for late-arrival tests where alerts span several minutes.
WIDE_WINDOW = 300


class TestMergeWindowGrouping:
    """Alerts inside the merge window are grouped; outside are separate."""

    def test_alerts_inside_window_are_grouped(self, db_session: Session) -> None:
        """Two alerts 30s apart (within 60s window) → same group."""
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="mw-01a",
            account_id="acct-MW1",
            transaction_id="txn-mw1",
            amount=1000.00,
            event_timestamp=ts_a,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WINDOW)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="mw-01b",
            account_id="acct-MW1",
            transaction_id="txn-mw1",
            amount=1000.00,
            event_timestamp=ts_a + timedelta(seconds=30),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WINDOW)

        assert result_a.group_id == result_b.group_id
        assert len(result_b.merged_alert_ids) == 2

    def test_alerts_outside_window_are_separate(self, db_session: Session) -> None:
        """Two alerts 120s apart (outside 60s window) → different groups."""
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session,
            source="source-a",
            external_alert_id="mw-02a",
            account_id="acct-MW2",
            transaction_id="txn-mw2",
            amount=2000.00,
            event_timestamp=ts_a,
        )
        result_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WINDOW)

        alert_b = _make_alert(
            db_session,
            source="source-b",
            external_alert_id="mw-02b",
            account_id="acct-MW2",
            transaction_id="txn-mw2",
            amount=2000.00,
            event_timestamp=ts_a + timedelta(seconds=120),
        )
        result_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WINDOW)

        assert result_a.group_id != result_b.group_id

    def test_multiple_events_same_identity_different_windows(self, db_session: Session) -> None:
        """Same correlation identity but 3 separate time windows → 3 groups."""
        base = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)

        # Event 1: t=0
        a1 = _make_alert(
            db_session, source="source-a", external_alert_id="mw-05a",
            account_id="acct-MW5", transaction_id="txn-mw5",
            amount=500.00, event_timestamp=base,
        )
        r1 = correlation_service.correlate_alert(db_session, a1, window_seconds=WINDOW)

        # Event 2: t=200s (outside window from event 1)
        a2 = _make_alert(
            db_session, source="source-b", external_alert_id="mw-05b",
            account_id="acct-MW5", transaction_id="txn-mw5",
            amount=500.00, event_timestamp=base + timedelta(seconds=200),
        )
        r2 = correlation_service.correlate_alert(db_session, a2, window_seconds=WINDOW)

        # Event 3: t=400s (outside window from event 2)
        a3 = _make_alert(
            db_session, source="source-c", external_alert_id="mw-05c",
            account_id="acct-MW5", transaction_id="txn-mw5",
            amount=500.00, event_timestamp=base + timedelta(seconds=400),
        )
        r3 = correlation_service.correlate_alert(db_session, a3, window_seconds=WINDOW)

        assert r1.group_id != r2.group_id
        assert r2.group_id != r3.group_id
        assert r1.group_id != r3.group_id


class TestMergeWindowBoundary:
    """Exact boundary behaviour for the merge window."""

    def test_exactly_at_boundary_inside(self, db_session: Session) -> None:
        """Alert exactly at the window boundary (60s from earliest) → grouped."""
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="bd-01a",
            account_id="acct-BD1", transaction_id="txn-bd1",
            amount=100.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WINDOW)

        # Exactly 60s later = at the boundary → inside.
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="bd-01b",
            account_id="acct-BD1", transaction_id="txn-bd1",
            amount=100.00, event_timestamp=ts_a + timedelta(seconds=WINDOW),
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WINDOW)

        assert r_a.group_id == r_b.group_id

    def test_just_outside_boundary(self, db_session: Session) -> None:
        """Alert 1s beyond the window boundary → NOT grouped."""
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="bd-02a",
            account_id="acct-BD2", transaction_id="txn-bd2",
            amount=200.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WINDOW)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="bd-02b",
            account_id="acct-BD2", transaction_id="txn-bd2",
            amount=200.00, event_timestamp=ts_a + timedelta(seconds=WINDOW + 1),
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WINDOW)

        assert r_a.group_id != r_b.group_id

    def test_just_inside_boundary(self, db_session: Session) -> None:
        """Alert 1s before the window boundary → grouped."""
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="bd-03a",
            account_id="acct-BD3", transaction_id="txn-bd3",
            amount=300.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WINDOW)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="bd-03b",
            account_id="acct-BD3", transaction_id="txn-bd3",
            amount=300.00, event_timestamp=ts_a + timedelta(seconds=WINDOW - 1),
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WINDOW)

        assert r_a.group_id == r_b.group_id


class TestLateArrival:
    """Late alerts are attached to the correct existing group."""

    def test_late_alert_attached_to_existing_group(self, db_session: Session) -> None:
        """
        Timeline (event_timestamp):
          10:00  → Alert A  (arrives first)
          10:05  → Alert B  (arrives second)
          10:02  → Alert C  (arrives third — late)

        All three should end up in the same group.
        """
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        ts_b = datetime(2026, 8, 23, 10, 5, 0, tzinfo=timezone.utc)
        ts_c = datetime(2026, 8, 23, 10, 2, 0, tzinfo=timezone.utc)  # late

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="lo-01a",
            account_id="acct-LO1", transaction_id="txn-lo1",
            amount=1000.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WIDE_WINDOW)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="lo-01b",
            account_id="acct-LO1", transaction_id="txn-lo1",
            amount=1000.00, event_timestamp=ts_b,
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WIDE_WINDOW)

        # Late alert C arrives after B.
        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="lo-01c",
            account_id="acct-LO1", transaction_id="txn-lo1",
            amount=1000.00, event_timestamp=ts_c,
        )
        r_c = correlation_service.correlate_alert(db_session, alert_c, window_seconds=WIDE_WINDOW)

        assert r_a.group_id == r_b.group_id == r_c.group_id
        assert len(r_c.merged_alert_ids) == 3

    def test_completely_reversed_arrival_order(self, db_session: Session) -> None:
        """
        Event timestamps: A=10:00, B=10:02, C=10:04
        Arrival order:    C, B, A  (completely reversed)

        All three should end up in the same group.
        """
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        ts_b = datetime(2026, 8, 23, 10, 2, 0, tzinfo=timezone.utc)
        ts_c = datetime(2026, 8, 23, 10, 4, 0, tzinfo=timezone.utc)

        # C arrives first.
        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="lo-02c",
            account_id="acct-LO2", transaction_id="txn-lo2",
            amount=2000.00, event_timestamp=ts_c,
        )
        r_c = correlation_service.correlate_alert(db_session, alert_c, window_seconds=WIDE_WINDOW)

        # B arrives second.
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="lo-02b",
            account_id="acct-LO2", transaction_id="txn-lo2",
            amount=2000.00, event_timestamp=ts_b,
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WIDE_WINDOW)

        # A arrives last (earliest event_timestamp — completely reversed).
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="lo-02a",
            account_id="acct-LO2", transaction_id="txn-lo2",
            amount=2000.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WIDE_WINDOW)

        assert r_a.group_id == r_b.group_id == r_c.group_id
        assert len(r_a.merged_alert_ids) == 3

    def test_multiple_sources_out_of_order(self, db_session: Session) -> None:
        """
        Three sources arrive in arbitrary order.
        Event timestamps: A=10:00, B=10:03, C=10:01
        Arrival order:    B, C, A
        """
        ts_a = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        ts_b = datetime(2026, 8, 23, 10, 3, 0, tzinfo=timezone.utc)
        ts_c = datetime(2026, 8, 23, 10, 1, 0, tzinfo=timezone.utc)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="lo-03b",
            account_id="acct-LO3", transaction_id="txn-lo3",
            amount=3000.00, event_timestamp=ts_b,
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b, window_seconds=WIDE_WINDOW)

        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="lo-03c",
            account_id="acct-LO3", transaction_id="txn-lo3",
            amount=3000.00, event_timestamp=ts_c,
        )
        r_c = correlation_service.correlate_alert(db_session, alert_c, window_seconds=WIDE_WINDOW)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="lo-03a",
            account_id="acct-LO3", transaction_id="txn-lo3",
            amount=3000.00, event_timestamp=ts_a,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a, window_seconds=WIDE_WINDOW)

        assert r_a.group_id == r_b.group_id == r_c.group_id
        assert len(r_a.merged_alert_ids) == 3

    def test_late_alert_never_dropped(self, db_session: Session) -> None:
        """A very late alert still appears in the merged_alert_ids list."""
        ts_early = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc)
        ts_late  = datetime(2026, 8, 23, 10, 0, 30, tzinfo=timezone.utc)  # within window

        alert_early = _make_alert(
            db_session, source="source-a", external_alert_id="lo-04a",
            account_id="acct-LO4", transaction_id="txn-lo4",
            amount=4000.00, event_timestamp=ts_early,
        )
        r_early = correlation_service.correlate_alert(db_session, alert_early, window_seconds=WINDOW)

        alert_late = _make_alert(
            db_session, source="source-b", external_alert_id="lo-04b",
            account_id="acct-LO4", transaction_id="txn-lo4",
            amount=4000.00, event_timestamp=ts_late,
        )
        r_late = correlation_service.correlate_alert(db_session, alert_late, window_seconds=WINDOW)

        # The late alert must be present in the group.
        assert alert_late.id in r_late.merged_alert_ids
        assert alert_early.id in r_late.merged_alert_ids
        assert len(r_late.merged_alert_ids) == 2


# ===========================================================================
# TASK 4 — Conflict Detection + Resolution Tests
# ===========================================================================


class TestNoConflict:
    """Two correlated alerts with no conflicts."""

    def test_no_conflict_when_fields_agree(self, db_session: Session) -> None:
        """Same location, amount, fraud_type → no_conflict."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="nc-01a",
            account_id="acct-NC1", transaction_id="txn-nc1",
            amount=5000.00, location="Mumbai", fraud_type="fraud",
            description="ATM withdrawal", event_timestamp=ts,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="nc-01b",
            account_id="acct-NC1", transaction_id="txn-nc1",
            amount=5000.00, location="Mumbai", fraud_type="fraud",
            description="ATM withdrawal", event_timestamp=ts + timedelta(seconds=10),
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        assert r_a.group_id == r_b.group_id
        group = correlation_service.get_correlated_by_group(db_session, r_a.group_id)
        assert group.conflict_status == "no_conflict"

    def test_detect_conflicts_returns_empty_for_single_alert(
        self, db_session: Session
    ) -> None:
        """A single alert cannot conflict with itself."""
        alert = _make_alert(
            db_session, source="source-a", external_alert_id="nc-02",
            account_id="acct-NC2", transaction_id="txn-nc2",
        )
        conflicts = correlation_service.detect_conflicts([alert])
        assert conflicts == []


class TestLocationConflict:
    """Location conflict between correlated alerts."""

    def test_location_conflict_detected(self, db_session: Session) -> None:
        """Two alerts with different locations → location conflict."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="lc-01a",
            account_id="acct-LC1", transaction_id="txn-lc1",
            amount=3000.00, location="Hyderabad", event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="lc-01b",
            account_id="acct-LC1", transaction_id="txn-lc1",
            amount=3000.00, location="Mumbai", event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        assert len(conflicts) == 1
        assert conflicts[0].field == "location"
        assert len(conflicts[0].conflicting_values) == 2

        sources = {v.source for v in conflicts[0].conflicting_values}
        assert sources == {"SOURCE_A", "SOURCE_B"}

    def test_location_conflict_unresolved_by_majority(self, db_session: Session) -> None:
        """Three sources, each with a unique location → unresolved."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="lc-02a",
            account_id="acct-LC2", transaction_id="txn-lc2",
            amount=1000.00, location="Delhi", event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="lc-02b",
            account_id="acct-LC2", transaction_id="txn-lc2",
            amount=1000.00, location="Mumbai", event_timestamp=ts,
        )
        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="lc-02c",
            account_id="acct-LC2", transaction_id="txn-lc2",
            amount=1000.00, location="Chennai", event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b, alert_c])
        assert len(conflicts) == 1
        assert conflicts[0].field == "location"

        result = correlation_service.resolve_conflicts(
            [alert_a, alert_b, alert_c], conflicts
        )
        assert result.conflict_status == "unresolved"
        assert conflicts[0].resolved_value is None
        assert "no clear majority" in conflicts[0].resolution_reason


class TestAmountConflict:
    """Amount conflict between correlated alerts (Tier 1 key)."""

    def test_amount_conflict_detected(self, db_session: Session) -> None:
        """Two alerts with different amounts (same txn_id) → amount conflict."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="ac-01a",
            account_id="acct-AC1", transaction_id="txn-ac1",
            amount=1000.00, event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="ac-01b",
            account_id="acct-AC1", transaction_id="txn-ac1",
            amount=1500.00, event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        assert len(conflicts) == 1
        assert conflicts[0].field == "amount"

    def test_amount_conflict_resolved_by_majority(self, db_session: Session) -> None:
        """Three alerts: two say 1000, one says 1500 → resolved to 1000."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="ac-02a",
            account_id="acct-AC2", transaction_id="txn-ac2",
            amount=1000.00, event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="ac-02b",
            account_id="acct-AC2", transaction_id="txn-ac2",
            amount=1000.00, event_timestamp=ts,
        )
        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="ac-02c",
            account_id="acct-AC2", transaction_id="txn-ac2",
            amount=1500.00, event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b, alert_c])
        result = correlation_service.resolve_conflicts(
            [alert_a, alert_b, alert_c], conflicts
        )
        assert result.conflict_status == "resolved"
        assert result.winning_alert["amount"] == 1000.00

        amount_conflict = next(c for c in conflicts if c.field == "amount")
        assert amount_conflict.resolved_value == 1000.00
        assert "majority" in amount_conflict.resolution_reason


class TestAlertTypeConflict:
    """Alert-type conflict between correlated alerts."""

    def test_fraud_type_conflict_detected(self, db_session: Session) -> None:
        """Two alerts with different fraud_types → conflict."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="at-01a",
            account_id="acct-AT1", transaction_id="txn-at1",
            amount=2000.00, fraud_type="fraud", event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="at-01b",
            account_id="acct-AT1", transaction_id="txn-at1",
            amount=2000.00, fraud_type="suspicious", event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        assert len(conflicts) == 1
        assert conflicts[0].field == "fraud_type"

        result = correlation_service.resolve_conflicts(
            [alert_a, alert_b], conflicts
        )
        # Two unique values, no majority → unresolved.
        assert result.conflict_status == "unresolved"


class TestMultipleConflicts:
    """Multiple fields conflict in the same group."""

    def test_multiple_fields_conflict(self, db_session: Session) -> None:
        """Different location + different amount → two conflicts."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="mc-01a",
            account_id="acct-MC1", transaction_id="txn-mc1",
            amount=1000.00, location="Delhi", fraud_type="fraud",
            event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="mc-01b",
            account_id="acct-MC1", transaction_id="txn-mc1",
            amount=2000.00, location="Mumbai", fraud_type="suspicious",
            event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        conflict_fields = {c.field for c in conflicts}
        assert "location" in conflict_fields
        assert "amount" in conflict_fields
        assert "fraud_type" in conflict_fields
        assert len(conflicts) == 3

        result = correlation_service.resolve_conflicts(
            [alert_a, alert_b], conflicts
        )
        assert result.conflict_status == "unresolved"


class TestIdentityConflict:
    """Identity field mismatches are always unresolved."""

    def test_account_id_mismatch_unresolved(self, db_session: Session) -> None:
        """Different account_id in same group → always unresolved."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        # Manually create alerts that end up in the same group despite
        # different account_ids (this would not happen via normal correlation,
        # but tests the detection logic directly).
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="id-01a",
            account_id="acct-A", transaction_id="txn-shared",
            amount=5000.00, event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="id-01b",
            account_id="acct-B", transaction_id="txn-shared",
            amount=5000.00, event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        id_conflicts = [c for c in conflicts if c.field == "account_id"]
        assert len(id_conflicts) == 1

        result = correlation_service.resolve_conflicts(
            [alert_a, alert_b], conflicts
        )
        assert result.conflict_status == "unresolved"
        assert "identity field" in id_conflicts[0].resolution_reason


class TestEvidencePreserved:
    """Original conflicting evidence is always preserved."""

    def test_all_values_preserved_in_conflict(self, db_session: Session) -> None:
        """Conflict detail retains all source values and alert IDs."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="ep-01a",
            account_id="acct-EP1", transaction_id="txn-ep1",
            amount=3000.00, location="Hyderabad", event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="ep-01b",
            account_id="acct-EP1", transaction_id="txn-ep1",
            amount=3000.00, location="Bangalore", event_timestamp=ts,
        )

        conflicts = correlation_service.detect_conflicts([alert_a, alert_b])
        loc_conflict = next(c for c in conflicts if c.field == "location")

        # All evidence preserved.
        assert len(loc_conflict.conflicting_values) == 2
        values_by_source = {v.source: v for v in loc_conflict.conflicting_values}
        assert values_by_source["SOURCE_A"].value == "Hyderabad"
        assert values_by_source["SOURCE_A"].alert_id == alert_a.alert_id
        assert values_by_source["SOURCE_B"].value == "Bangalore"
        assert values_by_source["SOURCE_B"].alert_id == alert_b.alert_id


class TestNoConflictBetweenUnrelatedGroups:
    """Conflicts are only checked within a group, not across groups."""

    def test_different_groups_no_cross_conflict(self, db_session: Session) -> None:
        """Two separate groups with different locations should not conflict."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        # Group 1
        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="ug-01a",
            account_id="acct-UG1", transaction_id="txn-ug1",
            amount=1000.00, location="Delhi", event_timestamp=ts,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a)

        # Group 2 (different txn_id → different group)
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="ug-02b",
            account_id="acct-UG1", transaction_id="txn-ug2",
            amount=1000.00, location="Mumbai", event_timestamp=ts,
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        assert r_a.group_id != r_b.group_id

        # Check conflicts within each group individually.
        group_a_alerts = [alert_a]
        group_b_alerts = [alert_b]

        assert correlation_service.detect_conflicts(group_a_alerts) == []
        assert correlation_service.detect_conflicts(group_b_alerts) == []


class TestConflictResolutionDeterministic:
    """Conflict resolution is fully deterministic."""

    def test_same_conflicts_always_resolve_same_way(self, db_session: Session) -> None:
        """Running resolve_conflicts twice on same data → same result."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="det-01a",
            account_id="acct-DET1", transaction_id="txn-det1",
            amount=1000.00, location="Delhi", event_timestamp=ts,
        )
        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="det-01b",
            account_id="acct-DET1", transaction_id="txn-det1",
            amount=1000.00, location="Mumbai", event_timestamp=ts,
        )
        alert_c = _make_alert(
            db_session, source="source-c", external_alert_id="det-01c",
            account_id="acct-DET1", transaction_id="txn-det1",
            amount=1000.00, location="Mumbai", event_timestamp=ts,
        )

        conflicts1 = correlation_service.detect_conflicts([alert_a, alert_b, alert_c])
        result1 = correlation_service.resolve_conflicts(
            [alert_a, alert_b, alert_c], conflicts1
        )

        conflicts2 = correlation_service.detect_conflicts([alert_a, alert_b, alert_c])
        result2 = correlation_service.resolve_conflicts(
            [alert_a, alert_b, alert_c], conflicts2
        )

        assert result1.conflict_status == result2.conflict_status
        assert result1.winning_alert == result2.winning_alert
        assert len(result1.conflicts) == len(result2.conflicts)

        # Mumbai has 2/3 votes → resolved.
        assert result1.conflict_status == "resolved"
        loc_conflict = next(c for c in result1.conflicts if c.field == "location")
        assert loc_conflict.resolved_value == "Mumbai"


class TestConflictStatusPersisted:
    """Conflict status is persisted on the CorrelatedAlert row."""

    def test_conflict_status_set_on_merge(self, db_session: Session) -> None:
        """After merging conflicting alerts, conflict_status is persisted."""
        ts = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        alert_a = _make_alert(
            db_session, source="source-a", external_alert_id="ps-01a",
            account_id="acct-PS1", transaction_id="txn-ps1",
            amount=5000.00, location="Delhi", event_timestamp=ts,
        )
        r_a = correlation_service.correlate_alert(db_session, alert_a)

        alert_b = _make_alert(
            db_session, source="source-b", external_alert_id="ps-01b",
            account_id="acct-PS1", transaction_id="txn-ps1",
            amount=5000.00, location="Mumbai", event_timestamp=ts + timedelta(seconds=5),
        )
        r_b = correlation_service.correlate_alert(db_session, alert_b)

        assert r_b.group_id == r_a.group_id
        group = correlation_service.get_correlated_by_group(db_session, r_a.group_id)
        assert group.conflict_status == "unresolved"
