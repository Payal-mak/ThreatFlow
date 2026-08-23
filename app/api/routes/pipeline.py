"""End-to-end demo endpoint: ingest -> correlate -> score -> act -> audit."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.alert import AlertCreate
from app.schemas.pipeline import PipelineResult
from app.services.pipeline_service import process_alert

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/{source}", response_model=PipelineResult, status_code=201)
def run_pipeline(source: str, alert: AlertCreate, db: Session = Depends(get_db)) -> PipelineResult:
    """Ingest one alert and run it through the complete ThreatFlow pipeline.

    source: "source-a", "source-b", or "source-c".
    """
    normalized_source = source.upper().replace("-", "_")
    result = process_alert(db, source=normalized_source, alert_in=alert)
    return PipelineResult(
        alert=result["alert"],
        correlation=result["correlation"],
        risk_result=result["risk_result"],
        action=result["action"],
    )
