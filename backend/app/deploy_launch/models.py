"""Domain models for the Deploy & Launch pipeline.

Deploy & Launch is deliberately NOT an LLM-driven workflow step (see
``config/workflows/registry.yaml``'s top-of-file comment): it is a real,
deterministic, code-driven Azure provisioning pipeline - ``PipelineService``
(``app.deploy_launch.pipeline_service``) executes each named step in this
exact order against real Azure SDKs (or their Null/local equivalents when
the required settings are not configured, mirroring ``AzureAgentGateway`` /
``LocalAgentGateway``), never fabricating a result for a step it did not
actually perform.

Not to be confused with ``app.deployment`` (an unrelated, pre-existing
package that checks whether an Azure *subscription* has the resource
providers Genie's own infrastructure needs registered - a Phase 10
concern). This package deploys one customer *mission's generated build*,
long after Genie itself is already running.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEPLOYMENT_STEP_NAMES",
    "DEPLOYMENT_STEP_ORDER",
    "AccessPolicyDocument",
    "AgentAccessPolicy",
    "DeploymentPipelineRun",
    "DeploymentPipelineStatus",
    "DeploymentStepId",
    "DeploymentStepResult",
    "DeploymentStepStatus",
    "MissionIdentityInfo",
    "ProvisionedAgentStatus",
    "RequirementFidelityItem",
    "RequirementFidelityReport",
]

DeploymentStepId = Literal[
    "generate-access-policy",
    "provision-foundry-agents",
    "deploy-backend-service",
    "sync-frontend-integration",
    "deploy-frontend-app",
    "generate-test-suite",
    "execute-test-suite",
    "run-security-scan",
    "launch-mission",
]

# The fixed, ordered pipeline - every run executes exactly these nine steps,
# in this exact order, each with a meaningful customer-facing name.
DEPLOYMENT_STEP_ORDER: tuple[DeploymentStepId, ...] = (
    "generate-access-policy",
    "provision-foundry-agents",
    "deploy-backend-service",
    "sync-frontend-integration",
    "deploy-frontend-app",
    "generate-test-suite",
    "execute-test-suite",
    "run-security-scan",
    "launch-mission",
)

DEPLOYMENT_STEP_NAMES: dict[DeploymentStepId, str] = {
    "generate-access-policy": "Generate Access Policy & Least Access",
    "provision-foundry-agents": "Deploy Agents to Foundry",
    "deploy-backend-service": "Deploy Backend Service",
    "sync-frontend-integration": "Update Frontend Integrations",
    "deploy-frontend-app": "Deploy Frontend",
    "generate-test-suite": "Generate Requirement Acceptance Tests",
    "execute-test-suite": "Requirement Fidelity Gate",
    "run-security-scan": "Security Scan (Backend & Frontend)",
    "launch-mission": "Launch",
}

DeploymentStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
DeploymentPipelineStatus = Literal["pending", "running", "completed", "failed"]
PrototypeCleanupStatus = Literal["active", "deletion_pending", "deletion_failed"]
RequirementFidelityStatus = Literal["pending", "testing", "repairing", "passed", "failed"]
RequirementEvidenceStatus = Literal["pending", "covered", "passed", "failed", "missing"]


class RequirementFidelityItem(BaseModel):
    """Observed coverage and execution evidence for one approved requirement."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(pattern=r"^REQ-\d{3,}$")
    statement: str = Field(min_length=1)
    status: RequirementEvidenceStatus = "pending"
    test_names: list[str] = Field(default_factory=list)
    evidence: str = ""


class RequirementFidelityReport(BaseModel):
    """Deterministic launch gate calculated from approved IDs and real test results."""

    model_config = ConfigDict(extra="forbid")

    status: RequirementFidelityStatus = "pending"
    requirements: list[RequirementFidelityItem] = Field(default_factory=list)
    total_requirements: int = Field(ge=0)
    covered_requirements: int = Field(default=0, ge=0)
    passed_requirements: int = Field(default=0, ge=0)
    coverage_percent: float = Field(default=0, ge=0, le=100)
    pass_percent: float = Field(default=0, ge=0, le=100)
    repair_attempts: int = Field(default=0, ge=0)
    max_repair_attempts: int = Field(default=0, ge=0)
    gaps: list[str] = Field(default_factory=list)
    execution_summary: str = ""


