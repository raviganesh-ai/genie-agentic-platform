# Genie Development Instructions

## Product

Application Name: Genie

Tagline: Agentic Experience Center

Genie is an Azure-native Agentic AI solutioning platform that ingests transcripts, recordings, documents, and customer context and transforms them into an interactive AI Mission Control experience.

Users must be able to observe requirement discovery, agent collaboration, architecture design, governance decisions, memory updates, approvals, and final outputs in real time.

This is NOT a report generator.

---

# Architecture Principles

Use:

- Python 3.12+
- FastAPI
- Pydantic v2
- React + TypeScript
- Fluent UI
- Azure AI Foundry
- azure-ai-projects SDK
- Azure AI Search
- Azure Storage
- Azure Key Vault
- Azure Monitor
- Application Insights
- Microsoft Entra ID
- Managed Identity
- Azure Container Apps or App Service
- Cosmos DB or Azure SQL

Follow Clean Architecture.

Use dependency injection.

Use strongly typed models.

Use async patterns throughout backend services.

---

# Production Agent Rules

Production agents must execute through Azure AI Foundry.

Use AzureAgentGateway as the only production execution path.

Production mode must never:

- Execute local agents
- Execute mock agents
- Return static AI responses
- Return demo outputs
- Use synthetic production data
- Fall back to local execution

If Azure AI Foundry is unavailable:

- Fail closed
- Record governance trace
- Optionally invoke debugging workflow
- Never invoke local fallback agents

---

# Configuration Rules

All configuration must be externalized.

Never hardcode:

- Agent definitions
- Workflow definitions
- Prompt templates
- Azure endpoints
- Deployment names
- Subscription IDs
- Tenant IDs
- Customer names
- Customer data
- API keys
- Secrets
- Connection strings
- Model deployment names
- Expected AI outputs

Store configuration under:

config/agents
config/prompts
config/workflows
config/policies

---

# Security Requirements

Use Microsoft Entra ID.

Use managed identity.

Use least privilege.

Use Key Vault for secrets.

Validate:

- User authorization
- Agent authorization
- Memory access authorization
- Tool access authorization
- Data access authorization

Denied access must emit governance events.

Errors, logs, and health endpoints must never expose secrets.

---

# Memory Architecture

Implement three memory tiers.

## Personal Agent Memory

Stores:

- Observations
- Work products
- Intermediate summaries

Accessible only by owning agent unless policy allows.

## Shared Collaboration Memory

Stores:

- Goals
- Constraints
- Assumptions
- Risks
- Findings
- Approved artifacts

Every read and write emits governance events.

## Enterprise Knowledge Memory

Stores:

- Industry patterns
- Reference architectures
- Reusable best practices

Customer data must never be promoted without approval.

Use Azure AI Search.

---

# Governance Requirements

Create interface:

Agent365GovernanceProvider

Do not invent undocumented APIs.

For development only:

LocalGovernanceTraceProvider

Governance must support:

- Agent registration
- Agent execution events
- Agent communication events
- Memory read events
- Memory write events
- Tool events
- Policy checks
- Approvals
- Decision lineage
- Session replay
- Trace graph

Production mode must fail if required governance services are unavailable.

---

# Validation Requirements

Run validation before application starts.

Required validators:

- RuntimeVersionValidator
- ConfigurationValidator
- ProviderModeValidator
- ProductionSafetyValidator
- AgentRegistryValidator
- WorkflowRegistryValidator
- PromptTemplateValidator
- MemoryPolicyValidator
- GovernanceProviderValidator
- NoHardcodingValidator

Application must not accept traffic until validation passes.

---

# Fail Closed Requirements

Startup must fail when:

- Foundry endpoint missing
- Foundry endpoint invalid
- Authentication fails
- Managed identity unavailable
- Mock provider enabled in production
- Governance provider missing
- Memory provider missing
- Agent registry missing
- Workflow registry missing
- Policies missing
- Prompt templates missing

Do not implement fallback behavior.

---

# Backend Standards

Use:

- FastAPI
- Async endpoints
- Pydantic models
- Structured JSON logging
- Correlation IDs
- OpenTelemetry hooks

Modules:

- sessions
- uploads
- transcription
- agents
- orchestration
- memory
- governance
- requirements
- architecture
- workshop
- outputs
- security
- validation
- debugging
- health

---

# Frontend Standards

Use:

- React
- TypeScript
- Fluent UI
- React Flow
- Recharts

UI must include a simple, linear primary flow:

- Upload (ingest transcripts/recordings/documents)
- Requirements (gathered/extracted from the uploaded material)
- Architecture (the solution architecture to build)
- UI & Agent Design (each requirement gets its own generated UI and its
  own dedicated agentic workflow)
- Governance (approvals/oversight)
- Deploy & Launch (provision the per-requirement agents/UI and mint a
  customer-facing launch link)

Do not use hardcoded demo data in production.

---

# Testing Requirements

Every feature requires tests.

Backend:

- Unit tests
- Integration tests

Frontend:

- Component tests
- Type checking

End-to-end:

- Playwright

Production safety tests are mandatory.

---

# Coding Instructions

Generate real code.

Avoid placeholders.

Avoid TODO comments in production paths.

Avoid pseudocode.

All interfaces should be strongly typed.

All services should be testable.

All production dependencies should be injected.

Prefer composition over inheritance.

Document assumptions in comments when SDK behavior is uncertain.

Never invent external APIs.

If unsure, create an abstraction layer and isolate assumptions behind interfaces.

---

# Delivery Approach

Implement in phases.

Phase 1:
- Repository foundation
- Configuration
- Validators
- FastAPI startup
- Tests

Phase 2:
- Agent Registry
- Workflow Registry
- Prompt Registry

Phase 3:
- AzureAgentGateway
- Foundry integration

Phase 4:
- Memory services

Phase 5:
- Governance services

Phase 6:
- Orchestration

Phase 7:
- APIs

Phase 8:
- React UI

Phase 9:
- Testing

Phase 10:
- Azure deployment

Generate code only for the requested phase.
Do not skip ahead.
