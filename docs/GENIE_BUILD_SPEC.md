# Genie
## Agentic Experience Center

Version: 1.0

---

# Executive Summary

Genie is a production-grade Azure-native Agentic AI solutioning platform.

The system ingests:

- Meeting transcripts
- Call recordings
- Audio
- Video
- Customer documents
- Supporting artifacts
- Customer context

Genie uses Azure AI Foundry hosted agents to transform customer inputs into an interactive AI Mission Control experience.

Users can observe:

- Requirement discovery
- Agent collaboration
- Memory updates
- Architecture creation
- Governance decisions
- Risk analysis
- Human approval checkpoints
- Final recommendations

This is not a static report generator.

The platform must operate as a real-time interactive customer workshop experience.

---

# Business Goals

Provide a professional customer-facing solutioning experience.

Enable transparent AI-driven discovery.

Provide governance and explainability.

Support executive-ready output generation.

Enforce production-grade security and compliance.

Provide traceability from customer input to recommendation.

---

# Architecture Principles

## Backend

- Python 3.12+
- FastAPI
- Async first
- Pydantic v2
- Dependency injection
- Clean Architecture

## Frontend

- React
- TypeScript
- Fluent UI

## Azure Services

- Azure AI Foundry
- Azure AI Search
- Azure Storage
- Azure Key Vault
- Azure Monitor
- Application Insights
- Microsoft Entra ID
- Azure Container Apps
- Azure App Service
- Cosmos DB or Azure SQL
- Azure AI Speech (optional)
- Azure Functions (optional)

---

# Production Requirements

Production mode must:

- Use Azure-hosted agents only
- Use Azure AI Foundry only
- Use AzureAgentGateway only

Production mode must never:

- Run local agents
- Run mock agents
- Return static outputs
- Return sample outputs
- Use local fallback execution
- Use synthetic production data

Production mode must fail closed.

---

# Repository Structure

backend/
frontend/
infra/
config/
tests/
docs/
scripts/
.github/
e2e/

See detailed repository layout below.

## Backend Structure

backend/
├── app/
│   ├── api/
│   ├── agents/
│   ├── architecture/
│   ├── debugging/
│   ├── governance/
│   ├── memory/
│   ├── models/
│   ├── orchestration/
│   ├── outputs/
│   ├── repositories/
│   ├── security/
│   ├── services/
│   ├── validation/
│   └── utils/
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
└── pyproject.toml

## Frontend Structure

frontend/
├── src/
│   ├── components/
│   ├── hooks/
│   ├── pages/
│   ├── services/
│   ├── styles/
│   └── types/
├── tests/
├── Dockerfile
└── package.json

---

# Core User Experience

## Landing Page

Features:

- Start new session
- Resume session
- Product overview

---

## Upload & Ingestion

Support:

- Transcript upload
- Audio upload
- Video upload
- Document upload

Display:

- Upload status
- Validation status
- Indexing status
- Transcription status

---

## Mission Control Dashboard

Display:

- Mission progress
- Business value score
- Risk score
- Readiness score
- Governance status
- Active agents

---

## Agent Arena

Each card displays:

- Avatar
- Agent name
- Status
- Progress
- Confidence score
- Current task
- Memory updates

Agent status values:

- Idle
- Analyzing
- Collaborating
- WaitingForApproval
- GeneratingOutput
- Completed
- Blocked
- Failed

---

## Collaboration Graph

Render:

- Agent nodes
- Communication edges
- Memory writes
- Dependencies
- Evidence

Use React Flow.

---

## Requirement Discovery Map

Display:

- Goals
- Functional requirements
- Non-functional requirements
- Risks
- Assumptions
- Constraints
- Opportunities

Users can:

- Approve
- Reject
- Edit
- Request evidence

---

## Architecture Studio

Display:

- Interactive Azure architecture

Each component displays:

- Rationale
- Security considerations
- Dependencies
- Cost notes
- Recommended by

Alternative design generation supported.

---

## Workshop Experience

User can:

- Chat with all agents
- Chat with individual agents
- Challenge recommendations
- Request alternatives
- Request re-analysis
- Modify requirements and re-trigger downstream agents

---

## Per-Requirement Agentic Flows & Launchable UIs

Genie does not produce a single monolithic workflow/UI for an entire
solution. Instead:

- Discovered requirements are grouped into logical requirement
  groups/epics (not one flow per atomic requirement, and not one flow
  for the whole solution).
- For each requirement group, Genie runs its own agentic flow
  (requirements refinement -> architecture -> solution design ->
  prototype generation) scoped to just that group.
- Each requirement group's flow produces its own generated, interactive
  UI, wired to the real backend agent services (not a static mockup).
- CX launches each requirement group's UI independently to interact
  with that specific agentic flow, test it, and provide feedback that
  re-triggers the agents for that group.
- The Mission Control Dashboard / Agent Arena must show these as
  distinct, separately trackable flows (own progress, own agent
  statuses, own governance trace) rather than a single merged view.

---

## Final Output Center

For each requirement group's completed flow, CX can download the
generated artifacts as a takeaway "startup kit" for their own
development, including:

- The generated workflow/agent configuration for that requirement group
- The generated prototype UI (HTML/CSS/JS or component source)
- Any generated backend integration code/contracts for that UI
- A manifest describing requirements, architecture decisions, and
  governance trace that produced the artifacts

Downloads must:

- Be scoped to a single session + requirement group (no cross-customer
  data leakage)
- Be authorized the same way session/workflow access is authorized
  today (no new implicit trust)
- Never include secrets, credentials, or internal configuration values
- Be packaged as a single downloadable archive (for example a zip)
