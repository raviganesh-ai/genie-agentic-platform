"""Deploy & Launch pipeline orchestration.

``DeploymentPipelineService`` is the real, deterministic, code-driven glue
that executes every step in ``DEPLOYMENT_STEP_ORDER`` (see
``app.deploy_launch.models``) against real Azure SDKs - or their Null/local
equivalents when the required settings are not configured, mirroring
``AzureAgentGateway``/``LocalAgentGateway`` - never fabricating a step's
result. This is explicitly NOT an LLM-driven workflow step: it is invoked
only after the ``solution-discovery-workflow`` has already produced an
approved architecture (``design-architecture``) and generated build
(``build-solution``). Test generation happens here too, as this
pipeline's own ``generate-test-suite`` step: it calls the Test Generation
Agent directly (``AgentOrchestrator.execute_agent`` - the same
outside-any-workflow-step execution path Workshop's per-component
"Regenerate" action already uses) against the approved requirements and
real deployed mission URLs. ``execute-test-suite`` exercises that deployed
prototype before Launch; failures trigger a bounded fresh build regeneration,
redeployment, and retest, and the pipeline fails closed if 100% requirement
coverage and passing evidence are not achieved before the repair budget is
exhausted.

Genie's Deploy & Launch stage has exactly one gate: the human clicking
Start. There is no separate approval-checkpoint request/decide dance -
``start()`` runs the upstream self-heal (see
``_ensure_upstream_steps_completed``) and then immediately kicks off the
pipeline.

``start()`` returns as soon as the run is created (status
``running``) - the nine steps themselves execute in a background asyncio
task, since real Azure agent/backend/frontend deployments plus a real test
run and security scan can legitimately take far longer than any single HTTP
request should block for. Callers (the API layer, the frontend) always
observe progress by polling ``get_run``/``list_runs_for_session`` (or the
live ``WorkflowEventBus`` stream) - never by relying on ``start()`` itself
to have finished the work.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

from app.agents.gateway import get_enabled_agent
from app.config.settings import Settings
from app.deploy_launch.access_policy_service import AccessPolicyService
from app.deploy_launch.backend_deployment_service import (
    BackendDeploymentService,
    NullBackendDeploymentService,
)
from app.deploy_launch.code_materializer import (
    MaterializedBuild,
    MaterializedCodeError,
    generate_backend_service_scaffold,
    materialize_build,
)
from app.deploy_launch.container_app_frontend_deployment_service import (
    ContainerAppFrontendDeploymentService,
    NullContainerAppFrontendDeploymentService,
)
from app.deploy_launch.mission_agent_provisioning_service import (
    MissionAgentProvisioningService,
    NullMissionAgentProvisioningService,
    ProvisionedMissionAgent,
)
from app.deploy_launch.mission_identity_service import (
    MissionIdentityService,
    NullMissionIdentityService,
)
from app.deploy_launch.models import (
    DEPLOYMENT_STEP_NAMES,
    DEPLOYMENT_STEP_ORDER,
    DeploymentPipelineRun,
    DeploymentStepId,
    DeploymentStepResult,
    ProvisionedAgentStatus,
)
from app.deploy_launch.resource_naming import prototype_resource_group_name
from app.deploy_launch.security_scan_service import SecurityScanService
from app.deploy_launch.test_execution_service import (
    TestExecutionService,
    extract_test_modules,
    has_pytest_discoverable_tests,
    validate_real_action_tests,
)
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.models.workflow_stream_models import WorkflowStreamEvent, WorkflowStreamEventType
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.repositories.deployment_run_repository import (
    DeploymentRunRepository,
    InMemoryDeploymentRunRepository,
)
from app.services.requirement_fidelity_service import (
    create_fidelity_report,
    record_fidelity_execution,
    record_test_coverage,
)
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = [
    "DeploymentPipelineService",
    "DeploymentPipelineStepFailedError",
    "create_deployment_pipeline_service",
]

_PIPELINE_AGENT_ID: Final = "deploy-launch-pipeline"
_logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "mission"


_APPROVED_MODEL_SCOPE_PREFIX: Final = "model:"


def _approved_model_deployment_ref(run: WorkflowRunResult) -> str | None:
    """Resolves the model the user actually selected on Genie's Landing page
    for this discovery run (see ``app.api.workflows.run_workflow``'s
    ``agent_scope_id = f"model:{model_deployment_ref}"``), so mission agent
    provisioning uses that approved model instead of always the platform
    default. Returns ``None`` for runs with no such scope (or a scope used
    for something other than a model override, e.g. a requirement group)."""

    scope_id = run.agent_scope_id or ""
    if not scope_id.startswith(_APPROVED_MODEL_SCOPE_PREFIX):
        return None
    ref = scope_id[len(_APPROVED_MODEL_SCOPE_PREFIX) :].strip()
    return ref or None


_FRONTEND_INDEX_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{mission_title}</title>
    <link rel="icon" href="data:,">
  <script src="runtime-config.js"></script>
</head>
<body>
  <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
</body>
</html>
"""

_FRONTEND_PACKAGE_JSON = """{
    "private": true,
    "type": "module",
    "scripts": {"build": "npm run design:check && vite build", "design:check": "impeccable detect MissionApp.tsx src/"},
    "dependencies": {"react": "18.3.1", "react-dom": "18.3.1"},
    "devDependencies": {"@vitejs/plugin-react": "4.3.4", "@types/react": "18.3.18", "@types/react-dom": "18.3.5", "impeccable": "3.6.0", "typescript": "5.7.2", "vite": "6.4.3"}
}
"""

_FRONTEND_TSCONFIG_JSON = """{
    "compilerOptions": {
        "target": "ES2020",
        "useDefineForClassFields": true,
        "lib": ["ES2020", "DOM", "DOM.Iterable"],
        "allowJs": false,
        "skipLibCheck": true,
        "esModuleInterop": true,
        "allowSyntheticDefaultImports": true,
        "strict": false,
        "forceConsistentCasingInFileNames": true,
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "resolveJsonModule": true,
        "isolatedModules": true,
        "noEmit": true,
        "jsx": "react-jsx"
    },
    "include": ["src"]
}
"""

_FRONTEND_VITE_CONFIG = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({ plugins: [react()] });
"""

# Every generated mission ships one deterministic visual system. The Build
# Agent supplies semantic markup and mission-specific controls; this shell
# owns palette, typography, spacing, surfaces, states, and motion so generated
# JSX cannot create a second visual language inside the prototype.
_FRONTEND_STYLES_CSS = """/* Genie Mission Prototype design system. */
:root {
    color-scheme: light;
    --genie-canvas: #eef2f7;
    --genie-surface: #ffffff;
    --genie-surface-subtle: #f7f9fc;
    --genie-text: #172033;
    --genie-muted: #526176;
    --genie-border: #d7dee8;
    --genie-border-strong: #b8c4d4;
    --genie-accent: #185abd;
    --genie-accent-strong: #0f4c9d;
    --genie-accent-soft: #edf5ff;
    --genie-success: #18794e;
    --genie-success-soft: #edf8f2;
    --genie-danger: #a4262c;
    --genie-danger-soft: #fdf3f4;
    --genie-shadow: 0 8px 24px rgba(18, 35, 58, 0.08);
}

* {
  box-sizing: border-box;
}

html,
body,
#root {
  height: 100%;
  margin: 0;
}

body {
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    background-color: var(--genie-canvas);
  background-image:
        linear-gradient(rgba(24, 90, 189, 0.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(24, 90, 189, 0.035) 1px, transparent 1px);
    background-size: 24px 24px;
  background-attachment: fixed;
    color: var(--genie-text);
}

h1, h2, h3 {
    font-family: "Segoe UI Variable Display", "Segoe UI", sans-serif;
  font-weight: 700;
    letter-spacing: 0;
  margin: 0 0 8px;
}

p {
    color: var(--genie-muted);
  line-height: 1.5;
}

button {
  font: inherit;
}

input, textarea, select {
  font: inherit;
    color: var(--genie-text);
    background-color: var(--genie-surface);
    border: 1px solid var(--genie-border-strong);
    border-radius: 4px;
  padding: 10px 12px;
}

input:focus, textarea:focus, select:focus {
    border-color: var(--genie-accent);
    outline: 3px solid rgba(24, 90, 189, 0.2);
  outline-offset: 1px;
}

.genie-shell {
    max-width: 1120px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}

@keyframes genie-fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.genie-fade-in {
  animation: genie-fade-in 260ms ease both;
}

.genie-header {
  margin-bottom: 28px;
}

.genie-eyebrow {
  display: inline-block;
    color: var(--genie-accent);
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-size: 12px;
  margin: 0 0 6px;
}

.genie-card {
    background-color: var(--genie-surface);
    border: 1px solid var(--genie-border);
    border-radius: 8px;
    padding: 24px;
  margin-bottom: 20px;
    box-shadow: var(--genie-shadow);
}

.genie-zone-title {
  font-size: 16px;
  margin-bottom: 12px;
}

.genie-input-surface {
    margin: 28px 0;
    padding: 24px;
    color: var(--genie-text);
    background-color: var(--genie-surface);
    border: 1px solid var(--genie-border);
    border-radius: 8px;
    box-shadow: var(--genie-shadow);
}

.genie-input-surface :where(h1, h2, h3, label, legend) {
    color: var(--genie-text) !important;
}

.genie-input-surface :where(p, small) {
    color: var(--genie-muted) !important;
}

.genie-input-surface > .genie-zone-title {
    margin: 0;
    padding: 0 0 14px;
    border-bottom: 1px solid var(--genie-border);
    font-size: 14px;
    letter-spacing: 0;
}

.genie-input-surface form {
    display: grid;
    gap: 0;
    width: 100%;
    max-width: none !important;
    margin: 0;
    padding: 20px 0 0 !important;
    color: var(--genie-text);
    background: transparent !important;
    border: 0 !important;
    border-radius: 0;
    box-shadow: none !important;
}

.genie-input-surface form > section {
    margin: 0 !important;
    padding: 22px 0;
    border-top: 1px solid var(--genie-border);
}

.genie-input-surface form > div:first-child {
    padding-bottom: 22px;
}

.genie-input-surface form > div:first-child h2 {
    margin-bottom: 6px !important;
    font-size: clamp(20px, 2.4vw, 26px) !important;
    line-height: 1.2;
}

.genie-input-surface form > section > h3 {
    margin-bottom: 6px !important;
    font-size: 16px !important;
    line-height: 1.3;
}

.genie-input-surface :where(label, legend) {
    font-weight: 650;
    line-height: 1.35;
}

.genie-input-surface :where(input, textarea, select) {
    color: var(--genie-text);
    background-color: var(--genie-surface);
    border-color: var(--genie-border-strong);
}

.genie-input-surface :where(input[type="text"], input[type="number"], input[type="date"], input[type="datetime-local"], textarea, select) {
    min-height: 42px;
}

.genie-input-surface :where(input[type="range"]) {
    width: 100%;
    padding-inline: 0;
}

.genie-input-surface :where(input, textarea, select)::placeholder {
    color: #68778c;
    opacity: 1;
}

.genie-input-surface :where(input, textarea, select):focus {
    border-color: var(--genie-accent);
    outline: 3px solid rgba(24, 90, 189, 0.22);
    outline-offset: 1px;
}

.genie-input-surface input[type="checkbox"],
.genie-input-surface input[type="radio"] {
    width: 17px;
    height: 17px;
    flex: 0 0 17px;
    accent-color: var(--genie-accent);
}

.genie-input-surface .genie-card {
    color: var(--genie-text);
    background-color: transparent;
    border-color: transparent;
    box-shadow: none !important;
}

.genie-input-surface fieldset {
    min-width: 0;
}

.genie-input-surface fieldset label {
    min-height: 36px;
    padding: 7px 9px;
    background-color: var(--genie-surface-subtle);
    border: 1px solid var(--genie-border);
    border-radius: 4px;
    cursor: pointer;
}

.genie-input-surface fieldset label:hover {
    background-color: var(--genie-accent-soft);
    border-color: #91add2;
}

.genie-input-surface .genie-dropzone {
    color: var(--genie-text);
    background-color: var(--genie-surface-subtle) !important;
    border: 1px dashed var(--genie-border-strong) !important;
    border-radius: 6px !important;
    padding: 22px !important;
}

.genie-input-surface .genie-dropzone:hover,
.genie-input-surface .genie-dropzone-active {
    background-color: var(--genie-accent-soft) !important;
    border-color: var(--genie-accent) !important;
}

.genie-input-surface .genie-btn {
    min-height: 42px;
    padding: 10px 18px;
    color: #ffffff;
    background: var(--genie-accent);
    border-color: var(--genie-accent);
    border-radius: 4px;
    box-shadow: 0 1px 2px rgba(18, 35, 58, 0.18);
}

.genie-input-surface .genie-btn:disabled {
    color: #526176;
    background: #d8e0ea;
    border-color: #d8e0ea;
    opacity: 1;
    box-shadow: none;
}

.genie-input-surface .genie-error,
.genie-input-surface [role="alert"] {
    display: block;
    padding: 10px 12px;
    color: var(--genie-danger);
    background-color: var(--genie-danger-soft);
    border: 1px solid #e7a9a9;
    border-left: 4px solid #b42318;
    border-radius: 6px;
    font-weight: 650;
    line-height: 1.45;
}

.genie-btn {
  padding: 10px 18px;
    border-radius: 4px;
    border: 1px solid var(--genie-border-strong);
  font-weight: 700;
  cursor: pointer;
    background-color: var(--genie-surface);
    color: var(--genie-text);
    transition: background-color 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
}

.genie-btn:hover:not(:disabled) {
    background-color: var(--genie-surface-subtle);
    border-color: #91add2;
}
.genie-btn:disabled {
  cursor: not-allowed;
    color: #68778c;
    background-color: #e4e9f0;
    border-color: #d1d9e4;
    opacity: 1;
}

.genie-btn-primary {
    background: var(--genie-accent);
    border-color: var(--genie-accent);
    color: #ffffff;
}

.genie-btn-primary:hover:not(:disabled) {
    background: var(--genie-accent-strong);
    border-color: var(--genie-accent-strong);
    box-shadow: 0 2px 8px rgba(24, 90, 189, 0.24);
}

.genie-error {
    color: var(--genie-danger);
  font-weight: 600;
}

.genie-output {
  white-space: pre-wrap;
  margin-top: 16px;
  padding: 14px 16px;
    color: var(--genie-text);
    background-color: var(--genie-surface-subtle);
    border: 1px solid var(--genie-border);
    border-radius: 4px;
}

.genie-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
    background-color: var(--genie-surface-subtle);
    color: var(--genie-muted);
}

