# ThreatFlow

Real-time fraud alert reconciliation engine. ThreatFlow ingests asynchronous, conflicting fraud alerts from multiple third-party sources, correlates them into a single reconciled event, scores risk using dynamic (configuration-driven) rules, and produces a prioritized, explainable action list — with every decision logged for audit.

Built for the myOnsite Ascend Hackathon 2026 — Round 2, under a 6-hour build constraint: mocked data only, no external APIs, no third-party ML services, backend/ai-ml/devops skills only.

## Pipeline

```
 3 MOCKED SOURCES
       |
  Source A/B/C
       |
       v
  INGESTION  ────────────────────────────────  ✅ done
       |
       v
  CORRELATION (dedup, out-of-order, merge,
  conflict resolution)  ─────────────────────  ⏳ in progress
       |
       v
  RECONCILED EVENT
       |
       v
  RULE ENGINE (dynamic rules, risk score,
  explanation trace)  ───────────────────────  ✅ done (module only — not yet wired to an endpoint)
       |
       v
  RISK RESULT
       |
       v
  ACTION ENGINE (priority, recommended
  action)  ───────────────────────────────────  ✅ done (module only — not yet wired to an endpoint)
       |
       v
  PRIORITIZED ACTIONS  (GET /actions)
       +
  SQLITE AUDIT LOG  ──────────────────────────  ✅ storage built, ⏳ not yet written to automatically
```

