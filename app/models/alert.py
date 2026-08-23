from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Alert(Base):
    """Raw alert as received from a source, prior to correlation or rule evaluation."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False, index=True)
    external_alert_id: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    alert_type: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
