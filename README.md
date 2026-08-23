# ThreatFlow — Real-Time Fraud Alert Reconciliation Engine

ThreatFlow is a real-time fraud alert reconciliation engine that ingests asynchronous, conflicting alerts from multiple sources, applies configurable risk rules, resolves conflicts deterministically, and outputs a prioritized action list with full auditability.

---

## Problem Statement

A financial institution's fraud detection system receives alerts from multiple third-party risk feeds, but these alerts often conflict or arrive out of order. The same underlying fraud event can:

- Arrive from multiple sources with different observations
- Arrive out of order or late
- Contain duplicate alerts
- Contain conflicting information (different locations, amounts, risk classifications)
- Contain incomplete fields

ThreatFlow solves this by providing a pipeline that ingests all alerts without loss, correlates them into unified events, resolves conflicts, applies configurable risk rules, and produces prioritized actions — all while preserving every decision for audit.

---

## Architecture

```
Multiple Alert Sources (SOURCE_A, SOURCE_B, SOURCE_C)
        │
        ▼
┌─────────────────────────┐
│  Nithilan — Ingestion   │  REST endpoints, validation, normalization,
│                         │  canonical Alert schema, timestamp handling
└────────────┬────────────┘
             │  Alert
             ▼
┌─────────────────────────────────────┐
│  Srinivas — Correlation & Conflict  │  Two-tier correlation keys, deduplication,
│                                     │  merge window, out-of-order handling,
│                                     │  conflict detection & resolution
└────────────┬────────────────────────┘
             │  CorrelatedEvent (grouped, conflict-resolved)
             ▼
┌─────────────────────────────────────┐
│  Payal — Dynamic Rule Engine + Risk │  JSON-configured rules, hot-reload,
│                                     │  risk score, risk level, evaluation trace
└────────────┬────────────────────────┘
             │  RiskResult
             ▼
┌─────────────────────────────────────┐
│  Saad — Actions + Audit             │  Priority calculation, action generation,
│                                     │  SQLite-backed audit trail
└─────────────────────────────────────┘

Shivika — Docker, Docker Compose, GitHub Actions CI/CD
```

---

## Team Responsibilities

### Nithilan — Ingestion

- Canonical Alert schema with full field set
- REST endpoints for 3 fraud alert sources (SOURCE_A, SOURCE_B, SOURCE_C)
- Input validation (source names, risk levels, non-negative amounts)
- `received_timestamp` / `received_at` generated at ingestion time (UTC)
- Incomplete evidence support (many fields optional)
- Duplicate and conflicting alerts stored without filtering
- Mock replay endpoint with predefined scenarios
- Batch ingestion support

### Srinivas — Correlation & Conflict Resolution

- Two-tier correlation key strategy:
  - **Tier 1:** `account_id | transaction_id` (strongest signal)
  - **Tier 2:** `account_id | rounded_amount | event_date` (fallback when transaction_id is absent)
- Exact duplicate detection (same source + same external_alert_id)
- Cross-source duplicate detection (same correlation key + same amount + same timestamp within 1s)
- Configurable merge window (default 300 seconds) operating on `event_timestamp`
- Out-of-order and late-arrival handling (alerts arriving after others join existing groups if within window)
- Field-level conflict detection (location, amount, fraud_type, description)
- Deterministic conflict resolution via majority vote
- Identity field mismatches (account_id, transaction_id) always marked unresolved
- All original evidence preserved for audit

### Payal — Dynamic Rule Engine + Risk

- Rules defined in `config/rules.json` (JSON format, no code changes needed)
- Hot-reload: rule file changes are picked up automatically without restart
- Malformed rule files logged safely without crashing evaluation
- Rule evaluation produces:
  - `risk_score` (sum of matched rule scores)
  - `risk_level` (LOW / MEDIUM / HIGH / CRITICAL based on score bands)
  - `matched_rules` (which rules fired)
  - `evaluation_trace` (full explainability for every rule)
- Default rules: HIGH_AMOUNT, LOCATION_MISMATCH, HIGH_AMOUNT_LOCATION_MISMATCH (compound), HIGH_SOURCE_RISK

### Saad — Actions + Audit

