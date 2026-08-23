# ThreatFlow — Planning Log

---

## Q1: Problem definition, tech stack, and work division?

### 1. Problem Definition (restated plainly)

Build a **real-time fraud-alert reconciliation engine**:

- **Input:** 3 mocked, asynchronous alert streams (same fraud event can be reported by multiple sources, out of order, with conflicting/incomplete fields).
- **Core job:**
  1. Ingest alerts without dropping or duplicating any.
  2. Correlate/merge alerts that refer to the same underlying event, even if they arrive out of order or with partial evidence.
  3. Score risk using **dynamic, configurable rules** (e.g. `amount > 10k AND location mismatch`), not hardcoded logic, not ML APIs.
  4. Resolve conflicts between merged alerts (e.g. differing risk levels from different sources).
  5. Output a **prioritized, conflict-resolved action list**.
  6. Log every decision (for audit) and ideally explain *why* alerts were merged/dropped/scored.
- **Hard constraints:** 6-hour build, no external API keys, no third-party ML services, everything mocked, must containerize + have a CI/CD pipeline, only backend/ai-ml/devops skills allowed (**no frontend** — ship via REST/API, not a UI).
- **What's actually being judged:** correctness of conflict-resolution + out-of-order handling logic, not visual polish. That's where most time should go.

---

### 2. Recommended Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **Language/Framework** | Python 3.11 + **FastAPI** | Fastest to stand up REST ingestion endpoints, async-native (good for concurrent/out-of-order streams), free Swagger docs at `/docs` (doubles as your "UI" for the demo since no frontend allowed), Pydantic gives you alert schema validation for free. |
| **Ingestion transport** | REST endpoints (`POST /alerts/source-a`, etc.) + in-process **asyncio.Queue** | PRD says "REST or Kafka" — Kafka setup/debugging eats hours you don't have. REST + async queue gets ingestion done in ~30 min. Mention Kafka-compose as a bonus stub only if time remains. |
| **Rule engine** | Custom lightweight rule evaluator — rules as **JSON/YAML config**, evaluated by a small Python interpreter (dict-based condition tree: `field`, `op`, `value`, `AND/OR`) | Satisfies "no third-party ML services" + "dynamic rules" + "no redeploy needed" (reload the JSON/YAML file at runtime) + supports rule versioning bonus (just version the config files). |
| **Conflict resolution / correlation** | In-memory windowed store keyed by correlation ID (e.g. account+txn hash) with a **merge window** (e.g. 30–60s) to tolerate out-of-order arrival | Standard late-arrival handling pattern from stream processing — simple to implement without a real stream engine. |
| **Persistence (audit log + decisions)** | **SQLite** (via SQLAlchemy) | Zero-setup, file-based, meets the auditability NFR, avoids running/configuring Postgres under time pressure. Upgrade to Postgres only if the devops person has a docker-compose template ready to go. |
| **Explainability (bonus)** | Structured decision trace stored as JSON per alert (`matched_rules`, `merge_reason`, `dropped_reason`) exposed via `GET /alerts/{id}/explain` | Directly satisfies the bonus scope item, cheap to add once rule engine returns match traces. |
| **Testing** | `pytest` | Conflict-resolution and rule logic are the graded core — cover them with unit tests, not manual clicking. |
| **Containerization** | Docker + `docker-compose` (single service, or +SQLite volume) | Required deliverable. |
| **CI/CD** | GitHub Actions: lint → pytest → docker build → (simulated) deploy step | Meets the "<15 min pipeline" NFR and the CI/CD deliverable. |
| **Mock data generator** | Small Python script producing 3 skewed/conflicting/out-of-order JSON alert batches, replayed via `POST` calls with artificial delays | Needed to demo out-of-order + conflict handling convincingly. |

**Explicitly avoid:** any frontend framework (React/Vue/etc.) — not in scope of allowed skills; any real ML/LLM API calls — disallowed by constraints; Kafka/Redis as a first choice — too much setup risk for 6 hours.

---

### 3. Team Split (5 members)

| # | Role | Owns |
|---|---|---|
| 1 | **Ingestion & Data** (backend) | REST endpoints for 3 sources, Pydantic alert schema, mock/replay data generator with deliberate out-of-order + conflicting payloads |
| 2 | **Conflict Resolution Core** (backend) | Correlation-ID matching, merge window logic, dedup, conflict-resolution strategy (e.g. most-recent-wins / highest-severity-wins / weighted merge) |
| 3 | **Rule Engine & Risk Scoring** (ai-ml) | JSON/YAML rule DSL + evaluator, dynamic risk scoring, rule hot-reload, rule versioning, explainability trace |
| 4 | **Prioritization, Output & Audit Log** (backend) | Priority sorting of the action list, `GET /actions` endpoint, SQLite decision/audit logging, `/explain` endpoint |
| 5 | **DevOps & Integration** (devops) | Dockerfile, docker-compose, GitHub Actions CI/CD, wiring all modules together, README/rules documentation, demo video recording |

Person 5 should start Docker/CI scaffolding **immediately in parallel**, not at the end — integrate incrementally as others' modules land.

---

### 4. Phases (6-hour window, ending 2:00 PM)

Work backward from 2:00 PM to get your real clock times; durations below are relative.

| Phase | Duration | Focus | Who |
|---|---|---|---|
| **0. Setup & Design** | 0:00–0:30 (30 min) | Repo/skeleton, agree on Alert schema + correlation-ID strategy + rule DSL format, split branches | Everyone |
| **1. Core Build** | 0:30–2:30 (2 hr) | Ingestion endpoints + mock generator (P1), conflict-resolution/merge logic (P2), rule engine + scoring (P3) — built in parallel against the agreed schema | P1, P2, P3 |
| **2. Prioritization & Audit** | 2:30–3:30 (1 hr) | Action-list generation/sorting, audit logging, explainability endpoint | P4 (+ P3 support) |
| **3. Integration** | 3:30–4:30 (1 hr) | Wire ingestion → conflict resolution → rule engine → prioritization → output into one running app; fix interface mismatches | P5 leads, everyone plugs in |
| **4. Testing & Edge Cases** | 4:30–5:15 (45 min) | pytest for conflict resolution + rule engine (the graded core), test out-of-order arrival, incomplete evidence, dedup correctness | P1–P4 |
| **5. Dockerize + CI/CD** | Runs *throughout*, finalized 5:15–5:40 (25 min) | Docker build validated, GitHub Actions green, pipeline demo-ready | P5 |
| **6. Docs + Demo Video** | 5:40–6:00 (20 min) | README (rules + conflict logic docs — required deliverable), record demo video showing ingestion → output | Everyone (P5 records) |

**Cut list if time runs short (in order):** rule versioning/rollback → mock fraud response API integration → explainability endpoint → polish docs. Never cut: conflict resolution correctness, out-of-order handling, Docker build, CI pipeline — these are explicit MVP + deliverable requirements.

---
