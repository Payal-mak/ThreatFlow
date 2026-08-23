"""SQLAlchemy model for structured ThreatFlow audit records."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditRecord(Base):
    """Persistent audit record for an important ThreatFlow operation."""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    outcome: Mapped[str] = mapped_column(String(100), nullable=False)

    reason: Mapped[str] = mapped_column(String(1000), nullable=False)

    meta_data: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    def __init__(self, **kwargs):
        if "metadata" in kwargs:
            kwargs["meta_data"] = kwargs.pop("metadata")
        super().__init__(**kwargs)

    def __getattribute__(self, name):
        if name == "metadata":
            return object.__getattribute__(self, "meta_data")
        return super().__getattribute__(name)
