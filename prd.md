# ThreatFlow

# ThreatFlow

Title:
ThreatFlow

Background:
A financial institution's fraud detection system receives alerts from multiple third-party risk feeds, but these alerts often conflict or arrive out of order. Manual triage takes hours, delaying response and increasing false positives.

Problem Statement:
Your team must build a real-time fraud alert reconciliation engine that ingests asynchronous, conflicting alerts from multiple sources, applies dynamic risk rules, and outputs a prioritized, conflict-resolved action list. The system must handle out-of-order data and incomplete evidence while ensuring no alert is missed or duplicated. The 6-hour build window forces aggressive prioritization of core logic over polish.

Scope:
Build a system that ingests fraud alerts from multiple sources, resolves conflicts using dynamic rules, and outputs a prioritized list of actions. The system must support rule evaluation under incomplete evidence and handle out-of-order data arrival.

MVP Scope:
• Ingest 3 streams of fraud alerts (mocked) via REST or Kafka • Apply conflict resolution logic to merge overlapping alerts • Evaluate risk score using dynamic rules (e.g., 'if amount > 10k AND location mismatch') • Output a prioritized list of actions • Deploy via Docker and CI/CD pipeline

Advanced/Bonus Scope:
• Add explainability: show why each alert was merged or dropped • Support rule versioning and rollback • Integrate with a mock fraud response API

Functional Requirements:
- Receive asynchronous fraud alerts from multiple sources
- Detect and resolve conflicts between overlapping alerts
- Evaluate risk scores using configurable rules
- Prioritize alerts based on risk and urgency
- Output a conflict-resolved action list
- Support rule updates without downtime
- Log all decisions for auditability

Non-Functional Requirements:
- Handle 100 alerts per minute with <1s latency
- Ensure no alert is dropped during out-of-order arrival
- Support rule changes without re-deploying the system
- Provide explainability for each merged alert
- Deployable via CI/CD in under 15 minutes

Constraints:
- Must be shippable in 6 hours
- No external API keys or real-time data sources
- All data must be mocked or simulated
- Must include conflict resolution logic
- Must support out-of-order data arrival
- No third-party ML services allowed
- Build using only these skills: backend, ai-ml, devops.

Deliverables:
- Running Docker container with the system
- Demo video showing alert ingestion and output
- CI/CD pipeline showing deployment
- Documentation of rules and conflict logic
