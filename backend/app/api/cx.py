"""Customer-experience (cx) API: serves the generated sample prototype.

This is the ONLY externally shareable, customer-facing surface in Genie.
Every route requires a ``CustomerSessionClaims`` (see
``app.security.cx_dependencies``) scoped to exactly one ``session_id`` +
``workflow_run_id`` pair, minted only by an already-authenticated internal
user (``POST /sessions/{session_id}/cx-access`` in ``app.api.sessions``).
No route here can read any other customer's session - by design, so a
leaked/shared link can never do more than interact with the one workflow
run it was minted for.

Routes ``GET /app``, ``GET /status``, and ``GET /progress`` are read-only.
Two routes are customer-triggerable write actions:

- ``POST /reanalyze`` lets the customer (via the generated prototype's own
  UI) challenge a recommendation, request an alternative architecture, or
  ask for a lower-cost/higher-security/MVP/Fabric-first redesign - the
  exact "customer interacts with the agentic workflow" capability
  described in ``app.models.reanalysis_models``. It routes through the
  unmodified Phase 6 ``AgentOrchestrator.request_reanalysis`` (identical
  code path to the internal ``/sessions/{id}/workshop/reanalysis`` route).
- ``POST /chat`` lets the customer send a free-form message to one named
  agent, or to every agent in the workflow, mirroring the internal
  ``WorkshopService.chat_with_agent``/``chat_with_all_agents`` pattern:
  the message is supplied as a ``user_message`` step input variable and
  the session's workflow run is resumed - the agent's own reply is
  whatever the Azure-hosted agent produces during that resumed run, never
  synthesized here.

``GET /starter-kit`` is a third read-only route: it lets the customer
download a zip of their generated prototype plus a README and access
policy doc (Phase 3 of the call-transcript-to-live-prototype feature) -
see ``app.services.starter_kit_service``.

Both write routes are rate-limited per session
(``app.security.cx_rate_limiter``) since a leaked link must never be able
to flood agent-routing work, and never let the customer choose an
arbitrary workflow_run_id or session_id - both are pinned to the minted
token's claims, never taken from the request body.

If ``POST /sessions/{id}/cx-access`` provisioned a dedicated Foundry agent
fleet for this session (see ``CustomerAgentProvisioningService``), every
``/chat`` and ``/reanalyze`` interaction here automatically executes
against those dedicated agents rather than the shared catalog pool - no
route in this module needs to know or care which pool is in effect, since
that resolution happens inside ``AzureAgentGateway``.

The generated prototype HTML is exactly the ``output_text`` an Azure-hosted
agent (the already-provisioned ``ui-designer-agent``) produced during the
workflow run - this module performs no content generation itself. It is
rendered inside a sandboxed shell page with a strict Content-Security-Policy
to contain any script the agent's output includes (defense in depth against
prompt-injection-turned-script-injection), and is served with
``Cache-Control: no-store`` since the URL may carry a one-time access token.
"""
from __future__ import annotations

import html as html_module
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    get_agent_orchestrator,
    get_cx_rate_limiter,
    get_starter_kit_service,
)
from app.config.settings import Settings
from app.models.reanalysis_models import ReanalysisRequestType, ReanalysisResult
from app.models.workflow_models import WorkflowRunResult, WorkflowStepInput
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.cx_dependencies import CX_SESSION_COOKIE_NAME, get_current_customer_session
from app.security.cx_rate_limiter import CxRateLimiter
from app.security.cx_tokens import CustomerSessionClaims
from app.services.starter_kit_service import StarterKitError, StarterKitService

router = APIRouter(prefix="/cx/{session_id}", tags=["customer-experience"])


class CxReanalysisRequest(BaseModel):
    """A customer-submitted challenge/redesign request for their own workflow run."""

    model_config = ConfigDict(extra="forbid")

    request_type: ReanalysisRequestType
    target_recommendation_id: str | None = None
    rationale: str = Field(default="", max_length=4000)


class CxChatRequest(BaseModel):
    """A customer-submitted chat message for one agent, or every agent in the run."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    agent_id: str | None = Field(
        default=None,
        description="Target a single agent by id, or omit to message every agent in the run.",
    )


class CxWorkflowStatus(BaseModel):
    """A minimal, least-privilege status view - no step content is exposed here."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    status: str
    steps_completed: int
    steps_total: int


