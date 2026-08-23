from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CorrelatedAlertResponse(BaseModel):
    """A correlated fraud-event group returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    correlation_key: str
    group_id: str
    merged_alert_ids: list[int]
    is_duplicate: bool
    account_id: str
    transaction_id: str | None
    amount: float | None
    location: str | None
    event_timestamp: datetime
    merchant: str | None
    device_id: str | None
    risk_level: str | None
    fraud_type: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime
    conflict_status: str | None


class CorrelationResult(BaseModel):
    """Internal value-object returned by the correlation service.

    Not persisted directly — used to pass data between pipeline stages.
    """

    correlation_key: str
    group_id: str
    merged_alert_ids: list[int]
    is_duplicate: bool
    winning_alert: dict[str, Any]
    conflict_status: str | None = None


# ---------------------------------------------------------------------------
# Conflict detection / resolution schemas (Task 4)
# ---------------------------------------------------------------------------

class ConflictValue(BaseModel):
    """A single conflicting value from a specific source."""

    source: str
    alert_id: str
    value: Any


class ConflictDetail(BaseModel):
    """One field-level conflict detected across alerts in the same group."""

    field: str
    conflicting_values: list[ConflictValue]
    resolved_value: Any | None = None
    resolution_reason: str = ""


class ConflictResult(BaseModel):
    """Full output of the conflict detection + resolution pipeline."""

    conflicts: list[ConflictDetail]
    conflict_status: str  # "no_conflict" | "resolved" | "unresolved"
    winning_alert: dict[str, Any]
