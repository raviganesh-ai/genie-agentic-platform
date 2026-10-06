# Deploy log

Every deployment to the shared Azure evaluation environment (backend Container App and/or frontend Static Web App) is recorded here: commit, what changed, and why. This file was previously the bottom of the main `README.md`; it was relocated here so the README stays focused on what Genie is and how to run it. Update this file as part of the same commit that ships the fix/feature, before pushing to `master` triggers [Continuous deployment](./DEPLOYMENT.md#continuous-deployment-github-actions).

### 2026-10-06 — First-party login + APIM subscription key (MSRC unauthenticated-IDOR fix)

- **Incident**: the legitimate public APIM URL itself (not just the direct-ingress bypass fixed earlier the same day, above) let anyone read/act on any session with zero credentials - `get_current_user()` resolved every request to the same deterministic `genie-internal-user` principal, which was a deliberate "intentionally anonymous" design from September 2026 (`530af94`) that assumed only trusted callers would ever reach the gateway. Once the gateway became genuinely public, that assumption broke: `app.services.session_service`'s per-session ownership check compared the same shared identity to itself for every caller, so it could never actually deny anyone.
- **Two independent layers, no Entra ID**: (1) the platform APIM now requires a shared `Ocp-Apim-Subscription-Key` on every call (`subscriptionRequired: true` + a `Microsoft.ApiManagement/service/subscriptions` resource in `infra/platform-private-gateway.bicep`) - closes "unauthenticated from the public internet" immediately, before any request reaches the backend; (2) a new, small first-party login (`app.security.auth_service`, `app.security.token_service`, `app.security.password_hashing` - PBKDF2-HMAC-SHA256 + self-issued/self-verified HS256 bearer tokens via the already-present `PyJWT` dependency, no new dependency, no external identity provider) replaces the stub identity so ownership checks compare real, distinct accounts. Disabled by default (`GENIE_AUTH_ENABLED=false`) so every existing dev/test environment is unaffected; `ProductionSafetyValidator` now fails startup if it is not enabled (with its signing key and user records configured) in production.
- **Frontend**: a new `/login` page and `AuthGate` wrapper (`frontend/src/features/auth/`) gate the whole app behind sign-in only when `GET /auth/status` reports it is required; `httpClient.ts` attaches both the subscription key and the bearer token to every call and bounces back to `/login` on any 401.
- **Verified**: new backend integration tests prove the actual incident is fixed - an unauthenticated request to `/sessions` is rejected with 401, and critically, one logged-in account cannot read a different account's session (403) where it previously could read anyone's. Full suites pass: 931 backend (pytest + ruff), 127 frontend (vitest + tsc + eslint).

### 2026-10-06 — Fix CI/CD branch mismatch (again) + lock backend ingress to the platform APIM only

- **CI/CD was silently not running again**: the repo's real default/only branch is `master` (confirmed via `git branch -a`, `git remote show origin`, and `gh repo view --json defaultBranchRef`), but `.github/workflows/ci.yml` watched `main` - the opposite of the 2026-10-02 fix below, because the branch was renamed again during an intervening repo refresh. Every push since then silently had zero CI/CD runs. Fixed by pointing every trigger/condition back at `master`; also fixed the same stale `main` reference in `docs/DEPLOYMENT.md` and this file's own header note.
- **Security: direct backend ingress bypassed the platform APIM entirely** - the Container App's own public default domain (`<app>.<env-domain>.azurecontainerapps.io`) was reachable from any network that could resolve/route to the Container Apps environment's private endpoint (e.g. a VPN/ExpressRoute-connected corporate network), completely bypassing APIM's CORS, rate-limit, and correlation-ID policy, and - because the backend's `get_current_user()` resolves every request to the same deterministic anonymous principal (see "Authentication" above) - with zero authentication of any kind. Root cause: the `private-endpoints` subnet had no explicit NSG rules, so Azure's default `AllowVnetInBound` rule let anything reaching that subnet through. Fixed by adding an explicit NSG on the `private-endpoints` subnet that allows inbound HTTPS only from the `api-management` subnet and denies everything else, both applied live and codified in `infra/modules/virtual-network.bicep` for any future full redeploy. `scripts/deploy_backend.ps1` now also asserts after every deployment that the container app's direct FQDN is unreachable, alongside the existing APIM-reachability assertion, so a future regression fails the deploy instead of shipping silently.
- **Verified live**: `https://genie-backend-corporate-4rq73swmt3glk-apim.azure-api.net/health/live` still returns `200 {"status":"ok"}`; the container app's direct FQDN now fails to connect.

### 2026-10-02 — Fix CI/CD never triggering (branch mismatch) + Deploy & Launch stage grouping + Governance page

- **Real, previously-undiscovered CI/CD bug**: `.github/workflows/ci.yml`'s `on: push`/`pull_request` triggers (and all three deploy jobs' `if:` conditions) watched branch `master`, but this repo's actual default/only branch has always been `main` (confirmed via `git branch -a` and `git remote show origin`). `gh run list` showed **zero workflow runs in the repo's history** - every "committed and deployed" step this project has ever gone through was a manual `az acr build`/`az containerapp update`/`swa deploy` command, never the documented CI/CD pipeline. Fixed by pointing every trigger/condition at `main`; this commit's own push is the first one the fixed workflow actually picks up.
- **Deploy & Launch "Mission Progress" simplified to 9 stages**: the real, always-executed 12-step `DEPLOYMENT_STEP_ORDER` pipeline is unchanged server-side, but `DeployLaunchPage.tsx` now groups a handful of internal/technical steps into their neighboring user-meaningful stage for display (contract validation into "Generate Access Policy & Least Access", schema validation into "Provision Data Layer", test generation/execution into "Validate Requirements") - matching the simple, named list the user asked for, with zero functionality removed. Retrying a failed stage resumes from that stage's own first incomplete real step.
- **"Governance" sidebar page was empty for non-modernization missions**: `PhaseTrackingPage.tsx` only ever rendered modernization-specific phase/task data, so a `discover_requirements`/prototype mission's Governance page looked entirely blank ("Governance was skipped"). It now renders the already-built but previously-unused `useGovernanceTrace` hook's real governance events, approval checkpoints (with inline Approve/Reject), and an overall compliance badge for every mission kind; the modernization phase/task tracker still renders additively once a session actually has tracked phases.
- **Tests**: updated `deploy_launch.test.tsx` (2 new tests for stage grouping/targeted retry, 1 existing test rewritten for the new visible stage names; 12/12 pass); new `phase_tracking_page.test.tsx` (3 tests); full frontend suite 91/91 passed, typecheck/lint clean.
- **Deployed via**: manual `npm run build` + `@azure/static-web-apps-cli deploy` (frontend-only change; no backend changes in this entry).

