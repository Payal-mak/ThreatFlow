# ThreatFlow — Member 2: Correlation & Conflict Resolution

## Srinivas — Correlation, Deduplication & Conflict Resolution

### Responsibility

Srinivas is responsible for the core correlation and reconciliation layer of ThreatFlow.

The module receives canonical `Alert` objects from the ingestion layer and determines which alerts belong to the same underlying fraud event.

### Responsibilities Completed

- Alert correlation
- Deduplication
- Merge-window handling
- Out-of-order alert handling
- Late-arrival handling
- Incomplete evidence handling
- Alert merging
- Field-level conflict detection
- Deterministic conflict resolution
- Correlation evidence and explainability
- Integration with the canonical Alert contract

---

## Processing Flow

```text
Canonical Alert
       ↓
Deduplication
       ↓
Correlation
       ↓
Merge Window
       ↓
Out-of-Order / Late Arrival Handling
       ↓
Alert Merging
       ↓
Conflict Detection
       ↓
Conflict Resolution
       ↓
Correlated Event
       ↓
Payal — Rule Engine
