"""In-memory pub/sub bus for live workflow step events.

Lets the SSE API route (``GET /sessions/{session_id}/workflow-events/
stream``) observe ``step_started``/``step_delta``/``step_completed``/
``step_failed`` events the instant ``WorkflowStepExecutor`` publishes them -
even while that same step is still executing inside a concurrent run/resume
HTTP request, since FastAPI/asyncio runs every request handler on one
shared event loop, allowing concurrent requests to interleave.

Purely in-process (no external broker): each Container App replica has its
own bus instance. A bounded accumulated delta snapshot lets reconnecting SSE
clients recover the current stream, but it never persists across restarts and
is never a system of record - that remains ``GovernanceService``.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.models.workflow_stream_models import WorkflowStreamEvent

__all__ = ["WorkflowEventBus"]

_QUEUE_MAXSIZE = 256
_MAX_ACCUMULATED_STREAMS = 32

_StreamKey = tuple[str, str, str, str]


class WorkflowEventBus:
    """Publishes/subscribes to live workflow step events, scoped per session id."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[WorkflowStreamEvent]]] = defaultdict(set)
        self._step_delta_text: OrderedDict[_StreamKey, str] = OrderedDict()

    async def publish(self, event: WorkflowStreamEvent) -> None:
        """Delivers ``event`` to every current subscriber of its session, if any.

        A no-op when nobody is subscribed - callers never need to check for
        subscribers first. Never raises and never blocks the publisher: a
        full queue (a subscriber that isn't draining fast enough) simply
        drops its oldest queued event to make room, rather than
        back-pressuring - or crashing - the workflow run doing the
        publishing.
        """

        if event.event_type == "step_started":
            matching_prefix = (event.session_id, event.workflow_run_id, event.step_id)
            for key in list(self._step_delta_text):
                if key[:3] == matching_prefix:
                    del self._step_delta_text[key]
        elif event.event_type == "step_delta" and event.delta:
            key = (event.session_id, event.workflow_run_id, event.step_id, event.agent_id)
            self._step_delta_text[key] = self._step_delta_text.get(key, "") + event.delta
            self._step_delta_text.move_to_end(key)
            while len(self._step_delta_text) > _MAX_ACCUMULATED_STREAMS:
                self._step_delta_text.popitem(last=False)

        for queue in list(self._subscribers.get(event.session_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)

    def get_step_delta_text(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        step_id: str,
        agent_id: str,
    ) -> str:
        """Returns accumulated live deltas for one agent execution stream."""

        return self._step_delta_text.get(
            (session_id, workflow_run_id, step_id, agent_id), ""
        )

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[WorkflowStreamEvent]]:
        """Yields a queue with current stream snapshots followed by live events."""

        queue: asyncio.Queue[WorkflowStreamEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[session_id].add(queue)
        for (event_session_id, workflow_run_id, step_id, agent_id), delta in list(
            self._step_delta_text.items()
        ):
            if event_session_id == session_id and delta:
                queue.put_nowait(
                    WorkflowStreamEvent(
                        event_type="step_delta",
                        session_id=session_id,
                        workflow_run_id=workflow_run_id,
                        step_id=step_id,
                        agent_id=agent_id,
                        delta=delta,
                    )
                )
        try:
            yield queue
        finally:
            self._subscribers[session_id].discard(queue)
            if not self._subscribers[session_id]:
                del self._subscribers[session_id]