- Converts RiskResult into prioritized Action objects
- Priority calculation: `risk_level_weight * 100 + risk_score`
- Recommended actions: BLOCK_TRANSACTION_AND_ESCALATE (CRITICAL), MANUAL_REVIEW (HIGH), REVIEW (MEDIUM), MONITOR (LOW)
- In-memory action store with deterministic priority ordering
- SQLite-backed audit trail recording every pipeline stage (ingestion, correlation, conflict, rule evaluation, risk scoring, action creation)
- Audit records include entity_id, event_type, outcome, reason, and metadata

### Shivika — DevOps

- Dockerfile (Python 3.11-slim, installs dependencies, copies app + config + tests)
- Docker Compose configuration
- GitHub Actions CI/CD pipeline: install dependencies → run tests → build Docker image
- `.dockerignore` for clean builds
- Final integration wiring (pipeline endpoint connecting all modules)

---

## Canonical Alert Schema

### AlertCreate (Ingestion Input)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `account_id` | string | **Yes** | Customer / Account identifier |
| `event_timestamp` | datetime | **Yes** | Timestamp of fraud event occurrence |
| `alert_id` | string | No | Canonical alert identifier (auto-generated if absent) |
| `external_alert_id` | string | No | External alert identifier (synced with alert_id if absent) |
| `transaction_id` | string | No | Transaction identifier (optional for incomplete evidence) |
| `amount` | float | No | Transaction amount (must be non-negative if provided) |
| `merchant` | string | No | Merchant name |
| `location` | string | No | Geographic location |
| `device_id` | string | No | Device identifier |
| `risk_level` | string | No | LOW, MEDIUM, HIGH, or CRITICAL |
| `fraud_type` | string | No | Fraud classification |
| `alert_type` | string | No | Alert type (synced with fraud_type) |
| `description` | string | No | Description of the alert |
| `metadata` | dict | No | Arbitrary source metadata |
| `extra_data` | dict | No | Extra data (synced with metadata) |

### AlertResponse (API Output)

All AlertCreate fields plus:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Internal database primary key |
| `source` | string | Normalized source (SOURCE_A, SOURCE_B, SOURCE_C) |
| `received_timestamp` | datetime | UTC timestamp when ingestion received the alert |
| `received_at` | datetime | UTC timestamp when ingestion received the alert |

### Notes

- `alert_id` and `external_alert_id` are automatically synchronized
- `fraud_type` and `alert_type` are automatically synchronized
- `metadata` and `extra_data` are automatically synchronized
- Source is normalized to uppercase (e.g., `source-a` → `SOURCE_A`)
- `risk_level` is normalized to uppercase

---

## Correlation & Deduplication

### Correlation Keys

The system does **not** rely solely on `transaction_id` matching. It uses a two-tier strategy:

1. **Tier 1 (strongest):** `account_id | transaction_id` — when transaction_id is present
2. **Tier 2 (fallback):** `account_id | rounded_amount | event_date` — when transaction_id is absent

Amounts are rounded to 2 decimal places to avoid floating-point mismatches.

### Important Safety Rule

**Same account does NOT automatically mean same event.**

```
Account A1 + TX100  →  Event Group 1
Account A1 + TX200  →  Event Group 2  (separate events)
```

### Deduplication

- **Exact duplicate:** Same source + same external_alert_id → merged into existing group, flagged as duplicate
- **Cross-source duplicate:** Different source, same correlation key, same amount, same event_timestamp (within 1s tolerance) → merged, flagged as duplicate
- Original evidence is never silently deleted

### Merge Window

- Configurable via `merge_window_seconds` (default: 300 seconds)
- Operates on `event_timestamp`, not arrival time
- Alerts within the window are grouped together
- Alerts outside the window create separate events

### Out-of-Order & Late Arrival

Alerts may arrive in any order. The system handles:
- Earlier alerts arriving after later ones (reverse order)
- Late alerts joining existing groups if within merge window
- Late alerts outside the window creating new groups
- No alert is ever dropped due to arrival order

---

## Conflict Resolution

### Detection

Field-level conflicts are detected when multiple alerts in the same group have different values for:
- `location`
- `amount`
- `fraud_type`
- `description`

Identity fields (`account_id`, `transaction_id`) mismatches indicate the grouping may be incorrect and are always unresolved.

### Resolution Strategy (Deterministic)

1. Collect all distinct non-null values for each conflicting field
2. **Majority vote:** The most common value wins
3. **No majority (tie or all-unique):** Field marked as **unresolved**, all evidence preserved
4. Identity field mismatches: Always **unresolved**

