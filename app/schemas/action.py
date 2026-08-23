"""API/output contract for prioritized actions."""

from datetime import datetime

from pydantic import BaseModel


class Action(BaseModel):
    """Recommended action generated from a RiskResult."""

    action_id: str
    event_id: str
    priority: int
    risk_score: float
    risk_level: str
    recommended_action: str
    created_at: datetime
    explanation: str