**Current state in one line:** every stage works in isolation and is fully tested, but nothing yet calls correlation → rule engine → action engine → audit automatically in sequence. See [Known gaps](#known-gaps--whats-left) below — that end-to-end wiring is the main remaining work.

## Team & ownership

| Member | Role | Status |
|---|---|---|
| Nithilan | Ingestion — APIs, schemas, mock alerts/replay | ✅ merged to `main` |
| Payal | Rule Engine + Risk — dynamic rules, scoring, explainability | ✅ merged to `main` |
| Saad | Output + Audit — actions, priority, SQLite audit | ✅ merged to `main` |
| Srinivas | Correlation — dedup, correlation, out-of-order, conflicts | ⏳ in progress (`feature/correlation-srinivas`) |
| Shivika | DevOps + Integration — Docker, CI/CD, final integration | ⏳ pending correlation merge |

## Project layout

```
app/
├── main.py                       # FastAPI app, router wiring
├── config.py                     # Settings (env-driven)
├── database.py                   # SQLAlchemy engine, session, Base
├── models/                       # ORM models: Alert, AuditRecord
├── schemas/                      # Pydantic contracts: Alert, Action, AuditRecord, RiskResult
├── rules/                        # Rule DSL: Condition/Rule/RuleSet models, operators, evaluator, loader
├── services/
│   ├── ingestion_service.py      # Alert persistence
│   ├── rule_engine.py            # RuleEngine: event -> RiskResult
│   ├── action_service.py         # RiskResult -> Action, prioritization
│   ├── audit_service.py          # SQLite-backed audit record writes/reads
│   └── mock_generator.py         # Mock/replay alert scenarios
└── api/routes/                   # health.py, alerts.py, actions.py
config/
└── rules.json                    # Demo rule configuration (hot-reloadable)
tests/
```

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

Swagger UI: `http://127.0.0.1:8000/docs` — since there is no frontend (out of scope per constraints), this is the primary way to demo the API: POST alerts, inspect the resulting JSON.

> **Local dev gotcha:** if you have an old `threatflow.db` file from before the schema settled, ingestion will fail with `no such column`. `Base.metadata.create_all()` only creates missing tables, it never migrates existing ones. Delete `threatflow.db` and restart if you hit this (it's git-ignored, so a fresh clone never has this problem).

## Testing

```bash
pytest -v
```

64 tests currently pass across ingestion, rule engine, actions, and audit.

## API reference

### Health
- `GET /health` (or `/api/health`)

### Ingestion
- `POST /alerts/source-a`, `/alerts/source-b`, `/alerts/source-c` — per-source ingestion
- `POST /alerts` — generic single-alert ingestion (source read from payload)
- `POST /alerts/batch` — ingest multiple alerts, no dedup/sorting
- `POST /alerts/replay` — replay a mock scenario: `{"scenario": "conflict"}` (`default`, `normal`, `duplicate`, `conflict`, `incomplete`, `out_of_order`)
- `GET /alerts` — recently ingested raw alerts in strict arrival order

Ingestion validates structure/source/risk_level, preserves `event_timestamp` as received, stamps `received_at`, allows incomplete evidence, and preserves all duplicates/conflicts as separate rows. It deliberately does **not** deduplicate, correlate, merge, score, or re-sort — that's the correlation and rule-engine layers' job.

### Actions
- `GET /actions` — prioritized action list, sorted by priority/risk descending. **Currently always returns `[]`** until something calls the rule engine + action service for an ingested event (see [Known gaps](#known-gaps--whats-left)).

### Rule Engine (module — not yet exposed via HTTP)

`app.services.rule_engine.RuleEngine` is a deterministic, configuration-driven scorer: `evaluate_event(event) -> RiskResult`. It accepts a dict, a Pydantic model, or any plain object — deliberately decoupled from a specific `ReconciledEvent` class so it doesn't depend on correlation's still-unmerged schema.

**Rule DSL** (`config/rules.json`):
```json
{
  "id": "HIGH_AMOUNT",
  "version": 1,
  "score": 40,
  "condition": { "field": "amount", "op": ">", "value": 10000 }
}
```
Conditions nest via `AND`/`OR`:
```json
{ "AND": [
    { "field": "amount", "op": ">", "value": 10000 },
    { "field": "location_mismatch", "op": "==", "value": true }
] }
```

Supported operators: `>`, `>=`, `<`, `<=`, `==`, `!=`, `IN`, `CONTAINS`. No `eval()`, no arbitrary code execution — every operator is an explicit whitelisted function.

A missing or `null` field never crashes evaluation — the condition is recorded as not matched, with the field name captured in `missing_fields` on the trace, distinct from a real failed comparison.

All matching rules contribute their `score`; the risk level is resolved from configurable bands (default: `0-29 LOW`, `30-59 MEDIUM`, `60-79 HIGH`, `80+ CRITICAL`). Rules hot-reload from disk on file-modification-time change — no redeploy needed — and a malformed edit is caught and logged (`last_reload_error`) without ever taking down evaluation; the previous valid ruleset stays active.

`RiskResult` — the output contract consumed by the action layer:
```python
RiskResult(event_id, risk_score, risk_level, matched_rules, evaluation_trace, rule_version)
```
Each trace entry: `rule_id, version, matched, score, reason, missing_fields`.

### Audit (module — not yet exposed via HTTP)

`app.services.audit_service` persists structured `AuditRecord`s (`entity_id, event_type, outcome, reason, metadata`) to SQLite. Built and tested, but nothing calls `create_audit_record()` during the live request flow yet, and there is no `GET /audit` endpoint to view records.

## Known gaps / what's left

1. **Correlation isn't merged yet** — `feature/correlation-srinivas` is blocked on a schema conflict with the ingestion `Alert` model (primary key naming: `id` vs `alert_id`, plus metadata field layout). This is the critical-path blocker.
2. **No orchestration wiring** — nothing currently calls correlation → `RuleEngine.evaluate_event()` → `action_service.create_action()` → `audit_service.create_audit_record()` in sequence. Each stage is independently built and tested but not glued together. This is next up once correlation lands.
3. **No `GET /audit` endpoint** — audit records can be written and queried at the service layer, but aren't exposed over HTTP yet.
4. **Actions are stored in-memory**, not persisted — acceptable for a live demo session, but they won't survive a server restart.
5. Docker/CI (`feature/ingestion-devops`) is also unmerged and stale; it should be rebased and merged last, after correlation and the pipeline wiring are in, so it validates the complete app.
