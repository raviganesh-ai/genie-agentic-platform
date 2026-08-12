"""Centralized domain-error -> HTTP status mapping for every Phase 7 API router.

Registered once as a FastAPI exception handler (see ``app.main.create_app``)
for the shared ``RuntimeError`` base every Phase 1-6 domain exception
derives from, so individual routes never need their own try/except - they
simply call a service method and let a raised domain error propagate,
keeping every route thin per "API routes must remain thin" in
``.github/copilot-instructions.md``. Every message surfaced here already
originates from an existing domain exception with a safe, non-secret
message (see "Errors, logs, and health endpoints must never expose
secrets").
"""
from __future__ import annotations

import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.agents.foundry.errors import FoundryAgentSynchronizationError, FoundryUnavailableError
from app.deploy_launch.backend_deployment_service import BackendDeploymentError
from app.deploy_launch.code_materializer import MaterializedCodeError
from app.deploy_launch.frontend_deployment_service import FrontendDeploymentError
from app.deploy_launch.mission_agent_provisioning_service import MissionAgentProvisioningError
from app.deploy_launch.pipeline_service import (
    DeploymentApprovalBlockedError,
    DeploymentApprovalPendingError,
    DeploymentPipelineStepFailedError,
)
from app.governance.approval_service import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    UnknownApprovalCheckpointError,
    UnknownApprovalRequestError,
)
from app.memory.memory_models import MemoryAccessDeniedError
from app.orchestration.reanalysis_service import ReanalysisRoutingError
from app.orchestration.workflow_execution_service import UnknownWorkflowRunError
from app.services.customer_agent_provisioning_service import CustomerAgentProvisioningError
from app.services.session_service import (
    SessionAccessDeniedError,
    SessionNotFoundError,
    UploadNotFoundError,
)
from app.services.workshop_service import UnknownWorkflowRunError as WorkshopUnknownRunError

__all__ = ["domain_error_handler"]

logger = logging.getLogger(__name__)

_SERVICE_UNAVAILABLE_ERRORS = (
    CustomerAgentProvisioningError,
    FoundryUnavailableError,
    FoundryAgentSynchronizationError,
    MissionAgentProvisioningError,
    BackendDeploymentError,
    FrontendDeploymentError,
)

_NOT_FOUND_ERRORS = (
    SessionNotFoundError,
    UploadNotFoundError,
    UnknownWorkflowRunError,
    WorkshopUnknownRunError,
    UnknownApprovalCheckpointError,
    UnknownApprovalRequestError,
)

_FORBIDDEN_ERRORS = (SessionAccessDeniedError, DeploymentApprovalBlockedError)

_CONFLICT_ERRORS = (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    MemoryAccessDeniedError,
    DeploymentApprovalPendingError,
)

_UNPROCESSABLE_ERRORS = (MaterializedCodeError, DeploymentPipelineStepFailedError)


def _status_code_for(exc: Exception) -> int:
    if isinstance(exc, _FORBIDDEN_ERRORS):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, _NOT_FOUND_ERRORS):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, _CONFLICT_ERRORS):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, _SERVICE_UNAVAILABLE_ERRORS):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(exc, _UNPROCESSABLE_ERRORS):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if isinstance(exc, ReanalysisRoutingError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    return status.HTTP_400_BAD_REQUEST


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    status_code = _status_code_for(exc)
    correlation_id = request.headers.get("x-correlation-id") or request.query_params.get(
        "correlation_id"
    )
    logger.error(
        "Domain error handled for %s %s (status=%s, correlation_id=%s): %s: %s",
        request.method,
        request.url.path,
        status_code,
        correlation_id,
        type(exc).__name__,
        exc,
    )
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})
