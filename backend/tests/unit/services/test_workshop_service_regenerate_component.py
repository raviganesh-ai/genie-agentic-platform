"""Unit tests for ``WorkshopService.regenerate_component``.

Covers the Workshop page's per-component "Regenerate" action: it must call
the Build Agent directly (via ``AgentOrchestrator.execute_agent``) against
the ``build-component-regeneration-v1`` prompt with the right
``component_kind``/``component_name`` classification, never resume the
whole ``build-solution`` workflow step - see
``app.services.workshop_service.WorkshopService.regenerate_component``.
"""
from __future__ import annotations

import pytest

from app.agents.models import AgentExecutionResult
from app.services.session_service import create_session_service
from app.services.workshop_service import WorkshopService

pytestmark = pytest.mark.asyncio


class _FakeOrchestrator:
    """Duck-typed stand-in recording the ``execute_agent`` call it received."""

    def __init__(self) -> None:
        self.execute_agent_calls: list[dict] = []

    async def get_workflow_run(self, workflow_run_id: str):
        return None

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        self.execute_agent_calls.append(
            {
                "agent_id": agent_id,
                "prompt_id": prompt_id,
                "variables": variables,
                "session_id": session_id,
                "trace_id": trace_id,
            }
        )
        return AgentExecutionResult(
            agent_id=agent_id,
            output_text="```tsx\n// agent: ui\nexport function App() { return null; }\n```",
            correlation_id=trace_id or "corr-1",
        )


async def _make_service() -> tuple[WorkshopService, _FakeOrchestrator, str]:
    orchestrator = _FakeOrchestrator()
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = WorkshopService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]
    return service, orchestrator, session.id


async def test_regenerate_component_calls_build_agent_directly_for_ui_label() -> None:
    service, orchestrator, session_id = await _make_service()

    code = await service.regenerate_component(
        session_id=session_id,
        requesting_user_id="user-1",
        component_label="ui",
        existing_code="// agent: ui\nexport function App() {}",
        instructions="Make the header sticky.",
        trace_id="trace-1",
    )

    assert "// agent: ui" in code
    assert len(orchestrator.execute_agent_calls) == 1
    call = orchestrator.execute_agent_calls[0]
    assert call["agent_id"] == "build-agent"
    assert call["prompt_id"] == "build-component-regeneration-v1"
    assert call["variables"] == {
        "component_kind": "ui",
        "component_name": "ui",
        "existing_code": "// agent: ui\nexport function App() {}",
        "instructions": "Make the header sticky.",
    }
    assert call["session_id"] == session_id
    assert call["trace_id"] == "trace-1"


async def test_regenerate_component_classifies_orchestrator_label() -> None:
    service, orchestrator, session_id = await _make_service()

    await service.regenerate_component(
        session_id=session_id,
        requesting_user_id="user-1",
        component_label="Orchestrator",
        existing_code="# agent: orchestrator\n...",
        instructions="Add retry logic.",
    )

    variables = orchestrator.execute_agent_calls[0]["variables"]
    assert variables["component_kind"] == "orchestrator"
    assert variables["component_name"] == "orchestrator"


async def test_regenerate_component_classifies_named_specialist_agent_label() -> None:
    service, orchestrator, session_id = await _make_service()

    await service.regenerate_component(
        session_id=session_id,
        requesting_user_id="user-1",
        component_label="requirements-analyst",
        existing_code="# agent: requirements-analyst\n...",
        instructions="Log every extracted requirement.",
    )

    variables = orchestrator.execute_agent_calls[0]["variables"]
    assert variables["component_kind"] == "agent"
    assert variables["component_name"] == "requirements-analyst"
