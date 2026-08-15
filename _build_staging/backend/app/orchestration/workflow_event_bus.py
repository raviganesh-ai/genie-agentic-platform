"""In-memory pub/sub bus for live workflow step events.

Lets the SSE API route (``GET /sessions/{session_id}/workflow-events/
stream``) observe ``step_started``/``step_delta``/``step_completed``/
``step_failed`` events the instant ``WorkflowStepExecutor`` publishes them -
even while that same step is still executing inside a concurrent run/resume
HTTP request, since FastAPI/asyncio runs every request handler on one
shared event loop, allowing concurrent requests to interleave.

Purely in-process (no external broker): each Container App replica has its
own bus instance, matching every other in-memory service in this codebase
pre-Phase-10 horizontal scaling (e.g. ``WorkflowExecutionService``'s run
history). Never persists across restarts and is never a system of record -
that remains ``GovernanceService``; this is a pure "is currently happening"
live signal.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.models.workflow_stream_models import WorkflowStreamEvent

__all__ = ["WorkflowEventBus"]

_QUEUE_MAXSIZE = 256


class WorkflowEventBus:
    """Publishes/subscribes to live workflow step events, scoped per session id."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[WorkflowStreamEvent]]] = defaultdict(set)

    async def publish(self, event: WorkflowStreamEvent) -> None:
        """Delivers ``event`` to every current subscriber of its session, if any.

        A no-op when nobody is subscribed - callers never need to check for
        subscribers first. Never raises and never blocks the publisher: a
        full queue (a subscriber that isn't draining fast enough) simply
        drops its oldest queued event to make room, rather than
        back-pressuring - or crashing - the workflow run doing the
        publishing.
        """

        for queue in list(self._subscribers.get(event.session_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[WorkflowStreamEvent]]:
        """Yields a queue of events published for ``session_id`` while the context is open."""

        queue: asyncio.Queue[WorkflowStreamEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[session_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[session_id].discard(queue)
            if not self._subscribers[session_id]:
                del self._subscribers[session_id]