### 2026-09-29 — Orchestrator prompts must surface specialist stage failures instead of silently swallowing them

- **Incident**: the live `sampl-989d7318` blind-MQM translation-QA mission was given a valid, correctly-formatted uploaded package and its generated Orchestrator ran to completion, but returned a well-formed, entirely empty result (0 documents processed, 0 experiments, null system winner, all 7 required languages reported as "missing"). The existing COVERAGE VALIDATION contract faithfully reported the gap, but could not distinguish "the input genuinely had none of the required items" from "an upstream specialist agent's call raised and was silently caught".
- **Prompt fix, not a patch to the deployed prototype**: both `build-generation-v1` and `build-generation-component-v1` gained a STAGE FAILURE TRANSPARENCY instruction requiring generated Orchestrators to never wrap a specialist agent's call in a broad try/except that swallows an exception into an empty/default result. Any caught specialist-agent failure must be captured into the returned `dict` as `"stage_errors"`, narrated via `on_progress` so the live UI shows it, and must set `"success": false` — so a broken pipeline is never indistinguishable from a plain empty-input success. This applies to all future generated missions; the already-deployed Sampl prototype instance itself was left unmodified.
- **Verification**: a new regression test (`test_orchestrator_prompts_require_surfacing_specialist_stage_failures`) pins the presence of this instruction and its required JSON keys in both prompts; the full prompt-contract and registry test suites (65 tests) pass.

