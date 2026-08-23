"""API/output contract for audit records."""

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AuditRecord(BaseModel):
    """Structured audit record exposed by the audit layer."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    timestamp: datetime
    entity_id: str
    event_type: str
    outcome: str
    reason: str
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("meta_data", "metadata"),
    )
