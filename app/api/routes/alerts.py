from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.alert import AlertCreate, AlertResponse
from app.services import ingestion_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/source-a", response_model=AlertResponse, status_code=201)
def ingest_source_a(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    return ingestion_service.create_alert(db, source="source-a", alert_in=alert)


@router.post("/source-b", response_model=AlertResponse, status_code=201)
def ingest_source_b(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    return ingestion_service.create_alert(db, source="source-b", alert_in=alert)


@router.post("/source-c", response_model=AlertResponse, status_code=201)
def ingest_source_c(alert: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    return ingestion_service.create_alert(db, source="source-c", alert_in=alert)
