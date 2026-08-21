# Genie
## Enhance Spec — v1.1.0 (post-v1.0.0 build-out)

Version: 1.1.0 (design/ideation phase — no code shipped against this spec yet)

Status: Draft, approved for design; implementation not yet started (git `dev` branch reserved for this work).

---

# Purpose

`GENIE_BUILD_SPEC.md` describes the v1.0 platform: ingest → discovery → architecture → agent/UI design →
governance → deploy & launch, with everything held in memory for the lifetime of a container.

This spec captures the next body of work (v1.1.0): making missions durable, giving generated prototypes a
real data layer, and turning "modify the prototype" into an ongoing conversation instead of a one-shot
regeneration. It is written before any implementation so the design can be reviewed and revised cheaply.

Four features, in delivery order:

1. Move core platform state from in-memory repositories to Cosmos DB.
2. Persist generated prototypes and allow interactive modify/redeploy ("Enhance").
3. Give generated mission agents a real, governed data layer (SQL or Cosmos, chosen per mission).
4. Let users upload real data or generate synthetic data for testing mission agents.

---

# Feature 1 — Cosmos DB persistence for platform state

## Problem

All of `backend/app/repositories/` (`session_repository.py`, `governance_event_repository.py`,
`personal_memory_repository.py`, `shared_memory_repository.py`, `enterprise_memory_repository.py`,
`approval_repository.py`, `recommendation_lineage_repository.py`, `upload_repository.py`,
`workflow_run_repository.py`) are `InMemoryXRepository` implementations today. Every container restart or
redeploy wipes sessions, governance trace, memory tiers, and pipeline run history. This was the direct cause
of the Requirement Fidelity Gate investigation being unable to look up historical failure detail — that data
never existed anywhere but process memory and a live SSE stream.

## Design

- **One shared Cosmos DB account** under Genie's own backend (not a per-mission account) — single place to
  operate, monitor, and secure. **Serverless throughput mode** (pay-per-request) — platform traffic is
  variable/low-volume per mission, so serverless avoids provisioned-capacity planning and idle cost.
- **Every container in this account emits governance events on both read and write** (not just
  `shared_memory`/`enterprise_memory` as originally implied by the v1.0 memory-tier rules) — full audit
  trail across all platform state, consistent with the platform's governance-first principle.
- Containers, each partitioned by `sessionId` or `missionId`:
  - `sessions`
  - `governance_events`
  - `personal_memory`
  - `shared_memory`
  - `enterprise_memory`
  - `approvals`
  - `recommendation_lineage`
  - `uploads`
  - `deployment_pipeline_runs` (new — persists `DeploymentPipelineRun` + `RequirementFidelityReport`, so
    fidelity results and pipeline step failures survive restarts and can be queried later)
- Relationships between records (mission → requirements → components → data entities, etc.) are stored as
  plain reference-id fields on the documents, **not** as a graph model. A **GraphQL query layer** sits in
  front of the Cosmos SQL API to resolve those references at query time — one Cosmos API type to operate,
  GraphQL absorbs the "join" complexity instead of the storage engine.
- Existing `InMemoryXRepository` classes are replaced by `CosmosXRepository` implementations behind the
  same repository interfaces already used by services — no service-layer changes beyond DI wiring.
- Config-driven per the repo's no-hardcoding rule: Cosmos account endpoint, database name, and per-container
  names come from configuration, never literals in code.

## Open follow-ups for implementation phase

- Decide GraphQL implementation (in-process resolver in FastAPI vs. a dedicated schema module) — not yet
  chosen, deferred to Phase 11 design.
- Data migration/backfill strategy for any existing in-memory-only state is not applicable (nothing durable
  exists today to migrate).
- Governance-event volume from auditing every read (not just writes) should be measured once Phase 11 lands;
  if read-event volume becomes a cost/noise problem, revisit the "all containers, full audit" decision above
  rather than silently narrowing scope.

---

# Feature 2 — Persisted prototypes + interactive "Enhance"

## Problem

`backend/app/deploy_launch/pipeline_service.py` (`DeploymentPipelineService`) plus
`backend_deployment_service.py`, `container_app_frontend_deployment_service.py`, `test_execution_service.py`,
and `security_scan_service.py` materialize a build to a temp directory, deploy it, and keep no durable
record of source or version history once the run completes. There is no way today to look at a mission's
prototype later, understand what changed between builds, or ask Genie to modify what was already deployed.

## Design

- Persist prototype source + version history in the new `mission_prototypes` Cosmos container (Feature 1),
  keyed by mission id, with one document per build revision (source refs, image tag, deploy timestamp,
  fidelity report id).
