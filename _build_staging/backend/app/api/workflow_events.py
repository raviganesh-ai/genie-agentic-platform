"""Live workflow step event stream API route (Server-Sent Events).

Lets a client observe workflow steps as they start, stream incremental
output, complete, or fail in near-real-time by subscribing to
``WorkflowEventBus`` for a session - instead of only ever seeing a fully
finished ``WorkflowRunResult`` once an entire run/resume HTTP call
completes. Since FastAPI/asyncio runs every request handler on one shared
event loop, this subscription can observe events published from *inside*
a concurrent run/resume request the instant ``WorkflowStepExecutor``
publishes them.

Session ownership is checked via ``SessionService`` exactly like every
other per-session route, before the subscription is ever opened.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_session_service, get_workflow_event_bus
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.security.auth_models import AuthenticatedUser
from app.security.dependencies import get_current_user
from app.services.session_service import SessionService

__all__ = ["router"]

router = APIRouter(prefix="/sessions/{session_id}/workflow-events", tags=["workflow-events"])

# How long to wait for a real event before sending an SSE comment as a
# keepalive - long enough to avoid pointless chatter, short enough to keep
# most reverse proxies/load balancers from treating the connection as idle
# and closing it before the next real event arrives.
_KEEPALIVE_INTERVAL_SECONDS = 15.0


@router.get("/stream")
async def stream_workflow_events(
    session_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
    event_bus: WorkflowEventBus = Depends(get_workflow_event_bus),
) -> StreamingResponse:
    # Fails closed (raises, mapped to 404 by the shared domain error
    # handler) if the session does not exist or does not belong to this
    # caller - identical ownership check to every other per-session route
    # (e.g. app.api.workflows) before any data becomes reachable.
    await session_service.get_session(session_id=session_id, requesting_user_id=user.user_id)

    return StreamingResponse(
        _event_source(session_id=session_id, event_bus=event_bus, request=request),
        media_type="text/event-stream",
        headers={
            # Every intermediary (proxy, browser) must treat this as a live
            # stream, never cache or buffer it - buffering would defeat the
            # entire "near real time" point of this route.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_source(
    *,
    session_id: str,
    event_bus: WorkflowEventBus,
    request: Request,
    keepalive_interval_seconds: float = _KEEPALIVE_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """Yields one SSE ``data:`` line per real ``WorkflowStreamEvent``.

    Extracted from the route so it can be driven directly in unit tests
    (with a fake ``request``/tiny ``keepalive_interval_seconds``) without
    needing a real HTTP connection or waiting out the real keepalive
    interval. Yields an SSE comment (``: keepalive``) instead whenever no
    real event arrives within ``keepalive_interval_seconds``, purely to
    keep proxies/load balancers from treating an idle-but-open connection
    as dead; stops once the client disconnects.
    """

    async with event_bus.subscribe(session_id) as queue:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_interval_seconds)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {event.model_dump_json()}\n\n"
