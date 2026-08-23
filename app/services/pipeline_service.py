"""Orchestrates the full ThreatFlow pipeline for one alert:

ingestion -> correlation -> rule engine -> action -> audit.

This wires together each module's existing public functions only —
ingestion_service, correlation_service, RuleEngine, and action_service are
untouched. It exists because none of those modules call each other
automatically; something has to run them in sequence and record each
step to the audit trail.
"""

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.schemas.alert import AlertCreate
from app.services import action_service, audit_service, correlation_service, ingestion_service
from app.services.rule_engine import RuleEngine

_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "rules.json"
_engine = RuleEngine(_RULES_PATH)

# Sources report their own risk assessment as a label; the rule engine's
# HIGH_SOURCE_RISK rule needs it as a number. This is the documented mapping.
_SOURCE_RISK_LEVEL_SCORE = {"LOW": 20, "MEDIUM": 50, "HIGH": 80, "CRITICAL": 95}


def _build_rule_event(
    correlation_result: Any, group_id: str, alerts_in_group: list[Alert]
) -> dict[str, Any]:
    winning = correlation_result.winning_alert
    conflicts = correlation_service.detect_conflicts(alerts_in_group)
    location_mismatch = any(c.field == "location" for c in conflicts)

    risk_level_label = (winning.get("risk_level") or "").upper()
    source_risk_score = _SOURCE_RISK_LEVEL_SCORE.get(risk_level_label, 0)

    return {
        "event_id": group_id,
        "account_id": winning.get("account_id"),
        "transaction_id": winning.get("transaction_id"),
        "amount": winning.get("amount"),
        "location": winning.get("location"),
        "location_mismatch": location_mismatch,
        "source_risk_score": source_risk_score,
        "conflict_status": correlation_result.conflict_status,
    }


def process_alert(db: Session, source: str, alert_in: AlertCreate) -> dict[str, Any]:
    """Run one alert through the full pipeline. Returns every stage's output."""

    stored_alert = ingestion_service.create_alert(db, source=source, alert_in=alert_in)
    audit_service.create_audit_record(
        db,
        entity_id=str(stored_alert.id),
        event_type="ALERT_RECEIVED",
        outcome="stored",
        reason=f"Alert {stored_alert.alert_id} ingested from {source}.",
    )

    correlation_result = correlation_service.correlate_alert(db, stored_alert)
    audit_service.create_audit_record(
        db,
        entity_id=correlation_result.group_id,
        event_type="ALERT_CORRELATED",
        outcome=correlation_result.conflict_status,
        reason=(
            f"Merged into group {correlation_result.group_id} "
            f"({len(correlation_result.merged_alert_ids)} alert(s) total)."
        ),
    )

    alerts_in_group = (
        db.query(Alert).filter(Alert.id.in_(correlation_result.merged_alert_ids)).all()
    )

    if correlation_result.conflict_status != "no_conflict":
        conflicts = correlation_service.detect_conflicts(alerts_in_group)
        audit_service.create_audit_record(
            db,
            entity_id=correlation_result.group_id,
            event_type="CONFLICT_DETECTED",
            outcome=correlation_result.conflict_status,
            reason="Conflicting fields: " + ", ".join(c.field for c in conflicts) if conflicts else "Conflict flagged.",
        )
        audit_service.create_audit_record(
            db,
            entity_id=correlation_result.group_id,
            event_type="CONFLICT_RESOLVED",
            outcome=correlation_result.conflict_status,
            reason="Resolved via majority-vote / most-recent-evidence strategy.",
        )

    rule_event = _build_rule_event(correlation_result, correlation_result.group_id, alerts_in_group)
    risk_result = _engine.evaluate_event(rule_event)

    audit_service.create_audit_record(
        db,
        entity_id=risk_result.event_id,
        event_type="RULE_EVALUATED",
        outcome=risk_result.risk_level,
        reason="Matched rules: " + ", ".join(r.rule_id for r in risk_result.matched_rules)
        if risk_result.matched_rules
        else "No rules matched.",
    )
    audit_service.create_audit_record(
        db,
        entity_id=risk_result.event_id,
        event_type="RISK_SCORED",
        outcome=str(risk_result.risk_score),
        reason=f"Risk score {risk_result.risk_score} -> {risk_result.risk_level}.",
    )

    action = action_service.create_action(risk_result)
    action_service.store_action(action)
    audit_service.create_audit_record(
        db,
        entity_id=action.action_id,
        event_type="ACTION_CREATED",
        outcome=action.recommended_action,
        reason=action.explanation,
    )

    return {
        "alert": stored_alert,
        "correlation": correlation_result,
        "risk_result": risk_result,
        "action": action,
    }
