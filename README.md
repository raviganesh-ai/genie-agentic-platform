# Genie — Agentic Experience Center

Genie is an Azure-native Agentic AI solutioning platform. It ingests transcripts, recordings, documents, and customer context and transforms them into an interactive AI **Mission Control** experience — where requirement discovery, agent collaboration, architecture design, governance decisions, memory updates, approvals, and final outputs can all be observed **in real time**.

## Purpose and use boundary

> [!IMPORTANT]
> **MICROSOFT CONFIDENTIAL — INTERNAL COLLABORATION ONLY**
>
> Genie is an **art-of-the-possible prototype** for rapidly validating an agentic solution vision, architecture, interaction model, and requirement coverage. It is not a generally available Microsoft product, production reference implementation, or support commitment. **Do not deploy Genie, or a Genie-generated prototype, directly to production.** Before any production use, complete an independent architecture review, threat model, privacy and Responsible AI review, accessibility review, data-governance assessment, operational-readiness review, performance and resilience testing, and approval through the owning organization's standard engineering and release processes.

Genie is **not** a report generator. Every artifact it produces is backed by a traceable chain of agent execution, governance events, and approved memory rather than a static template.

---

## Table of contents

- [Purpose and use boundary](#purpose-and-use-boundary)
- [Architecture](#architecture)
  - [Genie Azure platform](#genie-azure-platform)
  - [Generated prototype isolation](#generated-prototype-isolation)
- [Core concepts](#core-concepts)
- [Agents](#agents)
- [Workflows](#workflows)
- [How a mission gets built](#how-a-mission-gets-built)
- [Deploy & Launch pipeline](#deploy--launch-pipeline)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [Configuration reference](#configuration-reference)
- [Authentication](#authentication)
- [Deployment strategy](#deployment-strategy)
  - [Deploying into a brand-new Azure subscription](#deploying-into-a-brand-new-azure-subscription)
  - [Provisioning Foundry agents](#provisioning-foundry-agents)
  - [Deploying application code (backend + frontend)](#deploying-application-code-backend--frontend)
  - [Continuous deployment (GitHub Actions)](#continuous-deployment-github-actions)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Deploy log](#deploy-log)
- [Known gaps / next phases](#known-gaps--next-phases)

---

## Architecture

Genie follows Clean Architecture in application code and uses Azure-native identity, hosting, data, AI, and observability services. The diagrams below separate the long-lived **Genie control plane** from the isolated Azure resources created for each generated prototype.

### Genie Azure platform

[![Genie Azure platform architecture showing Azure service boundaries, identity, runtime, data, AI, registry, and observability](docs/architecture/genie-azure-platform.svg)](docs/architecture/genie-azure-platform.svg)

*Figure 1. Genie evaluation platform. Select the diagram to open the scalable SVG. The service symbols come from the [official Microsoft Azure Architecture Icons](https://learn.microsoft.com/azure/architecture/icons/).*

| Azure concern | Implementation |
|---|---|
| **Web experience** | React/TypeScript on Azure Static Web Apps; no sign-in or bearer token is required |
| **API access boundary** | Public Standard v2 API Management is the only Internet-facing API endpoint; outbound VNet integration, private DNS, and a Container Apps environment private endpoint reach FastAPI on `8000` while environment public access remains disabled |
| **Agent execution** | `AzureAgentGateway` is the only production execution path to independently provisioned Azure AI Foundry Prompt Agents |
| **Identity and secrets** | User-assigned managed identity and least-privilege Azure RBAC; secrets belong in Key Vault and are never embedded in images or source |
| **Memory and artifacts** | Cosmos DB for durable session/memory/lineage state, Azure AI Search for enterprise knowledge, and Azure Storage for uploads and generated artifacts |
| **Images and hosting** | Commit-pinned FastAPI images in Azure Container Registry, deployed to Azure Container Apps |
| **Observability** | Application Insights and Azure Monitor receive structured logs, traces, metrics, correlation IDs, and governance telemetry |
| **Infrastructure** | Subscription-scoped Bicep creates the resource group, foundational Azure resources, and dedicated APIM subnet; GitHub Actions deploys the gateway/private endpoint and application revisions through Azure OIDC |

### Generated prototype isolation

Deploy & Launch creates a separate runtime boundary for every newly generated prototype. Generated prototypes do **not** use Microsoft Entra ID or inherit Genie's managed identity, Container Apps environment, resource group, network, or data ownership. Genie and generated prototypes require no sign-in.

[![Generated prototype Azure isolation architecture showing dedicated API Management, private Container Apps, identity, Foundry agents, registry, and monitoring](docs/architecture/generated-prototype-isolation.svg)](docs/architecture/generated-prototype-isolation.svg)

*Figure 2. Per-prototype security and runtime isolation. Select the diagram to open the scalable SVG.*

Each prototype receives a tagged `genie-proto-<mission-slug>` resource group, dedicated APIM service, VNet and private DNS, internal Container Apps environment, exact CORS origin, backend/frontend Container Apps, mission managed identity, RBAC assignments, and generated Foundry agents. The generated frontend and APIM endpoint are anonymous; APIM enforces exact-origin browser CORS, per-client rate limiting, and correlation IDs before forwarding over the private network. CORS is not authentication, so non-browser clients that know the APIM URL can call it. The durable Cosmos inventory records the shared `genie-internal-user` owner, mission metadata, URLs, resource group, TTL, and cleanup state. The fixed internal principal has `Genie.Admin`, so all callers share inventory and cleanup authority.

### Layering rules (enforced by tests)

- Azure SDK imports are confined to an explicit allow-list of Foundry, deployment, transcription, and generated-prototype infrastructure adapters. A standing test (`tests/unit/test_architecture_boundary.py`) scans the backend source tree and fails if an application/domain module crosses that boundary.
- Every Genie business/debugging **agent is an independently deployed Azure AI Foundry agent resource** (created via the Foundry portal, CLI, or the provisioning scripts in this repo) — Genie never implements agent reasoning as ad hoc Python classes, and never calls `create_agent()` at request time.
- The frontend **never** calls Azure AI Foundry directly — a static scan test (`no_foundry_direct_access.test.tsx`) fails the build if any non-`httpClient.ts` file performs a raw `fetch()` call or imports a Foundry SDK / hostname.
- Execution goes through `AzureAgentGateway` whenever Azure AI Foundry is configured. Local/mock agents, static demo data, and fallback execution are only permitted when `GENIE_ALLOW_LOCAL_AGENTS=true` (and no Foundry endpoint is configured) — otherwise the gateway fails closed instead of ever silently falling back.

### Three-tier memory architecture

| Tier | Purpose | Backend |
|---|---|---|
| **Personal Agent Memory** | Observations, work products, intermediate summaries — accessible only by the owning agent unless policy allows | In-memory (dev) / Cosmos DB (production) |
| **Shared Collaboration Memory** | Goals, constraints, assumptions, risks, findings, approved artifacts — every read/write emits a governance event | In-memory (dev) / Cosmos DB (production) |
| **Enterprise Knowledge Memory** | Industry patterns, reference architectures, reusable best practices — customer data is never promoted here without approval | Azure AI Search |

### Governance

Every agent execution, agent-to-agent handoff, memory read/write, tool invocation, policy check, and approval decision is recorded as a `GovernanceEvent`, giving full session replay and decision-lineage traceability. `GovernanceProvider` is a pluggable interface (`Agent365GovernanceProvider` marker for production, `LocalGovernanceTraceProvider` for local/dev only) — production startup fails closed if no real provider is injected.

**Triage Mode** (sidebar toggle, off by default) renders a full-height, right-docked panel that polls `GET /sessions/{id}/governance/events` and filters to `agent_execution` events. Because `WorkflowStepExecutor.execute_step()` records that event the instant an individual agent call resolves — not once a whole (possibly multi-step) workflow run/resume call returns — the panel reflects the orchestrator's real agent-call order and timing, including cases where several ungated steps execute back-to-back within one HTTP call. Each feed item shows a gamified XP/level readout plus a truncated preview of that agent's real output (`detail.output_preview`); there is no synthetic or simulated activity.

### Fail-closed startup

Before accepting any traffic, the backend runs 10 mandatory startup validators (`RuntimeVersion`, `Configuration`, `ProviderMode`, `ProductionSafety`, `AgentRegistry`, `WorkflowRegistry`, `PromptTemplate`, `MemoryPolicy`, `GovernanceProvider`, `NoHardcoding`), plus (in production mode only) Foundry-specific validators that verify every enabled agent's `foundry_agent_id` actually resolves in Azure AI Foundry and has no critical configuration drift. `/health/ready` only returns 200 once all validation has passed.

---

## Core concepts

- **Config-driven, never hardcoded.** Agent definitions, workflow definitions, prompt templates, endpoints, deployment names, subscription/tenant ids, customer data, secrets, and expected AI outputs are never hardcoded — they live under `config/agents`, `config/prompts`, `config/workflows`, `config/policies`, and environment variables.
- **Strongly typed everywhere.** Pydantic v2 models on the backend, TypeScript types mirroring every backend model on the frontend.
- **Async throughout** the backend service layer.
- **Dependency injection** — every service is constructed via a `create_X(...)` factory that takes its collaborators as explicit parameters, making every layer independently testable.

---

## Agents

Agents are **never** implemented as Python/TypeScript classes containing reasoning logic. Each agent is a real resource provisioned in Azure AI Foundry; Genie's `config/agents/registry.yaml` registry only records *which* Foundry resource backs each logical agent id, its capabilities, memory access, and prompt template reference. `AzureAgentGateway` resolves the prompt, opens a thread against the existing Foundry agent, posts the message, and reads the reply — it never creates agents at runtime.

### The real agent lineup

Genie's mission pipeline is deliberately lean — seven real Foundry agents, no inert "catalog" of unused agents:

| Agent id | Name | Role | Prompt template | Notes |
|---|---|---|---|---|
| `genie-orchestrator` | Genie Orchestrator | mission_orchestration | `orchestrator-mission-v1` | The **only** agent every workflow step actually addresses directly — see [delegation pattern](#the-orchestrator-delegation-pattern) below |
| `requirements-analyst` | Requirements Analyst | requirement_discovery | `requirements-extraction-v1` | Extracts goals/requirements/risks and rules on agentic-workflow qualification |
| `architecture-designer` | Architecture Designer | architecture_design | `architecture-recommendation-v1` | Designs the multi-agent workflow and shapes an [Impeccable](https://impeccable.style/)-style, domain-specific surface mode and visual direction for the single-page UI |
| `build-agent` | Build Agent | solution_build | `build-generation-v1` / `build-generation-component-v1` / `build-component-regeneration-v1` | Generates the real UI + specialist agent + orchestrator code one component at a time; every UI generation/regeneration path applies the Impeccable design contract |
| `security-assessment-agent` | Security Assessment Agent | security_assessment | `security-assessment-v1` | Independent OWASP Top 10 review of the generated build; reports `SECURITY_GATE: PASS/FAIL` |
| `test-generation-agent` | Test Generation Agent | test_generation | `test-generation-v1` | Independent test-coverage review of the generated build; reports `TEST_COVERAGE_GATE: PASS/FAIL` |
| `debugging-agent` | Debugging Agent | debugging | `failure-diagnosis-v1` | Diagnoses a detected workflow/agent execution failure |

### The orchestrator delegation pattern

Every `solution-discovery-workflow` step invokes `genie-orchestrator`, never the specialist directly. `genie-orchestrator` calls exactly one of its own registered Foundry function-calling tools (`call_requirements_analyst`, `call_architecture_designer`, `call_build_agent`, `call_security_assessment_agent`, `call_test_generation_agent` — implemented in `app/agents/tools/orchestration_tools.py`) to execute that phase's real specialist through the exact same `AzureAgentGateway` path as any other agent call, then returns that specialist's output **verbatim** as the step's own result. This exists because the pinned `azure-ai-projects` SDK version has no native "Connected Agents" mechanism for Prompt Agents — `connected_agent_ids` in the registry is realized via genuine per-phase function tools instead, not a Foundry-native feature. Each agent's own registry entry also declares `connected_agent_ids` purely for inventory/documentation, mirroring what `genie-orchestrator` is actually wired to call.

### Agent metadata fields

Every agent entry carries: `id`, `name`, `role`, `description`, `capabilities`, `allowed_tools`, `memory_access` (subset of `personal`/`shared`/`enterprise`), `foundry_agent_id` (the real provisioned Foundry resource id), `model_deployment_ref` (optional — informational only, defaults to `Settings.default_llm`), `owner` (accountable team, never a person), `governance_policy_id`, `prompt_template_ref`, `connected_agent_ids` (documentation only — see above), and `enabled`.

### Default LLM

`Settings.default_llm` (env `GENIE_DEFAULT_LLM`) applies to any agent that doesn't declare its own `model_deployment_ref`. Currently `gpt-5-mini` by default (`architecture-designer` overrides to `gpt-5-1`, demonstrating per-agent LLM configurability). Anthropic Claude models were evaluated but require an Azure Marketplace subscription with non-zero SKU quota, which is not available by default.

---

## Workflows

Workflows (`config/workflows/registry.yaml`) define ordered, dependency-graphed steps; `WorkflowRuntime` computes execution "waves" from the `depends_on` graph and runs same-wave steps in parallel via `asyncio.gather`. Genie's own Mission Control flow is deliberately just human-paced stages — each step has `requires_human_proceed: true`, so a run simply stops at `waiting_for_proceed` until a human explicitly resumes it; editing an earlier, already-persisted stage never cascades into automatically re-running a downstream one.

- **`solution-discovery-workflow`** — three steps, all addressed to `genie-orchestrator` (see [delegation pattern](#the-orchestrator-delegation-pattern) above):
  1. `analyze-requirements` (prompt `orchestrator-requirements-phase-v1`, tool `call_requirements_analyst`) — extract requirements from the session transcript.
  2. `design-architecture` (prompt `orchestrator-architecture-phase-v1`, tool `call_architecture_designer`, human-gated) — derive the multi-agent workflow + UI design from the approved requirements.
  3. `build-solution` (prompt `orchestrator-build-phase-v1`, tool `call_build_agent`, human-gated) — generate the real UI + agent + orchestrator code for the approved architecture. On a retried attempt, this step's own prior output is fed back in as `previous_build_output` so `call_build_agent` skips regenerating any component that already succeeded, instead of rebuilding the whole mission from scratch.

  Test generation and its real execution are deliberately **not** steps in this workflow — they happen later, inside the separate [Deploy & Launch pipeline](#deploy--launch-pipeline), against the mission's actually-deployed build.
- **`debugging-workflow`** — a single `diagnose-failure` step (agent `debugging-agent`, prompt `failure-diagnosis-v1`) triggered on a detected workflow/agent execution failure, executed through the exact same `AzureAgentGateway` path as every other workflow (never a bespoke/local diagnostic path).

### Agentic-workflow qualification check

The Requirement Discovery Map doesn't just list extracted requirements — it also surfaces whether they actually **qualify for a multi-agent agentic workflow** in the first place, versus being better served by a simpler, deterministic solution. This is deliberately **not** a Python business rule: the judgment is made by the existing `requirements-analyst` agent as part of its normal `analyze-requirements` step. Its prompt (`requirements-extraction-v1`) instructs it to end its output with two machine-parseable lines:

```
AGENTIC_WORKFLOW_QUALIFICATION: QUALIFIED | NOT_QUALIFIED
QUALIFICATION_REASON: <one or two plain-language sentences>
```

The backend (`RequirementsService.get_qualification`, `GET /sessions/{id}/requirements/{workflow_run_id}/qualification`) only *extracts* this verdict via a regex parser — it never invents or overrides the agent's own reasoning. If the requirements don't qualify, `RequirementDiscoveryPage` shows a graceful Fluent `MessageBar` banner explaining why, using the agent's own stated reason. The step id this check watches is configurable via `GENIE_REQUIREMENTS_QUALIFICATION_STEP_ID` (defaults to `analyze-requirements`), never hardcoded.

---

## How a mission gets built

This is the "how": the exact mechanism Genie uses to turn an uploaded transcript into a working, requirements-traceable UI + multi-agent backend — end to end, nothing hardcoded or templated.

| Stage | Governed outcome | Approval or gate |
|---|---|---|
| **1. Ingest** | Transcript, recording, or document enters the session scope | Upload validation |
| **2. Requirements** | Requirements Analyst classifies scope and creates the numbered critical path | Human approval |
| **3. Architecture** | Architecture Designer produces the multi-agent workflow and single-page UI design | Human approval |
| **4. Build** | Build Agent generates one traceable component at a time | Requirement-fidelity validation |
| **5. Assess and test** | Security Assessment and Test Generation run independently | `SECURITY_GATE` and `TEST_COVERAGE_GATE` |
| **6. Workshop** | Peer-review findings and selected fixes are reconciled | Human approval |
| **7. Deploy & Launch** | The deterministic pipeline provisions the isolated Azure prototype | At least 90% executable coverage and 100% of executable tests passing |

### 1. Requirements are pinned into a single scope contract

`requirements-analyst` (prompt `requirements-extraction-v1`) classifies every functional requirement as `MUST-HAVE` or `NICE-TO-HAVE` (erring toward MUST-HAVE when ambiguous — the goal is to capture full scope, never shrink it), then emits a numbered **"Critical path (must build first):"** list. This list is the one scope boundary every later stage is measured against. The same step also emits the `AGENTIC_WORKFLOW_QUALIFICATION` verdict described above.

### 2. Architecture Designer decides *what* to build — strictly bounded to that scope

`architecture-designer` (prompt `architecture-recommendation-v1`) reasons out two sections from the approved requirements alone:

- **`## Multi-Agent Workflow`** — exactly one Orchestrator Agent plus however many specialist agents the critical path genuinely needs (no "best practice" agents invented beyond what the requirements ask for).
- **`## Single-Page UI Design`** — only the mission-specific *input* zone(s) (1-3 zones), using a fixed, deterministic field→control mapping so every mission's UI stays consistent: ≤6 mutually exclusive choices → dropdown; multi-select → checkbox group; bounded number where position matters → slider; exact number → number input; toggle → checkbox; file/folder → drag-and-drop picker; date/time → picker; only genuinely open-ended values → text field. The surrounding shell always supplies the live Agent Pipeline panel and Mission Queue (progress, streamed narration, results, downloads) identically for every mission, so this design step never touches that — only the bespoke input surface.

The prompt is explicit: *"Stay strictly within the 'Critical path:' scope... treat [anything outside it] as future backlog, out of scope for this design."*

### 3. Build Agent turns that design into real code — one component at a time, with zero drift allowed

`call_build_agent` (`app/agents/tools/orchestration_tools.py`) invokes the Build Agent **once per component** (`build-generation-component-v1`), in a fixed bottom-up order: each specialist agent module (alphabetical), then the Orchestrator module, then the UI component. The prompt hard-constrains every component to the architecture's own list: *"Every zone and every agent you generate code for MUST come directly from that exact list below — never invent, add, rename, or merge."* The UI component must implement **only** the input zones named in step 2, using the same deterministic control-mapping rule, styled with Genie's own shell classes (`genie-card`, `genie-zone-title`, `genie-btn-primary`, `genie-dropzone`) and a fixed contract (`{ onSubmit: (message, attachments?) => void }`) — it never renders its own progress/output/agent-status UI, because the shell already does. On a retried build, `previous_build_output` lets the tool skip regenerating any component that already succeeded rather than rebuilding the whole mission from scratch. Users can also request a targeted change to a single already-generated component from Workshop Center (`WorkshopService.regenerate_component`, prompt `build-component-regeneration-v1`) without touching anything else.

Which requirement IDs belong to which component is never left purely to the Build Agent's own judgment. `architecture_parsing.parse_component_requirement_assignments` deterministically extracts the `REQ-###` ids already cited in each specialist/UI bullet's own text (the Architecture Designer prompt requires this), and `call_build_agent` (1) fails closed with a governed error **before generating any component** if an approved requirement is never assigned to any specialist, the Orchestrator, or a UI zone, and (2) passes each component only its own assigned requirement ids via the `{assigned_requirements}` prompt variable — cheaper and more reliable than discovering a coverage gap only after a full build+deploy+test cycle.

The generated orchestrator module has one more fail-closed contract: `code_materializer.materialize_build` rejects the parsed build with a `MaterializedCodeError` unless it defines the exact literal class `class OrchestratorAgent:` — the deterministic backend scaffold (`_MAIN_PY_TEMPLATE`) hardcodes `from orchestrator import OrchestratorAgent`, so a misnamed class would otherwise deploy a prototype whose backend never actually runs the generated business logic. This is enforced at materialization time (before any deploy), on top of the `build-generation-component-v1` prompt's own instruction to name the class correctly.

### 4. Two independent agents verify the build against the original requirements — never trusting the builder's own claims

- **Security Assessment Agent** (`security-assessment-v1`) reviews the entire generated artifact against the OWASP Top 10 every time (not just the diff) and is explicitly told to *"form your own independent judgment... do not defer to, assume the correctness of, or be swayed by"* the Build Agent's own claims. Ends with `SECURITY_GATE: PASS|FAIL` plus structured findings.
- **Test Generation Agent** (`test-generation-v1`) generates real, pytest-discoverable unit/integration tests and Vitest/RTL UI tests, and independently confirms *"every requirement's critical path have at least one covering test"* before reporting `TEST_COVERAGE_GATE: PASS|FAIL`.

Both marker-line verdicts are parsed verbatim (never invented or overridden) by `PeerReviewService` (`app/services/peer_review_service.py`) using the same "agents return marker lines, never JSON" convention used throughout Genie. Workshop Center's **Apply Selected Fixes** action regenerates the build with a customer-chosen subset of findings and forces both gates to re-run against the new artifact.

### 5. Human approval + full decision lineage at every hop

Every stage above (`design-architecture`, `build-solution`) is `requires_human_proceed: true` — nothing auto-advances, and revisiting an earlier stage never silently cascades into re-running a downstream one. `TraceabilityService` (`app/governance/traceability_service.py`) assembles a complete, customer-facing lineage per recommendation — which agent produced it, which evidence/memory it used, which approvals were granted, and every governance event on its trace id — so any generated screen or agent can be traced back to the exact requirement and approval that authorized it. This is what Genie's **Replay Center** and **Triage Mode** panels render live.

---

## Deploy & Launch pipeline

Deploy & Launch is deliberately **not** an LLM-narrative workflow step — it is a real, deterministic, code-driven Azure provisioning pipeline (`app/deploy_launch/pipeline_service.py`) that executes each named step in this exact order against real Azure SDKs — never fabricating a result for a step it didn't actually perform. Production startup fails when required deployment or prototype-APIM settings are absent; explicit Null collaborators exist only for local tests. It runs once a mission's `build-solution` step has been approved, and it deploys **one customer mission's generated build** (a separate concern from Genie's own infrastructure, which is provisioned once via `infra/main.bicep`).

| # | Step id | Customer-facing name | What actually happens |
|---|---|---|---|
| 1 | `generate-access-policy` | Generate Access Policy & Least Access | Derives a least-privilege access policy for the mission's generated agents |
| 2 | `provision-foundry-agents` | Deploy Agents to Foundry | Provisions each generated specialist + orchestrator agent as a real Azure AI Foundry resource |
| 3 | `deploy-backend-service` | Deploy Backend Service | Provisions a prototype-owned VNet, private DNS zone, internal Container Apps environment, and dedicated Standard v2 API Management service; builds FastAPI; and deploys it with external ingress disabled. APIM is the only public API endpoint and reaches FastAPI only over the prototype private network |
| 4 | `sync-frontend-integration` | Update Frontend Integrations | Wires the generated anonymous UI to the dedicated APIM endpoint; no Entra, MSAL, bearer-token, or acceptance-key runtime configuration is generated |
| 5 | `deploy-frontend-app` | Deploy Frontend | Builds the mission UI under Node 22, runs the pinned Apache-2.0 Impeccable `3.6.0` detector over generated TSX/CSS, fails closed on deterministic design anti-patterns, then deploys a mission-specific frontend Container App. APIM's deny-by-default bootstrap CORS origin is replaced with the returned exact HTTPS origin |
| 6 | `generate-test-suite` | Generate Requirement Acceptance Tests | Test Generation Agent writes real black-box tests against the deployed prototype's actual mission URLs (no mocks/patches), targeting every approved requirement id. `MISSION_BACKEND_URL` is the dedicated HTTPS APIM endpoint and generated code receives no authentication credential. A requirement-coverage repair loop retries omitted IDs up to `GENIE_DEPLOYMENT_FIDELITY_MAX_REPAIR_ATTEMPTS`; after that, executable coverage must meet `GENIE_DEPLOYMENT_FIDELITY_MIN_COVERAGE_PERCENT` (default 90%) and every omitted ID remains an explicit fidelity gap. For a real deployed backend, `validate_real_action_tests` also rejects test doubles in place of real HTTP calls |
| 7 | `execute-test-suite` | Requirement Fidelity Gate | Generated tests call the dedicated APIM endpoint directly without Entra or an acceptance key. Launch requires the configured executable-coverage threshold and 100% passing evidence for all executable requirement tests. Failed, errored, skipped, timed-out, or unobserved executable tests still trigger automatic regeneration and redeployment up to `GENIE_DEPLOYMENT_FIDELITY_MAX_REPAIR_ATTEMPTS` times (default 3), then fail closed |
| 8 | `run-security-scan` | Security Scan (Backend & Frontend) | Real security scan of the deployed backend and frontend artifacts |
| 9 | `launch-mission` | Launch | Mints the customer-facing launch link once every prior step has passed |

Each step's real status (`pending` → `running` → `completed`/`failed`/`skipped`) streams live to the Deploy & Launch page so the human watches actual provisioning happen — never a simulated progress bar. The **Requirement Fidelity Gate** (step 7) is Genie's last line of defense: it never trusts the Build Agent's or Test Generation Agent's own claims of completeness, it only trusts pytest actually passing against the real running prototype.

---

## Repository layout

```
backend/           FastAPI application (Python 3.12+)
  app/
    agents/        AgentRegistry, AzureAgentGateway, orchestration tools, Foundry provider/sync/lifecycle
    api/            19 routers (thin — auth + delegation only)
    architecture/   Architecture Studio services
    config/         Settings (pydantic-settings, env prefix GENIE_)
    debugging/      Debugging workflow trigger service
    deploy_launch/  Deploy & Launch pipeline (real, deterministic Azure provisioning
                    of one mission's generated build - see "Deploy & Launch pipeline")
    deployment/     Pre-infra deployment-readiness tooling (resource provider checks)
    governance/      Governance, lineage, decision graph, approval, replay, traceability services
    memory/         Personal / Shared / Enterprise memory services
    models/         Shared Pydantic models
    orchestration/  WorkflowRuntime, AgentOrchestrator, handoff/collaboration/reanalysis
    outputs/        Final Output Center services
    prompts/        PromptRegistry
    repositories/   Protocol + in-memory repository implementations
    security/       Fixed internal principal and authorization dependencies
    services/       Session / Requirements / Workshop / Architecture / Peer Review / Output /
                    Foundry agent provisioning & lifecycle services
    transcription/  Upload/recording transcription (speech-to-text) service
    utils/          Shared YAML loader
    validation/     10 fail-closed startup validators + runner
    workflows/      WorkflowRegistry
  tests/            Unit + integration tests (pytest)

frontend/           React + TypeScript Mission Control UI (Vite)
  src/
    app/            App bootstrap and router
    components/     Shared UI components
    features/       9 feature pages (landing, upload, requirement-map,
                     architecture-studio, workshop-center, deploy-launch,
                     replay-center, final-output-center, triage) - plus two
                     hub wrappers in layouts/ (RequirementsHubPage,
                     OutputsHubPage) that group related pages under one
                     linear nav step via sub-tabs
    hooks/          Data-fetching hooks per feature
    layouts/        AppShell and navigation
    services/        httpClient and one API client per backend router
    state/           SessionContext, trace id registry
    types/           TypeScript types mirroring backend Pydantic models
  tests/            Vitest component + static-scan tests

config/             Externalized configuration (never hardcoded in source)
  agents/           Agent registry + Foundry Supported Agents catalog
  prompts/          Prompt templates
  workflows/        Workflow registry
  policies/         memory_policy.yaml, governance_policy.yaml, approval_policy.yaml
  deployment/       resource_providers.yaml (Azure RP allow-list for deployment tooling)

infra/              Bicep infrastructure-as-code
  main.bicep                        Subscription-scoped entry point (creates its own RG)
  modules/foundational-resources.bicep   RG-scope aggregator
  modules/*.bicep                   One module per resource type

scripts/            Operator CLI scripts (deployment readiness, RBAC role generation,
                    Foundry agent provisioning/sync/validation/inventory export)

docs/GENIE_BUILD_SPEC.md   Full build specification
e2e/                       Playwright end-to-end tests (scaffolding)
.github/copilot-instructions.md   Architecture/coding rules enforced across the repo
```

---

## Local development

### Prerequisites

- Python 3.12+
- Node.js 20+ (LTS)
- An Azure subscription with an Azure AI Foundry project (for anything beyond `LocalAgentGateway` dev mode)

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -v          # run tests
.\.venv\Scripts\python.exe -m ruff check app tests  # lint
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload
```

> **Windows on ARM64 note:** do not install `uvicorn[standard]` — `httptools` has no `win_arm64` wheel. Use plain `uvicorn`. If `azure-identity`'s dependency resolver pulls a `cryptography` version with no `win_arm64` wheel, run `pip install cryptography --only-binary=:all:` first.

Copy `.env.example` (if present) or set the environment variables listed in [Configuration reference](#configuration-reference). With `GENIE_ALLOW_LOCAL_AGENTS=true` and no Foundry endpoint configured, the backend runs entirely against in-memory stores and `LocalAgentGateway` (no Azure required).

### Frontend

```powershell
cd frontend
npm install
npm run dev          # local dev server
npm run build         # tsc -b && vite build -> dist/
npm run test          # vitest
npm run typecheck
npm run lint
```

Set `frontend/.env.development` (or `.env.local`) with `VITE_GENIE_API_BASE_URL` pointing at your local backend. No authentication variables are required.

---

## Configuration reference

All backend configuration is via environment variables prefixed `GENIE_` (pydantic-settings, see `backend/app/config/settings.py`). Nothing here should ever be hardcoded into source — every value below is meant to be supplied per-deployment.

| Variable | Default | Purpose |
|---|---|---|
| `GENIE_SERVICE_NAME` | `genie-backend` | Service identity for logs/telemetry |
| `GENIE_ENVIRONMENT` | `development` | `development` \| `test` \| `production` |
| `GENIE_LOG_LEVEL` | `INFO` | Log verbosity |
| `GENIE_GOVERNANCE_PROVIDER` | `local` | `local` \| `agent365` — production requires a real provider |
| `GENIE_ALLOW_MOCK_AGENTS` | `true` | Must be `false` in production |
| `GENIE_ALLOW_LOCAL_AGENTS` | `true` | Must be `false` in production |
| `GENIE_USE_SYNTHETIC_DATA` | `true` | Must be `false` in production |
| `GENIE_AZURE_FOUNDRY_ENDPOINT` | *(none)* | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `GENIE_AZURE_FOUNDRY_PROJECT_NAME` | *(none)* | Foundry project name |
| `GENIE_MEMORY_STORE_BACKEND` | `in_memory` | `in_memory` \| `cosmos_db` — production requires `cosmos_db` |
| `GENIE_MEMORY_STORE_ENDPOINT` | *(none)* | Required in production when backend is `cosmos_db` |
| `GENIE_MEMORY_STORE_DATABASE_NAME` | `genie` | Cosmos database containing durable sessions and prototype inventory |
| `GENIE_MEMORY_STORE_CONTAINER_NAME` | `memory` | Shared Cosmos container partitioned by record family |
| `GENIE_LINEAGE_STORE_BACKEND` | `in_memory` | Same pattern as memory store, for governance/lineage |
| `GENIE_LINEAGE_STORE_ENDPOINT` | *(none)* | Required in production |
| `GENIE_DEFAULT_LLM` | `gpt-5.1` | Default model deployment name for agents that omit `model_deployment_ref` |
| `GENIE_DEBUGGING_WORKFLOW_ID` | `debugging-workflow` | Workflow id run on `FailureDetected` |
| `GENIE_REQUIREMENTS_QUALIFICATION_STEP_ID` | `analyze-requirements` | Workflow step id whose output is checked for an agentic-workflow qualification verdict |
| `GENIE_DEPLOYMENT_FIDELITY_MAX_REPAIR_ATTEMPTS` | `3` | Max automatic regenerate-and-redeploy attempts the Requirement Fidelity Gate makes before failing closed |
| `GENIE_DEPLOYMENT_FIDELITY_MIN_COVERAGE_PERCENT` | `90` | Minimum approved-requirement percentage with executable acceptance tests required for launch; every executable test must still pass and uncovered requirement IDs remain visible as gaps |
| `GENIE_DEPLOYMENT_TEST_EXECUTION_TIMEOUT_SECONDS` | `300` | Max seconds the Requirement Fidelity Gate's real pytest subprocess (real black-box HTTP acceptance tests against the live deployed prototype, one per approved requirement) is allowed to run before being killed |
| `GENIE_PROTOTYPE_API_GATEWAY_ENABLED` | `false` | Enables a dedicated Azure API Management service and private runtime network for every newly deployed prototype; mandatory (`true`) in production |
| `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_EMAIL` | *(none)* | Required APIM publisher contact email supplied as external deployment configuration |
| `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_NAME` | *(none)* | Required APIM publisher display name supplied as external deployment configuration |
| `GENIE_PROTOTYPE_API_GATEWAY_SKU_NAME` | `StandardV2` | APIM SKU; `StandardV2` or `PremiumV2` so the gateway can reach the private backend VNet |
| `GENIE_PROTOTYPE_API_GATEWAY_CAPACITY` | `1` | Capacity units for each prototype's dedicated APIM service |
| `GENIE_PROTOTYPE_DEFAULT_TTL_DAYS` | `7` | Initial owner prototype lifetime (1-90 days) |
| `GENIE_PROTOTYPE_MAX_ACTIVE_PER_OWNER` | `3` | Per-owner active prototype quota |
| `GENIE_PROTOTYPE_CLEANUP_INTERVAL_SECONDS` | `3600` | Expired-prototype reconciliation interval; failed deletion remains visible and retryable |
| `GENIE_KEY_VAULT_URI` | *(none)* | Required in production |
| `GENIE_CORS_ALLOWED_ORIGINS` | *(empty)* | Comma-separated browser origins allowed to call the API (e.g. the deployed frontend's URL) |
| `GENIE_CONFIG_ROOT` | `config` | Root directory for agents/prompts/workflows/policies |
| `AZURE_CLIENT_ID` | *(none)* | **Required** when running under a Container App / VM with a **user-assigned** managed identity — tells `DefaultAzureCredential` which identity to use |

Frontend (`frontend/.env.production` / `.env.development`, Vite `VITE_` prefix):

| Variable | Purpose |
|---|---|
| `VITE_GENIE_API_BASE_URL` | Platform APIM gateway URL in production; local FastAPI URL in development |

---

## Authentication

Genie and every generated prototype are intentionally anonymous:

- **Genie API and frontend**: the SPA opens without a redirect or token and sends no `Authorization` header. Public Standard v2 APIM enforces the configured exact-origin CORS policy, rate limit, and correlation header, then reaches FastAPI through outbound VNet integration and the Container Apps private endpoint. Container Apps environment public access is disabled. CORS does not stop non-browser clients that know the public APIM URL.
- **Internal principal**: every API request resolves to the deterministic `genie-internal-user` principal with `Genie.Admin`. Request headers cannot change that identity. Sessions, governance attribution, prototype ownership, inventory, and cleanup authority are therefore shared across all callers; there is no per-user isolation or meaningful user-role distinction.
- **Generated prototypes**: every Deploy & Launch run owns a dedicated API Management service, VNet, private DNS zone, internal Container Apps environment, exact CORS policy, resource group, managed identity, RBAC assignments, and Foundry agents. The generated SPA and APIM endpoint require no sign-in or bearer token. APIM enforces exact-origin browser CORS, rate limiting, and correlation before forwarding over the private network; generated FastAPI has no public ingress.
- **Azure workload identity remains**: removing interactive user authentication does not remove managed identity. `DefaultAzureCredential` still uses the Container App's user-assigned identity for Cosmos DB, Foundry, Azure management, ACR, storage, and other Azure service calls under least-privilege RBAC.
- **Durable inventory and lifecycle**: production uses managed-identity Cosmos access. Startup hydrates deployment runs before readiness and marks interrupted work failed rather than pretending it completed. A cancellable hourly reconciler deletes expired terminal prototypes, stores `deletion_pending`/`deletion_failed` state, and preserves failures for retry.

---

## Deployment strategy

The long-lived Genie **evaluation environment** consists of: (1) Bicep infrastructure-as-code that provisions foundational Azure resources and a dedicated APIM subnet, (2) a public Standard v2 APIM gateway with outbound VNet integration, (3) a FastAPI Container App reached only through its environment private endpoint, and (4) a static React frontend deployed to Azure Static Web Apps. Generated prototypes are separate: each receives its own APIM service and private Container Apps environment. Deployment is split into readiness validation → infrastructure provisioning → agent provisioning → gateway/private-network cutover → application deployment, matching the repo's fail-closed philosophy: nothing proceeds until the previous step is verified. These deployment instructions reproduce the evaluation environment; they do not supersede the production-readiness work required by the [purpose and use boundary](#purpose-and-use-boundary).

### Deploying into a brand-new Azure subscription

**Prerequisites**

- Azure CLI (`az`) installed and logged in (`az login`) against the target subscription and tenant.
- Contributor-equivalent (or the custom role generated below) rights on the target subscription.
- PowerShell (scripts are `.ps1`; can be run via `pwsh` on non-Windows too).

**1. Create a least-privilege deployment identity (optional but recommended)**

```powershell
python scripts/generate_deployment_role_definition.py   # renders a custom RBAC role from config/deployment/resource_providers.yaml
./scripts/create_deployment_identity.ps1                # creates/assigns the "Genie Infrastructure Deployer" role
```

This role only grants `<namespace>/*` actions for the 12 resource-provider namespaces Genie actually needs (never `Owner`/`Contributor`). If your tenant blocks service-principal secret/certificate creation (common in managed/enterprise tenants), the script falls back to assigning the role directly to your signed-in user account.

**2. Validate deployment readiness**

```powershell
python scripts/validate_deployment_readiness.py
```

Confirms every required Azure resource-provider namespace (`Microsoft.Resources`, `Authorization`, `ManagedIdentity`, `KeyVault`, `Storage`, `CognitiveServices`, `Search`, `DocumentDB`, `App`, `Web`, `OperationalInsights`, `Insights`, plus `Marketplace`/`MarketplaceOrdering`/`SaaS` if you plan to deploy partner models) is `Registered`. Registers via:

```powershell
az provider register --namespace <namespace>
```

Re-run the validator until every provider shows `Registered` (registration is asynchronous and can take several minutes) before proceeding.

**3. Provision infrastructure**

```powershell
./scripts/deploy_infra.ps1 -EnvironmentName dev -Location eastus2
# Optional: -AiSearchLocation eastus   (if AI Search lacks capacity in the primary region)
```

This gates on step 2 passing, then runs:

```powershell
az deployment sub create `
  --location <location> `
  --template-file infra/main.bicep `
  --parameters infra/main.parameters.json environmentName=<env> location=<location>
```

`main.bicep` is **subscription-scoped** and assumes the target subscription is empty — it creates its own resource group (`<resourcePrefix>-<environmentName>-rg`, default prefix `genie`) and every foundational resource:

| Module | Resource |
|---|---|
| `managed-identity.bicep` | User-assigned managed identity (shared by every resource below via least-privilege RBAC) |
| `key-vault.bicep` | Azure Key Vault |
| `storage-account.bicep` | Azure Storage Account |
| `ai-search.bicep` | Azure AI Search (Enterprise Knowledge Memory) |
| `cosmos-db.bicep` | Cosmos DB (Shared/Personal Memory, sessions) |
| `ai-foundry.bicep` | Azure AI Foundry account + project |
| `virtual-network.bicep` | VNet with dedicated Container Apps, private endpoint, and delegated APIM integration subnets |
| `container-apps-environment.bicep` | Container Apps environment (hosts the backend) |
| `static-web-app.bicep` | Azure Static Web App (hosts the frontend) |
| `log-analytics.bicep` + `app-insights.bicep` | Observability |

Every resource is granted only the specific RBAC role it needs on the shared managed identity (Key Vault Secrets User, Storage Blob Data Contributor, Search Index Data Contributor, Cognitive Services User, Cosmos DB Built-in Data Contributor) — never a broad Owner/Contributor grant.

After the backend Container App exists, `infra/platform-private-gateway.bicep` adds the Standard v2 APIM service and anonymous proxy API, exact-origin policy, private endpoint, and `privatelink.<region>.azurecontainerapps.io` DNS integration. `scripts/deploy_platform_gateway.ps1` controls the safe cutover rather than having foundational provisioning disable access before a gateway can be verified.

Capture the outputs (`aiFoundryEndpoint`, `aiSearchEndpoint`, `keyVaultUri`, `storageAccountName`, `cosmosDbEndpoint`, `managedIdentityPrincipalId`, `containerAppsEnvironmentId`, `staticWebAppDefaultHostname`, `applicationInsightsConnectionString`) via:

```powershell
az deployment sub show --name <deployment-name> --query properties.outputs
```

**4. Deploy an LLM model**

Deploy at least one model to the AI Foundry account (Cognitive Services deployment). For models sold directly by Azure (e.g. `gpt-5.1`):

```powershell
az cognitiveservices account deployment create `
  --name <foundry-account-name> --resource-group <rg> `
  --deployment-name <deployment-name> --model-name <model> --model-version <version> `
  --model-format OpenAI --sku-name GlobalStandard --sku-capacity 10
```

For Azure Marketplace / partner models (e.g. Anthropic Claude), the CLI has no flag for `ModelProviderData` — use a direct REST call instead:

```powershell
az rest --method put `
  --uri "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/deployments/<name>?api-version=2026-05-15-preview" `
  --body '{"properties":{"model":{"format":"...","name":"...","version":"..."},"modelProviderData":{"industry":"Technology","organizationName":"<org>","countryCode":"US"}},"sku":{"name":"GlobalStandard","capacity":1}}'
```

> Requires the target Marketplace SKU to actually have non-zero quota on the subscription — request a quota increase first if `InsufficientQuota` is returned.

Set `Settings.DEFAULT_LLM` / `GENIE_DEFAULT_LLM` (and any per-agent `model_deployment_ref` overrides in `config/agents/registry.yaml`) to match the deployment name you chose.

### Provisioning Foundry agents

Genie agents are **real Foundry agent resources**, provisioned once per environment — never created ad hoc by the running application.

```powershell
# Requires the caller to hold "Cognitive Services User" (or equivalent) at the
# Foundry PROJECT scope (not just the account scope) for data-plane writes.
python scripts/provision_foundry_agents.py
```

Uses the `azure-ai-projects` SDK (`AIProjectClient(endpoint=..., credential=DefaultAzureCredential())`) to create one Foundry agent per enabled entry in `config/agents/*.yaml` and prints each resulting `foundry_agent_id` for you to paste back into the registry YAML (the script deliberately never writes back to source control itself).

Verify every agent resolves correctly:

```powershell
python scripts/validate_foundry_agents.py     # runs the fail-closed Foundry validators directly
python scripts/sync_foundry_agents.py         # confirms each enabled agent's foundry_agent_id exists (status: synchronized)
python scripts/export_foundry_inventory.py    # dumps the config-derived inventory as JSON
```

All three require `GENIE_AZURE_FOUNDRY_ENDPOINT` / `GENIE_AZURE_FOUNDRY_PROJECT_NAME` to be set and must be run from the repository root (so the relative `config/` path resolves correctly).

### Deploying application code (backend + frontend)

**Backend (Azure Container Apps)**

1. Build and push the immutable FastAPI image to Azure Container Registry:

   ```powershell
   az acr build --registry <acr-name> --image genie-backend:<commit> `
     --file backend/Dockerfile <source>
   ```

   `<source>` can be a local directory (`.`) or a git URL (`https://github.com/<org>/<repo>.git#<branch>`, optionally with an embedded token for a private repo: `https://<token>@github.com/...`) — use whichever works reliably in your build environment.

2. Provision APIM, prove it reaches the current backend, create private endpoint/DNS integration, disable Container Apps environment public access, and prove both the private gateway route and direct-route denial:

   ```powershell
   $gateway = ./scripts/deploy_platform_gateway.ps1 `
     -SubscriptionId <subscription-id> `
     -ResourceGroup <rg> `
     -ContainerAppName genie-backend `
     -AllowedOrigin https://<static-web-app-host> `
     -PublisherEmail <publisher-email> `
     -PublisherName "Genie" | ConvertFrom-Json
   ```

   The first deployment can take tens of minutes while Standard v2 APIM is created. Public Container Apps access is not changed unless APIM is healthy and private endpoint provisioning succeeds. Repeated runs are idempotent.

3. Atomically update the backend image, remove retired gateway containers and auth settings, configure CORS/probes, target FastAPI port `8000`, and verify the revision through APIM:

   ```powershell
   ./scripts/deploy_backend.ps1 `
     -SubscriptionId <subscription-id> `
     -ResourceGroup <rg> `
     -ContainerAppName genie-backend `
     -BackendImage <acr-name>.azurecr.io/genie-backend:<commit> `
     -AllowedOrigin https://<static-web-app-host> `
    -GatewayUrl $gateway.gatewayUrl `
     -MemoryStoreEndpoint https://<cosmos-account>.documents.azure.com/ `
     -PrototypeApiGatewayPublisherEmail <publisher-email> `
     -PrototypeApiGatewayPublisherName "Genie" `
     -RevisionSuffix <unique-suffix>
   ```

   The script preserves the existing identity, environment, secrets, resources,
   and unrelated containers; removes `genie-auth-gateway`/`mise-sidecar` plus
   stale user-auth settings; and applies the image, CORS, probes, and ingress
  target in one ARM patch. It requires environment public access to remain
  `Disabled`, waits for the exact revision, and verifies readiness plus anonymous
  `GET /sessions` through APIM. Roll back by running the same script with the
  previous backend image tag and a new revision suffix.

   > If you're using a **user-assigned** managed identity, retain
   > `AZURE_CLIENT_ID=<identity-client-id>` or `DefaultAzureCredential` cannot
   > resolve which identity to use and the container will crash-loop.

4. Verify:

   ```powershell
  curl https://<apim-name>.azure-api.net/health/live
  curl https://<apim-name>.azure-api.net/health/ready
  curl -i https://<apim-name>.azure-api.net/sessions  # must return 200
  curl -i https://<container-app-fqdn>/health/ready   # must not return 2xx
   ```

**Frontend (Azure Static Web Apps)**

1. Set `VITE_GENIE_API_BASE_URL` to the APIM gateway URL for the build. Production CI receives this URL directly from the verified `prepare-gateway` job; no production endpoint is committed in an env file.
2. Build:

   ```powershell
   cd frontend
   npm ci
   npm run build      # -> dist/
   ```

3. Deploy:

   ```powershell
   $token = az staticwebapp secrets list --name <swa-name> --query properties.apiKey -o tsv
   npx @azure/static-web-apps-cli deploy dist --deployment-token $token --env production
   ```

### Continuous deployment (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR to `master` (the repo's actual default branch — double-check this before ever pointing it at `main`). On a real push to `master`, once the `backend` and `frontend` CI jobs pass, two deploy jobs run the exact same steps documented above, automatically:

- **`prepare-gateway`** — logs into Azure via OIDC federated credential (no client secret), idempotently provisions Standard v2 APIM and its delegated subnet/NSG, proves the gateway reaches the current backend, and emits the verified URL without changing Container Apps public access.
- **`deploy-frontend`** — builds the frontend against that exact gateway job output and deploys it with `@azure/static-web-apps-cli` using a stored deployment token. It no longer trusts a separately maintained production API URL variable.
- **`deploy-backend`** — waits for the frontend cutover, builds the commit-pinned FastAPI image, creates/verifies private endpoint/DNS, disables Container Apps public access, proves APIM still works and direct ingress is denied, then runs `deploy_backend.ps1` so the image, retired-sidecar removal, CORS, probes, and ingress are updated atomically and verified through APIM.

**One-time setup** (already performed for this environment — documented here so it can be reproduced on a new subscription/repo):

1. A dedicated app registration (`genie-github-actions-deploy`, no client secret) holds a **federated identity credential** trusting this repo's GitHub Actions OIDC issuer, scoped to the `production` GitHub Environment — narrower than a branch-based subject, since it also requires the workflow job to declare `environment: production`. **Important**: the subject must match GitHub's *actual* token claim exactly, which is `repo:<org>/<repo>:environment:<env>` only if the org/repo have never been renamed — if either has been renamed, GitHub appends numeric IDs instead (`repo:<org>@<orgId>/<repo>@<repoId>:environment:<env>`). Get the exact value from a failed `azure/login@v2` run's log line `Federated token details: ... subject claim - ...` if login fails with `AADSTS700213`.
2. That identity's service principal holds three least-privilege assignments (never a subscription- or resource-group-wide Owner/Contributor grant):
   - **Container Registry Tasks Contributor**, scoped to just the ACR resource — covers `az acr build`'s scheduleRun/upload actions without granting registry data-plane push/pull.
  - **Container Apps Contributor**, scoped to just the `genie-backend-corporate` Container App resource — covers the atomic ARM patch.
  - **Genie Platform Gateway Deployer**, scoped to the Genie resource group — a custom role containing only resource-group deployment, APIM API, delegated subnet/NSG, private endpoint/DNS, and Container Apps environment update/approval actions. It contains no delete or authorization-management action. Create/update and assign it once with `scripts/configure_platform_gateway_deployer.ps1 -SubscriptionId <id> -ResourceGroup <rg> -PrincipalObjectId <oidc-service-principal-object-id>`.
3. The **runtime Genie backend managed identity** has the custom `Genie Prototype Resource Group Operator` role plus API Management Service Contributor, Network Contributor, Container Apps Contributor, Managed Identity Contributor, and Managed Identity Operator at subscription scope. The custom role permits only resource-group read/write/delete; the built-in roles are restricted to their respective provider surfaces. Shared ACR and role-assignment permissions remain constrained to existing resource scopes. New prototypes do not require Microsoft Graph application writes.
4. The repo's **Settings → Secrets and variables → Actions** has:
  - **Secrets**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (identify the federated deployment app, not credentials by themselves), and `SWA_DEPLOYMENT_TOKEN`.
  - **Variables**: `AZURE_ACR_NAME`, `AZURE_CONTAINER_APP_NAME`, `AZURE_RESOURCE_GROUP`, `GENIE_GATEWAY_ALLOWED_ORIGIN`, `GENIE_MEMORY_STORE_ENDPOINT`, `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_EMAIL`, and `GENIE_PROTOTYPE_API_GATEWAY_PUBLISHER_NAME`. `VITE_GENIE_API_BASE_URL` is no longer a production GitHub variable; `prepare-gateway` emits it from the APIM deployment.

If this identity/RBAC/secrets setup is ever missing or revoked, `deploy-backend`/`deploy-frontend` fail fast (within seconds, at an explicit "Check required secrets" step) rather than hanging — the `backend`/`frontend` test jobs are unaffected either way and still gate every PR.

---

## Testing

| Layer | Command | Notes |
|---|---|---|
| Backend unit + integration | `pytest` (from `backend/`) | Includes production-safety and fail-closed startup tests |
| Backend lint | `ruff check app tests` | |
| Frontend component tests | `npm run test` (Vitest) | Includes static-scan tests guarding against hardcoded demo data and direct Foundry access |
| Frontend type check | `npm run typecheck` | |
| Frontend lint | `npm run lint` | `--max-warnings=0` |
| End-to-end | Playwright (`e2e/`) | Scaffolding present; expand per feature as needed |

---

## Troubleshooting

- **`az acr build` hangs indefinitely with a local (`.`) build context** — a known issue on some Windows ARM64 machines (tar-packing step never completes). Workaround: build from a pushed git remote URL instead (`az acr build ... https://github.com/<org>/<repo>.git#<branch>`); for a private repository, embed a token in the URL rather than making the repository public.
- **Container App crash-loops with `ManagedIdentityCredential: ... Unable to load the proper Managed Identity`** — set `AZURE_CLIENT_ID` to the user-assigned identity's **client id** (not principal id).
- **`PermissionDenied` calling `create_agent`** — the "Cognitive Services User" role must be assigned at the Foundry **project** scope, not just the account scope; RBAC propagation can take a few minutes.
- **`InsufficientQuota` deploying a Marketplace model** — the subscription has a default quota of 0 for most partner model SKUs; request a quota increase before retrying.
- **az/gh/npm not found in a fresh terminal** — these CLIs are commonly installed but not yet appended to `PATH` in a brand-new shell session; re-add their install directories to `$env:Path` for that session.

---

## Deploy log

Every deployment to the shared Azure evaluation environment (backend Container App and/or frontend Static Web App) is recorded here: commit, what changed, and why. Update this section as part of the same commit that ships the fix/feature, before pushing to `master` triggers [Continuous deployment](#continuous-deployment-github-actions).

### 2026-09-11 — Private Genie backend behind platform API Management

- **Network boundary**: Standard v2 APIM remains the anonymous public API edge, while outbound VNet integration resolves the existing Container App FQDN through a Container Apps environment private endpoint and `privatelink.eastus2.azurecontainerapps.io`. Environment public network access is disabled after the gateway route is proven.
- **Fail-closed cutover**: `deploy_platform_gateway.ps1` verifies APIM before changing public access, requires an explicitly approved private-link connection, verifies APIM again after cutover, and fails if direct Container Apps ingress still returns a successful response. `deploy_backend.ps1` now verifies every revision only through APIM and refuses an environment whose public access is enabled.
- **CI and least privilege**: `prepare-gateway` emits the verified APIM URL directly to the frontend build; only after that deployment does `deploy-backend` finalize the private-network cutover, avoiding an outage against the old direct-backend bundle. The GitHub OIDC principal receives the resource-group-scoped `Genie Platform Gateway Deployer` custom role with no delete or RBAC-management actions.
- **Infrastructure and tests**: foundational Bicep reserves a `/24` NSG-associated subnet delegated to `Microsoft.Web/serverFarms`; the platform template owns APIM, anonymous proxy operations/policy, private DNS, and private endpoint resources. Focused deployment-contract tests and Bicep/PowerShell syntax checks protect the topology and cutover ordering.

### 2026-09-10 — Anonymous Genie control plane and direct FastAPI ingress

- **No interactive authentication**: removed MSAL, the Entra token validator, bearer headers, app-registration configuration, the .NET MISE project, its private package-feed dependency, and all associated CI jobs. Genie opens directly and generated prototypes remain anonymous.
- **Shared identity semantics**: every request receives the fixed `genie-internal-user` principal with `Genie.Admin`; caller headers cannot influence identity. Session ownership, governance attribution, prototype inventory, and cleanup authority are shared across all callers.
- **Direct backend rollout**: `deploy_backend.ps1` atomically removes retired gateway containers and auth settings, deploys the commit-pinned FastAPI image, configures exact-origin CORS and health probes, moves ingress to port `8000`, waits for the exact ready revision, and verifies anonymous API access.
- **Deployment hardening**: the first CI attempt built and pushed the backend image but stopped before its ARM patch because the existing container JSON omitted the optional `probes` property. The script now adds or replaces that property explicitly, so both legacy containers without probes and later revisions with probes follow the same atomic path.
- **Workload identity retained**: the Container App's user-assigned managed identity and least-privilege RBAC remain the production path to Cosmos DB, Foundry, Azure management, ACR, and other Azure services. CORS is not authentication; the public Genie API URL is intentionally callable outside a browser.

### 2026-09-10 — Anonymous prototypes behind dedicated API gateways

This prototype-only change was extended later the same day by [Anonymous Genie control plane and direct FastAPI ingress](#2026-09-10--anonymous-genie-control-plane-and-direct-fastapi-ingress). The details below remain as deployment history.

- **Authentication boundary**: Genie itself remains protected by corporate Microsoft Entra ID and its MISE gateway. Newly generated prototype frontends and APIs no longer provision, configure, or require Entra applications, MSAL, bearer tokens, shared callback slots, or acceptance keys.
- **Private backend**: each prototype still owns a dedicated Standard v2 APIM service, VNet, private DNS zone, and internal Container Apps environment. Generated FastAPI ingress remains private; APIM is the only public API endpoint.
- **Gateway policy and tests**: APIM retains exact-origin CORS, per-client rate limiting, correlation IDs, and private forwarding. Acceptance tests call the APIM URL directly without credentials. Exact-origin CORS protects browser use but does not authenticate non-browser callers.
- **Deployment cleanup**: CI no longer requires shared-prototype Entra variables. At this point the rollout still preserved Genie's corporate Entra and MISE settings; the later entry removes them.

### 2026-09-10 — Superseded: acceptance testing across APIM and tenant isolation

This intermediate authenticated-prototype design was replaced the same day by [Anonymous prototypes behind dedicated API gateways](#2026-09-10--anonymous-prototypes-behind-dedicated-api-gateways). The details below remain as deployment history and are not current behavior.

- **Root cause**: production uses a corporate-tenant shared prototype audience while Genie's Azure managed identity belongs to the subscription tenant. The old shared-auth test branch bypassed authentication through a replica-local MISE port; dedicated APIM removed that endpoint, and the managed identity cannot receive an app role from the other tenant.
- **Fix**: each protected deployment now generates a 256-bit acceptance key, stores it as a Container App secret and APIM secret named value, and passes it only through Genie's trusted loopback proxy. Generated pytest receives only the loopback URL. APIM strips spoofed internal headers, validates the key, removes it before private forwarding, and FastAPI compares APIM's proof using constant-time comparison. Normal browser traffic continues to require corporate Entra validation at both APIM and FastAPI.
- **Lifecycle**: the in-memory key is removed before pytest starts; APIM and Container App copies disappear with the prototype resource group. Missing keys fail the launch before acceptance tests run.

### 2026-09-10 — Dedicated API gateway and private backend per prototype

- **Isolation**: every new prototype provisions a dedicated Standard v2 Azure API Management service, VNet, private DNS zone, and internal Container Apps environment in its own resource group. Generated FastAPI has no public ingress; browser and acceptance-test traffic reaches it only through APIM.
- **Security**: APIM validates the corporate Entra tenant and audience, enforces exact-origin CORS and per-client rate limiting, and forwards the token for FastAPI defense-in-depth validation. Generated prototypes no longer deploy or configure a MISE sidecar.
- **Lifecycle**: deterministic resource names make retries idempotent, while existing owner/admin abandonment deletes external Foundry and identity artifacts before deleting the prototype resource group and all gateway/network/runtime resources together.
- **Deployment**: CI passes externally configured APIM publisher metadata to Genie and removes legacy prototype-MISE environment variables. The long-lived Genie control plane continues to use its existing MISE gateway.

### 2026-09-10 — Retire the detached legacy Container App

- **Cleanup**: removed the legacy `genie-backend` Container App and its two revisions after the corporate/private-network cutover. Its logs contained only platform health probes; its latest revision could not access private Cosmos and was unhealthy.
- **Current deployment**: `genie-backend-corporate` is the sole Genie Container App. The deployed frontend bundle and GitHub Actions variables both target its `proudtree-6b064653.eastus2.azurecontainerapps.io` endpoint, and its single `gh45` revision was healthy with 100% traffic before retirement.
- **Preserved dependencies**: the shared managed identity, ACR, legacy Container Apps environment, and current private environment were not deleted. Removing the old app therefore does not remove credentials, images, networking, or resources used by the corporate deployment.

### 2026-09-09 — Fix corporate Entra discovery after private-network cutover

- **Incident**: authenticated frontend requests such as `GET /models/available` returned `503` because the deployment script configured `GENIE_ENTRA_AUTHORITY` with a tenant `/v2.0` path while `EntraTokenValidator` independently appended that same path.
- **Fix**: deployments now configure the host-only `https://login.microsoftonline.com` authority. The validator rejects authorities containing tenant, version, query, or fragment components so this configuration error fails during startup instead of surfacing after sign-in.

### 2026-09-09 — Corporate workforce identity and durable prototype ownership

- **Corporate access**: Genie now targets Microsoft corporate tenant `72f988bf-86f1-41af-91ab-2d7cd011db47`; a real delegated token was verified for the expected audience/scope with canonical user identity and `Genie.Admin`. GitHub's Azure deployment tenant remains separate.
- **Ownership and administration**: sessions and deployment runs use canonical `<tid>:<oid>` ownership. Cosmos persists the global prototype inventory; owner routes stay owner-scoped, and `Genie.Admin` can list and clean all prototypes.
- **Lifecycle and isolation**: each prototype gets a tagged resource group, 7-day TTL, three-active-prototype owner quota, hourly cleanup reconciliation, and durable retryable cleanup state. Interrupted runs fail closed on restart.
- **Shared prototype authentication**: new prototypes reuse one corporate Entra registration and a bounded pool of 50 pre-registered frontend callbacks. Each owns its API gateway and exact CORS origin. Generated acceptance tests receive no bearer token, and public FastAPI exposure remains blocked.
- **Legacy retirement**: the pre-cutover directory inventory found zero legacy `Genie Prototype - *` registrations. Existing prototype Container Apps are not modified by the auth cutover and can age out through normal cleanup.
- **Derek POC retirement**: removed all 82 `derekpoc-*` Foundry agents, four Container Apps, four ACR repositories, two managed identities, and their two Foundry role assignments. Post-cleanup inventories found no Derek resources, images, agents, app registrations, or service principals.
- **Deployment**: CI now configures corporate frontend/gateway/backend identity together, enables managed-identity Cosmos persistence, and no longer conflates application sign-in tenant with GitHub OIDC tenant. Frontend deployment waits for the backend rollout to become ready, preventing a partial identity cutover.
- **Cosmos networking**: Cosmos keeps local authentication and public network access disabled. The VNet-injected environment uses a dedicated Cosmos private endpoint and `privatelink.documents.azure.com` private DNS zone. Its `genie-backend-corporate` app passed gateway readiness, unauthenticated-rejection, and private Cosmos checks; CI/CD now targets that app and releases the corporate frontend only after its backend revision is ready.

### 2026-09-03 — Azure architecture and usage boundary

- **Documentation**: replaced Mermaid diagrams with professional, renderer-independent architecture images built from the official Microsoft Azure Architecture Icons. The diagrams separate the Genie control plane from each generated prototype and cover Static Web Apps, Container Apps, MISE, Entra ID, managed identity/RBAC, Foundry, Azure data services, ACR, and Azure Monitor.
- **Usage boundary**: added a prominent **Microsoft Confidential — Internal Collaboration Only** notice. Genie is an art-of-the-possible rapid-prototyping environment and must not be deployed directly to production without independent security, privacy, Responsible AI, accessibility, data-governance, resilience, and operational-readiness reviews.

### 2026-09-03 — Independent MISE gateway for every generated prototype

- **What changed**: Deploy & Launch now creates one owned Entra API/SPA registration and one independently configured MISE gateway container for each new prototype. Public backend ingress targets that gateway on `8080`; generated FastAPI remains private on `8000` and validates the same unique audience as defense in depth.
- **Browser and test authentication**: generated Vite shells use MSAL redirect login, the prototype's `access_as_user` scope, bearer forwarding, and silent refresh. A trusted loopback proxy owns the short-lived `Prototype.Invoke` app token and injects it only while forwarding to the fixed prototype backend; generated pytest code receives no bearer token.
- **Fail-closed lifecycle**: the backend starts only when the pinned gateway image, tenant, and managed-identity test principal are configured. Entra app creation, service-principal creation, app-role assignment, mission-identity ACR pull configuration, SPA redirect finalization, exact-origin CORS revision, and token acquisition all fail the deployment rather than exposing FastAPI or selecting a local fallback. Failed runs retain the complete protected boundary for retry; explicit owner-authorized abandonment removes runtime resources, RBAC assignments, the mission identity, and the Entra application in dependency order.
- **Deployment**: CI/CD passes the already-built commit-pinned gateway image into Genie's runtime through `scripts/deploy_authentication_gateway.ps1`; no manual application deploy is used. The runtime managed identity requires tenant-admin-consented Graph application permissions documented in [Authentication](#authentication).
- **Operational verification**: the implementation and fail-closed tests are complete, but this environment's Graph application permissions and tenant-admin consent remain unverified until a real Deploy & Launch run successfully provisions its prototype registration, service principal, and role assignment.

### 2026-09-02 — Impeccable design contract for generated prototype frontends

- **What changed**: the Architecture Designer now shapes a domain-specific surface mode and visual direction using the method from [Impeccable](https://impeccable.style/); initial UI generation, component generation, and Workshop UI regeneration all enforce the same anti-slop, responsive, accessible design contract.
- **Fail-closed design check**: every materialized prototype frontend pins `impeccable` `3.6.0` and runs `impeccable detect MissionApp.tsx src/` before Vite. The generated frontend image now builds on Node 22 to satisfy the CLI runtime requirement. A deterministic finding exits with code 2 and prevents deployment. The generated scaffold's Vite pin moves from vulnerable `6.0.7` to npm's non-major fixed release `6.4.3`.
- **Shell quality**: removed nested mission-input cards and replaced bounce motion with a restrained loading cadence. The exact generated shell templates pass the real Impeccable detector with zero findings.
- **Scope**: this affects newly generated or regenerated prototypes and future Deploy & Launch runs. It does not retroactively redesign already-deployed prototype source.

### 2026-08-31 — MISE authentication gateway and private FastAPI ingress

- **What changed**: added a .NET 8 ASP.NET Core gateway using `Microsoft.Identity.ServiceEssentials.AspNetCore` `2.5.3` and YARP, host-level authentication/CORS/readiness tests, and an atomic Container App rollout that deploys one gateway per replica and moves external ingress from FastAPI port `8000` to gateway port `8080`. FastAPI retains its existing Entra token validation as defense in depth.
- **Security boundary**: liveness/readiness and configured browser preflight are anonymous; every API request requires a valid user or application access token for the configured Genie API audience. The Entra registration now emits `idtyp` for delegated tokens, and the original bearer token is forwarded to FastAPI. MISE feed credentials are supplied only through GitHub Actions secrets and ACR secret build arguments.
- **Deployment**: CI/CD builds both commit-pinned images and applies them with `scripts/deploy_authentication_gateway.ps1`. `AZURE_DEVOPS_TOKEN` is configured with MicrosoftIT Packaging Read access; authenticated MISE restore, gateway build/tests, and both ACR image builds passed. Rollout failures remained fail-closed before changing ingress: Linux PowerShell required `[System.IO.Path]::GetTempPath()`, and ARM rejected the unsupported legacy `imageType` container property, which has been removed.
- **Post-deploy evidence**: record the ready revision, UTC authenticated-request window, correlation ID, response status, and MCAPS SFI telemetry confirmation in `docs/MISE_SFI_VERIFICATION.md`.

### 2026-08-21 — Requirement Fidelity Gate: fix false "no JUnit result" for parametrized acceptance tests

- **What changed**: `backend/app/services/requirement_fidelity_service.py` (`record_fidelity_execution`) now matches an expected acceptance-test name against pytest's JUnit XML output by exact name **or** any `name[param]`-bracketed instance of it, instead of exact string equality only.
- **Root cause**: pytest always reports a `@pytest.mark.parametrize`-decorated test's real JUnit case name as `"<def name>[<param id>]"`, never the bare `def` name alone. Any requirement whose generated acceptance test used `parametrize` (a natural way to test "cover N languages/items") always showed `"no JUnit result"` evidence in the Requirement Fidelity Gate, even when every parametrized case actually passed — an unrecoverable false failure loop.
- **Tests**: new regression test `test_parametrized_test_case_names_are_matched_to_their_bare_def_name` (`backend/tests/unit/services/test_requirement_fidelity_service.py`); full backend suite (515 tests) + ruff clean.
- **Deployed via**: CI/CD (`.github/workflows/ci.yml`) on push to `master` — no manual `az acr build`/`az containerapp update` needed.

### 2026-08-21 — Requirement fidelity: fix dropped-leading-zero test name matching for Generate Requirement Acceptance Tests

- **What changed**: `backend/app/services/requirement_fidelity_service.py` (`_test_names_for_requirement`) now matches a generated test's function name against a requirement id with a digit-boundary-aware, leading-zero-tolerant regex (`_requirement_name_pattern`) instead of a plain substring check.
- **Root cause**: the Test Generation Agent occasionally writes a test function name that drops a requirement id's leading zero(s) — e.g. `test_req_16_...` for `REQ-016` — which a plain `"req_016" in name` substring check never matches, permanently reporting that requirement as having no executable acceptance test (`generate-test-suite` step failure: "Generated test suite does not cover every approved requirement"). The old substring check was also unsafe in the other direction — it could wrongly match `REQ-016` against a test written for an unrelated id like `REQ-0160`.
- **Tests**: new regression tests `test_test_name_matching_tolerates_a_dropped_leading_zero` and `test_test_name_matching_does_not_collide_with_a_similar_numeric_id` (`backend/tests/unit/services/test_requirement_fidelity_service.py`); full backend suite (517 tests) + ruff clean.
- **Deployed via**: CI/CD (`.github/workflows/ci.yml`) on push to `master` — no manual `az acr build`/`az containerapp update` needed.

### 2026-08-22 — Requirement fidelity: replace fragile name-based coverage matching with an explicit `# REQ-xxx` tag comment

- **What changed**: this is the second real bug in two days in the same "map a generated test back to a requirement id" logic (the earlier parametrize-bracket bug, then the dropped-leading-zero bug). Rather than another narrow regex patch, `backend/app/services/requirement_fidelity_service.py` adds `_tagged_test_names`, a new *primary* coverage signal: a `# REQ-xxx` comment directly above a test function (blank lines and decorator lines like `@pytest.mark.parametrize(...)` may sit between the comment and the `def`; any other line clears a pending tag). The Test Generation Agent only has to copy the id verbatim from the requirements it was already given — never transform it into a valid, zero-padded Python identifier, which is exactly what kept going wrong. The old name-based regex (`_requirement_name_pattern`) is kept as a secondary fallback for tests that don't carry a tag. `config/prompts/registry.yaml`'s `test-generation-v1` prompt (bumped to version `1.7.0`) now requires this tag comment on every generated test, in addition to (not instead of) naming the function after the requirement.
- **Tests**: new regression tests `test_tag_comment_covers_a_requirement_regardless_of_function_name`, `test_tag_comment_survives_blank_lines_and_decorators_but_not_other_code`, `test_one_tag_comment_block_covers_multiple_requirement_ids`; updated `test_comment_only_requirement_mention_is_not_executable_coverage` to test a true narrative in-body mention (which still correctly does not count) rather than a tag directly above a `def` (which now correctly does). Full backend suite (520 tests) + ruff clean.
- **Deployed via**: CI/CD (`.github/workflows/ci.yml`) on push to `master` — no manual `az acr build`/`az containerapp update` needed.

### 2026-08-24 — Requirement Fidelity Gate: fix every requirement wrongly reporting "no JUnit result" when the real test run times out

- **What changed**: this is a fourth, previously-unaddressed bug in the Requirement Fidelity Gate — but unlike the three prior fixes (all about matching a generated test's *name* back to a requirement id), this one is about the pytest subprocess never finishing at all. `backend/app/deploy_launch/test_execution_service.py`'s hardcoded 120s subprocess timeout was too short for the gate's *real* black-box acceptance tests — genuine HTTP calls against a live deployed mission prototype, one test per approved requirement, which can easily take several minutes for a mission with dozens of requirements. On timeout, the pytest process is killed before it can write any JUnit XML, so every requirement's expected test name is "unobserved" — previously reported as "no JUnit result: `<test name>`" for every single requirement, which reads exactly like the test-name-matching bugs already fixed and sent debugging in the wrong direction. Fixed by (1) making the timeout a real, externalized setting — `GENIE_DEPLOYMENT_TEST_EXECUTION_TIMEOUT_SECONDS` (`Settings.deployment_test_execution_timeout_seconds`, default `300`, up from the old hardcoded `120`) — instead of a hardcoded constant, and (2) adding a `TestExecutionResult.timed_out` flag that `record_fidelity_execution` (`backend/app/services/requirement_fidelity_service.py`) uses to report a clear, honest "Test execution did not finish: `<summary>`" evidence string instead of a misleading per-requirement "no JUnit result".
- **Tests**: `test_run_tests_times_out_on_a_hanging_generated_test` now also asserts `result.timed_out is True`; new regression test `test_execution_incomplete_reports_a_timeout_not_a_name_mismatch_for_every_requirement` (`backend/tests/unit/services/test_requirement_fidelity_service.py`). Full backend suite (523 tests) + ruff clean.
- **Deployed via**: CI/CD (`.github/workflows/ci.yml`) on push to `master` — no manual `az acr build`/`az containerapp update` needed.

## Known gaps / next phases

- CI/CD (`.github/workflows/ci.yml`) runs backend pytest/ruff and frontend typecheck/lint/vitest on every push/PR to `master`, then auto-deploys the public APIM/private Container Apps backend and Static Web App on pushes once both pass.
- End-to-end Playwright coverage (`e2e/`) is scaffolded but not yet fully built out.
- Genie and prototype APIM URLs are callable by non-browser clients without authentication. Exact-origin CORS constrains browsers only; the FastAPI backends are network-private, but authentication must be reintroduced before handling sensitive or multi-user workloads.
- Shared Collaboration Memory currently has no write call sites anywhere in the backend — nothing ever calls `memory_service.shared.write`. The Requirement Discovery Map's main requirements list (which reads from Shared Memory) is therefore likely empty in real usage today; the agentic-workflow qualification check above was deliberately built to read `WorkflowRunResult.step_results` directly instead, so it works independently of this gap.
