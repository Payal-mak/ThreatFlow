# ThreatFlow — Ingestion Module (Nithilan)

ThreatFlow is a real-time fraud alert reconciliation engine. This repository contains the **Ingestion Module** (Nithilan's responsibility), designed to ingest, validate, normalize, timestamp, and persist raw fraud alert streams from multiple third-party sources.

---

## INGESTION → CORRELATION CONTRACT

The ingestion layer produces normalized `Alert` objects for Srinivas's Correlation layer.

### Alert Contract Schema

```json
{
  "alert_id": "A-1001",
  "external_alert_id": "A-1001",
  "source": "SOURCE_A",
  "transaction_id": "TX-5001",
  "account_id": "C-101",
  "event_timestamp": "2026-08-23T09:10:00Z",
  "received_timestamp": "2026-08-23T09:10:03.123456Z",
  "received_at": "2026-08-23T09:10:03.123456Z",
  "amount": 15000.0,
  "merchant": "Amazon",
  "location": "Chennai",
  "device_id": "DEV-22",
  "risk_level": "HIGH",
  "fraud_type": "POS_FRAUD",
  "metadata": {"feed_id": "feed-a-v1"}
}
```

### Contract Rules & Guarantees

1. **Ingestion DOES:**
   - Validate input structure and source names (`SOURCE_A`, `SOURCE_B`, `SOURCE_C`).
   - Validate optional `risk_level` (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).
   - Validate non-negative `amount` if present (`amount >= 0`).
   - Preserve `event_timestamp` exact value as received from the source.
   - Generate `received_timestamp` (`received_at`) at ingestion time (UTC).
   - Support incomplete evidence (`transaction_id`, `amount`, `location`, `device_id`, `risk_level`, `merchant` can be `null`).
   - Preserve arrival order strictly.
   - Store all duplicate and conflicting alerts.

2. **Ingestion DOES NOT:**
   - Deduplicate alerts.
   - Correlate alerts.
   - Merge alerts.
   - Resolve location/risk/amount conflicts.
   - Calculate risk scores or modify `risk_level`.
   - Re-sort alerts by `event_timestamp`.
   - Generate downstream actions or audit entries.

---

## API Endpoints

### 1. Source Specific Ingestion
- `POST /alerts/source-a` (or `/api/alerts/source-a`)
- `POST /alerts/source-b` (or `/api/alerts/source-b`)
- `POST /alerts/source-c` (or `/api/alerts/source-c`)

### 2. Generic Single Alert Ingestion
- `POST /alerts` (or `/api/alerts`)

### 3. Batch Alert Ingestion
- `POST /alerts/batch` (or `/api/alerts/batch`)

### 4. Mock Replay Generator
- `POST /alerts/replay` (or `/api/alerts/replay`)
  Accepts scenario payload: `{"scenario": "conflict"}` (scenarios: `default`, `normal`, `duplicate`, `conflict`, `incomplete`, `out_of_order`).

### 5. Ingested Alerts Retrieval
- `GET /alerts` (or `/api/alerts`)

### 6. Health Check
- `GET /health` (or `/api/health`)

---

## How to Run & Verify

### Run Development Server
```bash
uvicorn app.main:app --reload
```
or:
```bash
uvicorn ingestion.main:app --reload
```
Swagger UI will be available at: `http://127.0.0.1:8000/docs`

### Run Automated Tests
```bash
pytest tests/ -v
```

---

## Example Usage

### Single Alert Request (`POST /alerts/source-a`)
```json
{
  "alert_id": "A-1001",
  "account_id": "C-101",
  "transaction_id": "TX-5001",
  "event_timestamp": "2026-08-23T09:10:00Z",
  "amount": 15000,
  "location": "Chennai",
  "risk_level": "HIGH"
}
```

### Replay Scenario Request (`POST /alerts/replay`)
```json
{
  "scenario": "conflict"
}
```