class CxStepProgress(BaseModel):
    """One workflow step's live, customer-safe progress - no output content exposed."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    agent_id: str
    agent_name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CxWorkflowProgress(BaseModel):
    """An Agent-Arena-style live view of every step in the customer's own run."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    status: str
    steps: list[CxStepProgress]


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _resolve_run(
    *, orchestrator: AgentOrchestrator, claims: CustomerSessionClaims
) -> WorkflowRunResult:
    run = orchestrator.get_workflow_run(claims.workflow_run_id)
    if run is None or run.session_id != claims.session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown workflow run for this session."
        )
    return run


def _get_prototype_text(run: WorkflowRunResult, *, settings: Settings) -> str:
    for result in run.step_results:
        if result.step_id == settings.cx_prototype_step_id and result.status == "completed":
            return result.output_text or ""
    return ""


def _set_session_cookie(response: Response, *, session_id: str, token: str, claims: CustomerSessionClaims) -> None:
    max_age = max(int((claims.expires_at - datetime.now(UTC)).total_seconds()), 0)
    response.set_cookie(
        key=CX_SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path=f"/cx/{session_id}",
        httponly=True,
        secure=True,
        samesite="strict",
    )


_PROTOTYPE_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


@router.get("/app")
async def get_prototype_app(
    session_id: str,
    request: Request,
    response: Response,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    settings: Settings = Depends(_get_settings),
) -> Response:
    run = _resolve_run(orchestrator=orchestrator, claims=claims)
    prototype_text = _get_prototype_text(run, settings=settings)

    if not prototype_text:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The sample prototype has not been generated yet for this session.",
        )

    # The agent's output is treated as an untrusted fragment: escaped and
    # placed inside a sandboxed shell rather than executed as top-level
    # trusted markup, then rendered in a single inline script/style block
    # covered by the strict CSP above.
    shell = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="robots" content="noindex, nofollow" />
<title>Genie Sample Prototype</title>
</head>
<body>
<div id="genie-prototype-root"></div>
<script>
  window.GENIE_SESSION_ID = {html_module.escape(claims.session_id)!r};
  window.GENIE_CX_BASE_URL = "";
</script>
<script id="genie-prototype-content" type="text/plain">{html_module.escape(prototype_text)}</script>
<script>
  document.getElementById('genie-prototype-root').textContent =
    document.getElementById('genie-prototype-content').textContent;
