from fastapi import FastAPI

from app.api.routes.actions import router as actions_router
from app.api.routes.alerts import router as alerts_router
from app.api.routes.correlation import router as correlation_router
from app.api.routes.health import router as health_router
from app.config import settings
from app.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    description="ThreatFlow Fraud Alert Ingestion Module",
)

app.include_router(health_router)
app.include_router(health_router, prefix="/api")

app.include_router(alerts_router)
app.include_router(alerts_router, prefix="/api")

app.include_router(correlation_router)

app.include_router(actions_router)
app.include_router(actions_router, prefix="/api")
