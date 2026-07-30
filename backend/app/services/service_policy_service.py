"""Service policy application service.

Consolidates the real, externally configured policies that govern a build
once it is deployed - the approval checkpoints gating deployment (``config/
policies/approval_policy.yaml``, together with each checkpoint's actual,
real per-session approval status), the governance tracking policy every
agent execution is subject to (``config/policies/governance_policy.yaml``),
and the memory tier access policy the deployed agents' memory reads/writes
are subject to (``config/policies/memory_policy.yaml``) - together with the
Governance Reviewer agent's own real ``SECURITY_REVIEW:`` narrative
describing the access control it determined this specific build needs
before deployment, parsed directly from the ``governance-review`` step's
own output text (the same "agents return marker-line text, Genie only
reports what the agent itself stated" convention already established by
``app.services.peer_review_service``). Nothing here is ever invented,
estimated, or hardcoded.
"""
from __future__ import annotations

import re
from typing import Final

from app.governance.approval_service import ApprovalService
from app.governance.governance_service import GovernanceService
from app.memory.memory_access_policy_service import MemoryAccessPolicyService
from app.models.service_policy import DeploymentCheckpointStatus, ServicePolicy, ServicePolicyStatus
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = ["ServicePolicyService", "create_service_policy_service"]

_DEPLOYMENT_CHECKPOINT_IDS: Final[tuple[str, ...]] = (
    "build-review-approval",
    "final-output-approval",
)

_SECURITY_REVIEW_PATTERN: Final = re.compile(r"SECURITY_REVIEW:\s*(.+)", re.IGNORECASE)


def _extract_access_control_summary(output_text: str) -> str:
    """Extracts the Governance Reviewer's own ``SECURITY_REVIEW:`` sentence(s)
    summarizing the security assessment and the access control it decided
    this build needs before deployment - read verbatim, never invented."""

    match = _SECURITY_REVIEW_PATTERN.search(output_text)
    return match.group(1).strip() if match else ""


class ServicePolicyService:
    """Builds the real, consolidated Service Policy view for a workflow run."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        session_service: SessionService,
        approval_service: ApprovalService,
        governance_service: GovernanceService,
        memory_policy_service: MemoryAccessPolicyService,
        governance_review_step_id: str,
        deployment_checkpoint_ids: tuple[str, ...] = _DEPLOYMENT_CHECKPOINT_IDS,
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service
        self._approval_service = approval_service
        self._governance_service = governance_service
        self._memory_policy_service = memory_policy_service
        self._governance_review_step_id = governance_review_step_id
        self._deployment_checkpoint_ids = deployment_checkpoint_ids

    async def get_service_policy(
        self, *, session_id: str, requesting_user_id: str, workflow_run_id: str
    ) -> ServicePolicy:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"No workflow run '{workflow_run_id}' found.")

        session_requests = await self._approval_service.list_requests_for_session(session_id)
        checkpoints: list[DeploymentCheckpointStatus] = []
        for checkpoint_id in self._deployment_checkpoint_ids:
            checkpoint = self._approval_service.checkpoint(checkpoint_id)
            matching = [request for request in session_requests if request.checkpoint_id == checkpoint_id]
            status = matching[-1].status if matching else "not_reached"
            checkpoints.append(
                DeploymentCheckpointStatus(
                    checkpoint_id=checkpoint.id,
                    name=checkpoint.name,
                    description=checkpoint.description,
                    required=checkpoint.required,
                    status=status,
                )
            )

        step = next(
            (result for result in run.step_results if result.step_id == self._governance_review_step_id),
            None,
        )
        status: ServicePolicyStatus = "pending"
        access_control_summary = ""
        assessed_by_agent_id: str | None = None
        if step is not None and step.status == "completed":
            status = "ready"
            access_control_summary = _extract_access_control_summary(step.output_text or "")
            assessed_by_agent_id = step.agent_id

        policy_document = self._governance_service.policy
        return ServicePolicy(
            status=status,
            deployment_checkpoints=checkpoints,
            governance_tracking=policy_document.agent_execution_governance,
            decision_lineage=policy_document.decision_lineage,
            session_replay=policy_document.session_replay,
            memory_access_policy=self._memory_policy_service.document,
            access_control_summary=access_control_summary,
            assessed_by_agent_id=assessed_by_agent_id,
        )


def create_service_policy_service(
    *,
    orchestrator: AgentOrchestrator,
    session_service: SessionService,
    approval_service: ApprovalService,
    governance_service: GovernanceService,
    memory_policy_service: MemoryAccessPolicyService,
    governance_review_step_id: str = "governance-review",
    deployment_checkpoint_ids: tuple[str, ...] = _DEPLOYMENT_CHECKPOINT_IDS,
) -> ServicePolicyService:
    return ServicePolicyService(
        orchestrator=orchestrator,
        session_service=session_service,
        approval_service=approval_service,
        governance_service=governance_service,
        memory_policy_service=memory_policy_service,
        governance_review_step_id=governance_review_step_id,
        deployment_checkpoint_ids=deployment_checkpoint_ids,
    )
