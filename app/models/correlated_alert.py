from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CorrelatedAlert(Base):
    """Persisted result of the correlation + deduplication pipeline.

    Each row represents one correlated fraud event group, tracking which raw
    alerts contributed to it and whether duplicates were detected.
    """

    __tablename__ = "correlated_alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Deterministic key derived from the alert fields that identify the same
    # underlying fraud event (see correlation_service.compute_correlation_key).
    correlation_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Opaque group identifier that ties all raw alerts belonging to the same
    # fraud event together.  Generated once per unique correlation_key.
    group_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # List of ``alerts.alert_id`` values that were merged into this group.
    merged_alert_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # True when more than one raw alert contributed to this group.
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Snapshot of the "winning" (most complete / most recent) alert fields so
    # downstream consumers do not need to re-join the alerts table.
    account_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float | None] = mapped_column(nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    fraud_type: Mapped[str | None] = mapped_column(String, nullable=True, name="alert_type")
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # Timestamp when this correlation group was created / last updated.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Placeholder for future conflict-resolution status (ready for Task 3).
    # Possible future values: "pending", "resolved", "conflict".
    conflict_status: Mapped[str | None] = mapped_column(String, nullable=True)
