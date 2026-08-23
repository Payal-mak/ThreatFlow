"""Read-only API for the ThreatFlow audit trail."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.audit import AuditRecord as AuditRecordSchema
from app.services.audit_service import get_audit_records

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditRecordSchema])
def list_audit_records(
    entity_id: str | None = Query(default=None, description="Filter by entity ID"),
    db: Session = Depends(get_db),
) -> list[AuditRecordSchema]:
    """Return the audit trail, optionally filtered to one entity."""
    return get_audit_records(db, entity_id=entity_id)
