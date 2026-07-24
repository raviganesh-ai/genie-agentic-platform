"""Output/deliverable application service.

Implements the "Final Output Center"/"Output APIs" requirement: assembles
customer-facing deliverables (executive summary, requirements package,
architecture package, roadmap package, final output package) purely by
delegating to the unmodified Phase 6
``AgentOrchestrator.generate_deliverable_package`` - every section's
content is exactly what an Azure-hosted agent already produced during the
workflow run; this service generates nothing itself.
"""
from __future__ import annotations

from typing import get_args

from app.models.workflow_models import DeliverablePackage, DeliverableType
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.services.session_service import SessionService
from app.services.workshop_service import UnknownWorkflowRunError

__all__ = ["OutputService", "create_output_service"]


class OutputService:
    """Generates and lists supported deliverable packages for a session's workflow run."""

    def __init__(
        self, *, orchestrator: AgentOrchestrator, session_service: SessionService
    ) -> None:
        self._orchestrator = orchestrator
        self._session_service = session_service

    def supported_deliverable_types(self) -> list[DeliverableType]:
        return list(get_args(DeliverableType))

    async def generate_deliverable(
        self,
        *,
        session_id: str,
        requesting_user_id: str,
        workflow_run_id: str,
        deliverable_type: DeliverableType,
    ) -> DeliverablePackage:
        await self._session_service.get_session(
            session_id=session_id, requesting_user_id=requesting_user_id
        )
        run = self._orchestrator.get_workflow_run(workflow_run_id)
        if run is None:
            raise UnknownWorkflowRunError(f"Unknown workflow run id '{workflow_run_id}'.")

        return self._orchestrator.generate_deliverable_package(
            workflow_run_result=run, deliverable_type=deliverable_type
        )


def create_output_service(
    *, orchestrator: AgentOrchestrator, session_service: SessionService
) -> OutputService:
    return OutputService(orchestrator=orchestrator, session_service=session_service)