### 2026-09-29 — Connect generated UI and backend by construction

- **Sample integration fix**: the Sample prototype UI submitted shorthand JSON fields such as `runId`, while its generated orchestrator read different names such as `runIdOutputDirectoryName`; the backend therefore failed before its first stream event and the UI reported that it returned no output.
- **No new gate**: UI generation now receives the already-generated orchestrator source as authoritative context and uses its exact request keys directly. Genie does not add a new validation stage or withhold a prototype over component naming.
- **Prototype stays useful**: if generated pipeline glue still cannot accept a request, the mission backend records the error and uses its existing Orchestrator Agent fallback with the full uploaded sample content instead of returning an empty stream.
- **Portable regression coverage**: the structured-upload fallback test uses the generated backend's configured working directory with isolated temporary storage on both Linux CI and Windows development hosts.

### 2026-09-29 — Fix generated prototype frontend manifests

- **Valid package metadata**: generated prototype frontends now serialize `package.json` from structured data, preserving the quoted nonblocking Impeccable command as valid JSON.
- **Observed failure fixed**: SampleDemo ACR runs `chdg` through `chdk` failed at `npm install` with `EJSONPARSE`; the generated application code was never reached.

### 2026-09-28 — Keep prototyping stages moving

- **Prototype-first flow**: generated acceptance-test formatting, missing coverage, weak evidence, test failures, timeouts, and exhausted fidelity repair no longer withhold an already deployed prototype. Genie launches it with explicit validation gaps.
- **Repair and truth preserved**: requirement and goal checks still run, trigger bounded regeneration, and cannot falsely become passing evidence when a generated suite uses mocks or fails real-action validation.
- **Advisory visual lint**: Impeccable still inspects every generated frontend and reports findings, but styling guidance no longer stops a buildable prototype from deploying.
- **Best-effort progress narration**: missing exact specialist hand-off phrases may reduce Agent Pipeline animation detail, but no longer blocks otherwise runnable generated code.
- **Real blockers only**: unreadable input, unavailable Foundry execution, denied authorization or governance decisions, generated code that cannot run, high/critical security findings, and failed Azure provisioning remain blocking because no safe working prototype exists.

### 2026-09-28 — Judge the prototype, not architecture formatting

- **Unblocked generation**: Architecture and generated source no longer fail merely because their text omits one or more literal approved `REQ-*` identifiers, and component generation no longer repeats that precheck before invoking the Build Agent.
- **Intelligent responsibility split**: approved requirements and goals continue to guide architecture and generation, while semantic fidelity is established by the generated implementation and real deployed outcome evidence rather than ID repetition in an intermediate document.
- **Meaningful safeguards preserved**: incomplete or failed generated components remain blocked; approved-goal and supporting-requirement gaps remain visible and drive bounded repair.

### 2026-09-28 — Make approved goals non-negotiable

- **Goal completeness**: every approved goal now requires its own dedicated end-to-end test and passing evidence, even when aggregate requirement coverage has already met the configured threshold. The threshold remains unchanged for requirement-level coverage.
- **No silent gaps**: a goal without a corresponding executable, passing test is a visible validation gap carried into Launch, never hidden by an otherwise-passing aggregate percentage.

### 2026-09-28 — Prove the approved mission goal end to end

