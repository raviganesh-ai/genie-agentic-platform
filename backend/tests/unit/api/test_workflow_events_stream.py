"""Unit tests for the workflow-events SSE generator (``_event_source``).

Drives ``_event_source`` directly - no real HTTP connection, no waiting out
the real keepalive interval - by injecting a fake ``Request``-like object
and a tiny ``keepalive_interval_seconds``. The route itself
(auth/session-ownership wiring) is exercised by the app-startup/session
integration tests' shared patterns; this file only covers the streaming
generator's own behavior.
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.workflow_events import _event_source
from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_event_bus import WorkflowEventBus


class _FakeRequest:
    """Stands in for ``fastapi.Request``, exposing only ``is_disconnected``."""

    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _event(**overrides: object) -> WorkflowStreamEvent:
    defaults: dict[str, object] = {
        "event_type": "step_started",
        "session_id": "session-1",
        "workflow_run_id": "run-1",
        "step_id": "step-a",
        "agent_id": "requirements-analyst",
    }
    defaults.update(overrides)
    return WorkflowStreamEvent.model_validate(defaults)


async def test_event_source_yields_a_data_line_for_a_published_event() -> None:
    bus = WorkflowEventBus()
    request = _FakeRequest()
    source = _event_source(
        session_id="session-1", event_bus=bus, request=request, keepalive_interval_seconds=5.0
    )

    async def _publish_soon() -> None:
        await asyncio.sleep(0.01)
        await bus.publish(_event(event_type="step_completed", output_preview="done"))

    asyncio.create_task(_publish_soon())
    line = await asyncio.wait_for(source.__anext__(), timeout=1.0)

    assert line.startswith("data: ")
    assert '"event_type":"step_completed"' in line
    assert '"output_preview":"done"' in line
    await source.aclose()


async def test_event_source_yields_a_keepalive_comment_when_no_event_arrives() -> None:
    bus = WorkflowEventBus()
    request = _FakeRequest()
    source = _event_source(
        session_id="session-1", event_bus=bus, request=request, keepalive_interval_seconds=0.02
    )

    line = await asyncio.wait_for(source.__anext__(), timeout=1.0)

    assert line == ": keepalive\n\n"
    await source.aclose()


async def test_event_source_stops_once_the_client_disconnects() -> None:
    bus = WorkflowEventBus()
    request = _FakeRequest()
    request.disconnected = True
    source = _event_source(
        session_id="session-1", event_bus=bus, request=request, keepalive_interval_seconds=5.0
    )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(source.__anext__(), timeout=1.0)


async def test_event_source_only_yields_events_for_its_own_session() -> None:
    bus = WorkflowEventBus()
    request = _FakeRequest()
    source = _event_source(
        session_id="session-1", event_bus=bus, request=request, keepalive_interval_seconds=0.05
    )

    async def _publish_for_other_session() -> None:
        await asyncio.sleep(0.01)
        await bus.publish(_event(session_id="session-2"))

    asyncio.create_task(_publish_for_other_session())
    line = await asyncio.wait_for(source.__anext__(), timeout=1.0)

    # No event for session-1 was ever published, so the first thing observed
    # must be a keepalive, never the other session's event.
    assert line == ": keepalive\n\n"
    await source.aclose()
