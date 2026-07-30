"""Service policy domain model for the Deploy & Launch phase.

Consolidates the real, externally configured policies that will govern a
build once it is deployed - shown alongside the generated build, Security
Assessment, Test Coverage, and Peer Review sections on the Governance
Center so a human reviewer can see exactly what will govern this solution
before approving the final deployment gate. Every field here is either:

1. copied verbatim from an already-loaded, already-enforced policy
   document (``config/policies/approval_policy.yaml``,
   ``config/policies/governance_policy.yaml``,
   ``config/policies/memory_policy.yaml`` - see
   ``app.services.service_policy_service``), together with each
   deployment checkpoint's actual, real per-session approval status, or
2. the Governance Reviewer agent's own real narrative text describing the
   access control it determined this specific build needs before
   deployment, read verbatim from its ``governance-review`` step output.

Nothing here is ever invented or estimated.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.governance.governance_service import (
    AgentExecutionGovernancePolicy,
    DecisionLineagePolicy,
    SessionReplayPolicy,
)
from app.memory.memory_access_policy_service import MemoryPolicyDocument
from app.models.approval_models import ApprovalRequestStatus

__all__ = [
    "DeploymentCheckpointStatus",
    "ServicePolicy",
    "ServicePolicyStatus",
]

ServicePolicyStatus = Literal["pending", "ready"]
"""
- pending: the governance-review step has not completed yet, so the
  Governance Reviewer's own access-control narrative is not yet available.
- ready: the step completed; the full service policy (including that
  narrative) is available.
"""

DeploymentCheckpointRuntimeStatus = ApprovalRequestStatus | Literal["not_reached"]


class DeploymentCheckpointStatus(BaseModel):
    """One ``approval_policy.yaml`` checkpoint relevant to deployment, together
    with its actual, real per-session/workflow-run status - never a
    fabricated or assumed status. ``not_reached`` means no approval request
    for this checkpoint has been raised for this session yet."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool
    status: DeploymentCheckpointRuntimeStatus


class ServicePolicy(BaseModel):
    """The real, consolidated policy that will govern this build once deployed."""

    model_config = ConfigDict(extra="forbid")

    status: ServicePolicyStatus
    deployment_checkpoints: list[DeploymentCheckpointStatus] = Field(default_factory=list)
    governance_tracking: AgentExecutionGovernancePolicy
    decision_lineage: DecisionLineagePolicy
    session_replay: SessionReplayPolicy
    memory_access_policy: MemoryPolicyDocument
    access_control_summary: str = Field(
        default="",
        description=(
            "The Governance Reviewer agent's own real narrative describing the "
            "access control it determined this build needs before deployment "
            "(read verbatim from its governance-review step output, never "
            "invented). Empty until that step completes."
        ),
    )
    assessed_by_agent_id: str | None = Field(
        default=None,
        description="The agent id that produced the governance-review step output, if it has run.",
    )
