from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.schemas.alert import AlertCreate


def create_alert(db: Session, source: str, alert_in: AlertCreate) -> Alert:
    """Persist a raw alert from a given source. No correlation or scoring happens here."""
    db_alert = Alert(source=source, **alert_in.model_dump())
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert
