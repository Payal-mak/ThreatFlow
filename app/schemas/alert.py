from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AlertCreate(BaseModel):
    """Payload accepted from a source's ingestion endpoint."""

    external_alert_id: str
    account_id: str
    transaction_id: str | None = None
    amount: float | None = None
    location: str | None = None
    event_timestamp: datetime
    alert_type: str | None = None
    description: str | None = None
    extra_data: dict[str, Any] | None = None


class AlertResponse(BaseModel):
    """Stored alert as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    external_alert_id: str
    account_id: str
    transaction_id: str | None
    amount: float | None
    location: str | None
    event_timestamp: datetime
    received_at: datetime
    alert_type: str | None
    description: str | None
    extra_data: dict[str, Any] | None