- **Prototype view actions**: **Launch**, **Enhance**, **Delete**.
  - **Launch** — existing behavior, mints the customer-facing link.
  - **Enhance** — opens a **Prototype Workshop** chat session: a real multi-turn conversation where the
    user asks Genie to mature the prototype, rather than a single free-form change-request box. Changes
    **batch across turns** — the conversation accumulates proposed changes, and nothing redeploys until the
    user takes an explicit **Redeploy** action. This keeps pipeline-run volume/cost bounded and gives the
    user control over when a new version actually goes live, at the cost of the prototype being stale
    relative to the conversation until Redeploy is clicked (the Workshop UI must make this staleness
    visually obvious, e.g. a "N pending changes — Redeploy to apply" banner). Since enhancement requests are
    open-ended (not necessarily tied to an existing REQ id), each batch of changes applied via Redeploy mints
    **new `REQ-###`** entries appended to the mission's requirement baseline, so the Requirement Fidelity
    Gate keeps computing coverage against a real, growing baseline instead of going stale. Every Redeploy
    still runs the full pipeline chain (materialize → deploy → execute-test-suite / Fidelity Gate →
    security-scan → launch) — conversational does not mean ungated.
  - **Delete** — full teardown: the mission's Container Apps (backend + frontend), the dedicated SQL
    DB/Cosmos container provisioned for it under Feature 3, and associated ACR image tags, plus the
    corresponding Cosmos records (`mission_prototypes`, `mission_data_schema`, etc.). Delete is the primary
    cost-control mechanism (no separate automated budget/quota cap was requested) — implementation must
    verify teardown is complete, since a partial teardown leaving orphaned billed resources is the failure
    mode to guard against in testing.

## Genie's role in the Workshop: active reasoning, not a passive scribe

The Workshop is not a chat box that blindly transcribes user requests into a change list. Genie reasons
about the mission's current state and actively helps the user mature the prototype:

- **Reuses the existing multi-agent orchestration pattern**, not a single freeform chatbot. The Workshop is
  backed by the mission's orchestrator agent, which calls the same specialist agents used during the
  original build-out (architecture, requirements, security) via the existing `call_<specialist>_agent` tool
  pattern — an enhancement conversation gets the same collaborative reasoning the initial build did, not a
  lesser path.
- **Proactive analysis on open.** Before the user types anything, Genie analyzes the mission's current
  `RequirementFidelityReport`, governance trace, and architecture artifacts to surface a short list of
  concrete, evidence-backed suggestions (e.g. unmet/partially-met requirements, untested code paths, known
  security-scan findings, gaps versus the approved architecture) — the user starts from "here's what's worth
  maturing" rather than a blank prompt.
