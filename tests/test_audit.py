from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.audit import AuditRecord
from app.services.audit_service import (
    create_audit_record,
    get_audit_records,
)


def create_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    return TestingSessionLocal()


def test_audit_record_creation():
    db = create_test_db()

    record = create_audit_record(
        db,
        entity_id="event-100",
        event_type="RISK_SCORED",
        outcome="SUCCESS",
        reason="Risk score calculated successfully",
        metadata={
            "risk_score": 85,
            "risk_level": "CRITICAL",
        },
    )

    assert record.id is not None
    assert record.entity_id == "event-100"
    assert record.event_type == "RISK_SCORED"
    assert record.outcome == "SUCCESS"
    assert record.metadata["risk_score"] == 85

    db.close()


def test_multiple_audit_records():
    db = create_test_db()

    create_audit_record(
        db,
        entity_id="event-200",
        event_type="ALERT_RECEIVED",
        outcome="SUCCESS",
        reason="Alert received",
    )

    create_audit_record(
        db,
        entity_id="event-200",
        event_type="RISK_SCORED",
        outcome="SUCCESS",
        reason="Risk evaluated",
        metadata={"score": 75},
    )

    records = get_audit_records(
        db,
        entity_id="event-200",
    )

    assert len(records) == 2
    assert records[0].event_type == "ALERT_RECEIVED"
    assert records[1].event_type == "RISK_SCORED"

    db.close()


def test_metadata_serialization():
    db = create_test_db()

    record = create_audit_record(
        db,
        entity_id="event-300",
        event_type="ACTION_CREATED",
        outcome="SUCCESS",
        reason="Action generated",
        metadata={
            "priority": 90,
            "rules": ["HIGH_AMOUNT", "LOCATION_MISMATCH"],
            "nested": {
                "source": "SOURCE_A",
            },
        },
    )

    fetched = get_audit_records(
        db,
        entity_id="event-300",
    )[0]

    assert fetched.metadata["priority"] == 90
    assert fetched.metadata["rules"] == [
        "HIGH_AMOUNT",
        "LOCATION_MISMATCH",
    ]
    assert fetched.metadata["nested"]["source"] == "SOURCE_A"

    db.close()