.genie-badge-active {
    background-color: var(--genie-accent-soft);
    color: var(--genie-accent-strong);
}

.genie-badge-complete {
    background-color: var(--genie-success-soft);
    color: var(--genie-success);
}

.genie-badge-pending {
    background-color: #edf0f4;
    color: #68778c;
}

@keyframes genie-live-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(63, 166, 106, 0.55); }
  50% { box-shadow: 0 0 0 4px rgba(63, 166, 106, 0); }
}

.genie-live-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
    background-color: var(--genie-success);
  animation: genie-live-pulse 1.6s ease-in-out infinite;
}

@keyframes genie-loading-dot {
    0%, 100% { transform: scale(0.82); opacity: 0.42; }
    50% { transform: scale(1); opacity: 1; }
}

.genie-bounce-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.genie-bounce-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
    background-color: var(--genie-accent);
    animation: genie-loading-dot 1.1s cubic-bezier(0.22, 1, 0.36, 1) infinite;
}

.genie-bounce-dot:nth-child(2) { animation-delay: 0.15s; }
.genie-bounce-dot:nth-child(3) { animation-delay: 0.3s; }

.genie-agent-activity {
    border-color: #91add2;
    box-shadow: inset 4px 0 0 var(--genie-accent), var(--genie-shadow);
}

@keyframes genie-indeterminate-rail {
  from { transform: translateX(-100%); }
  to { transform: translateX(340%); }
}

.genie-progress-rail {
  height: 4px;
  margin-top: 12px;
  overflow: hidden;
  border-radius: 2px;
    background: #dbe8f8;
}

.genie-progress-rail::after {
  content: "";
  display: block;
  width: 30%;
  height: 100%;
  border-radius: inherit;
    background: var(--genie-accent);
  animation: genie-indeterminate-rail 1.3s ease-in-out infinite;
}

.genie-hero {
  position: relative;
  overflow: hidden;
    padding: 32px;
    border-radius: 8px;
  margin-bottom: 24px;
    background: var(--genie-surface);
    border: 1px solid var(--genie-border);
    border-left: 4px solid var(--genie-accent);
    box-shadow: var(--genie-shadow);
}

.genie-hero-kicker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
    color: var(--genie-accent);
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-size: 12px;
  margin: 0 0 10px;
}

.genie-hero h1 {
    color: var(--genie-text);
    font-size: 32px;
  margin-bottom: 6px;
}

.genie-hero p {
  max-width: 620px;
  margin: 0;
}

.genie-dropzone {
    border: 1px dashed var(--genie-border-strong);
    border-radius: 6px;
  padding: 24px;
  text-align: center;
  cursor: pointer;
  transition: border-color 160ms ease, background-color 160ms ease, transform 160ms ease;
    background-color: var(--genie-surface-subtle);
}

.genie-dropzone:hover {
    border-color: #91add2;
    background-color: var(--genie-accent-soft);
}

.genie-dropzone-active {
    border-color: var(--genie-accent);
    background-color: var(--genie-accent-soft);
  transform: scale(1.01);
}

.genie-dropzone-icon {
  font-size: 28px;
  display: block;
  margin-bottom: 8px;
}

.genie-queue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
  margin-top: 16px;
}

.genie-queue-item {
    background-color: var(--genie-surface-subtle);
    border: 1px solid var(--genie-border);
    border-radius: 6px;
  padding: 16px;
  transition: transform 160ms ease, border-color 160ms ease;
}

.genie-queue-item:hover {
  transform: translateY(-2px);
    border-color: #91add2;
}

.genie-queue-item-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.genie-queue-item-title {
  font-weight: 700;
  font-size: 14px;
  word-break: break-word;
}

.genie-step-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
    color: #68778c;
  margin: 0 0 8px;
}

.genie-kind-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
    border-radius: 4px;
    color: var(--genie-accent-strong);
    background-color: var(--genie-accent-soft);
  flex-shrink: 0;
}

.genie-empty-state {
    border: 1px dashed var(--genie-border-strong);
    border-radius: 6px;
  padding: 20px;
  text-align: center;
    color: #68778c;
}

.genie-icon-btn {
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  padding: 0;
  font: inherit;
  opacity: 0.7;
}

.genie-icon-btn:hover {
  opacity: 1;
}

.genie-pipeline-caption {
  font-size: 13px;
  margin: 0 0 16px;
}

.genie-pipeline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0;
}

.genie-pipeline-node {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
    border-radius: 4px;
    border: 1px solid var(--genie-border);
    background-color: var(--genie-surface-subtle);
  font-size: 12px;
  font-weight: 700;
    color: var(--genie-muted);
  white-space: nowrap;
  transition: background-color 200ms ease, border-color 200ms ease, color 200ms ease, transform 200ms ease;
}

.genie-pipeline-node-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
    background-color: #91a0b4;
  flex-shrink: 0;
}

@keyframes genie-pipeline-node-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(47, 131, 224, 0.5); }
  50% { box-shadow: 0 0 0 7px rgba(47, 131, 224, 0); }
}

.genie-pipeline-node-active {
    border-color: var(--genie-accent);
    background-color: var(--genie-accent-soft);
    color: var(--genie-accent-strong);
  transform: scale(1.08);
  animation: genie-pipeline-node-pulse 1.4s ease-in-out infinite;
}

.genie-pipeline-node-active .genie-pipeline-node-dot {
    background-color: var(--genie-accent);
  animation: genie-live-pulse 1.2s ease-in-out infinite;
}

.genie-pipeline-node-complete {
    border-color: #8bc6aa;
    background-color: var(--genie-success-soft);
    color: var(--genie-success);
}

.genie-pipeline-node-complete .genie-pipeline-node-dot {
    background-color: var(--genie-success);
}

.genie-pipeline-connector {
  position: relative;
  width: 32px;
  height: 3px;
  margin: 0 4px;
  border-radius: 2px;
    background-color: var(--genie-border-strong);
  flex-shrink: 0;
  overflow: visible;
  transition: background-color 200ms ease;
}

.genie-pipeline-connector-active {
    background-color: var(--genie-accent);
}

@keyframes genie-pipeline-particle-travel {
  0% { left: -4px; opacity: 0; }
  12% { opacity: 1; }
  88% { opacity: 1; }
  100% { left: calc(100% - 4px); opacity: 0; }
}

.genie-pipeline-connector-active::after {
  content: "";
  position: absolute;
  top: 50%;
  left: -4px;
  width: 8px;
  height: 8px;
  margin-top: -4px;
  border-radius: 50%;
    background-color: var(--genie-accent);
    box-shadow: 0 0 0 3px rgba(24, 90, 189, 0.16);
  animation: genie-pipeline-particle-travel 1.1s linear infinite;
}

.genie-quick-request {
  margin-top: 4px;
}

.genie-quick-request-summary {
  cursor: pointer;
  font-weight: 700;
    color: var(--genie-accent-strong);
  list-style: none;
}

.genie-quick-request-summary::-webkit-details-marker {
  display: none;
}

.genie-quick-request-body {
  margin-top: 14px;
}

.genie-form {
    display: grid;
    gap: 0;
}

.genie-form-section {
    padding: 22px 0;
    border-top: 1px solid var(--genie-border);
}

.genie-form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
    gap: 16px;
}

.genie-field {
    display: grid;
    align-content: start;
    gap: 6px;
    min-width: 0;
}

.genie-field-help {
    margin: 0;
    color: var(--genie-muted);
    font-size: 12px;
    line-height: 1.45;
}

.genie-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding-top: 22px;
    border-top: 1px solid var(--genie-border);
}

@media (max-width: 640px) {
    .genie-shell {
        padding: 20px 16px 40px;
    }

    .genie-hero,
    .genie-card,
    .genie-input-surface {
        padding: 20px;
    }

    .genie-hero h1 {
        font-size: 26px;
    }

    .genie-input-surface {
        margin: 20px 0;
    }

    .genie-input-surface form > section,
    .genie-form-section {
        padding: 18px 0;
    }

    .genie-input-surface fieldset label {
        min-height: 40px;
    }

    .genie-pipeline {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 8px;
        align-items: stretch;
    }

    .genie-pipeline-node {
        width: 100%;
        min-width: 0;
        border-radius: 6px;
        white-space: normal;
        overflow-wrap: anywhere;
    }

    .genie-pipeline-node-active {
        transform: none;
    }

    .genie-pipeline-connector {
        width: 3px;
        height: 12px;
        margin: 0 0 0 18px;
    }

    .genie-actions .genie-btn {
        width: 100%;
    }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}
"""

_FRONTEND_MAIN_TSX = """import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as GeneratedModule from "../MissionApp";
import "./styles.css";

type Attachment = { name: string; content: string };

// Any custom, mission-specific component the Build Agent generated receives
// exactly these props: a single onSubmit callback to hand off whatever
// structured input it collected, plus the list of specialist agent names for
// display purposes only. It never receives (and must never build its own)
// progress/output/streaming state - the shell below owns all of that.
type MissionAppProps = {
    onSubmit: (message: string, attachments?: Attachment[]) => void;
    missionAgents: string[];
};
type GeneratedComponent = React.ComponentType<Partial<MissionAppProps>>;

// The Build Agent's generated component is untrusted, LLM-authored code -
// a real runtime bug in it (for example a `ReferenceError` from a variable
// it forgot to declare) must never blank out the entire Mission Control
// page. This boundary confines that failure to just the Mission Input
// zone, so the Quick Request fallback and the rest of the shell stay usable.
class MissionInputBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { failed: false };
    }
    static getDerivedStateFromError() {
        return { failed: true };
    }
    componentDidCatch(error: unknown) {
        console.error("Mission Input custom UI crashed:", error);
    }
    render() {
        if (this.state.failed) {
            return (
                <p className="genie-error" role="alert">
                    This mission's custom input form hit an error and could not load.
                    Use "Quick request" below to send a message or file directly instead.
                </p>
            );
        }
        return this.props.children;
    }
}
const moduleValue = GeneratedModule as unknown as {
    default?: GeneratedComponent;
    App?: GeneratedComponent;
    MissionApp?: GeneratedComponent;
};
const GeneratedMissionApp = moduleValue.default ?? moduleValue.App ?? moduleValue.MissionApp;

// Kept in sync with the mission backend's own _MAX_ATTACHMENT_CHARS guard
// (app.deploy_launch.code_materializer) - checked client-side too so a user
// gets immediate feedback instead of waiting on a 413 response.
const MAX_ATTACHMENT_CHARS = 200_000;

type AgentStatus = "pending" | "active" | "complete";
type QueueItemStatus = "queued" | "running" | "complete" | "error";
type QueueItem = {
    id: string;
    kind: "message" | "file";
    title: string;
    message: string;
    attachments: Attachment[];
    status: QueueItemStatus;
    output: string;
    error: string;
};

/**
 * Deterministic, best-effort "who's working right now" visualization: since
 * the Orchestrator narrates its own coordination and hand-offs as it streams
 * (see the build-generation prompts' REAL-TIME INTERACTION CONTRACT), the
 * furthest-mentioned agent name in the text-so-far is treated as the active
 * one, every agent mentioned before it as complete, and everything else as
 * still pending - never a fabricated progress value.
 */
