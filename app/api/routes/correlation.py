from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.correlated_alert import CorrelatedAlertResponse
from app.services import correlation_service

router = APIRouter(prefix="/correlation", tags=["correlation"])


@router.get("/groups", response_model=list[CorrelatedAlertResponse])
def list_correlated_groups(db: Session = Depends(get_db)) -> list[CorrelatedAlertResponse]:
    """Return all correlated fraud-event groups."""
    return correlation_service.get_all_correlated(db)


@router.get("/groups/{group_id}", response_model=CorrelatedAlertResponse)
def get_correlated_group(
    group_id: str, db: Session = Depends(get_db)
) -> CorrelatedAlertResponse:
    """Return a single correlation group by its opaque group_id."""
    group = correlation_service.get_correlated_by_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Correlation group not found")
    return group
