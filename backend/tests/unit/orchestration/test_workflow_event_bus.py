"""Unit tests for WorkflowEventBus pub/sub semantics."""
from __future__ import annotations

import asyncio

import pytest

from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_event_bus import WorkflowEventBus


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


async def test_publish_with_no_subscribers_is_a_safe_noop() -> None:
    bus = WorkflowEventBus()
    await bus.publish(_event())  # must not raise


async def test_subscriber_receives_published_event_for_its_session() -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1") as queue:
        await bus.publish(_event())
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.step_id == "step-a"


async def test_subscriber_never_receives_events_for_a_different_session() -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1") as queue:
        await bus.publish(_event(session_id="session-2"))
        assert queue.empty()


async def test_multiple_subscribers_to_the_same_session_each_receive_the_event() -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1") as queue_a, bus.subscribe("session-1") as queue_b:
        await bus.publish(_event())
        received_a = await asyncio.wait_for(queue_a.get(), timeout=1.0)
        received_b = await asyncio.wait_for(queue_b.get(), timeout=1.0)
    assert received_a.step_id == received_b.step_id == "step-a"


async def test_queue_is_removed_after_subscriber_unsubscribes() -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1"):
        pass
    assert bus._subscribers.get("session-1") in (None, set())


async def test_full_queue_drops_oldest_event_instead_of_blocking_publisher() -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1") as queue:
        for i in range(300):
            await bus.publish(_event(step_id=f"step-{i}"))
        assert queue.full()
        first_remaining = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert first_remaining.step_id != "step-0"


@pytest.mark.parametrize("event_type", ["step_started", "step_delta", "step_completed", "step_failed"])
async def test_all_event_types_round_trip_through_the_bus(event_type: str) -> None:
    bus = WorkflowEventBus()
    async with bus.subscribe("session-1") as queue:
        await bus.publish(_event(event_type=event_type, delta="chunk", output_preview="done", error="boom"))
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.event_type == event_type