- **Direct evidence**: Requirement Validation now also asserts the approved mission goal's own observable outcome against the deployed prototype, not only per-requirement test pass/fail.
- **Composable with repair**: a failing goal assertion participates in the same bounded automatic regenerate-and-redeploy loop as any other fidelity gap.

### 2026-09-28 — Reject generated request-schema drift before deployment

- **Earlier failure surface**: a generated frontend/backend request-schema mismatch is now caught before Azure provisioning rather than surfacing only once live traffic hits the deployed prototype.
- **Bounded repair**: detected drift triggers the same regenerate-and-redeploy path used for other build-materialization failures.

### 2026-09-28 — Keep generated Container App names Azure-valid

- **Naming fix**: generated Container App names are validated and, when necessary, deterministically shortened/hashed to satisfy Azure's naming constraints before any deployment call is made, instead of failing mid-provisioning on a rejected name.

### 2026-09-28 — Prevent false generated-build repair loops

- **Root cause**: a transient evaluation error in the bounded repair loop could be misclassified as a genuine build defect, triggering an unnecessary regenerate-and-redeploy cycle.
- **Fix**: repair triggering now distinguishes a real, reproducible build/fidelity failure from a transient evaluation error before consuming a repair attempt.

### 2026-09-28 — Keep generated POCs testable with representative samples

- **Fix**: generated prototypes that expect sample/representative input data now receive it as part of materialization, so their own generated acceptance tests exercise realistic data rather than an empty or placeholder payload.

### 2026-09-27 — Preserve workflow handoffs across revisions

- **Fix**: durable workflow step outputs and their Shared Collaboration Memory handoffs survive a backend revision rollout, preventing an in-flight mission from losing context mid-pipeline during a deploy.

### 2026-09-27 — Restore architecture fidelity gate state

- **Fix**: corrected a regression where the Architecture Fidelity Gate's own tracked state could be lost or reset incorrectly across a session, re-establishing accurate pass/fail history for architecture review.

### 2026-09-27 — Restore the September 13 application baseline

- **Fix**: reverted an intermediate regression back to the last known-good application baseline from 2026-09-13 after discovering it had broken previously-working behavior.

### 2026-09-13 — Complete Discovery PDF export and recoverable Azure pricing

- **Persistence**: production uses the existing managed-identity Cosmos document store with a dedicated logical `discovery-cases` partition and `discovery-case` record type. Local development and tests use an isolated in-memory repository. Discovery cases survive backend restarts without provisioning another physical Cosmos container.
- **Foundry state machine**: authenticated transition APIs now run persona extraction and deep analysis through `requirements-analyst`, and consented recommendations plus probable-solution generation through `architecture-designer`. Strict JSON schemas, ordered durable states, revision-safe reanalysis, and retryable failure states fail closed on malformed agent output or invalid transitions.
- **Consent and governance**: skipped questions first become `recommendation_offered`; no recommendation is generated until the user explicitly accepts. Persona, gap/Q&A, and solution artifacts are revisioned in Shared Collaboration Memory with normal policy and governance events. Deleting an abandoned case performs a governed, prefix-scoped cascade without touching unrelated session memory.
- **Real pricing and diagrams**: pricing assumptions are resolved against the public Azure Retail Prices API, with partial/unavailable coverage shown when records cannot be verified and no synthetic fallback. Architecture options render in React Flow using a local allowlist copied from Microsoft's official Azure Architecture Icons V24 pack.
- **Direct Build handoff**: selecting a solution starts `discovery-build-workflow`, which reuses the production Orchestrator and Build Agent with the selected requirements and architecture. Deploy & Launch falls back to those persisted Build inputs, so it does not rerun or forge Requirements/Architecture stages.
- **Single-page UI**: the Landing page now separates Prototype and Discovery entry points. `/discovery` combines uploads, persona selection, pain points/gaps, batch or interactive Q&A, recommendation consent, solution comparison, pricing evidence, architecture diagrams, resumability, deletion, and prototype handoff in one responsive Fluent UI workspace.
- **Verification**: focused tests cover repository/API lifecycle, transition ordering, recommendation consent, durable reload, no-fabrication pricing, Shared Memory behavior, Deploy & Launch compatibility, and the progressive frontend state; the full backend suite passes with 591 tests.

