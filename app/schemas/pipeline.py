"""Combined response contract for the end-to-end pipeline endpoint."""

from pydantic import BaseModel

from app.schemas.action import Action
from app.schemas.alert import AlertResponse
from app.schemas.correlated_alert import CorrelationResult
from app.schemas.risk_result import RiskResult


class PipelineResult(BaseModel):
    alert: AlertResponse
    correlation: CorrelationResult
    risk_result: RiskResult
    action: Action
