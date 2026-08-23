"""SQLite-backed audit logging service for ThreatFlow."""

from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditRecord


def create_audit_record(
    db: Session,
    *,
    entity_id: str,
    event_type: str,
    outcome: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> AuditRecord:
    """Create and persist a structured audit record."""

    record = AuditRecord(
        entity_id=entity_id,
        event_type=event_type,
        outcome=outcome,
        reason=reason,
        metadata=metadata or {},
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    return record


def get_audit_records(
    db: Session,
    *,
    entity_id: str | None = None,
) -> list[AuditRecord]:
    """Return audit records, optionally filtered by entity ID."""

    query = db.query(AuditRecord)

    if entity_id is not None:
        query = query.filter(AuditRecord.entity_id == entity_id)

    return query.order_by(
        AuditRecord.timestamp.asc(),
        AuditRecord.id.asc(),
    ).all()
