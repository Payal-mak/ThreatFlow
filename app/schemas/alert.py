from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


VALID_SOURCES = {"SOURCE_A", "SOURCE_B", "SOURCE_C", "source-a", "source-b", "source-c"}
VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class AlertCreate(BaseModel):
    """Payload accepted from a source's ingestion endpoint."""

    alert_id: str | None = Field(default=None, description="Canonical alert identifier")
    external_alert_id: str | None = Field(default=None, description="External alert identifier")
    source: str | None = Field(default=None, description="Alert source (SOURCE_A, SOURCE_B, SOURCE_C)")
    account_id: str = Field(..., description="Customer / Account identifier")
    transaction_id: str | None = Field(default=None, description="Transaction identifier (optional for incomplete evidence)")
    amount: float | None = Field(default=None, description="Transaction amount (must be non-negative if provided)")
    merchant: str | None = Field(default=None, description="Merchant name")
    location: str | None = Field(default=None, description="Geographic location")
    device_id: str | None = Field(default=None, description="Device identifier")
    event_timestamp: datetime = Field(..., description="Timestamp of fraud event occurrence")
    risk_level: str | None = Field(default=None, description="Risk level (LOW, MEDIUM, HIGH, CRITICAL)")
    fraud_type: str | None = Field(default=None, description="Fraud type / classification")
    alert_type: str | None = Field(default=None, description="Alert type classification")
    description: str | None = Field(default=None, description="Description of the alert")
    metadata: dict[str, Any] | None = Field(default=None, description="Arbitrary source metadata")
    extra_data: dict[str, Any] | None = Field(default=None, description="Extra dictionary data")

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "AlertCreate":
        # Synchronize alert_id and external_alert_id
        if not self.alert_id and self.external_alert_id:
            self.alert_id = self.external_alert_id
        elif not self.external_alert_id and self.alert_id:
            self.external_alert_id = self.alert_id

        # Validate source if provided
        if self.source is not None:
            norm_source = self.source.upper().replace("-", "_")
            if norm_source not in {"SOURCE_A", "SOURCE_B", "SOURCE_C"}:
                raise ValueError(f"Invalid source '{self.source}'. Must be one of SOURCE_A, SOURCE_B, SOURCE_C")
            self.source = norm_source

        # Validate amount
        if self.amount is not None and self.amount < 0:
            raise ValueError("Amount cannot be negative")

        # Validate risk_level if provided
        if self.risk_level is not None:
            norm_risk = self.risk_level.upper()
            if norm_risk not in VALID_RISK_LEVELS:
                raise ValueError(f"Invalid risk_level '{self.risk_level}'. Must be one of {sorted(VALID_RISK_LEVELS)}")
            self.risk_level = norm_risk

        # Synchronize fraud_type and alert_type
        if not self.fraud_type and self.alert_type:
            self.fraud_type = self.alert_type
        elif not self.alert_type and self.fraud_type:
            self.alert_type = self.fraud_type

        # Synchronize metadata and extra_data
        if self.metadata is None and self.extra_data is not None:
            self.metadata = self.extra_data
        elif self.extra_data is None and self.metadata is not None:
            self.extra_data = self.metadata

        return self


class AlertResponse(BaseModel):
    """Stored alert as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: str
    external_alert_id: str
    source: str
    account_id: str
    transaction_id: str | None = None
    amount: float | None = None
    merchant: str | None = None
    location: str | None = None
    device_id: str | None = None
    event_timestamp: datetime
    received_timestamp: datetime
    received_at: datetime
    risk_level: str | None = None
    fraud_type: str | None = None
    alert_type: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    extra_data: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def extract_from_orm(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return data
        if hasattr(data, "meta_data") or hasattr(data, "extra_data"):
            meta_val = getattr(data, "meta_data", None) or getattr(data, "extra_data", None)
            return {
                "id": getattr(data, "id", None),
                "alert_id": getattr(data, "alert_id", None),
                "external_alert_id": getattr(data, "external_alert_id", None),
                "source": getattr(data, "source", None),
                "account_id": getattr(data, "account_id", None),
                "transaction_id": getattr(data, "transaction_id", None),
                "amount": getattr(data, "amount", None),
                "merchant": getattr(data, "merchant", None),
                "location": getattr(data, "location", None),
                "device_id": getattr(data, "device_id", None),
                "event_timestamp": getattr(data, "event_timestamp", None),
                "received_timestamp": getattr(data, "received_timestamp", None),
                "received_at": getattr(data, "received_at", None),
                "risk_level": getattr(data, "risk_level", None),
                "fraud_type": getattr(data, "fraud_type", None),
                "alert_type": getattr(data, "alert_type", None),
                "description": getattr(data, "description", None),
                "metadata": meta_val,
                "extra_data": getattr(data, "extra_data", None) or meta_val,
            }
        return data