</script>
</body>
</html>"""

    token = request.cookies.get(CX_SESSION_COOKIE_NAME) or request.query_params.get("t") or ""
    html_response = Response(content=shell, media_type="text/html")
    html_response.headers["Content-Security-Policy"] = _PROTOTYPE_CSP
    html_response.headers["X-Content-Type-Options"] = "nosniff"
    html_response.headers["X-Frame-Options"] = "DENY"
    html_response.headers["Cache-Control"] = "no-store"
    if token:
        _set_session_cookie(html_response, session_id=session_id, token=token, claims=claims)
    return html_response


@router.get("/status")
async def get_prototype_status(
    session_id: str,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> CxWorkflowStatus:
    run = _resolve_run(orchestrator=orchestrator, claims=claims)
    completed = sum(1 for r in run.step_results if r.status == "completed")
    return CxWorkflowStatus(
        workflow_run_id=run.workflow_run_id,
        status=run.status,
        steps_completed=completed,
        steps_total=len(run.step_results),
    )


@router.get("/progress")
async def get_prototype_progress(
    session_id: str,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> CxWorkflowProgress:
    """A live, Agent-Arena-style view of the customer's own workflow run.

    Reports which agents have run/are running/have completed for this
    session, without exposing any step's ``output_text`` - a leaked link
    can watch agents work end to end but never read raw agent output
    through this route (``GET /app`` and ``POST /chat`` remain the only
    routes that return generated content, both scoped to this session).
    """

    run = _resolve_run(orchestrator=orchestrator, claims=claims)
    steps: list[CxStepProgress] = []
    for result in run.step_results:
        try:
            agent_name = orchestrator.agent_registry.get(result.agent_id).name
        except KeyError:
            agent_name = result.agent_id
        steps.append(
            CxStepProgress(
                step_id=result.step_id,
                agent_id=result.agent_id,
                agent_name=agent_name,
                status=result.status,
                started_at=result.started_at,
                completed_at=result.completed_at,
            )
        )
    return CxWorkflowProgress(workflow_run_id=run.workflow_run_id, status=run.status, steps=steps)


@router.post("/reanalyze", status_code=status.HTTP_201_CREATED)
async def submit_reanalysis_request(
    session_id: str,
    body: CxReanalysisRequest,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    rate_limiter: CxRateLimiter = Depends(get_cx_rate_limiter),
) -> ReanalysisResult:
    # _resolve_run both confirms the run exists AND belongs to this exact
    # session - the customer can never reference another session's run,
    # since workflow_run_id/session_id are taken from the token's claims,
    # never from the request body.
    _resolve_run(orchestrator=orchestrator, claims=claims)
    rate_limiter.check(claims.session_id)
    return orchestrator.request_reanalysis(
        session_id=claims.session_id,
        workflow_run_id=claims.workflow_run_id,
        trace_id=str(uuid4()),
        requested_by=f"customer:{claims.session_id}",
        request_type=body.request_type,
        target_recommendation_id=body.target_recommendation_id,
        rationale=body.rationale,
    )


@router.post("/chat", status_code=status.HTTP_200_OK)
async def submit_chat_message(
    session_id: str,
    body: CxChatRequest,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    rate_limiter: CxRateLimiter = Depends(get_cx_rate_limiter),
) -> WorkflowRunResult:
    """Let the customer chat directly with one agent, or every agent in the run.

    Mirrors ``WorkshopService.chat_with_agent``/``chat_with_all_agents``:
    the message is supplied as a ``user_message`` step input variable and
    the session's own workflow run is resumed - the reply is exactly
    whatever the Azure-hosted agent (the session's dedicated agent, if
    ``POST /sessions/{id}/cx-access`` provisioned one) produces, never
    synthesized here. Shares the same per-session rate limit budget as
    ``POST /reanalyze``, since both trigger real agent-routing work.
    """

    run = _resolve_run(orchestrator=orchestrator, claims=claims)
    rate_limiter.check(claims.session_id)
    workflow = orchestrator.workflow_registry.get(run.workflow_id)

    if body.agent_id is not None:
        step = next((s for s in workflow.steps if s.agent_id == body.agent_id), None)
        if step is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No step in this workflow is assigned to agent '{body.agent_id}'.",
            )
        step_inputs = {
            step.id: WorkflowStepInput(step_id=step.id, variables={"user_message": body.message})
        }
    else:
        step_inputs = {
            step.id: WorkflowStepInput(step_id=step.id, variables={"user_message": body.message})
            for step in workflow.steps
        }

    return await orchestrator.resume_workflow(
        workflow_run_id=claims.workflow_run_id,
        session_id=claims.session_id,
        trace_id=str(uuid4()),
        step_inputs=step_inputs,
    )


@router.get("/starter-kit")
async def download_starter_kit(
    session_id: str,
    claims: CustomerSessionClaims = Depends(get_current_customer_session),
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
    starter_kit_service: StarterKitService = Depends(get_starter_kit_service),
    settings: Settings = Depends(_get_settings),
) -> Response:
    """Let the customer download a zip of their generated prototype + deploy docs.

    Phase 3 of the call-transcript-to-live-prototype feature: packages the
    exact same prototype ``output_text`` served by ``GET /app`` alongside a
    ``README.md`` (generic hosting instructions) and an ``ACCESS_POLICY.md``
    (token TTL, rate limit, and dedicated-agent-count figures read straight
    from ``Settings``/the provisioning service - never hardcoded). Contains
    no Genie platform source code or other customer's data.
    """

    run = _resolve_run(orchestrator=orchestrator, claims=claims)
    prototype_text = _get_prototype_text(run, settings=settings)

    try:
        archive_bytes = starter_kit_service.build_zip(
            prototype_html=prototype_text,
            settings=settings,
            dedicated_agent_count=orchestrator.provisioned_customer_agent_count(claims.session_id),
        )
    except StarterKitError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    response = Response(content=archive_bytes, media_type="application/zip")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="genie-starter-kit-{session_id[:8]}.zip"'
    )
    response.headers["Cache-Control"] = "no-store"
    return response
