from fastapi import FastAPI

from app.api.routes.alerts import router as alerts_router
from app.api.routes.health import router as health_router
from app.config import settings
from app.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, description="ThreatFlow Fraud Alert Ingestion Module")

# Mount health and alert routes (supporting both /alerts and /api/alerts)
app.include_router(health_router)
app.include_router(health_router, prefix="/api")
app.include_router(alerts_router)
app.include_router(alerts_router, prefix="/api")