function computeAgentStatuses(agents: string[], text: string, loading: boolean): Record<string, AgentStatus> {
    const lowerText = text.toLowerCase();
    const mentions = agents
        .map((name) => ({ name, index: lowerText.lastIndexOf(name.toLowerCase()) }))
        .filter((entry) => entry.index >= 0)
        .sort((a, b) => a.index - b.index);
    if (mentions.length === 0) {
        return Object.fromEntries(agents.map((name) => [name, "pending" as AgentStatus]));
    }
    const activeName = mentions[mentions.length - 1].name;
    const mentionedNames = new Set(mentions.map((entry) => entry.name));
    return Object.fromEntries(
        agents.map((name) => {
            if (name === activeName) return [name, (loading ? "active" : "complete") as AgentStatus];
            if (mentionedNames.has(name)) return [name, "complete" as AgentStatus];
            return [name, "pending" as AgentStatus];
        }),
    );
}

function newItemId(prefix: string): string {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function MissionConsole() {
    const missionTitle = window.__MISSION_TITLE__ || "Mission Prototype";
    const missionAgents = window.__MISSION_AGENTS__ || [];
    const [draftMessage, setDraftMessage] = useState("");
    const [queue, setQueue] = useState<QueueItem[]>([]);
    const [isDragging, setIsDragging] = useState(false);
    const [dropError, setDropError] = useState("");
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    // Every queued input (typed message or dropped file) is processed as its
    // OWN independent call to the mission backend - each gets its own live
    // status, its own streamed output, and its own download button, instead
    // of forcing everything through a single shared request/response.
    async function runItem(item: QueueItem) {
        setQueue((prior) => prior.map((entry) => (entry.id === item.id ? { ...entry, status: "running", output: "", error: "" } : entry)));
        try {
            const backendUrl = window.__MISSION_BACKEND_URL__;
            if (!backendUrl) throw new Error("Mission backend URL is not configured.");
            const result = await fetch(`${backendUrl}/invoke/stream`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ message: item.message, attachments: item.attachments }),
            });
            if (!result.ok || !result.body) throw new Error(`Mission backend returned ${result.status}.`);
            const reader = result.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let sawOutput = false;
            for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const frames = buffer.split("\\n\\n");
                buffer = frames.pop() ?? "";
                for (const frame of frames) {
                    const payload = frame.replace(/^data:\\s*/, "");
                    if (!payload) continue;
                    const parsed = JSON.parse(payload);
                    if (parsed.delta) {
                        sawOutput = true;
                        setQueue((prior) => prior.map((entry) => (entry.id === item.id ? { ...entry, output: entry.output + parsed.delta } : entry)));
                    } else if (parsed.done) {
                        sawOutput = true;
                        setQueue((prior) => prior.map((entry) => (entry.id === item.id ? { ...entry, output: parsed.output_text || entry.output } : entry)));
                    }
                }
            }
            setQueue((prior) => prior.map((entry) => (entry.id !== item.id
                ? entry
                : sawOutput
                    ? { ...entry, status: "complete" }
                    : { ...entry, status: "error", error: "The mission backend returned no output." })));
        } catch (caught) {
            setQueue((prior) => prior.map((entry) => (entry.id === item.id
                ? { ...entry, status: "error", error: caught instanceof Error ? caught.message : "Unable to contact the mission backend." }
                : entry)));
        }
    }

    function enqueue(item: QueueItem) {
        setQueue((prior) => [...prior, item]);
        void runItem(item);
    }

    // The single hand-off point for any Build-Agent-generated custom input
    // form: it calls this exactly once per user action with a composed
    // message (and optional attachments), and the shell takes it from there
    // - creating a real, live-tracked Mission Queue item automatically, with
    // zero extra plumbing required by the generated component.
    function submitFromCustomUI(message: string, attachments?: Attachment[]) {
        const trimmed = message.trim();
        if (!trimmed) return;
        enqueue({
            id: newItemId("mission"),
            kind: attachments && attachments.length > 0 ? "file" : "message",
            title: trimmed.length > 60 ? `${trimmed.slice(0, 60)}...` : trimmed,
            message: trimmed,
            attachments: attachments ?? [],
            status: "queued",
            output: "",
            error: "",
        });
    }

    function addMessageToQueue() {
        const trimmed = draftMessage.trim();
        if (!trimmed) return;
        enqueue({
            id: newItemId("msg"),
            kind: "message",
            title: trimmed.length > 60 ? `${trimmed.slice(0, 60)}...` : trimmed,
            message: trimmed,
            attachments: [],
            status: "queued",
            output: "",
            error: "",
        });
        setDraftMessage("");
    }

    async function addFilesToQueue(files: FileList | File[]) {
        setDropError("");
        for (const file of Array.from(files)) {
            try {
                const content = await file.text();
                if (content.length > MAX_ATTACHMENT_CHARS) {
                    setDropError(`"${file.name}" exceeds the ${MAX_ATTACHMENT_CHARS.toLocaleString()} character limit and was skipped.`);
                    continue;
                }
                enqueue({
                    id: newItemId("file"),
                    kind: "file",
                    title: file.name,
                    message: `Process the attached file "${file.name}" and report the outcome.`,
                    attachments: [{ name: file.name, content }],
                    status: "queued",
                    output: "",
                    error: "",
                });
            } catch {
                setDropError(`Unable to read "${file.name}" as text - only plain-text files are supported.`);
            }
        }
    }

    function handleDrop(event: React.DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragging(false);
        if (event.dataTransfer.files.length > 0) void addFilesToQueue(event.dataTransfer.files);
    }

    function removeItem(id: string) {
        setQueue((prior) => prior.filter((entry) => entry.id !== id));
    }

    function downloadItemOutput(item: QueueItem) {
        if (!item.output) return;
        const blob = new Blob([item.output], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        const safeTitle = item.title.replace(/[^a-z0-9-_]+/gi, "-").toLowerCase() || "mission-output";
        anchor.href = url;
        anchor.download = `${safeTitle}.txt`;
        anchor.click();
        URL.revokeObjectURL(url);
    }

    const activeCount = queue.filter((entry) => entry.status === "running").length;
    const completeCount = queue.filter((entry) => entry.status === "complete").length;

    return <main className="genie-shell genie-fade-in">
        <header className="genie-hero">
            <p className="genie-hero-kicker"><span className="genie-live-dot" /> Mission Control</p>
            <h1>{missionTitle}</h1>
            <p>Give the agents one or more things to work on - type a request, or drag in files - and watch each one processed live, end to end.</p>
        </header>
        {missionAgents.length > 0 ? (
            <section className={activeCount > 0 ? "genie-card genie-agent-activity" : "genie-card"}>
                <h2 className="genie-zone-title">Agent Pipeline</h2>
                <p className="genie-pipeline-caption">
                    The Orchestrator hands work through each specialist below in sequence -
                    a node lights up while its agent is actively contributing, and turns
                    solid once that agent's part of the hand-off is complete.
                </p>
                <div className="genie-pipeline">
                    {missionAgents.map((name, index) => {
                        const perItemStatuses = queue
                            .filter((entry) => entry.status === "running" || entry.status === "complete")
                            .map((entry) => computeAgentStatuses(missionAgents, entry.output, entry.status === "running")[name]);
                        const status: AgentStatus = perItemStatuses.includes("active")
                            ? "active"
                            : perItemStatuses.includes("complete")
                                ? "complete"
                                : "pending";
                        return (
                            <React.Fragment key={name}>
                                <div className={`genie-pipeline-node genie-pipeline-node-${status}`}>
                                    <span className="genie-pipeline-node-dot" />
                                    <span>{name}</span>
                                </div>
                                {index < missionAgents.length - 1 ? (
                                    <div className={status === "pending" ? "genie-pipeline-connector" : "genie-pipeline-connector genie-pipeline-connector-active"} />
                                ) : null}
                            </React.Fragment>
                        );
                    })}
                </div>
                {activeCount > 0 ? <div className="genie-progress-rail" /> : null}
            </section>
        ) : null}
        {GeneratedMissionApp ? (
            <section className="genie-input-surface genie-fade-in">
                <h2 className="genie-zone-title">Mission Input</h2>
                <MissionInputBoundary>
                    <GeneratedMissionApp onSubmit={submitFromCustomUI} missionAgents={missionAgents} />
                </MissionInputBoundary>
            </section>
        ) : null}
        {GeneratedMissionApp ? (
            <details className="genie-card genie-quick-request">
                <summary className="genie-quick-request-summary">+ Quick request (send a message or file directly)</summary>
                <div className="genie-quick-request-body">
                    <textarea
                        value={draftMessage}
                        onChange={(event) => setDraftMessage(event.target.value)}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                                event.preventDefault();
                                addMessageToQueue();
                            }
                        }}
                        rows={3}
                        style={{ width: "100%" }}
                        placeholder="Describe a task, question, or decision - press Ctrl+Enter or click Add to Queue."
                    />
                    <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
                        <button type="button" className="genie-btn genie-btn-primary" onClick={addMessageToQueue} disabled={!draftMessage.trim()}>+ Add to Queue</button>
                    </div>
                    <div
                        className={isDragging ? "genie-dropzone genie-dropzone-active" : "genie-dropzone"}
                        style={{ marginTop: 16 }}
                        onClick={() => fileInputRef.current?.click()}
                        onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                        onDragLeave={() => setIsDragging(false)}
                        onDrop={handleDrop}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click(); }}
                    >
                        <span className="genie-dropzone-icon">📂</span>
                        <p style={{ margin: 0 }}>Drag &amp; drop one or more files here, or click to browse</p>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            accept=".txt,.md,.json,.csv,.log,.yaml,.yml"
                            style={{ display: "none" }}
                            onChange={(event) => {
                                if (event.target.files) void addFilesToQueue(event.target.files);
                                event.target.value = "";
                            }}
                        />
                    </div>
                    {dropError ? <p role="alert" className="genie-error">{dropError}</p> : null}
                </div>
            </details>
        ) : (
            <section className="genie-card">
                <h2 className="genie-zone-title">Give the Mission Something to Do</h2>
                <textarea
                    value={draftMessage}
                    onChange={(event) => setDraftMessage(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                            event.preventDefault();
                            addMessageToQueue();
                        }
                    }}
                    rows={3}
                    style={{ width: "100%" }}
                    placeholder="Describe a task, question, or decision - press Ctrl+Enter or click Add to Queue."
                />
                <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
                    <button type="button" className="genie-btn genie-btn-primary" onClick={addMessageToQueue} disabled={!draftMessage.trim()}>+ Add to Queue</button>
                </div>
                <div
                    className={isDragging ? "genie-dropzone genie-dropzone-active" : "genie-dropzone"}
                    style={{ marginTop: 16 }}
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                    onDragLeave={() => setIsDragging(false)}
                    onDrop={handleDrop}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click(); }}
                >
                    <span className="genie-dropzone-icon">📂</span>
                    <p style={{ margin: 0 }}>Drag &amp; drop one or more files here, or click to browse</p>
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".txt,.md,.json,.csv,.log,.yaml,.yml"
                        style={{ display: "none" }}
                        onChange={(event) => {
                            if (event.target.files) void addFilesToQueue(event.target.files);
                            event.target.value = "";
                        }}
                    />
                </div>
                {dropError ? <p role="alert" className="genie-error">{dropError}</p> : null}
            </section>
        )}
        <section className="genie-card">
            <h2 className="genie-zone-title">Mission Queue{queue.length > 0 ? ` - ${completeCount}/${queue.length} complete` : ""}</h2>
            {queue.length === 0 ? (
                <div className="genie-empty-state">
                    {GeneratedMissionApp
                        ? "No inputs yet - submit the form above to see the agents get to work."
                        : "No inputs yet - add a request or drop a file above to see the agents get to work."}
                </div>
            ) : (
                <div className="genie-queue-grid">
                    {queue.map((item) => {
                        const itemAgentStatuses = computeAgentStatuses(missionAgents, item.output, item.status === "running");
                        return (
                            <article key={item.id} className={item.status === "running" ? "genie-queue-item genie-agent-activity" : "genie-queue-item"}>
                                <div className="genie-queue-item-header">
                                    <span className="genie-kind-icon">{item.kind === "file" ? "📄" : "💬"}</span>
                                    <span className="genie-queue-item-title" style={{ flex: 1 }}>{item.title}</span>
                                    <button type="button" className="genie-icon-btn" onClick={() => removeItem(item.id)} aria-label={`Remove ${item.title}`}>×</button>
                                </div>
                                <p className="genie-step-label">
                                    {item.status === "queued" ? "Waiting in queue…" : null}
                                    {item.status === "running" ? (item.output ? "Agents collaborating…" : "Contacting mission backend…") : null}
                                    {item.status === "complete" ? "Complete" : null}
                                    {item.status === "error" ? "Failed" : null}
                                </p>
                                {item.status === "running" && !item.output ? (
                                    <span className="genie-bounce-dots"><span className="genie-bounce-dot" /><span className="genie-bounce-dot" /><span className="genie-bounce-dot" /></span>
                                ) : null}
                                {item.status === "running" && missionAgents.length > 0 ? (
                                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                                        {missionAgents.map((name) => (
                                            <span key={name} className={`genie-badge genie-badge-${itemAgentStatuses[name] ?? "pending"}`} style={{ fontSize: 11 }}>
                                                {name}
                                            </span>
                                        ))}
                                    </div>
                                ) : null}
                                {item.status === "error" ? (
                                    <>
                                        <p role="alert" className="genie-error">{item.error}</p>
                                        <button type="button" className="genie-btn" onClick={() => void runItem(item)}>Retry</button>
                                    </>
                                ) : null}
                                {item.output ? (
                                    <>
                                        <pre className="genie-output" style={{ maxHeight: 220, overflow: "auto" }}>{item.output}</pre>
                                        {item.status === "complete" ? (
                                            <button type="button" className="genie-btn" onClick={() => downloadItemOutput(item)} style={{ marginTop: 8 }}>⬇ Download output</button>
                                        ) : null}
                                    </>
                                ) : null}
                            </article>
                        );
                    })}
                </div>
            )}
        </section>
    </main>;
}