### 2026-09-13 — Architecture-derived Azure solution cost

- **What changed**: the Discovery architecture's own selected Azure services now directly drive the recoverable Azure Retail Prices lookups used for the solution's cost estimate, rather than a separate, loosely-coupled pricing pass.

### 2026-09-13 — Professional Azure service architecture

- **What changed**: Discovery's rendered architecture diagrams use the official Microsoft Azure Architecture Icons set consistently across every generated solution option.

### 2026-09-13 — Separate gaps and assumptions with bounded solution recovery

- **What changed**: Discovery now distinguishes genuine evidence gaps from explicit assumptions in its own output, and a failed/partial solution-generation attempt recovers within a bounded number of attempts instead of surfacing an opaque failure.

### 2026-09-13 — Discovery question progress and solution response compatibility

- **What changed**: the Discovery clarification flow surfaces real progress through its question set, and solution-generation responses remain backward compatible with earlier persisted session shapes.

### 2026-09-13 — Adaptive Discovery clarification

- **What changed**: Discovery's clarification question flow adapts the next question to prior answers instead of asking a fixed, non-adaptive sequence.

### 2026-09-12 — Discovery deep-dive response resilience

- **What changed**: Discovery's deep-dive analysis step tolerates and recovers from a malformed or partial agent response instead of failing the whole case.

### 2026-09-12 — Multimodal, evidence-grounded Discovery

- **What changed**: Discovery's evidence ingestion accepts multimodal input (documents and images) and grounds its persona/requirement extraction directly in that evidence rather than general model knowledge.

### 2026-09-12 — Discovery: evidence readiness and file removal

- **What changed**: Discovery's upload stage reports real per-file readiness status and lets the user remove an uploaded file before analysis proceeds.

### 2026-09-12 — Discovery: real DOCX extraction

- **What changed**: Discovery's evidence ingestion extracts real text content from uploaded `.docx` files instead of treating them as opaque binary attachments.

### 2026-09-12 — Discovery: large evidence upload resilience

- **What changed**: Discovery's upload pipeline handles larger evidence files reliably instead of failing or timing out on them.

### 2026-09-11 — Discovery upload usability

- **What changed**: usability improvements to Discovery's upload experience (progress, feedback, and error clarity for evidence files).

### 2026-09-11 — Discovery: complete persona-to-prototype experience

- **What changed**: Discovery's end-to-end flow — from persona extraction through recommendation to prototype handoff — was completed as one coherent experience in this entry (superseded/expanded by the 2026-09-13 PDF export and pricing entry above).

### 2026-09-11 — Fidelity launches directly; generated UIs use Impeccable