- **Reasons about impact before adding to the batch.** When the user proposes a change, Genie evaluates it
  against the approved requirement baseline, the Feature 3 data schema (if applicable), and prior governance
  decisions before queuing it — flagging conflicts, ambiguity, or downstream effects (e.g. "this changes a
  field the approved data schema depends on — it will need to go back through the data-layer approval
  checkpoint") instead of silently queuing whatever is typed. The user can still force a change through after
  seeing the flag; Genie advises, it does not block unilaterally outside of existing approval checkpoints.
- **Every suggestion and reasoning step is governance-traced** (decision lineage), same as the rest of the
  platform — a later reviewer can see not just what changed but why Genie suggested or flagged it.
- This reasoning happens up front and per-proposed-change, but does not change the batching decision above:
  suggestions and flags accumulate in the conversation same as user-authored changes, and nothing redeploys
  until the explicit Redeploy action.

## Version retention

- Only the **latest** version of a prototype is retained. Each Redeploy overwrites the mission's live
  `mission_prototypes` record and image tag in place rather than accumulating a version history — no
  version-history browsing UI is needed for v1.1.0. If a future need for rollback/history emerges, that is a
  separate, later feature — do not build speculative version-history storage now.

---

# Feature 3 — Real data layer for generated mission agents

## Problem

Generated mission agents currently have no durable, governed way to store or query mission-specific data;
anything they need has to be improvised in-process.

## Design

- The Data Requirements Analysis stage infers a schema artifact and a recommended provider (SQL vs. Cosmos
  vs. hybrid) automatically from the mission's approved requirements.
- New **approval checkpoint** (same pattern as the existing `build-review-approval` checkpoint): Genie
  renders the derived data structure (entities, fields, relationships, provider recommendation) and blocks
  any real Azure SQL/Cosmos provisioning until a human explicitly confirms it. Adding this checkpoint
  requires updating both `config/workflows/registry.yaml` (`requires_approval_checkpoint`) and
  `config/policies/approval_policy.yaml` (checkpoint id) together — the two files must move in lockstep or
  the pipeline raises `UnknownApprovalCheckpointError`.
- **Topology: dedicated per mission.** Each mission gets its own Azure SQL database or its own Cosmos
  container (whichever the confirmed provider is) — not a shared multi-tenant store. This keeps Feature 2's
  Delete teardown simple and unambiguous (drop the mission's dedicated resource, nothing else to scope
  around) at the cost of higher per-mission Azure spend than a shared store would carry.
- Mission data access is wired as a governed, traceable **tool call** for the mission's own orchestrator
  agent — mirroring the existing `call_<specialist>_agent` tool-registration pattern in
  `app/agents/tools/orchestration_tools.py` — using the official **Azure MCP Server** (Cosmos DB tools) or
  **Azure SQL MCP** tooling depending on the confirmed provider, rather than opaque in-process SQL/SDK
  calls. This keeps mission data reads/writes visible in the same governance trace as everything else.
  **Confirmed direction, pending a pre-Phase-13 availability/approval spike**: before Phase 13
  implementation starts, verify the Azure MCP Server / Azure SQL MCP tooling is actually available and
  approved for use in this environment (per the repo's "never invent external APIs" rule). If it turns out
  to be unavailable, fall back to direct Azure SDK calls wrapped in our own governance-logging layer
  (mirrors the same tool-call event shape, just without the MCP dependency) — do not silently substitute
  this fallback without flagging the change.

---

# Feature 4 — Real or synthetic test data for mission agents

## Problem

Once a mission agent has a real data layer (Feature 3), it needs data to operate against during testing and
demos — either the customer's own data or a synthetic stand-in when real data isn't available/appropriate.

## Design

- Deferred until Feature 3's data layer and approval checkpoint exist — depends on the confirmed schema and
  provisioned provider.
- Two paths surfaced to the user once the data layer is provisioned: upload real data (validated against the
  confirmed schema) or request Genie generate synthetic data matching the schema.
- **Real customer data is masked/anonymized before mission agents can query it.** Uploaded data is validated
  against the confirmed schema, then passed through a PII masking/anonymization step (field-level, driven by
  the schema's declared field types — e.g. names, emails, phone numbers, addresses) before it is written to
  the mission's dedicated data store. Mission agents only ever see the masked data, never the raw upload.
  The raw upload is retained only as long as needed to perform masking, then discarded — it is not persisted
  alongside the masked copy.
- Detailed design (exact masking rules per field type, synthetic data generation approach, volume limits) is
  deferred to its own implementation phase once Features 1–3 are in place, but the "mask before mission
  agents can query it" requirement above is locked and must not be silently dropped during that design.

---

# Delivery sequencing

Continuing the existing phase numbering from `GENIE_BUILD_SPEC.md` (Phases 1–10 = v1.0.0):

- **Phase 11** — Cosmos DB persistence (Feature 1): repository interfaces unchanged, `CosmosXRepository`
  implementations, config-driven wiring, `deployment_pipeline_runs` container added.
- **Phase 12** — Prototype persistence + Enhance workshop (Feature 2): `mission_prototypes` container,
  Launch/Enhance/Delete actions, Prototype Workshop chat UI, per-turn `REQ-###` minting, full teardown on
  Delete.
- **Phase 13** — Mission data layer (Feature 3): Data Requirements Analysis stage, new approval checkpoint,
  Azure MCP (Cosmos/SQL) tool registration for mission orchestrator agents.
- **Phase 14** — Test data (Feature 4): upload + synthetic data generation against the confirmed schema.

Each phase follows the existing repo convention: generate code only for the phase being worked, tests
required for every feature (backend unit/integration, frontend component + type-check, Playwright e2e where
applicable), README "Deploy log" entry per deploy, and CI/CD as the only deploy path (no manual
`az acr build` / `az containerapp update` unless CI/CD is genuinely unavailable).

**Merge cadence: one PR per phase.** Each of Phase 11, 12, 13, 14 is reviewed and merged from `dev` into
`master` independently as it completes (not held back for a single combined v1.1.0 release). Each merge to
`master` triggers the normal CI/CD deploy path and gets its own README "Deploy log" entry. `dev` stays the
working branch for whichever phase is next.

---

# Locked decisions (do not re-litigate without a new explicit conversation)

- Cosmos DB (Feature 1): serverless throughput, one shared account, governance events on read **and**
  write for every container.
- Enhance (Feature 2): changes batch across chat turns; nothing redeploys until the user clicks an explicit
  Redeploy action; only the latest prototype version is retained (no version history store). The Workshop
  uses active multi-agent reasoning (proactive suggestions on open, impact analysis per proposed change,
  governance-traced) — it is not a passive request-transcription chatbot.
- Mission data layer (Feature 3): dedicated SQL DB/Cosmos container per mission (not shared); Azure MCP
  Server is the intended tool-access path, pending a pre-Phase-13 availability/approval spike, with direct
  SDK-plus-governance-wrapper as the documented fallback if MCP tooling isn't actually available.
- Test data (Feature 4): uploaded real customer data is masked/anonymized before mission agents can query
  it; raw uploads are not retained past the masking step.
- Delivery: one PR per phase into `master`, not a single combined release.

---

# Open questions carried into implementation

- GraphQL resolver implementation choice for Feature 1 (in-process vs. dedicated module) — pure
  implementation detail, does not need a product decision.
- Exact Fidelity Gate semantics across an open-ended Enhance conversation (this spec commits to "new
  `REQ-###` per batch of changes applied via Redeploy"; validate this holds up once real conversations are
  tested).
- Synthetic data generation approach and exact PII-masking rules per field type for Feature 4 (not yet
  designed — the "mask before query" requirement itself is locked, the mechanics are not).
- Azure MCP Server / Azure SQL MCP availability and approval for this environment — must be confirmed before
  Phase 13 implementation begins.