createRoot(document.getElementById("root")!).render(<MissionConsole />);
"""

_FRONTEND_ENV_D_TS = """interface Window {
    __MISSION_BACKEND_URL__?: string;
    __MISSION_TITLE__?: string;
    __MISSION_AGENTS__?: string[];
}
"""


class DeploymentPipelineStepFailedError(RuntimeError):
    """Raised when a pipeline step's own real result (test run, security scan) fails."""


class _RequirementFidelityRepairNeeded(DeploymentPipelineStepFailedError):
    """Carries observed acceptance-test evidence into one automatic rebuild."""

    def __init__(self, *, summary: str, evidence: str) -> None:
        super().__init__(summary)
        self.evidence = evidence


class _GeneratedBuildRepairNeeded(DeploymentPipelineStepFailedError):
    """Carries deterministic generated-code validation evidence into a rebuild."""

    def __init__(self, evidence: str) -> None:
        super().__init__("Generated build validation failed; automatically regenerating it.")
        self.evidence = evidence


@dataclass
class _RunWorkspace:
    """Filesystem locations materialized for one pipeline run - kept only in memory."""

    backend_root: Path
    frontend_root: Path


class DeploymentPipelineService:
    """Executes the fixed, nine-step Deploy & Launch pipeline for one mission."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_service: SessionService,
        event_bus: WorkflowEventBus,
        access_policy_service: AccessPolicyService,
        mission_identity_service: MissionIdentityService | NullMissionIdentityService,
        mission_agent_provisioning_service: MissionAgentProvisioningService
        | NullMissionAgentProvisioningService,
        backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
        frontend_deployment_service: (
            ContainerAppFrontendDeploymentService | NullContainerAppFrontendDeploymentService
        ),
        test_execution_service: TestExecutionService,
        security_scan_service: SecurityScanService,
        build_workspace_root: Path,
        run_repository: DeploymentRunRepository | None = None,
        prototype_default_ttl_days: int = 7,
        prototype_max_active_per_owner: int = 3,
        architecture_step_id: str = "design-architecture",
        build_step_id: str = "build-solution",
        requirements_step_id: str = "analyze-requirements",
        fidelity_max_repair_attempts: int = 3,
        fidelity_min_coverage_percent: float = 90.0,
        upstream_grace_check_attempts: int = 5,
        upstream_grace_check_interval_seconds: float = 2.0,
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._event_bus = event_bus
        self._access_policy_service = access_policy_service
        self._mission_identity_service = mission_identity_service
        self._mission_agent_provisioning_service = mission_agent_provisioning_service
        self._backend_deployment_service = backend_deployment_service
        self._frontend_deployment_service = frontend_deployment_service
        self._test_execution_service = test_execution_service
        self._security_scan_service = security_scan_service
        self._run_repository = run_repository or InMemoryDeploymentRunRepository()
        self._prototype_default_ttl_days = prototype_default_ttl_days
        self._prototype_max_active_per_owner = prototype_max_active_per_owner
        self._build_workspace_root = build_workspace_root
        self._architecture_step_id = architecture_step_id
        self._build_step_id = build_step_id
        self._requirements_step_id = requirements_step_id
        self._fidelity_max_repair_attempts = fidelity_max_repair_attempts
        self._fidelity_min_coverage_percent = fidelity_min_coverage_percent
        self._upstream_grace_check_attempts = upstream_grace_check_attempts
        self._upstream_grace_check_interval_seconds = upstream_grace_check_interval_seconds
        self._runs: dict[str, DeploymentPipelineRun] = {}
        self._workspaces: dict[str, _RunWorkspace] = {}
        self._materialized_builds: dict[str, MaterializedBuild] = {}
        self._agent_foundry_names: dict[str, dict[str, str]] = {}
        self._generated_test_outputs: dict[str, str] = {}
        self._background_tasks: dict[str, asyncio.Task[None]] = {}

    async def initialize(self) -> None:
        """Hydrate the prototype inventory and fail interrupted runs closed."""

        for run in await self._run_repository.list_all():
            if run.status == "running":
                run.status = "failed"
                run.updated_at = datetime.now(UTC)
                running_step = next((step for step in run.steps if step.status == "running"), None)
                if running_step is not None:
                    running_step.status = "failed"
                    running_step.error = "Deployment was interrupted by a service restart."
                    running_step.completed_at = run.updated_at
                await self._run_repository.put(run)
            self._runs[run.id] = run

    async def _persist_run(self, run: DeploymentPipelineRun) -> None:
        await self._run_repository.put(run)

    def get_run(self, pipeline_run_id: str) -> DeploymentPipelineRun | None:
        return self._runs.get(pipeline_run_id)

    def list_runs_for_session(self, session_id: str) -> list[DeploymentPipelineRun]:
        return [run for run in self._runs.values() if run.session_id == session_id]

    def list_all_runs(self) -> list[DeploymentPipelineRun]:
        return sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)

    def get_build_root(self, pipeline_run_id: str) -> Path | None:
        workspace = self._workspaces.get(pipeline_run_id)
        return workspace.backend_root if workspace else None

    async def start(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        trace_id: str | None = None,
        resume_from_step: str | None = None,
        requesting_tenant_id: str = "",
        requesting_object_id: str = "",
    ) -> DeploymentPipelineRun:
        """Kicks off every Deploy & Launch step in order as soon as the human
        clicks Start - there is no separate approval checkpoint to decide.

        If ``resume_from_step`` is provided, the pipeline resumes from that step
        instead of starting from the first step, allowing retry/recovery from a
        failed step without re-running prior completed steps.

        The pipeline run is created (status ``running``, every step
        ``pending``) and stored - and therefore immediately visible to
        ``get_run``/``list_runs_for_session`` pollers - before ANY
        potentially slow work happens. Resolving the session/workflow run
        and self-healing a not-yet-finished upstream step (see
        ``_ensure_upstream_steps_completed``) can legitimately take a while
        (a real upstream agent resume), so that work - like the nine
        pipeline steps themselves - runs in the background task, never as
        an invisible delay before the user sees anything. Callers must poll
        (or the ``WorkflowEventBus``/SSE stream) for live per-step
        progress, never the return value of this call itself; a
        client-side network hiccup on this call must never be mistaken for
        the pipeline itself failing.
        """

        resolved_trace_id = trace_id or str(uuid4())

        active_run = next(
            (
                run
                for run in self._runs.values()
                if run.session_id == session_id
                and run.workflow_run_id == workflow_run_id
                and run.owner_user_id == requesting_user_id
                and run.status == "running"
            ),
            None,
        )
        if active_run is not None:
            return active_run

        if not resume_from_step:
            active_count = sum(
                1
                for run in self._runs.values()
                if run.owner_user_id == requesting_user_id
                and run.cleanup_status == "active"
                and (run.status == "running" or run.backend_url or run.frontend_url)
            )
            if active_count >= self._prototype_max_active_per_owner:
                raise DeploymentPipelineStepFailedError(
                    "Active prototype limit reached; delete or wait for an existing prototype "
                    "to expire before creating another."
                )

        # A retry (resume_from_step set) MUST continue the SAME run - i.e. the
        # same pipeline_run.id - not mint a fresh one. Durable step results retain
        # the access policy and real provisioned-agent names; restart-volatile build
        # artifacts are reconstructed in _restore_retry_artifacts before execution.
        existing_run: DeploymentPipelineRun | None = None
        if resume_from_step:
            candidates = [
                run
                for run in self._runs.values()
                if run.session_id == session_id
                and run.workflow_run_id == workflow_run_id
                and run.owner_user_id == requesting_user_id
                and run.status == "failed"
                and run.cleanup_status == "active"
            ]
            if candidates:
                existing_run = max(candidates, key=lambda run: run.updated_at)

        if existing_run is not None:
            pipeline_run = existing_run
            pipeline_run.status = "running"
            pipeline_run.updated_at = datetime.now(UTC)
            workspace = self._workspaces.get(pipeline_run.id)
            if workspace is None:
                workspace = _RunWorkspace(
                    backend_root=self._build_workspace_root / pipeline_run.id / "backend",
                    frontend_root=self._build_workspace_root / pipeline_run.id / "frontend",
                )
                self._workspaces[pipeline_run.id] = workspace
            backend_root = workspace.backend_root
            frontend_root = workspace.frontend_root
        else:
            # A caller asked to resume a specific step (e.g. a "Retry" click
            # against a previously failed run) but no matching durable failed
            # run was found. Honoring
            # resume_from_step against a brand-new pipeline_run would skip
            # every earlier step without them ever having actually run on
            # this object - e.g. jumping straight to "execute-test-suite"
            # with no generated tests produces a false "Running 0 generated
            # test module(s)" / "fidelity report unavailable" failure
            # instead of an honest restart. Fail safe: always start this
            # fresh run from the very first step instead.
            resume_from_step = None
            pipeline_run = DeploymentPipelineRun(
                id=str(uuid4()),
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                owner_user_id=requesting_user_id,
                owner_tenant_id=requesting_tenant_id,
                owner_object_id=requesting_object_id,
                status="running",
                expires_at=datetime.now(UTC) + timedelta(days=self._prototype_default_ttl_days),
                last_accessed_at=datetime.now(UTC),
                steps=[
                    DeploymentStepResult(step_id=step_id, name=DEPLOYMENT_STEP_NAMES[step_id])
                    for step_id in DEPLOYMENT_STEP_ORDER
                ],
            )
            self._runs[pipeline_run.id] = pipeline_run

            backend_root = self._build_workspace_root / pipeline_run.id / "backend"
            frontend_root = self._build_workspace_root / pipeline_run.id / "frontend"
            self._workspaces[pipeline_run.id] = _RunWorkspace(
                backend_root=backend_root, frontend_root=frontend_root
            )

        await self._persist_run(pipeline_run)

        task = asyncio.create_task(
            self._prepare_and_run(
                pipeline_run=pipeline_run,
                requesting_user_id=requesting_user_id,
                trace_id=resolved_trace_id,
                backend_root=backend_root,
                frontend_root=frontend_root,
                resume_from_step=resume_from_step,
            )
        )
        self._background_tasks[pipeline_run.id] = task
        task.add_done_callback(
            lambda _task, _id=pipeline_run.id: self._background_tasks.pop(_id, None)
        )

        return pipeline_run

    async def _restore_retry_artifacts(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        run: WorkflowRunResult,
        trace_id: str,
    ) -> None:
        """Rebuilds restart-volatile artifacts from durable workflow and run data."""

        build_output_text = await self._get_step_output(
            run, self._build_step_id, trace_id=trace_id
        )
        materialized = materialize_build(build_output_text)
        required_agent_names = list(materialized.agent_modules)
        if materialized.orchestrator_module is not None:
            required_agent_names.append("orchestrator")

        foundry_names = {
            agent.agent_name: agent.foundry_agent_name
            for agent in pipeline_run.provisioned_agents
            if agent.status == "completed" and agent.foundry_agent_name
        }
        missing_agent_names = [
            agent_name for agent_name in required_agent_names if agent_name not in foundry_names
        ]
        if missing_agent_names:
            raise DeploymentPipelineStepFailedError(
                "Cannot resume after service restart because completed Foundry agent records "
                f"are missing for: {', '.join(missing_agent_names)}."
            )

        self._materialized_builds[pipeline_run.id] = materialized
        self._agent_foundry_names[pipeline_run.id] = {
            agent_name: foundry_names[agent_name] for agent_name in required_agent_names
        }

    def _fail_run(self, pipeline_run: DeploymentPipelineRun, *, error: str) -> None:
        """Resolves a run to ``failed`` for a failure that happened before any
        pipeline step began executing (session/workflow-run lookup, upstream
        self-heal) - attributed to the first step so it is still visible in
        the same per-step list the user is already watching, rather than a
        silent, unexplained stall."""
        pipeline_run.status = "failed"
        pipeline_run.updated_at = datetime.now(UTC)
        if pipeline_run.steps:
            first_step = pipeline_run.steps[0]
            first_step.status = "failed"
            first_step.error = error
            first_step.completed_at = datetime.now(UTC)

    async def _prepare_and_run(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        requesting_user_id: str,
        trace_id: str,
        backend_root: Path,
        frontend_root: Path,
        resume_from_step: str | None = None,
    ) -> None:
        """The background task body ``start()`` schedules: resolves the
        session/workflow run, self-heals any not-yet-finished upstream step,
        then runs every pipeline step - always resolving the run to a
        terminal status (``completed`` or ``failed``). This task's own
        exception is never re-raised anywhere (there is no caller left to
        catch it), so every failure must already have been recorded on the
        run/step themselves before this returns.

        If ``resume_from_step`` is provided, only steps from that point onward
        are executed, allowing recovery from a failed step without re-running
        prior completed steps."""

        try:
            session = await self._session_service.get_session(
                session_id=pipeline_run.session_id, requesting_user_id=requesting_user_id
            )
            run = await self._get_workflow_run(pipeline_run.workflow_run_id)
            run = await self._ensure_upstream_steps_completed(
                run=run, session_id=pipeline_run.session_id, trace_id=trace_id
            )
        except Exception as exc:  # noqa: BLE001 - top-level background-task boundary; see docstring above.
            self._fail_run(pipeline_run, error=str(exc))
            await self._persist_run(pipeline_run)
            return

        # A human-readable mission slug rooted in the mission's own title (set once
        # by the user on Upload/Landing and carried through Workshop/Architecture
        # Studio) - never a generic "mission-<uuid>" string - so the Foundry agents
        # this pipeline provisions are recognizable as belonging to this mission.
        # The short run-id suffix keeps names unique across repeat/retry deploys of
        # the same mission (Foundry agent names must be unique).
        mission_slug = f"{_slugify(session.title)}-{pipeline_run.id[:8]}"
        pipeline_run.mission_title = session.title
        pipeline_run.mission_slug = mission_slug
        pipeline_run.resource_group_name = prototype_resource_group_name(mission_slug)
        await self._persist_run(pipeline_run)

        resume_index = (
            DEPLOYMENT_STEP_ORDER.index(resume_from_step)
            if resume_from_step in DEPLOYMENT_STEP_ORDER
            else None
        )
        if resume_index is not None and resume_index >= DEPLOYMENT_STEP_ORDER.index(
            "deploy-backend-service"
        ):
            try:
                await self._restore_retry_artifacts(
                    pipeline_run=pipeline_run,
                    run=run,
                    trace_id=trace_id,
                )
            except Exception as exc:  # noqa: BLE001 - fail-closed retry reconstruction boundary.
                self._fail_run(pipeline_run, error=str(exc))
                await self._persist_run(pipeline_run)
                return

        next_step = resume_from_step
        generated_build_repair_attempts = 0
        while True:
            try:
                await self._execute_steps(
                    pipeline_run=pipeline_run,
                    run=run,
                    mission_slug=mission_slug,
                    mission_title=session.title,
                    backend_root=backend_root,
                    frontend_root=frontend_root,
                    trace_id=trace_id,
                    resume_from_step=next_step,
                )
                break
            except _GeneratedBuildRepairNeeded as exc:
                if generated_build_repair_attempts >= self._fidelity_max_repair_attempts:
                    step = self._step_result(pipeline_run, "provision-foundry-agents")
                    step.error = (
                        "Generated build validation failed after "
                        f"{generated_build_repair_attempts} automatic repair attempt(s): "
                        f"{exc.evidence}"
                    )
                    pipeline_run.status = "failed"
                    pipeline_run.updated_at = datetime.now(UTC)
                    await self._persist_run(pipeline_run)
                    return
                generated_build_repair_attempts += 1
                step = self._step_result(pipeline_run, "provision-foundry-agents")
                step.status = "running"
                step.detail = (
                    "Regenerating the generated build to satisfy deterministic validation "
                    f"(attempt {generated_build_repair_attempts} of "
                    f"{self._fidelity_max_repair_attempts})..."
                )
                step.error = None
                step.completed_at = None
                pipeline_run.updated_at = datetime.now(UTC)
                await self._persist_run(pipeline_run)
                await self._publish(
                    pipeline_run,
                    step_id="provision-foundry-agents",
                    event_type="step_started",
                    output_preview=step.detail,
                )
                try:
                    run = await self._repair_prototype(
                        run=run,
                        pipeline_run=pipeline_run,
                        trace_id=trace_id,
                        evidence=exc.evidence,
                    )
                except Exception as repair_exc:  # noqa: BLE001 - fail-closed repair boundary.
                    step = self._step_result(pipeline_run, "provision-foundry-agents")
                    step.status = "failed"
                    step.error = f"Automatic generated-build repair failed: {repair_exc}"
                    step.completed_at = datetime.now(UTC)
                    pipeline_run.status = "failed"
                    pipeline_run.updated_at = datetime.now(UTC)
                    await self._persist_run(pipeline_run)
                    return
                next_step = "provision-foundry-agents"
            except _RequirementFidelityRepairNeeded as exc:
                report = pipeline_run.fidelity_report
                if report is None:
                    self._fail_run(
                        pipeline_run, error="Requirement fidelity report is unavailable."
                    )
                    return
                pipeline_run.fidelity_report = report.model_copy(
                    update={
                        "status": "repairing",
                        "repair_attempts": report.repair_attempts + 1,
                    }
                )
                try:
                    run = await self._repair_prototype(
                        run=run,
                        pipeline_run=pipeline_run,
                        trace_id=trace_id,
                        evidence=exc.evidence,
                    )
                except Exception as repair_exc:  # noqa: BLE001 - fail-closed repair boundary.
                    pipeline_run.fidelity_report = pipeline_run.fidelity_report.model_copy(
                        update={
                            "status": "failed",
                            "gaps": [f"Automatic prototype repair failed: {repair_exc}"],
                        }
                    )
                    pipeline_run.status = "failed"
                    pipeline_run.updated_at = datetime.now(UTC)
                    await self._persist_run(pipeline_run)
                    return
                pipeline_run.launch_url = None
                next_step = "provision-foundry-agents"
            except Exception:  # noqa: BLE001 - top-level background-task boundary; every
                # failure must resolve the run's status here since there is no
                # synchronous caller left to catch/report it (see the docstring above).
                pipeline_run.status = "failed"
                pipeline_run.updated_at = datetime.now(UTC)
                await self._persist_run(pipeline_run)
                return

        pipeline_run.status = "completed"
        pipeline_run.updated_at = datetime.now(UTC)
        await self._persist_run(pipeline_run)

    async def abandon(
        self,
        *,
        pipeline_run_id: str,
        mission_title: str | None = None,
    ) -> None:
        """Tears down every resource owned by one non-running prototype run."""

        pipeline_run = self._runs.get(pipeline_run_id)
        if pipeline_run is None:
            raise DeploymentPipelineStepFailedError(
                f"No Deploy & Launch run '{pipeline_run_id}' found."
            )
        if pipeline_run.status == "running":
            raise DeploymentPipelineStepFailedError(
                "A running Deploy & Launch run cannot be abandoned."
            )

        pipeline_run.cleanup_status = "deletion_pending"
        pipeline_run.cleanup_error = None
        pipeline_run.updated_at = datetime.now(UTC)
        await self._persist_run(pipeline_run)

        try:
            mission_slug = pipeline_run.mission_slug
            if mission_slug is None:
                resolved_title = mission_title or pipeline_run.mission_title
                if resolved_title is None:
                    raise DeploymentPipelineStepFailedError(
                        "Prototype mission metadata is unavailable; cleanup cannot continue safely."
                    )
                mission_slug = f"{_slugify(resolved_title)}-{pipeline_run.id[:8]}"
            await self._backend_deployment_service.delete(mission_slug=mission_slug)
            await self._frontend_deployment_service.delete(
                mission_slug=mission_slug,
            )
            await self._mission_agent_provisioning_service.delete(
                foundry_agent_names=[
                    agent.foundry_agent_name
                    for agent in pipeline_run.provisioned_agents
                    if agent.foundry_agent_name is not None
                ]
            )
            if pipeline_run.access_policy and pipeline_run.access_policy.mission_identity:
                mission_identity = pipeline_run.access_policy.mission_identity
                await self._mission_identity_service.delete(
                    identity_name=mission_identity.identity_name,
                    principal_id=mission_identity.identity_principal_id,
                    resource_group_name=pipeline_run.resource_group_name,
                    role_assignment_ids=mission_identity.role_assignment_ids,
                )
        except Exception as exc:
            pipeline_run.cleanup_status = "deletion_failed"
            pipeline_run.cleanup_error = str(exc)
            pipeline_run.updated_at = datetime.now(UTC)
            await self._persist_run(pipeline_run)
            raise

        workspace = self._workspaces.get(pipeline_run.id)
        if workspace is not None:
            await asyncio.to_thread(
                shutil.rmtree, workspace.backend_root.parent, ignore_errors=True
            )
        self._materialized_builds.pop(pipeline_run.id, None)
        self._agent_foundry_names.pop(pipeline_run.id, None)
        self._generated_test_outputs.pop(pipeline_run.id, None)
        self._workspaces.pop(pipeline_run.id, None)
        self._runs.pop(pipeline_run.id, None)
        await self._run_repository.delete(pipeline_run_id=pipeline_run.id)

    async def cleanup_expired(self, *, now: datetime | None = None) -> list[str]:
        """Delete every terminal prototype whose owner-controlled TTL elapsed."""

        cutoff = now or datetime.now(UTC)
        deleted: list[str] = []
        for run in list(self._runs.values()):
            if run.status == "running" or run.expires_at is None or run.expires_at > cutoff:
                continue
            try:
                await self.abandon(pipeline_run_id=run.id)
            except Exception:
                _logger.exception("Expired prototype cleanup failed for run %s.", run.id)
                continue
            deleted.append(run.id)
        return deleted

    async def wait_for_run(self, pipeline_run_id: str) -> DeploymentPipelineRun:
        """Awaits a still-in-flight run's background execution to finish and
        returns its final state. Normal callers (the API/frontend) always
        poll instead; this exists for callers that genuinely need the
        finished result in-process (e.g. tests)."""

        task = self._background_tasks.get(pipeline_run_id)
        if task is not None:
            await task
        return self._runs[pipeline_run_id]

    async def _get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult:
        run = await self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found.")
        return run

    async def _step_completed(self, run: WorkflowRunResult, step_id: str, *, trace_id: str) -> bool:
        """A step counts as done once its real output exists anywhere - the
        official ``WorkflowRunResult`` step_results entry (``status ==
        "completed"``), or - faster, and what actually matters for Deploy &
        Launch - Shared Collaboration Memory already holds that step's real
        specialist output (see ``_read_step_memory_output``). Genie's only
        real gate on Deploy & Launch is the human's own review/approval on
        Workshop (see the module docstring): once the human can see and
        approve the real generated code, this pipeline must never impose an
        additional backend-side wait of its own for the same content to be
        echoed back a second time by genie-orchestrator.
        """
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is not None and step.status == "completed":
            return True
        memory_output = await self._read_step_memory_output(
            session_id=run.session_id, trace_id=trace_id, step_id=step_id
        )
        return memory_output is not None

    async def _read_step_memory_output(
        self, *, session_id: str, trace_id: str, step_id: str
    ) -> str | None:
        """Reads a workflow step's real specialist output straight out of
        Shared Collaboration Memory, if the delegation tool call that
        produced it has already written it there (see
        ``app.agents.tools.orchestration_tools``'s ``_delegate``) - which
        happens the instant that specialist's own real generation finishes,
        well before genie-orchestrator's own separate, slower
        echo-completion turn resolves the workflow step's own
        ``WorkflowStepResult``. Returns ``None`` when nothing is written yet
        (or memory is unavailable), so callers fall back to the official
        step status instead.
        """
        # agent_registry/memory_service are always present on the real
        # AgentOrchestrator (see its constructor) but are accessed
        # defensively here - via getattr, never a direct attribute access -
        # since some lighter-weight orchestrator test doubles only
        # implement the handful of methods a given test actually exercises
        # (get_workflow_run/resume_workflow/execute_agent), not the fuller
        # AgentOrchestrator surface. Missing either simply means this
        # faster memory-based path is unavailable - callers already fall
        # back to the official step status in that case.
        agent_registry = getattr(self._orchestrator, "agent_registry", None)
        memory_service = getattr(self._orchestrator, "memory_service", None)
        if agent_registry is None or memory_service is None:
            return None

        # Reads as genie-orchestrator's own identity - every
        # solution-discovery-workflow step (including build-solution) is
        # itself configured under this agent id (see
        # config/workflows/registry.yaml), and this is the exact same key
        # (the step id) and identity WorkflowStepExecutor._read_step_output
        # already uses to read a prior step's real output back out of
        # Shared Memory.
        requesting_agent = get_enabled_agent(agent_registry, "genie-orchestrator")
        records = await memory_service.shared.read(
            requesting_agent=requesting_agent,
            session_id=session_id,
            trace_id=trace_id,
            key=step_id,
        )
        if not records:
            return None
        output_text = records[0].content.get("output_text")
        return output_text if isinstance(output_text, str) and output_text else None

    async def _ensure_upstream_steps_completed(
        self, *, run: WorkflowRunResult, session_id: str, trace_id: str
    ) -> WorkflowRunResult:
        """Self-heals a workflow run that has not yet finished every step
        Deploy & Launch reads from (``build-solution``)
        before asking the user to click Start - e.g. an earlier page's
        fire-and-forget kickoff silently never reached the server, or the
        run is merely paused on the ``build-review-approval`` checkpoint
        that Workshop's "Proceed to Deploy & Launch" action has already
        decided by the time this runs - by resuming the SAME run here
        rather than forcing the user to notice a stuck step and manually
        return to Workshop. Only a genuinely unrecoverable state (an
        undecided/rejected approval checkpoint, or a real step failure)
        still surfaces as an error, via ``_get_step_output`` once
        ``_execute_steps`` actually reads that step's output below.

        ``build-solution``'s ``policies``/``excluded_agents`` variables are
        deliberately NOT auto-derived by ``variable_sources`` in
        config/workflows/registry.yaml (see that file's comment) - every
        OTHER caller that can (re)start this step supplies them explicitly
        as a step_input override (Architecture Studio's approval handler,
        Workshop's "Re-run UI & Agent Design"). This self-heal path is
        backend-only and has no access to whatever governance-policy text
        or excluded-agent list the user typed into those frontend pages
        (never persisted server-side - see SessionContext's
        ``governancePolicies``), so it cannot reproduce the user's real
        choices. Omitting the override entirely used to hard-fail with
        ``PromptResolutionError: Prompt 'orchestrator-build-phase-v1' is
        missing required variable(s): ['excluded_agents', 'policies']`` -
        every "Retry Deploy & Launch" click hit the identical failure
        forever, since nothing about the run's state changes between
        retries. Supplying safe empty-string defaults here instead lets a
        stuck build-solution genuinely (re)run - matching the prompt/tool's
        own contract that an empty policies/excluded_agents string simply
        means "no extra policy text" / "no excluded agents" (see
        ``_parse_excluded_agent_names`` in orchestration_tools.py). Only
        ever attached when build-solution itself has NOT already
        completed - passing ANY step_input for a step re-executes it even
        if already completed (see workflow_runtime.resume_workflow), so an
        already-finished build must never be re-triggered here with blank
        overrides that could silently discard the user's real governance
        policies.
        """
        required_step_ids = (self._build_step_id,)

        # The common case this self-heal exists for is a genuine race of a
        # few SECONDS - Workshop's "Proceed" is a client-side navigation
        # that does not wait for genie-orchestrator's own, slightly slower
        # official step-completion write to land. A single, instantaneous
        # check right as Deploy & Launch loads can lose that race even
        # though the real work is already finished or about to be -
        # unconditionally resuming (which discards the user's real
        # policies/excluded_agents, see below) in that situation needlessly
        # re-runs an already-approved build. Poll a few times with a short
        # delay before concluding the step is genuinely not done and
        # falling back to a real resume.
        for attempt in range(self._upstream_grace_check_attempts):
            if all(
                [
                    await self._step_completed(run, step_id, trace_id=trace_id)
                    for step_id in required_step_ids
                ]
            ):
                return run
            if attempt < self._upstream_grace_check_attempts - 1:
                await asyncio.sleep(self._upstream_grace_check_interval_seconds)

        step_inputs: dict[str, WorkflowStepInput] = {}
        if not await self._step_completed(run, self._build_step_id, trace_id=trace_id):
            step_inputs[self._build_step_id] = WorkflowStepInput(
                step_id=self._build_step_id,
                variables={"policies": "", "excluded_agents": ""},
            )

        resumed = await self._orchestrator.resume_workflow(
            workflow_run_id=run.workflow_run_id,
            session_id=session_id,
            trace_id=trace_id,
            step_inputs=step_inputs or None,
        )

        # `resume_workflow` can legitimately return WITHOUT raising even when
        # a required step still isn't done - e.g. the run paused again on an
        # EARLIER stage's own `requires_human_proceed` gate (design-architecture
        # comes before build-solution; this self-heal only ever targets
        # build-solution, so it cannot clear a still-pending architecture
        # proceed) or on a governance approval checkpoint. Blindly trusting
        # this resume "worked" let `_execute_steps` reach a much later,
        # unrelated step (e.g. provision-foundry-agents) before failing with a
        # confusing "Workflow step 'build-solution' has not completed"
        # error - fail closed HERE instead, immediately and clearly, so the
        # very first pipeline step records an actionable message pointing at
        # the real blocker (the resumed run's own `status`/`detail`).
        resumed_completed = [
            await self._step_completed(resumed, step_id, trace_id=trace_id)
            for step_id in required_step_ids
        ]
        if not all(resumed_completed):
            raise UnknownWorkflowRunError(
                "Deploy & Launch cannot start: the mission workflow is not fully "
                f"complete yet (status='{resumed.status}'"
                + (f", {resumed.detail}" if resumed.detail else "")
                + "). Go back to Workshop and proceed through any pending step "
                "before starting Deploy & Launch again."
            )

        return resumed

    async def _get_step_output(self, run: WorkflowRunResult, step_id: str, *, trace_id: str) -> str:
        step = next((r for r in run.step_results if r.step_id == step_id), None)
        if step is not None and step.status == "completed":
            return step.output_text or ""
        memory_output = await self._read_step_memory_output(
            session_id=run.session_id, trace_id=trace_id, step_id=step_id
        )
        if memory_output is not None:
            return memory_output
        raise UnknownWorkflowRunError(
            f"Workflow step '{step_id}' has not completed for run '{run.workflow_run_id}'."
        )

    async def _get_approved_requirements(self, run: WorkflowRunResult, *, trace_id: str) -> str:
        architecture_step = next(
            (result for result in run.step_results if result.step_id == self._architecture_step_id),
            None,
        )
        if architecture_step is not None:
            approved_requirements = architecture_step.resolved_variables.get(
                "approved_requirements"
            )
            if approved_requirements:
                return approved_requirements
        build_step = next(
            (result for result in run.step_results if result.step_id == self._build_step_id),
            None,
        )
        if build_step is not None:
            discovery_requirements = build_step.resolved_variables.get("requirements")
            if discovery_requirements:
                return discovery_requirements
        return await self._get_step_output(run, self._requirements_step_id, trace_id=trace_id)

    async def _get_approved_architecture(
        self, run: WorkflowRunResult, *, trace_id: str
    ) -> str:
        architecture_step = next(
            (result for result in run.step_results if result.step_id == self._architecture_step_id),
            None,
        )
        if architecture_step is not None and architecture_step.status == "completed":
            return architecture_step.output_text or ""
        build_step = next(
            (result for result in run.step_results if result.step_id == self._build_step_id),
            None,
        )
        if build_step is not None:
            discovery_architecture = build_step.resolved_variables.get("architecture")
            if discovery_architecture:
                return discovery_architecture
        return await self._get_step_output(run, self._architecture_step_id, trace_id=trace_id)

    async def _restore_repair_memory_references(
        self, run: WorkflowRunResult, *, trace_id: str
    ) -> None:
        """Restore missing workflow memory keys from durable completed steps."""

        agent_registry = getattr(self._orchestrator, "agent_registry", None)
        memory_service = getattr(self._orchestrator, "memory_service", None)
        if agent_registry is None or memory_service is None:
            return

        orchestrator_agent = get_enabled_agent(agent_registry, "genie-orchestrator")
        classifications = {
            self._requirements_step_id: "requirement",
            self._architecture_step_id: "architecture_finding",
        }
        for step_id, classification in classifications.items():
            existing = await memory_service.shared.read(
                requesting_agent=orchestrator_agent,
                session_id=run.session_id,
                trace_id=trace_id,
                key=step_id,
            )
            if existing:
                continue
            step = next(
                (
                    result
                    for result in run.step_results
                    if result.step_id == step_id and result.status == "completed"
                ),
                None,
            )
            build_step = next(
                (result for result in run.step_results if result.step_id == self._build_step_id),
                None,
            )
            resolved_name = (
                "requirements" if step_id == self._requirements_step_id else "architecture"
            )
            if (
                step is None
                and build_step is not None
                and build_step.resolved_variables.get(resolved_name)
            ):
                continue
            if step is None or not step.output_text:
                raise UnknownWorkflowRunError(
                    f"Automatic fidelity repair cannot restore required workflow output '{step_id}'."
                )
            await memory_service.shared.write(
                agent=orchestrator_agent,
                session_id=run.session_id,
                trace_id=trace_id,
                key=step_id,
                classification=classification,
                content={"output_text": step.output_text},
                approval_status="approved",
                evidence_references=[f"workflow-run:{run.workflow_run_id}:{step_id}"],
            )

    async def _repair_prototype(
        self,
        *,
        run: WorkflowRunResult,
        pipeline_run: DeploymentPipelineRun,
        trace_id: str,
        evidence: str,
    ) -> WorkflowRunResult:
        approved_requirements = await self._get_approved_requirements(run, trace_id=trace_id)
        instruction = (
            "Regenerate the prototype to resolve every deterministic validation or deployed "
            "acceptance failure below. "
            "Keep every approved requirement in scope, preserve its REQ id, and fix the actual "
            "implementation rather than weakening or removing tests.\n\n"
            f"Approved requirements:\n{approved_requirements}\n\n"
            f"Observed failure evidence:\n{evidence[-12_000:]}"
        )
        await self._restore_repair_memory_references(run, trace_id=trace_id)
        repaired = await self._orchestrator.resume_workflow(
            workflow_run_id=run.workflow_run_id,
            session_id=run.session_id,
            trace_id=trace_id,
            step_inputs={
                self._build_step_id: WorkflowStepInput(
                    step_id=self._build_step_id,
                    variables={
                        "user_message": instruction,
                        "previous_build_output": "",
                    },
                )
            },
        )
        build_step = next(
            (
                result
                for result in repaired.step_results
                if result.step_id == self._build_step_id and result.status == "completed"
            ),
            None,
        )
        if build_step is None:
            raise UnknownWorkflowRunError(
                "Automatic fidelity repair did not produce a completed build-solution step."
            )
        return repaired

    async def _execute_steps(
        self,
        *,
        pipeline_run: DeploymentPipelineRun,
        run: WorkflowRunResult,
        mission_slug: str,
        mission_title: str,
        backend_root: Path,
        frontend_root: Path,
        trace_id: str,
        resume_from_step: str | None = None,
    ) -> None:
        """Executes the deployment pipeline steps in order.

        If ``resume_from_step`` is provided, only executes from that step onward,
        skipping already-completed prior steps. All steps after the resume point
        are reset to "not-started" status.
        """
        orchestrator_foundry_name = self._agent_foundry_names.get(pipeline_run.id, {}).get(
            "orchestrator", "orchestrator"
        )
        test_output_text = self._generated_test_outputs.get(pipeline_run.id, "")

        # Determine the starting index based on resume_from_step
        start_index = 0
        if resume_from_step:
            try:
                start_index = DEPLOYMENT_STEP_ORDER.index(resume_from_step)
                # Reset all steps from the resume point onward to "not-started"
                for step_id in DEPLOYMENT_STEP_ORDER[start_index:]:
                    step_result = self._step_result(pipeline_run, step_id)
                    step_result.status = "pending"
                    step_result.error = None
                    step_result.detail = ""
                    step_result.started_at = None
                    step_result.completed_at = None
            except ValueError:
                # Invalid step ID provided, start from beginning
                start_index = 0

        for step_id in DEPLOYMENT_STEP_ORDER[start_index:]:
            await self._publish(pipeline_run, step_id=step_id, event_type="step_started")
            step_result = self._step_result(pipeline_run, step_id)
            step_result.status = "running"
            step_result.started_at = datetime.now(UTC)
            pipeline_run.updated_at = step_result.started_at
            await self._persist_run(pipeline_run)

            try:
                if step_id == "generate-access-policy":
                    document = await self._access_policy_service.generate(
                        mission_id=mission_slug,
                        resource_group_name=pipeline_run.resource_group_name,
                        resource_tags={
                            "genie-managed-by": "genie",
                            "genie-prototype-id": pipeline_run.id,
                            "genie-owner-id": pipeline_run.owner_user_id,
                            "genie-expires-at": (
                                pipeline_run.expires_at.isoformat()
                                if pipeline_run.expires_at is not None
                                else ""
                            ),
                        },
                    )
                    pipeline_run.access_policy = document
                    detail = f"Generated least-access policy for {len(document.agents)} agent(s) with managed identity {document.mission_identity.identity_name if document.mission_identity else 'unknown'}."

                elif step_id == "provision-foundry-agents":
                    architecture_document = await self._get_approved_architecture(
                        run, trace_id=trace_id
                    )
                    build_output_text = await self._get_step_output(
                        run, self._build_step_id, trace_id=trace_id
                    )
                    try:
                        materialized = materialize_build(build_output_text)
                    except MaterializedCodeError as exc:
                        raise _GeneratedBuildRepairNeeded(str(exc)) from exc
                    agent_names = list(materialized.agent_modules.keys())
                    if materialized.orchestrator_module is not None:
                        agent_names.append("orchestrator")

                    # Mark every agent "running" before provisioning starts so
                    # the UI can show a real per-agent in-progress list while
                    # it is in flight, not just the step's own aggregate
                    # status. Agents are provisioned strictly one at a time
                    # (see MissionAgentProvisioningService.provision) - the
                    # callback below flips each one to "completed" the
                    # instant its own Foundry agent is created, so the list
                    # fills in live rather than jumping straight from "all
                    # running" to "all completed" at the very end.
                    pipeline_run.provisioned_agents = [
                        ProvisionedAgentStatus(agent_name=name, status="running")
                        for name in agent_names
                    ]
                    step_result.detail = (
                        f"Deploying agent 1 of {len(agent_names)} to Azure AI Foundry..."
                    )

                    async def _on_agent_provisioned(
                        record: ProvisionedMissionAgent,
                        *,
                        _step_result: DeploymentStepResult = step_result,
                        _agent_names: list[str] = agent_names,
                    ) -> None:
                        completed_so_far = 0
                        for index, agent in enumerate(pipeline_run.provisioned_agents):
                            if agent.agent_name == record.agent_name:
                                pipeline_run.provisioned_agents[index] = ProvisionedAgentStatus(
                                    agent_name=agent.agent_name,
                                    status="completed",
                                    foundry_agent_name=record.foundry_agent_name,
                                )
                            if pipeline_run.provisioned_agents[index].status == "completed":
                                completed_so_far += 1
                        if completed_so_far < len(_agent_names):
                            _step_result.detail = (
                                f"Deploying agent {completed_so_far + 1} of {len(_agent_names)} "
                                "to Azure AI Foundry..."
                            )

                    provisioned = await self._mission_agent_provisioning_service.provision(
                        mission_slug=mission_slug,
                        agent_names=agent_names,
                        architecture_document=architecture_document,
                        on_agent_provisioned=_on_agent_provisioned,
                        model_deployment_ref=_approved_model_deployment_ref(run),
                    )
                    provisioned_by_name = {record.agent_name: record for record in provisioned}
                    pipeline_run.provisioned_agents = [
                        ProvisionedAgentStatus(
                            agent_name=name,
                            status="completed" if name in provisioned_by_name else "failed",
                            foundry_agent_name=(
                                provisioned_by_name[name].foundry_agent_name
                                if name in provisioned_by_name
                                else None
                            ),
                        )
                        for name in agent_names
                    ]
                    orchestrator_record = next(
                        (r for r in provisioned if r.agent_name == "orchestrator"), None
                    )
                    if orchestrator_record is not None:
                        orchestrator_foundry_name = orchestrator_record.foundry_agent_name
                    self._materialized_builds[pipeline_run.id] = materialized
                    self._agent_foundry_names[pipeline_run.id] = {
                        record.agent_name: record.foundry_agent_name for record in provisioned
                    }
                    detail = f"Provisioned {len(provisioned)} Foundry agent(s) for this mission."

                elif step_id == "deploy-backend-service":
                    materialized = self._materialized_builds[pipeline_run.id]
                    scaffold = generate_backend_service_scaffold(
                        mission_title=mission_title,
                        orchestrator_agent_name=orchestrator_foundry_name,
                        agent_foundry_names=self._agent_foundry_names[pipeline_run.id],
                    )
                    materialized.write_to_directory(backend_root, backend_service_scaffold=scaffold)

                    async def _on_backend_progress(
                        message: str, *, _step_result: DeploymentStepResult = step_result
                    ) -> None:
                        _step_result.detail = message

                    mission_identity_resource_id = (
                        pipeline_run.access_policy.mission_identity.identity_resource_id
                        if pipeline_run.access_policy
                        and pipeline_run.access_policy.mission_identity
                        else None
                    )
                    backend_result = await self._backend_deployment_service.deploy(
                        mission_slug=mission_slug,
                        build_root=backend_root,
                        mission_identity_resource_id=mission_identity_resource_id,
                        on_progress=_on_backend_progress,
                    )
                    pipeline_run.backend_url = backend_result.backend_url
                    detail = (
                        f"Backend deployed at {backend_result.backend_url}, integrated with "
                        f"orchestrator agent '{orchestrator_foundry_name}'."
                    )

                elif step_id == "sync-frontend-integration":
                    materialized = self._materialized_builds[pipeline_run.id]
                    frontend_root.mkdir(parents=True, exist_ok=True)
                    (frontend_root / "MissionApp.tsx").write_text(
                        materialized.ui_component or "", encoding="utf-8"
                    )
                    (frontend_root / "index.html").write_text(
                        _FRONTEND_INDEX_HTML_TEMPLATE.format(
                            mission_title=html.escape(mission_title)
                        ),
                        encoding="utf-8",
                    )
                    (frontend_root / "package.json").write_text(
                        _FRONTEND_PACKAGE_JSON, encoding="utf-8"
                    )
                    (frontend_root / "tsconfig.json").write_text(
                        _FRONTEND_TSCONFIG_JSON, encoding="utf-8"
                    )
                    (frontend_root / "vite.config.ts").write_text(
                        _FRONTEND_VITE_CONFIG, encoding="utf-8"
                    )
                    src_root = frontend_root / "src"
                    src_root.mkdir(parents=True, exist_ok=True)
                    (src_root / "main.tsx").write_text(_FRONTEND_MAIN_TSX, encoding="utf-8")
                    (src_root / "env.d.ts").write_text(_FRONTEND_ENV_D_TS, encoding="utf-8")
                    (src_root / "styles.css").write_text(_FRONTEND_STYLES_CSS, encoding="utf-8")
                    public_root = frontend_root / "public"
                    public_root.mkdir(parents=True, exist_ok=True)
                    # Specialist agent display names (never "orchestrator" -
                    # that's the internal coordinator, not shown as its own
                    # collaborator) - lets the deterministic shell render a
                    # real, accurate live Agent Collaboration panel without
                    # depending on the LLM-generated UI to invent/describe
                    # its own agent roster correctly.
                    mission_agent_names = [
                        name
                        for name in self._agent_foundry_names.get(pipeline_run.id, {})
                        if name != "orchestrator"
                    ]
                    (public_root / "runtime-config.js").write_text(
                        f'window.__MISSION_BACKEND_URL__ = "{pipeline_run.backend_url}";\n'
                        f"window.__MISSION_TITLE__ = {json.dumps(mission_title)};\n"
                        f"window.__MISSION_AGENTS__ = {json.dumps(mission_agent_names)};\n",
                        encoding="utf-8",
                    )
                    detail = f"Frontend wired to real backend URL {pipeline_run.backend_url}."

                elif step_id == "deploy-frontend-app":

                    async def _on_frontend_progress(
                        message: str, *, _step_result: DeploymentStepResult = step_result
                    ) -> None:
                        _step_result.detail = message

                    frontend_deployment_arguments = {
                        "mission_slug": mission_slug,
                        "ui_root": frontend_root,
                        "on_progress": _on_frontend_progress,
                        "mission_identity_resource_id": (
                            pipeline_run.access_policy.mission_identity.identity_resource_id
                            if pipeline_run.access_policy
                            and pipeline_run.access_policy.mission_identity
                            else None
                        ),
                    }
                    frontend_result = await self._frontend_deployment_service.deploy(
                        **frontend_deployment_arguments
                    )
                    pipeline_run.frontend_url = frontend_result.frontend_url
                    await self._backend_deployment_service.configure_gateway_frontend_origin(
                        mission_slug=mission_slug,
                        frontend_origin=frontend_result.frontend_url,
                    )
                    detail = f"Frontend deployed at {frontend_result.frontend_url}."

                elif step_id == "generate-test-suite":
                    # Generated for real, right here, against the approved
                    # requirements and materialized pre-deployment build.
                    # Deploy & Launch is deliberately NOT
                    # a workflow step (see module docstring), so this calls
                    # the Test Generation Agent directly via
                    # AgentOrchestrator.execute_agent, the same
                    # outside-any-workflow-step execution path already used
                    # by Workshop's per-component "Regenerate" action.
                    build_output_text = await self._get_step_output(
                        run, self._build_step_id, trace_id=trace_id
                    )
                    requirements_text = await self._get_approved_requirements(
                        run, trace_id=trace_id
                    )
                    report = create_fidelity_report(
                        requirements_text,
                        max_repair_attempts=self._fidelity_max_repair_attempts,
                    )
                    if pipeline_run.fidelity_report is not None:
                        report = report.model_copy(
                            update={
                                "repair_attempts": pipeline_run.fidelity_report.repair_attempts,
                            }
                        )
                    if report.total_requirements == 0:
                        pipeline_run.fidelity_report = report
                        raise DeploymentPipelineStepFailedError(
                            "Requirement fidelity cannot run because the approved baseline "
                            "contains no REQ IDs. Re-run Requirement Discovery and approve "
                            "the resulting requirements before deployment."
                        )
                    generation_result = await self._orchestrator.execute_agent(
                        agent_id="test-generation-agent",
                        prompt_id="test-generation-v1",
                        variables={
                            "artifact": build_output_text,
                            "requirements": requirements_text,
                            "user_message": (
                                "Generate black-box acceptance tests against the real deployed "
                                "prototype. Read its URLs only from MISSION_BACKEND_URL and "
                                "MISSION_FRONTEND_URL environment variables. Exercise real HTTP "
                                "behavior. Do not use mocks, patches, monkeypatch, response "
                                "interceptors, fabricated responses, or static assertions."
                            ),
                        },
                        session_id=pipeline_run.session_id,
                        trace_id=pipeline_run.id,
                    )
                    test_output_text = generation_result.output_text
                    modules = extract_test_modules(test_output_text)
                    if not has_pytest_discoverable_tests(modules):
                        correction_result = await self._orchestrator.execute_agent(
                            agent_id="test-generation-agent",
                            prompt_id="test-generation-v1",
                            variables={
                                "artifact": build_output_text,
                                "requirements": requirements_text,
                                "user_message": (
                                    "Your prior response contained no pytest-discoverable Python test. "
                                    "Return one or more fenced python blocks containing module-level "
                                    "test_<name> functions with real assertions."
                                ),
                            },
                            session_id=pipeline_run.session_id,
                            trace_id=pipeline_run.id,
                        )
                        test_output_text = correction_result.output_text
                        modules = extract_test_modules(test_output_text)
                    if not has_pytest_discoverable_tests(modules):
                        raise DeploymentPipelineStepFailedError(
                            "Test Generation Agent did not produce a pytest-discoverable test function "
                            "after a corrective retry."
                        )
                    report = record_test_coverage(
                        report,
                        modules,
                        minimum_coverage_percent=self._fidelity_min_coverage_percent,
                    )
                    missing_test_ids = [
                        item.requirement_id
                        for item in report.requirements
                        if item.status == "missing"
                    ]
                    # Large approved-requirement sets can exceed what the agent
                    # covers in a single completion; give it the same repair
                    # budget used later for whole-prototype fidelity repairs
                    # instead of giving up after exactly one corrective retry.
                    # Each retry only asks for the STILL-missing IDs and its
                    # new modules are ACCUMULATED alongside every earlier
                    # completion's modules (never discarded) - a "complete
                    # replacement suite" re-ask made large gaps unrecoverable
                    # because every retry had to re-cover already-covered
                    # requirements too within the same completion-length
                    # budget that produced the gap in the first place.
                    coverage_retry = 0
                    while (
                        report.coverage_percent < self._fidelity_min_coverage_percent
                        and coverage_retry < self._fidelity_max_repair_attempts
                    ):
                        coverage_retry += 1
                        correction_result = await self._orchestrator.execute_agent(
                            agent_id="test-generation-agent",
                            prompt_id="test-generation-v1",
                            variables={
                                "artifact": build_output_text,
                                "requirements": requirements_text,
                                "user_message": (
                                    "Your prior suite omitted these approved requirement IDs: "
                                    + ", ".join(missing_test_ids)
                                    + ". Return ONLY new test module(s) covering these still-"
                                    "missing requirement IDs - do not repeat tests for "
                                    "requirement IDs you already covered. Every executable "
                                    "test function name must include its normalized requirement ID "
                                    "(for example, REQ-001 must use test_req_001_<behavior>) and "
                                    "must assert that requirement's real behavior."
                                ),
                            },
                            session_id=pipeline_run.session_id,
                            trace_id=pipeline_run.id,
                        )
                        test_output_text = test_output_text + "\n\n" + correction_result.output_text
                        modules = extract_test_modules(test_output_text)
                        report = record_test_coverage(
                            report,
                            modules,
                            minimum_coverage_percent=self._fidelity_min_coverage_percent,
                        )
                        missing_test_ids = [
                            item.requirement_id
                            for item in report.requirements
                            if item.status == "missing"
                        ]
                    self._generated_test_outputs[pipeline_run.id] = test_output_text
                    pipeline_run.fidelity_report = report
                    if (
                        not has_pytest_discoverable_tests(modules)
                        or report.coverage_percent < self._fidelity_min_coverage_percent
                    ):
                        raise DeploymentPipelineStepFailedError(
                            "Generated test suite does not meet the minimum executable coverage "
                            f"threshold of {self._fidelity_min_coverage_percent:g}%; actual "
                            f"coverage is {report.coverage_percent:g}%; missing requirement ids: "
                            + ", ".join(missing_test_ids)
                        )
                    if pipeline_run.backend_url and pipeline_run.backend_url.startswith("https://"):
                        materialized = self._materialized_builds[pipeline_run.id]
                        ui_component = materialized.ui_component or ""
                        require_attachment_handoff = bool(
                            re.search(r"\btype\s*=\s*['\"]file['\"]", ui_component)
                        )
                        real_action_errors = validate_real_action_tests(
                            modules,
                            require_attachment_handoff=require_attachment_handoff,
                        )
                        real_action_retry = 0
                        while (
                            real_action_errors
                            and real_action_retry < self._fidelity_max_repair_attempts
                        ):
                            real_action_retry += 1
                            correction_result = await self._orchestrator.execute_agent(
                                agent_id="test-generation-agent",
                                prompt_id="test-generation-v1",
                                variables={
                                    "artifact": build_output_text,
                                    "requirements": requirements_text,
                                    "user_message": (
                                        "Your prior suite failed this fail-closed check: "
                                        + " ".join(real_action_errors)
                                        + " Return a complete replacement suite. Every Python "
                                        "test must exercise the real deployed prototype over "
                                        "real HTTP using MISSION_BACKEND_URL and/or "
                                        "MISSION_FRONTEND_URL from the environment - never "
                                        "unittest.mock, MagicMock, patch(), monkeypatch, respx, "
                                        "responses, or any other interception library."
                                    ),
                                },
                                session_id=pipeline_run.session_id,
                                trace_id=pipeline_run.id,
                            )
                            test_output_text = correction_result.output_text
                            modules = extract_test_modules(test_output_text)
                            if not has_pytest_discoverable_tests(modules):
                                real_action_errors = validate_real_action_tests(
                                    modules,
                                    require_attachment_handoff=require_attachment_handoff,
                                )
                                continue
                            report = record_test_coverage(
                                report,
                                modules,
                                minimum_coverage_percent=self._fidelity_min_coverage_percent,
                            )
                            self._generated_test_outputs[pipeline_run.id] = test_output_text
                            pipeline_run.fidelity_report = report
                            real_action_errors = validate_real_action_tests(
                                modules,
                                require_attachment_handoff=require_attachment_handoff,
                            )
                        if real_action_errors:
                            raise DeploymentPipelineStepFailedError(
                                "Generated acceptance tests are not real-action tests: "
                                + " ".join(real_action_errors)
                            )
                        # The real-action repair loop replaces the whole suite on
                        # every attempt to purge mocks/patches, which can regress
                        # the requirement coverage the earlier loop secured -
                        # re-verify it here rather than silently shipping a gap.
                        final_missing_ids = [
                            item.requirement_id
                            for item in report.requirements
                            if item.status == "missing"
                        ]
                        if report.coverage_percent < self._fidelity_min_coverage_percent:
                            raise DeploymentPipelineStepFailedError(
                                "Generated test suite does not meet the minimum executable coverage "
                                f"threshold of {self._fidelity_min_coverage_percent:g}%; actual "
                                f"coverage is {report.coverage_percent:g}%; missing requirement ids: "
                                + ", ".join(final_missing_ids)
                            )
                    detail = (
                        f"Generated {len(modules)} requirement acceptance test module(s) "
                        "for the real deployed prototype."
                    )

                elif step_id == "execute-test-suite":
                    test_output_text = self._generated_test_outputs.get(
                        pipeline_run.id, test_output_text
                    )
                    modules = extract_test_modules(test_output_text)
                    timeout_minutes = max(1, round(self._test_execution_service.timeout_seconds / 60))
                    step_result.detail = (
                        f"Running {len(modules)} generated test module(s) with pytest against the "
                        f"real deployed prototype (up to {timeout_minutes} minute(s))..."
                    )
                    runtime_environment = {
                        "MISSION_BACKEND_URL": pipeline_run.backend_url or "",
                        "MISSION_FRONTEND_URL": pipeline_run.frontend_url or "",
                    }
                    test_result = await self._test_execution_service.run_tests(
                        build_root=backend_root,
                        test_output_text=test_output_text,
                        runtime_environment=runtime_environment,
                    )
                    pipeline_run.test_summary = test_result.summary
                    # ``success`` fails closed even when pytest itself exits 0
                    # (e.g. zero test functions were actually collected) - see
                    # TestExecutionResult.success's docstring.
                    report = pipeline_run.fidelity_report
                    if report is None:
                        raise DeploymentPipelineStepFailedError(
                            "Requirement fidelity report is unavailable after test execution."
                        )
                    final_failure = report.repair_attempts >= report.max_repair_attempts
                    pipeline_run.fidelity_report = record_fidelity_execution(
                        report,
                        success=test_result.success,
                        summary=test_result.summary,
                        passed_test_names=test_result.passed_test_names,
                        failed_test_names=test_result.failed_test_names,
                        errored_test_names=test_result.errored_test_names,
                        skipped_test_names=test_result.skipped_test_names,
                        final_failure=final_failure,
                        execution_incomplete=test_result.timed_out,
                        minimum_coverage_percent=self._fidelity_min_coverage_percent,
                    )
                    if pipeline_run.fidelity_report.status == "passed":
                        detail = test_result.summary
                    else:
                        if final_failure:
                            raise DeploymentPipelineStepFailedError(
                                "Requirement fidelity gate failed after "
                                f"{report.repair_attempts} automatic repair attempt(s): "
                                f"{test_result.summary}"
                            )
                        raise _RequirementFidelityRepairNeeded(
                            summary=(
                                "Requirement fidelity evidence failed; automatically regenerating "
                                f"the prototype (attempt {report.repair_attempts + 1} of "
                                f"{report.max_repair_attempts})."
                            ),
                            evidence=test_result.raw_output or test_result.summary,
                        )

                elif step_id == "run-security-scan":
                    step_result.detail = "Scanning the deployed backend build's dependencies and code for vulnerabilities..."
                    scan_result = await self._security_scan_service.scan(build_root=backend_root)
                    pipeline_run.security_findings_count = len(scan_result.findings)
                    if scan_result.blocking:
                        step_result.status = "failed"
                        step_result.error = scan_result.summary
                        step_result.completed_at = datetime.now(UTC)
                        await self._publish(
                            pipeline_run,
                            step_id=step_id,
                            event_type="step_failed",
                            error=scan_result.summary,
                        )
                        raise DeploymentPipelineStepFailedError(
                            f"Security scan found blocking findings: {scan_result.summary}"
                        )
                    detail = scan_result.summary

                elif step_id == "launch-mission":
                    report = pipeline_run.fidelity_report
                    if (
                        report is None
                        or report.status != "passed"
                        or report.coverage_percent < self._fidelity_min_coverage_percent
                        or report.pass_percent != 100
                    ):
                        raise DeploymentPipelineStepFailedError(
                            "Launch blocked: requirement fidelity must meet the minimum executable "
                            f"coverage threshold of {self._fidelity_min_coverage_percent:g}% and "
                            "have 100% passing executable evidence."
                        )
                    pipeline_run.launch_url = pipeline_run.frontend_url
                    detail = f"Mission launched at {pipeline_run.launch_url}."

                else:  # pragma: no cover - DEPLOYMENT_STEP_ORDER is exhaustive
                    detail = ""

            except DeploymentPipelineStepFailedError as exc:
                if step_result.status != "failed":
                    step_result.status = "failed"
                    step_result.error = str(exc)
                    step_result.completed_at = datetime.now(UTC)
                    await self._publish(
                        pipeline_run,
                        step_id=step_id,
                        event_type="step_failed",
                        error=str(exc),
                    )
                pipeline_run.updated_at = datetime.now(UTC)
                await self._persist_run(pipeline_run)
                raise
            except Exception as exc:
                # Catch every failure here (not just the specific, expected
                # error types) - a real Azure SDK network/timeout error would
                # otherwise skip this step's own status update entirely,
                # leaving it stuck showing "Running..." forever even though
                # the pipeline as a whole has already been marked "failed"
                # (see the `except Exception` in `start()` below) - a
                # confusing, inconsistent UI. Every step must always resolve
                # to a terminal, detailed status (completed or failed).
                if step_id == "provision-foundry-agents":
                    # Provisioning is atomic (all-or-nothing, see
                    # MissionAgentProvisioningService.provision) - any agent
                    # still "running" here never actually finished.
                    pipeline_run.provisioned_agents = [
                        agent.model_copy(update={"status": "failed"})
                        if agent.status == "running"
                        else agent
                        for agent in pipeline_run.provisioned_agents
                    ]
                step_result.status = "failed"
                step_result.error = str(exc)
                step_result.completed_at = datetime.now(UTC)
                await self._publish(
                    pipeline_run, step_id=step_id, event_type="step_failed", error=str(exc)
                )
                pipeline_run.updated_at = datetime.now(UTC)
                await self._persist_run(pipeline_run)
                raise

            step_result.status = "completed"
            step_result.detail = detail
            step_result.completed_at = datetime.now(UTC)
            pipeline_run.updated_at = step_result.completed_at
            await self._persist_run(pipeline_run)
            await self._publish(
                pipeline_run, step_id=step_id, event_type="step_completed", output_preview=detail
            )

    def _step_result(
        self, pipeline_run: DeploymentPipelineRun, step_id: DeploymentStepId
    ) -> DeploymentStepResult:
        return next(step for step in pipeline_run.steps if step.step_id == step_id)

    async def _publish(
        self,
        pipeline_run: DeploymentPipelineRun,
        *,
        step_id: DeploymentStepId,
        event_type: WorkflowStreamEventType,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._event_bus.publish(
            WorkflowStreamEvent(
                event_type=event_type,
                session_id=pipeline_run.session_id,
                workflow_run_id=pipeline_run.id,
                step_id=step_id,
                agent_id=_PIPELINE_AGENT_ID,
                output_preview=output_preview,
                error=error,
            )
        )


def create_deployment_pipeline_service(
    *,
    settings: Settings,
    orchestrator: AgentOrchestrator,
    session_service: SessionService,
    event_bus: WorkflowEventBus,
    access_policy_service: AccessPolicyService,
    mission_identity_service: MissionIdentityService | NullMissionIdentityService,
    mission_agent_provisioning_service: MissionAgentProvisioningService
    | NullMissionAgentProvisioningService,
    backend_deployment_service: BackendDeploymentService | NullBackendDeploymentService,
    frontend_deployment_service: (
        ContainerAppFrontendDeploymentService | NullContainerAppFrontendDeploymentService
    ),
    run_repository: DeploymentRunRepository | None = None,
) -> DeploymentPipelineService:
    """Wires a ``DeploymentPipelineService`` from already-constructed collaborators.

    Every Azure-calling collaborator is constructed by its own factory
    (``create_backend_deployment_service`` etc.) before being passed in here
    - this factory only assembles the pipeline glue, it never chooses
    real-vs-Null itself.
    """

    return DeploymentPipelineService(
        orchestrator=orchestrator,
        session_service=session_service,
        event_bus=event_bus,
        access_policy_service=access_policy_service,
        mission_identity_service=mission_identity_service,
        mission_agent_provisioning_service=mission_agent_provisioning_service,
        backend_deployment_service=backend_deployment_service,
        frontend_deployment_service=frontend_deployment_service,
        run_repository=run_repository,
        prototype_default_ttl_days=settings.prototype_default_ttl_days,
        prototype_max_active_per_owner=settings.prototype_max_active_per_owner,
        test_execution_service=TestExecutionService(
            timeout_seconds=settings.deployment_test_execution_timeout_seconds
        ),
        security_scan_service=SecurityScanService(),
        build_workspace_root=settings.deployment_build_workspace_root,
        fidelity_max_repair_attempts=settings.deployment_fidelity_max_repair_attempts,
        fidelity_min_coverage_percent=settings.deployment_fidelity_min_coverage_percent,
    )