### Example

```
Source A:  location = Hyderabad
Source B:  location = Mumbai
Source C:  location = Hyderabad

Majority vote: 2/3 say Hyderabad → resolved_value = Hyderabad
Conflict status: resolved
```

All original observations (source, alert_id, value) are preserved in the conflict details for audit.

---

## Rule Engine

Rules are defined in `config/rules.json` and evaluated against each correlated event. The engine supports:

- Simple conditions: `{ "field": "amount", "op": ">", "value": 10000 }`
- Compound conditions: `{ "AND": [ ... ] }`
- Hot-reload: file changes picked up automatically
- Safe failure: malformed rules logged, previous ruleset preserved

### Default Rules

| Rule ID | Score | Condition | Description |
|---------|-------|-----------|-------------|
| `HIGH_AMOUNT` | 40 | amount > 10000 | High-value transaction |
| `LOCATION_MISMATCH` | 40 | location_mismatch == true | Conflicting locations across sources |
| `HIGH_AMOUNT_LOCATION_MISMATCH` | 80 | amount > 10000 AND location_mismatch == true | Compound: high-value + location conflict |
| `HIGH_SOURCE_RISK` | 30 | source_risk_score >= 70 | Source reported high risk |

### Risk Levels

| Level | Score Range |
|-------|-------------|
| LOW | 0–29 |
| MEDIUM | 30–59 |
| HIGH | 60–79 |
| CRITICAL | 80+ |

---

## Actions & Audit

### Action Generation

Each `RiskResult` is converted to an `Action` with:
- **Priority:** `risk_level_weight * 100 + risk_score`
- **Recommended action:** Based on risk level (CRITICAL → BLOCK, HIGH → MANUAL_REVIEW, etc.)
- **Explanation:** Human-readable summary of why this action was recommended

### Audit Trail

Every pipeline stage is recorded in SQLite:
- `ALERT_RECEIVED` — alert ingested
- `ALERT_CORRELATED` — alert merged into a group
- `CONFLICT_DETECTED` / `CONFLICT_RESOLVED` — conflict handling
- `RULE_EVALUATED` — rules applied to the event
- `RISK_SCORED` — risk score computed
- `ACTION_CREATED` — action generated

---

## API Endpoints

### Pipeline (End-to-End)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/pipeline/{source}` | Ingest and process through entire pipeline (source: source-a, source-b, source-c) |

### Ingestion

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/alerts/source-a` | Ingest alert from SOURCE_A |
| `POST` | `/alerts/source-b` | Ingest alert from SOURCE_B |
| `POST` | `/alerts/source-c` | Ingest alert from SOURCE_C |
| `POST` | `/alerts` | Generic single alert ingestion |
| `POST` | `/alerts/batch` | Batch alert ingestion |
| `POST` | `/alerts/replay` | Replay mock scenarios (conflict, duplicate, incomplete, out_of_order) |
| `GET` | `/alerts` | Retrieve recently ingested alerts |

### Correlation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/correlation/groups` | List all correlated fraud-event groups |
| `GET` | `/correlation/groups/{group_id}` | Get a specific correlation group |

### Actions & Audit

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/actions` | List prioritized action list |
| `GET` | `/audit` | List audit records (optional: `?entity_id=...` filter) |

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |

All endpoints are also available under the `/api` prefix (e.g., `/api/alerts/source-a`).

Swagger UI: `http://127.0.0.1:8000/docs`

---

## Quick Start

### Development Server

```bash
uvicorn app.main:app --reload
```

### Run Tests

```bash
pytest -v
```

### Docker

```bash
docker compose up --build
```

Then verify: `http://localhost:8000/health`

### Demo Flow (Swagger UI)

1. Open `http://localhost:8000/docs`
2. POST to `/pipeline/source-a` with an alert payload
3. See the full pipeline output: alert → correlation → risk result → action
4. Check `/actions` for the prioritized action list
5. Check `/audit` for the decision trail

### Example Pipeline Request

```json
POST /pipeline/source-a

{
  "external_alert_id": "A-1001",
  "account_id": "C-101",
  "transaction_id": "TX-5001",
  "event_timestamp": "2026-08-23T09:10:00Z",
  "amount": 15000,
  "location": "Chennai",
  "risk_level": "HIGH"
}
```

---

## Project Structure

