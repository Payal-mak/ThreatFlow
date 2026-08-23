# ThreatFlow

Real-time fraud alert reconciliation engine.

ThreatFlow ingests asynchronous, conflicting fraud alerts from multiple sources, correlates them into a single reconciled event (handling duplicates, out-of-order arrival, and conflicting fields), scores risk using dynamic, configuration-driven rules, and produces a prioritized, explainable action list — with every decision logged for audit.

Built for the myOnsite Ascend Hackathon 2026 — Round 2, under a 6-hour build constraint: mocked data only, no external APIs, no third-party ML services.

## How it works

```
Source A ─┐
Source B ─┼──▶ INGESTION ──▶ CORRELATION ──▶ RULE ENGINE ──▶ ACTION
Source C ─┘    (per-source    (dedup, merge,   (dynamic,      (priority +
                REST APIs)     conflicts)       scored rules)  recommendation)
                                                                     │
                                                                     ▼
                                                    PRIORITIZED ACTIONS + AUDIT LOG
```

- **Ingestion** — validates and timestamps each alert from its source, preserving all raw evidence.
- **Correlation** — groups alerts belonging to the same underlying event (by account/transaction, within a merge window so out-of-order alerts still attach correctly), detects and resolves conflicting fields.
- **Rule engine** — a deterministic, no-`eval()` rule DSL (`config/rules.json`): conditions on event fields combined with AND/OR, scored and mapped to a risk level. Rules hot-reload from disk — no redeploy needed to change scoring logic.
- **Action + audit** — turns each risk result into a prioritized, recommended action, and logs every pipeline stage to a SQLite audit trail.

## Setup

```bash
git clone https://github.com/Payal-mak/ThreatFlow.git
cd ThreatFlow

python -m venv .venv
.venv\Scripts\activate            # PowerShell; use .venv/Scripts/activate on Git Bash

pip install -r requirements.txt
```

## Running

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` for the interactive Swagger UI — this is the primary way to use the API: POST alerts, see the pipeline's response.

## Try it

`POST /pipeline/{source}` runs one alert through the full pipeline (ingest → correlate → score → act → audit) and returns the result. Post the same transaction from a couple of sources with different details to see correlation and conflict resolution kick in:

```bash
curl -X POST http://127.0.0.1:8000/pipeline/source-a \
  -H "Content-Type: application/json" \
  -d '{"external_alert_id":"A001","transaction_id":"TX1001","account_id":"ACC55","amount":15000,"location":"Ahmedabad","risk_level":"MEDIUM","event_timestamp":"2026-08-23T10:31:01Z"}'
```

Then check the results:

```bash
curl http://127.0.0.1:8000/actions
curl "http://127.0.0.1:8000/audit?entity_id=<group_id from the response above>"
```

## Testing

```bash
pytest -v
```

## Tech stack

Python 3.11, FastAPI, Pydantic, SQLAlchemy + SQLite, pytest, Docker, GitHub Actions CI/CD.
