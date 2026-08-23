from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.alert import AlertCreate, AlertResponse
from app.services import ingestion_service
from app.services.mock_generator import get_mock_replay_sequence, get_scenario_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


class BatchAlertInput(BaseModel):
    alerts: List[AlertCreate]


class ReplayRequest(BaseModel):
    scenario: str = "default"


class IngestionSingleResponse(BaseModel):
    status: str = "accepted"
    alert_id: str
    message: str = "Alert accepted for processing"
    alert: AlertResponse


class IngestionBatchResponse(BaseModel):
    status: str = "accepted"
    count: int
    message: str = "Batch alerts accepted for processing"
    alerts: List[AlertResponse]


class IngestionReplayResponse(BaseModel):
    status: str = "accepted"
    scenario: str
    count: int
    message: str = "Mock alerts replayed and ingested"
    alerts: List[AlertResponse]


@router.post("/source-a", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def ingest_source_a(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    """Ingest a fraud alert specifically from SOURCE_A."""
    return ingestion_service.create_alert(db, source="SOURCE_A", alert_in=alert)


@router.post("/source-b", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def ingest_source_b(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    """Ingest a fraud alert specifically from SOURCE_B."""
    return ingestion_service.create_alert(db, source="SOURCE_B", alert_in=alert)


@router.post("/source-c", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def ingest_source_c(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    """Ingest a fraud alert specifically from SOURCE_C."""
    return ingestion_service.create_alert(db, source="SOURCE_C", alert_in=alert)


@router.post("", response_model=IngestionSingleResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_single(alert: AlertCreate, db: Session = Depends(get_db)) -> IngestionSingleResponse:
    """Generic endpoint to ingest a single fraud alert."""
    created = ingestion_service.create_alert(db, source=alert.source or "SOURCE_A", alert_in=alert)
    return IngestionSingleResponse(
        status="accepted",
        alert_id=created.alert_id,
        message="Alert accepted for processing",
        alert=created,
    )


@router.post("/batch", response_model=IngestionBatchResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_batch(payload: BatchAlertInput, db: Session = Depends(get_db)) -> IngestionBatchResponse:
    """Ingest a batch of fraud alerts without deduplication or sorting."""
    tuples = [(alert.source, alert) for alert in payload.alerts]
    created_list = ingestion_service.create_batch(db, tuples)
    return IngestionBatchResponse(
        status="accepted",
        count=len(created_list),
        message="Batch alerts accepted for processing",
        alerts=created_list,
    )


@router.post("/replay", response_model=IngestionReplayResponse, status_code=status.HTTP_202_ACCEPTED)
def replay_mock_scenario(payload: ReplayRequest = ReplayRequest(), db: Session = Depends(get_db)) -> IngestionReplayResponse:
    """Replay deterministic mock scenarios (default, conflict, duplicate, incomplete, out_of_order)."""
    raw_mock_alerts = get_scenario_alerts(payload.scenario)
    tuples = []
    for raw in raw_mock_alerts:
        parsed = AlertCreate.model_validate(raw)
        tuples.append((parsed.source, parsed))

    created_list = ingestion_service.create_batch(db, tuples)
    return IngestionReplayResponse(
        status="accepted",
        scenario=payload.scenario,
        count=len(created_list),
        message="Mock alerts replayed and ingested",
        alerts=created_list,
    )


@router.get("", response_model=List[AlertResponse])
def get_recent_alerts(limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db)) -> List[AlertResponse]:
    """Retrieve recently ingested raw alerts in strict arrival order."""
    return ingestion_service.get_alerts(db, limit=limit)