```
ThreatFlow/
├── app/
│   ├── api/routes/
│   │   ├── alerts.py          # Ingestion endpoints
│   │   ├── correlation.py     # Correlation group queries
│   │   ├── actions.py         # Prioritized action list
│   │   ├── audit.py           # Audit trail queries
│   │   ├── health.py          # Health check
│   │   └── pipeline.py        # End-to-end pipeline endpoint
│   ├── models/
│   │   ├── alert.py           # Alert ORM model
│   │   ├── correlated_alert.py # CorrelatedAlert ORM model
│   │   └── audit.py           # AuditRecord ORM model
│   ├── rules/
│   │   ├── conditions.py      # Rule condition evaluator
│   │   ├── loader.py          # Ruleset loader
│   │   └── models.py          # Rule/Ruleset models
│   ├── schemas/
│   │   ├── alert.py           # AlertCreate, AlertResponse
│   │   ├── correlated_alert.py # CorrelatedAlertResponse, ConflictResult
│   │   ├── risk_result.py     # RiskResult, RuleEvaluationTrace
│   │   ├── action.py          # Action schema
│   │   ├── audit.py           # AuditRecord schema
│   │   └── pipeline.py        # PipelineResult schema
│   ├── services/
│   │   ├── ingestion_service.py    # Alert persistence
│   │   ├── correlation_service.py  # Correlation, dedup, conflict
│   │   ├── rule_engine.py          # Dynamic rule evaluator
│   │   ├── action_service.py       # Action generation
│   │   ├── audit_service.py        # Audit logging
│   │   ├── mock_generator.py       # Mock data generator
│   │   └── pipeline_service.py     # Pipeline orchestrator
│   ├── config.py              # App settings
│   ├── database.py            # SQLAlchemy setup
│   └── main.py                # FastAPI app
├── config/
│   └── rules.json             # Rule engine configuration
├── tests/
│   ├── test_correlation.py    # Correlation + conflict tests
│   ├── test_integration.py    # End-to-end integration tests
│   ├── test_rule_engine.py    # Rule engine tests
│   ├── test_ingestion.py      # Ingestion tests
│   ├── test_actions.py        # Action service tests
│   ├── test_actions_api.py    # Actions API tests
│   ├── test_audit.py          # Audit tests
│   ├── test_health.py         # Health endpoint test
│   └── test_pipeline.py       # Pipeline endpoint test
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .github/workflows/ci.yml  # CI/CD pipeline
├── requirements.txt
├── pytest.ini
└── README.md
```

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python |
| Framework | FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite |
| Testing | pytest |
| Container | Docker, Docker Compose |
| CI/CD | GitHub Actions |
| Rule Format | JSON (`config/rules.json`) |

---

## Testing

The project includes **120 tests** across all modules:

| Test Suite | Scenarios |
|------------|-----------|
| Correlation | Transaction ID matching, amount/date fallback, cross-source correlation, deduplication, merge window, out-of-order arrival, late alerts, conflict detection, majority-vote resolution, identity conflicts, determinism |
| Integration | Single alert, multi-source, exact duplicate, separate transactions, out-of-order, late inside/outside window, location conflict, fraud_type conflict, incomplete evidence, three-source event, determinism, output structure |
| Rule Engine | All operators (>, >=, <, <=, ==, !=, in, contains), AND/OR conditions, nested conditions, missing/null fields, risk score/level calculation, hot-reload, malformed config handling, determinism |
| Ingestion | Valid ingestion from all 3 sources, missing required fields, unknown source, invalid risk level, negative amount, incomplete evidence, timestamp handling, duplicate preservation, out-of-order preservation, mock replay |
| Actions | Priority calculation, action mapping, deterministic ordering, required fields |
| Audit | Record creation, multiple records, metadata serialization |
| Health | Health endpoint returns 200 |
| Pipeline | Three-source conflict scenario, correlation, risk scoring, action generation, audit trail verification |

---

## Project Status

| Component | Owner | Status |
|-----------|-------|--------|
| Ingestion | Nithilan | Complete |
| Correlation & Deduplication | Srinivas | Complete |
| Conflict Resolution | Srinivas | Complete |
| Rule Engine | Payal | Complete |
| Risk Scoring | Payal | Complete |
| Actions | Saad | Complete |
| Audit | Saad | Complete |
| Docker | Shivika | Complete |
| CI/CD | Shivika | Complete |
| Pipeline Integration | Team | Complete |

