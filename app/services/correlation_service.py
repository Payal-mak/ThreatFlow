"""Alert Correlation, Deduplication, Merge-Window & Conflict Service

=============================================================
CORRELATION KEY STRATEGY
=============================================================

Two raw alerts from *different* sources are considered to represent the
**same underlying fraud event** when they share a deterministic
*correlation key* computed from the fields that exist on the current
``Alert`` model.

The key is built in two tiers:

1. **transaction_id present (strongest signal)**
   ``correlation_key = f"{account_id}|{transaction_id}"``

2. **transaction_id absent (fallback)**
   ``correlation_key = f"{account_id}|{amount}|{event_date}"``

Amounts are rounded to **2 decimal places** so minor floating-point
differences between sources do not break the key.

=============================================================
DEDUPLICATION RULES
=============================================================

a) *Same-source exact duplicate* — same ``source`` + same
   ``external_alert_id``.
b) *Cross-source duplicate* — different ``source`` but same
   ``correlation_key``, same ``amount``, AND same ``event_timestamp``
   within 1-second tolerance.

=============================================================
MERGE WINDOW
=============================================================

A configurable time window (``settings.merge_window_seconds``, default
300 s) controls how far apart two alerts' ``event_timestamp`` values
may be before they are considered separate events.

=============================================================
OUT-OF-ORDER & LATE ARRIVAL
=============================================================

Alerts may arrive in any order relative to their ``event_timestamp``.
The merge-window check operates purely on ``event_timestamp`` (not
``received_at``), so late alerts are naturally handled.

=============================================================
CONFLICT DETECTION & RESOLUTION
=============================================================

After correlation groups are formed, field-level conflicts are
detected and resolved.

Conflict fields (from the Alert schema):
- ``location``       — different sources may report different locations
- ``amount``         — Tier-1 key groups allow different amounts
- ``fraud_type``     — different classification labels
- ``description``    — different free-text descriptions
- ``transaction_id`` — mismatch means grouping may be wrong
- ``account_id``     — mismatch means grouping may be wrong

Resolution strategy (deterministic):
1. For each conflict field, collect all distinct non-null values.
2. Pick the **most common value** (majority vote).
3. If there is no clear majority (tie or all-unique) → mark the field
   as **unresolved** and preserve all evidence.
4. ``account_id`` and ``transaction_id`` mismatches are **always
   unresolved** — these define the correlation identity, so a
   mismatch indicates the grouping itself may be incorrect.

All original evidence (alert IDs, sources, values) is preserved in
the conflict details so that the audit trail remains intact.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert import Alert
from app.models.correlated_alert import CorrelatedAlert
from app.schemas.correlated_alert import (
    ConflictDetail,
    ConflictResult,
    ConflictValue,
    CorrelationResult,
)


# ---------------------------------------------------------------------------
# Correlation key computation
# ---------------------------------------------------------------------------

def _normalise_amount(amount: float | None) -> str:
    """Return a canonical string for *amount*, or ``""`` if None."""
    if amount is None:
        return ""
    return f"{round(amount, 2):.2f}"


def _normalise_timestamp(ts: datetime) -> str:
    """Return the calendar-date string (YYYY-MM-DD) for *ts*."""
    return ts.date().isoformat()


def compute_correlation_key(alert: Alert) -> str:
    """Derive a deterministic correlation key from a raw alert.

    Returns
    -------
    str
        A deterministic, source-agnostic key that identifies the
        underlying fraud event.
    """
    account_id = alert.account_id

    if alert.transaction_id:
        return f"{account_id}|{alert.transaction_id}"

    amount_part = _normalise_amount(alert.amount)
    date_part = _normalise_timestamp(alert.event_timestamp) if alert.event_timestamp else ""
    return f"{account_id}|{amount_part}|{date_part}"


def compute_correlation_key_from_dict(data: dict[str, Any]) -> str:
    """Same as :func:`compute_correlation_key` but accepts a plain dict."""
    account_id = data.get("account_id", "")

    transaction_id = data.get("transaction_id")
    if transaction_id:
        return f"{account_id}|{transaction_id}"

    amount_part = _normalise_amount(data.get("amount"))
    event_ts = data.get("event_timestamp")
    if isinstance(event_ts, datetime):
        date_part = _normalise_timestamp(event_ts)
    elif event_ts is not None:
        date_part = str(event_ts)[:10]
    else:
        date_part = ""
    return f"{account_id}|{amount_part}|{date_part}"


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _alert_to_dict(alert: Alert) -> dict[str, Any]:
    """Serialise an Alert row to a plain dict for downstream use.

    Uses main's Alert model:
    - ``alert.id``       — DB integer primary key (used for merged_alert_ids)
    - ``alert.alert_id`` — canonical string identifier (used for provenance)
    """
    return {
        "id": alert.id,
        "alert_id": alert.alert_id,
        "source": alert.source,
        "external_alert_id": alert.external_alert_id,
        "account_id": alert.account_id,
        "transaction_id": alert.transaction_id,
        "amount": alert.amount,
        "location": alert.location,
        "event_timestamp": alert.event_timestamp,
        "received_timestamp": alert.received_timestamp,
        "received_at": alert.received_at,
        "merchant": alert.merchant,
        "device_id": alert.device_id,
        "risk_level": alert.risk_level,
        "fraud_type": alert.fraud_type,
        "alert_type": alert.alert_type,
        "description": alert.description,
        "meta_data": alert.meta_data,
        "extra_data": alert.extra_data,
    }


def is_exact_duplicate(existing: Alert, incoming: Alert) -> bool:
    """Same source + same external_alert_id → exact duplicate."""
    return (
        existing.source == incoming.source
        and existing.external_alert_id == incoming.external_alert_id
    )


def is_cross_source_duplicate(
    existing: Alert,
    incoming: Alert,
    timestamp_tolerance_seconds: float = 1.0,
) -> bool:
    """Different sources, same correlation key, same amount, timestamps within tolerance."""
    if existing.source == incoming.source:
        return False

    key_existing = compute_correlation_key(existing)
    key_incoming = compute_correlation_key(incoming)
    if key_existing != key_incoming:
        return False

    if _normalise_amount(existing.amount) != _normalise_amount(incoming.amount):
        return False

    if existing.event_timestamp and incoming.event_timestamp:
        diff = abs((existing.event_timestamp - incoming.event_timestamp).total_seconds())
        if diff > timestamp_tolerance_seconds:
            return False

    return True


# ---------------------------------------------------------------------------
# Merge-window helpers
# ---------------------------------------------------------------------------

def _get_group_event_time_range(
    alert_ids: list[int], db: Session
) -> tuple[datetime, datetime] | None:
    """Return (min_ts, max_ts) for the raw alerts in a group, or None."""
    if not alert_ids:
        return None
    alerts = db.query(Alert).filter(Alert.id.in_(alert_ids)).all()
    if not alerts:
        return None
    timestamps = [a.event_timestamp for a in alerts if a.event_timestamp is not None]
    if not timestamps:
        return None
    return min(timestamps), max(timestamps)


def is_within_merge_window(
    new_timestamp: datetime,
    group_range: tuple[datetime, datetime],
    window_seconds: int,
) -> bool:
    """Check whether *new_timestamp* falls inside the group's merge window.

    The window extends ``window_seconds`` in both directions from the
    group's earliest and latest ``event_timestamp``.
    """
    min_ts, max_ts = group_range
    lower = min_ts - timedelta(seconds=window_seconds)
    upper = max_ts + timedelta(seconds=window_seconds)
    return lower <= new_timestamp <= upper


# ---------------------------------------------------------------------------
# Out-of-order & late-arrival handling
# ---------------------------------------------------------------------------

def find_matching_group(
    new_alert: Alert,
    candidate_groups: list[CorrelatedAlert],
    db: Session,
    window_seconds: int | None = None,
) -> CorrelatedAlert | None:
    """Find the best existing group for *new_alert* using the merge window.

    Returns the first group whose event-timestamp range overlaps the new
    alert's ``event_timestamp`` within the configured window, or ``None``
    if no group matches.
    """
    if window_seconds is None:
        window_seconds = settings.merge_window_seconds

    best: CorrelatedAlert | None = None
    best_range_width: float | None = None

    for group in candidate_groups:
        group_range = _get_group_event_time_range(group.merged_alert_ids, db)
        if group_range is None:
            continue

        if is_within_merge_window(new_alert.event_timestamp, group_range, window_seconds):
            # Prefer the tightest-fitting group (smallest time span).
            width = (group_range[1] - group_range[0]).total_seconds()
            if best_range_width is None or width < best_range_width:
                best = group
                best_range_width = width

    return best


# ---------------------------------------------------------------------------
# Conflict detection & resolution
# ---------------------------------------------------------------------------

# Fields where a mismatch is always unresolved because they define
# the correlation identity itself.
_IDENTITY_FIELDS = {"account_id", "transaction_id"}

# Fields that are checked for value conflicts.
_CONFLICT_FIELDS = ["location", "amount", "fraud_type", "description"]

# Fields that are metadata / identity — never conflict-checked.
_SKIP_FIELDS = {
    "id", "alert_id", "source", "external_alert_id", "event_timestamp",
    "received_timestamp", "received_at", "meta_data", "extra_data",
    "alert_type", "merchant", "device_id", "risk_level",
}


def _collect_field_values(
    alerts: list[Alert], field: str
) -> list[ConflictValue]:
    """Gather all non-null values for *field* across *alerts*."""
    values: list[ConflictValue] = []
    for alert in alerts:
        raw = getattr(alert, field, None)
        if raw is not None:
            values.append(ConflictValue(
                source=alert.source,
                alert_id=alert.alert_id,
                value=raw,
            ))
    return values


def _majority_value(values: list[ConflictValue]) -> tuple[Any | None, str]:
    """Return the most common value and a human-readable reason.

    Returns ``(None, "")`` when no single value has a strict majority.
    """
    if not values:
        return None, ""

    # For floats, round to 2 decimal places before counting.
    counter: Counter[Any] = Counter()
    for v in values:
        key = round(v.value, 2) if isinstance(v.value, float) else v.value
        counter[key] += 1

    most_common_val, most_common_count = counter.most_common(1)[0]

    # Require a strict majority (> half) to declare a winner.
    if most_common_count > len(values) / 2:
        # Resolve to the actual value (not rounded) from the first alert
        # that carries the winning value.
        for v in values:
            compare = round(v.value, 2) if isinstance(v.value, float) else v.value
            if compare == most_common_val:
                return v.value, (
                    f"majority vote: {most_common_count}/{len(values)} "
                    f"sources reported this value"
                )

    return None, ""


def detect_conflicts(alerts_in_group: list[Alert]) -> list[ConflictDetail]:
    """Identify field-level conflicts among alerts in the same group.

    For each conflict-relevant field, collect all distinct non-null
    values.  If more than one distinct value exists, it is a conflict.

    Returns a list of :class:`ConflictDetail` objects — one per
    conflicting field.
    """
    if len(alerts_in_group) < 2:
        return []

    conflicts: list[ConflictDetail] = []

    for field in _CONFLICT_FIELDS:
        values = _collect_field_values(alerts_in_group, field)
        if len(values) < 2:
            continue

        # Check how many distinct values exist.
        distinct = {v.value for v in values}
        if len(distinct) <= 1:
            continue

        conflicts.append(ConflictDetail(
            field=field,
            conflicting_values=values,
        ))

    # Check identity fields — these should always agree within a group.
    for field in _IDENTITY_FIELDS:
        values = _collect_field_values(alerts_in_group, field)
        if len(values) < 2:
            continue
        distinct = {v.value for v in values}
        if len(distinct) > 1:
            conflicts.append(ConflictDetail(
                field=field,
                conflicting_values=values,
                resolution_reason=(
                    f"identity field '{field}' differs across sources — "
                    "grouping may be incorrect"
                ),
            ))

    return conflicts


def resolve_conflicts(
    alerts_in_group: list[Alert],
    conflicts: list[ConflictDetail],
) -> ConflictResult:
    """Apply deterministic resolution to detected conflicts.

    Strategy:
    - Identity-field conflicts (``account_id``, ``transaction_id``) are
      always marked **unresolved**.
    - For other fields, use majority vote.  If no clear majority, mark
      the field as **unresolved**.
    - The winning alert (for non-conflicting fields) is selected by the
      existing ``_select_winning_alert`` heuristic.

    Returns a :class:`ConflictResult` with the full conflict picture.
    """
    winning = _select_winning_alert(alerts_in_group)
    winning_dict = _alert_to_dict(winning)

    resolved_fields: dict[str, Any] = {}
    unresolved_fields: list[str] = []

    for conflict in conflicts:
        if conflict.field in _IDENTITY_FIELDS:
            # Identity mismatches are always unresolved.
            conflict.resolution_reason = (
                f"identity field '{conflict.field}' differs across sources "
                "— grouping may be incorrect; requires manual review"
            )
            unresolved_fields.append(conflict.field)
            continue

        # Try majority vote.
        winner_val, reason = _majority_value(conflict.conflicting_values)
        if winner_val is not None:
            conflict.resolved_value = winner_val
            conflict.resolution_reason = reason
            resolved_fields[conflict.field] = winner_val
        else:
            conflict.resolution_reason = (
                "no clear majority; all source values preserved for manual review"
            )
            unresolved_fields.append(conflict.field)

    # Determine overall status.
    if not conflicts:
        status = "no_conflict"
    elif unresolved_fields:
        status = "unresolved"
    else:
        status = "resolved"

    # Apply resolved field values to the winning alert snapshot.
    for field, value in resolved_fields.items():
        winning_dict[field] = value

    return ConflictResult(
        conflicts=conflicts,
        conflict_status=status,
        winning_alert=winning_dict,
    )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _select_winning_alert(alerts: list[Alert]) -> Alert:
    """Choose the most authoritative alert from a group.

    Heuristic (simple, deterministic):
    1. Prefer the alert with the most non-null fields.
    2. Break ties by most-recent ``received_at``.
    """
    def _completeness(a: Alert) -> tuple[int, datetime]:
        filled = sum(
            1 for field in (
                a.transaction_id, a.amount, a.location,
                a.fraud_type, a.description, a.meta_data,
                a.merchant, a.device_id, a.risk_level,
            )
            if field is not None
        )
        return (filled, a.received_at)

    return max(alerts, key=_completeness)


def correlate_alert(
    db: Session,
    new_alert: Alert,
    window_seconds: int | None = None,
) -> CorrelationResult:
    """Run the full correlation + dedup + merge-window + conflict pipeline.

    This is the main entry-point called after ingestion.  It:

    1. Computes the correlation key for *new_alert*.
    2. Finds existing groups with the same key.
    3. For each candidate group, checks whether the new alert's
       ``event_timestamp`` falls inside the group's merge window.
    4. If a match is found → merges into that group (out-of-order
       alerts are attached to the correct group).
    5. If no match → creates a new group.
    6. After merging, runs conflict detection on the full group.
    7. Returns a ``CorrelationResult`` describing what happened.

    Parameters
    ----------
    window_seconds : int | None
        Override the default merge window (from settings) for this call.
        Useful for testing.
    """
    if window_seconds is None:
        window_seconds = settings.merge_window_seconds

    correlation_key = compute_correlation_key(new_alert)

    # Find all existing CorrelatedAlert groups with this key.
    existing_groups = (
        db.query(CorrelatedAlert)
        .filter(CorrelatedAlert.correlation_key == correlation_key)
        .all()
    )

    # Also find raw alerts that share this key (for duplicate checks).
    # merged_alert_ids stores Alert.id (integer PK) values.
    candidate_alert_ids: list[int] = []
    for grp in existing_groups:
        candidate_alert_ids.extend(grp.merged_alert_ids)

    candidate_alerts: list[Alert] = []
    if candidate_alert_ids:
        candidate_alerts = (
            db.query(Alert)
            .filter(Alert.id.in_(candidate_alert_ids))
            .all()
        )

    # --- Duplicate detection ---
    is_dup = False
    for existing in candidate_alerts:
        if is_exact_duplicate(existing, new_alert) or is_cross_source_duplicate(existing, new_alert):
            is_dup = True
            break

    # --- Merge-window + out-of-order matching ---
    matched_group = find_matching_group(
        new_alert, existing_groups, db, window_seconds=window_seconds
    )

    if matched_group is not None:
        # Merge into the matched group (handles late arrivals too).
        merged_ids = list(matched_group.merged_alert_ids or [])
        if new_alert.id not in merged_ids:
            merged_ids.append(new_alert.id)

        all_alerts_in_group = candidate_alerts + [new_alert]

        # --- Conflict detection & resolution ---
        conflicts = detect_conflicts(all_alerts_in_group)
        conflict_result = resolve_conflicts(all_alerts_in_group, conflicts)

        # Use the conflict-resolved winning alert for the group snapshot
        # so that majority-vote values are persisted, not the raw winner.
        resolved = conflict_result.winning_alert

        matched_group.merged_alert_ids = merged_ids
        matched_group.is_duplicate = matched_group.is_duplicate or is_dup
        matched_group.account_id = resolved["account_id"]
        matched_group.transaction_id = resolved["transaction_id"]
        matched_group.amount = resolved["amount"]
        matched_group.location = resolved["location"]
        matched_group.event_timestamp = resolved["event_timestamp"]
        matched_group.merchant = resolved["merchant"]
        matched_group.device_id = resolved["device_id"]
        matched_group.risk_level = resolved["risk_level"]
        matched_group.fraud_type = resolved["fraud_type"]
        matched_group.description = resolved["description"]
        matched_group.conflict_status = conflict_result.conflict_status

        db.commit()
        db.refresh(matched_group)

        return CorrelationResult(
            correlation_key=correlation_key,
            group_id=matched_group.group_id,
            merged_alert_ids=merged_ids,
            is_duplicate=matched_group.is_duplicate,
            winning_alert=conflict_result.winning_alert,
            conflict_status=conflict_result.conflict_status,
        )

    # --- No match → new group ---
    group_id = uuid.uuid4().hex
    winning_dict = _alert_to_dict(new_alert)

    new_group = CorrelatedAlert(
        correlation_key=correlation_key,
        group_id=group_id,
        merged_alert_ids=[new_alert.id],
        is_duplicate=is_dup,
        account_id=winning_dict["account_id"],
        transaction_id=winning_dict["transaction_id"],
        amount=winning_dict["amount"],
        location=winning_dict["location"],
        event_timestamp=winning_dict["event_timestamp"],
        merchant=winning_dict["merchant"],
        device_id=winning_dict["device_id"],
        risk_level=winning_dict["risk_level"],
        fraud_type=winning_dict["fraud_type"],
        description=winning_dict["description"],
        conflict_status="no_conflict",
    )
    db.add(new_group)
    db.commit()
    db.refresh(new_group)

    return CorrelationResult(
        correlation_key=correlation_key,
        group_id=group_id,
        merged_alert_ids=[new_alert.id],
        is_duplicate=is_dup,
        winning_alert=winning_dict,
        conflict_status="no_conflict",
    )


def get_all_correlated(db: Session) -> list[CorrelatedAlert]:
    """Return every correlated group, ordered by creation time."""
    return db.query(CorrelatedAlert).order_by(CorrelatedAlert.created_at).all()


def get_correlated_by_group(db: Session, group_id: str) -> CorrelatedAlert | None:
    """Fetch a single correlation group by its opaque group_id."""
    return db.query(CorrelatedAlert).filter(CorrelatedAlert.group_id == group_id).first()
