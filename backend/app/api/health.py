"""Health check API routes.

Extracted from ``app.main`` verbatim (same paths, same response shapes) so
Phase 7 wires it in via ``include_router`` like every other Phase 7
router. Never requires authentication (health checks must remain reachable
by infrastructure probes) and never exposes secrets or internal detail.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    if getattr(request.app.state, "ready", False):
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(status_code=503, content={"status": "not_ready"})
