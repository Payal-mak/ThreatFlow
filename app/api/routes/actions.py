"""API routes for prioritized ThreatFlow actions."""

from fastapi import APIRouter

from app.schemas.action import Action
from app.services.action_service import get_actions

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("", response_model=list[Action])
def list_actions() -> list[Action]:
    """Return the current prioritized action list."""
    return get_actions()
