from datetime import datetime, timezone
import uuid
from typing import List, Tuple
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.schemas.alert import AlertCreate


def create_alert(db: Session, source: str | None, alert_in: AlertCreate) -> Alert:
    """
    Persist a raw alert from a given source.
    Generates received_timestamp, validates schema/source, and preserves event_timestamp.
    No deduplication, sorting, merging, or risk calculation happens here.
    """
    # Determine canonical source
    effective_source = source or alert_in.source or "SOURCE_A"
    effective_source = effective_source.upper().replace("-", "_")

    # Auto-generate alert_id if missing
    alert_id = alert_in.alert_id or alert_in.external_alert_id or f"A-{uuid.uuid4().hex[:8].upper()}"
    external_alert_id = alert_in.external_alert_id or alert_id

    # Current UTC timestamp when ThreatFlow ingestion API receives the alert
    now_utc = datetime.now(timezone.utc)

    db_alert = Alert(
        alert_id=alert_id,
        external_alert_id=external_alert_id,
        source=effective_source,
        account_id=alert_in.account_id,
        transaction_id=alert_in.transaction_id,
        amount=alert_in.amount,
        merchant=alert_in.merchant,
        location=alert_in.location,
        device_id=alert_in.device_id,
        event_timestamp=alert_in.event_timestamp,
        received_timestamp=now_utc,
        received_at=now_utc,
        risk_level=alert_in.risk_level,
        fraud_type=alert_in.fraud_type or alert_in.alert_type,
        alert_type=alert_in.alert_type or alert_in.fraud_type,
        description=alert_in.description,
        meta_data=alert_in.metadata or alert_in.extra_data,
        extra_data=alert_in.extra_data or alert_in.metadata,
    )
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert


def create_batch(db: Session, alerts_in: List[Tuple[str | None, AlertCreate]]) -> List[Alert]:
    """Ingest a batch of alerts in exact arrival sequence without sorting or deduplication."""
    results = []
    for src, alert_payload in alerts_in:
        created = create_alert(db, source=src, alert_in=alert_payload)
        results.append(created)
    return results


def get_alerts(db: Session, limit: int = 100) -> List[Alert]:
    """Retrieve recently ingested alerts sorted strictly by arrival order (primary key ID)."""
    return db.query(Alert).order_by(Alert.id.asc()).limit(limit).all()
