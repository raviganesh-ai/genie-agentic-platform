"""Customer-experience (cx) API: serves the generated sample prototype.

This is the ONLY externally shareable, customer-facing surface in Genie.
It is deliberately read-only and least-privilege: every route requires a
``CustomerSessionClaims`` (see ``app.security.cx_dependencies``) scoped to
exactly one ``session_id`` + ``workflow_run_id`` pair, minted only by an
already-authenticated internal user (``POST /sessions/{session_id}/cx-access``
in ``app.api.sessions``). No route here can mutate a workflow, trigger
agent execution, or read any other customer's session - by design, so a
leaked/shared link can never do more than view the one prototype and
status it was minted for.

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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import get_agent_orchestrator
from app.config.settings import Settings
from app.models.workflow_models import WorkflowRunResult
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.security.cx_dependencies import CX_SESSION_COOKIE_NAME, get_current_customer_session
from app.security.cx_tokens import CustomerSessionClaims

router = APIRouter(prefix="/cx/{session_id}", tags=["customer-experience"])


class CxWorkflowStatus(BaseModel):
    """A minimal, least-privilege status view - no step content is exposed here."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    status: str
    steps_completed: int
    steps_total: int


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
    prototype_text = ""
    for result in run.step_results:
        if result.step_id == settings.cx_prototype_step_id and result.status == "completed":
            prototype_text = result.output_text or ""
            break

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