class AgentAccessPolicy(BaseModel):
    """One agent's real, deterministic least-privilege access grant.

    Built directly from that agent's own ``AgentDefinition`` (never an LLM
    narrative) - see ``app.deploy_launch.access_policy_service``.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    memory_access: list[str] = Field(default_factory=list)


class MissionIdentityInfo(BaseModel):
    """One mission's real Azure managed identity and its RBAC role assignments.

    Populated by ``MissionIdentityService`` during the ``generate-access-policy``
    step - every grant here is a real, verifiable Azure RBAC role assignment,
    never fabricated or synthetic.
    """

    model_config = ConfigDict(extra="forbid")

    identity_name: str = Field(
        min_length=1, description="Azure resource name of the managed identity"
    )
    identity_principal_id: str = Field(
        min_length=1, description="Azure AD principal ID (object ID)"
    )
    identity_client_id: str = Field(min_length=1, description="Managed identity client ID (app ID)")
    identity_resource_id: str = Field(min_length=1, description="Full Azure resource ID")
    role_assignment_ids: list[str] = Field(default_factory=list)
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AccessPolicyDocument(BaseModel):
    """The real, consolidated least-access policy generated for one mission's build.

    Supersedes the old, LLM-narrative ``ServicePolicy`` concept: every
    grant here is derived deterministically from the catalog's own
    ``AgentDefinition.allowed_tools``/``memory_access`` (never invented),
    and the mission's own real Azure managed identity with its RBAC roles
    (never synthetic). Both are observable, verifiable facts.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mission_identity: MissionIdentityInfo | None = None
    agents: list[AgentAccessPolicy] = Field(default_factory=list)


class DeploymentStepResult(BaseModel):
    """One pipeline step's real, observed outcome."""

    model_config = ConfigDict(extra="forbid")

    step_id: DeploymentStepId
    name: str = Field(min_length=1)
    status: DeploymentStepStatus = "pending"
    detail: str = Field(default="")
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ProvisionedAgentStatus(BaseModel):
    """One mission agent's real, observed Foundry provisioning outcome.

    Populated during the ``provision-foundry-agents`` step - never a
    fabricated placeholder - so the UI can show per-agent progress (not
    just a single aggregate step status) as each agent's own
    ``foundry_agent_name`` becomes known.
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1)
    status: DeploymentStepStatus = "pending"
    foundry_agent_name: str | None = None


class PrototypeAuthenticationInfo(BaseModel):
    """Durable metadata needed to recover or retire a prototype auth boundary."""

    model_config = ConfigDict(extra="forbid")

    application_object_id: str = Field(min_length=1)
    service_principal_object_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    delegated_scope: str = Field(min_length=1)
    application_role_id: str = Field(min_length=1)
    mission_slug: str = Field(min_length=1)
    shared: bool = False
    frontend_redirect_uri: str | None = None


class DeploymentPipelineRun(BaseModel):
    """The full, real state of one mission's Deploy & Launch pipeline run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    owner_tenant_id: str = ""
    owner_object_id: str = ""
    mission_title: str | None = None
    mission_slug: str | None = None
    status: DeploymentPipelineStatus = "pending"
    cleanup_status: PrototypeCleanupStatus = "active"
    cleanup_error: str | None = None
    resource_group_name: str | None = None
    shared_authentication_slot: int | None = Field(default=None, ge=1)
    prototype_authentication: PrototypeAuthenticationInfo | None = None
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    steps: list[DeploymentStepResult] = Field(default_factory=list)
    access_policy: AccessPolicyDocument | None = None
    provisioned_agents: list[ProvisionedAgentStatus] = Field(default_factory=list)
    backend_url: str | None = None
    frontend_url: str | None = None
    launch_url: str | None = None
    test_summary: str | None = None
    fidelity_report: RequirementFidelityReport | None = None
    security_findings_count: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