- **Direct launch sequence**: a Deploy & Launch run proceeds directly from Requirement Validation to Launch. This release originally required the configured evidence threshold; the newer prototype-first behavior documented above supersedes that restriction and launches with visible validation gaps. `run-security-scan` remains a legacy deserialization value for historical persisted runs.
- **Security posture**: the earlier independent Security Assessment Agent review remains part of build governance. Runtime readiness and black-box acceptance tests still fail closed before launch; this change removes only the redundant post-fidelity static-scan gate from the provisioning sequence.
- **Professional prototype UI decision**: generated mission UIs follow the [Impeccable](https://impeccable.style/) design methodology. Both Build Agent generation paths and targeted UI regeneration carry the design contract, and every generated frontend pins `impeccable@3.6.0` and runs `impeccable detect MissionApp.tsx src/` before Vite builds it. The newer prototype-first behavior documented above makes detector findings advisory.
- **Verification**: focused pipeline tests prove that an injected blocking scanner is never called and that the final two steps are Requirement Validation → Launch. Frontend type checking and build verify the displayed mission trace matches the backend order.

### 2026-09-11 — Mission agents use the user's approved model; the Build Agent gets a real model catalog

- **Problem**: `MissionAgentProvisioningService` always provisioned every mission's specialist and orchestrator agents on `settings.default_llm`, silently ignoring whatever model the user actually selected on Genie's own Landing page for that session. Separately, the Build Agent's prompts never received any real Foundry model list, so a mission whose requirements called for a model-selection dropdown could only invent fake, ungrounded model-ID strings (`"gpt-4o-primary"`, `"claude-opus-primary"`) - the exact strings observed live in DerekPoC.
- **Model threading**: the Landing page's selected model is already recorded on the discovery workflow run as `agent_scope_id = "model:<ref>"` (see `app.api.workflows.run_workflow`). `DeploymentPipelineService` now resolves that value (`_approved_model_deployment_ref`) and forwards it into `MissionAgentProvisioningService.provision(..., model_deployment_ref=...)`, which uses it in place of the platform default for every agent it creates in Foundry. Runs with no selected model (no `model:` scope) fall back to the platform default exactly as before.
- **Real model catalog for generation**: the `build-solution` workflow step gained a new `available_models` variable, resolved via a new `model-catalog` `variable_sources` kind in `WorkflowStepExecutor` that calls the same `ModelCatalogService` (real ARM-backed Foundry deployment listing) the Landing page's own model picker uses. `build-generation-v1` and `build-generation-component-v1` now receive this real list and are explicitly instructed to populate any generated model-selection UI/config field only from it - and to add no such field at all when no requirement calls for one.
- **Verification**: added unit tests for the provisioning override (default vs. approved-model), the pipeline's scope-parsing/wiring end to end, and the new `model-catalog` variable resolution (present and absent service). Full backend suite passes with 580 tests; Ruff clean.

### 2026-09-11 — Generated UI submit payloads are structurally flat

- **Incident**: after the upload filename gate was bypassed in the stale DerekPoC deployment, its mission request failed with `Primary strong-model identifier is required (REQ-011)`. Live bundle inspection proved the UI submitted `evaluation_config.primary_model_id`, while the generated orchestrator read the required flat key with `config.get("primary_model_id")`. The field was present, but hidden from the orchestrator by UI-only grouping objects.
- **Enforced invariant**: build materialization now extracts the object literal serialized and passed directly or through a local variable to the UI's `onSubmit(...)`, parses its top-level entries with balanced brace and quoted-string handling, and rejects submit payloads whose field values are nested objects. Flat payloads, array-valued fields, and unrelated serialized objects elsewhere in the generated component remain valid.
- **Automatic recovery**: this deterministic failure uses the existing bounded `build-solution` repair path before Foundry or Azure provisioning. The Build Agent receives the exact structural contract violation and must regenerate the UI/orchestrator pair with identical flat keys; exhausted retries fail closed instead of deploying another mismatched prototype.
- **Verification**: focused tests reproduce the live DerekPoC nested payload and prove an equivalent flat payload materializes successfully. The full backend suite passes with 573 tests.

### 2026-09-11 — Generated upload validation is filename-independent

- **Incident**: DerekPoC parsed an uploaded JSON package successfully and displayed its corpus preview, but kept **Confirm & start run** disabled. Its generated UI compared the end-user-controlled filename to the example `blind_mqm_n30_package.json`, stored the mismatch as a warning, and included any warning in the button's disabled condition. The existing Build Agent prompt already prohibited this exact behavior, proving that a prompt-only rule was insufficient.
- **Enforced invariant**: build materialization now rejects generated TSX that compares an uploaded file's name to an exact filename-like literal.
- **Automatic recovery**: deterministic materialization failures enter the existing bounded `build-solution` regeneration path before Foundry agents or Azure prototype infrastructure are provisioned. Genie supplies the precise validation evidence to the Build Agent, retries from provisioning with the repaired build, and fails closed after the configured repair budget instead of deploying an unusable form.
- **Verification**: focused tests reproduce the exact filename-gated TSX, guard against false positives, and prove the deployment pipeline regenerates the invalid UI and completes from the repaired build.

### 2026-09-11 — End-to-end generated prototype fidelity recovery

- **Incident**: the rebuilt DerekPoC passed APIM runtime readiness and rendered its frontend, but its generated flat request payload did not match the orchestrator contract. The generated backend caught the resulting structured orchestration exception and returned conversational fallback text as HTTP 200, so the broken mission path looked successful. Its deployed acceptance tests then collected zero tests because a relative workspace path was appended twice. After that fidelity failure, automatic repair could not resume `build-solution`: durable workflow step outputs had survived the Genie rollout, but their required Shared Collaboration Memory records had not.
- **Fail-closed mission contract**: generated React and orchestrator prompts now require the same exact flat JSON payload. Structured mission requests propagate orchestration errors as failed HTTP responses; conversational fallback remains available only for explicit plain-text chat. Acceptance-test contracts require the exact deployed UI payload and mission-specific assertions rather than accepting any nonempty HTTP 200 response.
- **Deterministic test and repair recovery**: test execution resolves its workspace root before materializing and invoking pytest, preventing relative paths from being duplicated. Before an automatic fidelity repair, Genie restores only missing `analyze-requirements` and `design-architecture` Shared Memory keys from their completed durable workflow step results. Those writes use the Genie Orchestrator identity, approved lineage, evidence references, policy enforcement, and normal governance events; existing memory is never overwritten and an absent durable output fails the repair closed.
- **Verification**: focused materializer, prompt-contract, test-execution, and deployment-pipeline tests cover structured failure propagation, exact schema parity, production-relative pytest paths, and governed memory restoration before repair resume.

### 2026-09-11 — Generated prototype runtime readiness gate

- **Incident**: DerekPoC provisioned successfully but its first mission request returned no result. The generated orchestrator constructed Agent Framework's `FoundryAgent` with an invalid positional argument; provisioning and basic HTTP health checks therefore reported success even though the generated application could not initialize.
- **Deterministic runtime**: Genie now owns the Foundry SDK boundary in `mission_foundry_runtime.py`. Generated mission code uses `MissionFoundryAgent`, while materialization rewrites legacy direct imports to that adapter. The adapter resolves the latest provisioned agent version, uses keyword-only SDK construction, serializes typed payloads, and returns dictionary-compatible results. Build Agent contracts prohibit generated raw SDK construction.
- **Fail-closed launch**: generated `/health/ready` imports and constructs the real orchestrator, and backend deployment polls that endpoint through the prototype's dedicated APIM gateway. A missing endpoint, incompatible generated constructor, adapter import error, or non-success response now stops deployment before frontend launch rather than publishing a nonfunctional prototype. Existing black-box acceptance tests then exercise the deployed backend and frontend before the prototype is marked complete.
- **Verification**: deploy-launch tests execute the emitted adapter against SDK-compatible fakes, validate generated import normalization and orchestrator readiness, and require backend deployment to pass the gateway readiness check.

### 2026-09-11 — Valid public prototype Container Apps environment payload

- **Incident**: DerekPoC deployment `80f64d5a` successfully provisioned its mission identity, Foundry agents, private backend environment, APIM, and backend Container App, then failed at `deploy-frontend-app`. `azure-mgmt-appcontainers` flattened an otherwise empty `ManagedEnvironment` model to only `location` and `tags`, while the Azure control plane requires every managed-environment request body to contain `properties`.
- **Fix**: public prototype environments now explicitly set `zone_redundant=False`. This preserves the intended single-region Consumption environment while forcing the typed SDK model to serialize `properties: { zoneRedundant: false }`; private backend environments already serialize a populated `properties.vnetConfiguration` object.
- **Verification**: the focused deployment-service suite asserts the exact payload produced by the installed Azure SDK serializer, preventing a future model or SDK refactor from silently dropping `properties`. The failed partial deployment is cleaned through Genie's owned prototype cleanup path before a fresh retry.

### 2026-09-11 — Durable workflow checkpoints across backend rollouts

- **Incident**: a corrected prototype deployment rollout restarted Genie after DerekPoC's approved requirements, architecture, and generated build had completed. Deployment-run inventory rehydrated from Cosmos, but the source `WorkflowRunResult` still used `InMemoryWorkflowRunRepository`; the next fresh Deploy & Launch run therefore failed at `generate-access-policy` with `No workflow run ... found.` before provisioning resources.
- **Fix**: production now injects `CosmosWorkflowRunRepository` and `CosmosUploadRepository` through the existing managed-identity `CosmosDocumentStore`. Every workflow wave already called the repository checkpoint hook, so no orchestration behavior changed; complete step-result snapshots, upload metadata, and extracted transcript/document text now survive process and revision restarts and remain queryable by run id or session.
- **Verification**: focused Cosmos repository tests recreate repository instances to simulate a backend restart, then verify exact typed workflow recovery and exact upload-text recovery through both direct lookup and session listing. Local development and tests continue to use the in-memory repositories unless `GENIE_MEMORY_STORE_BACKEND=cosmos_db` is configured.

### 2026-09-11 — Private Genie backend behind platform API Management

- **Network boundary**: Standard v2 APIM remains the anonymous public API edge, while outbound VNet integration resolves the existing Container App FQDN through a Container Apps environment private endpoint and `privatelink.eastus2.azurecontainerapps.io`. Environment public network access is disabled after the gateway route is proven.
- **Fail-closed cutover**: `deploy_platform_gateway.ps1` verifies APIM and prepares private DNS before changing public access, then disables public access through the `2025-10-02-preview` managed-environment ARM API before creating the private endpoint as required by Azure. This pinned ARM PATCH exposes `publicNetworkAccess` without depending on the preview Container Apps CLI flag, which is unavailable on some hosted runners. The script retries Azure's transient `ManagedEnvironmentNotHealthy` endpoint response, preserves an existing succeeded endpoint rather than resetting its connection state, normalizes the Azure CLI private-endpoint response shape when checking approval, and resumes a closed-but-incomplete cutover before its next APIM probe. Rejected or disconnected endpoints still fail immediately. It verifies APIM again after cutover and fails if direct Container Apps ingress still returns a successful response. An endpoint failure leaves public ingress disabled. `deploy_backend.ps1` verifies every revision only through APIM and anonymous `GET /sessions`, never direct Container Apps ingress.
- **CI and least privilege**: `prepare-gateway` emits the verified APIM URL directly to the frontend build; only after that deployment does `deploy-backend` finalize the private-network cutover, avoiding an outage against the old direct-backend bundle. The GitHub OIDC principal receives the resource-group-scoped `Genie Platform Gateway Deployer` custom role, including the VNet join action required by private DNS links, with no delete or RBAC-management actions.
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
- **Deployment**: CI/CD passes the already-built commit-pinned gateway image into Genie's runtime through `scripts/deploy_authentication_gateway.ps1`; no manual application deploy is used. The runtime managed identity requires tenant-admin-consented Graph application permissions documented in [Authentication](../README.md#authentication).
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
- **Root cause**: pytest always reports a `@pytest.mark.parametrize`-decorated test's real JUnit case name as `"<def name>[<param id>]"`, never the bare `def` name alone. Any requirement whose generated acceptance test used `parametrize` (a natural way to test "cover N languages/items") always showed `"no JUnit result"` evidence in the Requirement Fidelity Gate, even though the test genuinely ran and passed.
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
