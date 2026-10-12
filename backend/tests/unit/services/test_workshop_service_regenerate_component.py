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
from app.agents.tool_execution import ToolExecutionError
from app.agents.tools.orchestration_tools import MARKER_PREFIX_BY_COMPONENT_KIND
from app.services.session_service import create_session_service
from app.services.workshop_service import WorkshopService

pytestmark = pytest.mark.asyncio


class _FakeOrchestrator:
    """Duck-typed stand-in recording the ``execute_agent``/
    ``update_build_component`` calls it received."""

    def __init__(self) -> None:
        self.execute_agent_calls: list[dict] = []
        self.update_build_component_calls: list[dict] = []

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
            output_text=self._render_valid_output(variables),
            correlation_id=trace_id or "corr-1",
        )

    def _render_valid_output(self, variables: dict[str, str]) -> str:
        """Renders a fenced block that always satisfies
        ``validate_component_output`` for whatever component_kind/
        component_name this call actually requested - a real regeneration
        call's own output must match its own request, not a fixed stand-in,
        now that regenerate_component validates it before persisting."""

        kind = variables["component_kind"]
        name = variables["component_name"]
        marker_prefix = MARKER_PREFIX_BY_COMPONENT_KIND.get(kind, "")
        label = kind if kind in ("orchestrator", "ui") else f"{marker_prefix}{name}"
        if kind in ("ui", "page_view"):
            return f'```tsx\n// agent: {label}\nexport function App() {{ return null; }}\n```'
        if kind in ("api_contract", "gateway_policy", "identity_config"):
            return f"```yaml\n# agent: {label}\nkey: value\n```"
        return f'```python\n# agent: {label}\ndef run(): ...\n```'

    async def update_build_component(
        self, *, workflow_run_id: str, component_label: str, new_block_text: str
    ) -> None:
        self.update_build_component_calls.append(
            {
                "workflow_run_id": workflow_run_id,
                "component_label": component_label,
                "new_block_text": new_block_text,
            }
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
        workflow_run_id="run-1",
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

    # The regenerated code must also be persisted back into the run's own
    # build-solution output - otherwise a subsequent Deploy & Launch would
    # silently keep re-materializing the original, unmodified component.
    assert len(orchestrator.update_build_component_calls) == 1
    persisted = orchestrator.update_build_component_calls[0]
    assert persisted["workflow_run_id"] == "run-1"
    assert persisted["component_label"] == "ui"
    assert persisted["new_block_text"] == code


async def test_regenerate_component_classifies_orchestrator_label() -> None:
    service, orchestrator, session_id = await _make_service()

    await service.regenerate_component(
        session_id=session_id,
        requesting_user_id="user-1",
        workflow_run_id="run-1",
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
        workflow_run_id="run-1",
        component_label="requirements-analyst",
        existing_code="# agent: requirements-analyst\n...",
        instructions="Log every extracted requirement.",
    )

    variables = orchestrator.execute_agent_calls[0]["variables"]
    assert variables["component_kind"] == "agent"
    assert variables["component_name"] == "requirements-analyst"


async def test_regenerate_component_classifies_prefixed_page_label() -> None:
    """Regression coverage for a real incident: a "page:" (and
    service:/model:/api_contract:/gateway_policy:/identity_config:)
    prefixed component_label previously fell through to the generic
    "agent" kind (expects Python), not "page_view" (expects tsx) - any
    Regenerate call for an actual page therefore sent the wrong
    component_kind to the Build Agent and, once output validation was
    added, would have been rejected outright."""

    service, orchestrator, session_id = await _make_service()

    code = await service.regenerate_component(
        session_id=session_id,
        requesting_user_id="user-1",
        workflow_run_id="run-1",
        component_label="page:Landing Page — Developer Home",
        existing_code="// agent: page:Landing Page — Developer Home\n...",
        instructions="Add a loading spinner.",
    )

    variables = orchestrator.execute_agent_calls[0]["variables"]
    assert variables["component_kind"] == "page_view"
    assert variables["component_name"] == "Landing Page — Developer Home"
    assert "// agent: page:Landing Page — Developer Home" in code

    persisted = orchestrator.update_build_component_calls[0]
    assert persisted["component_label"] == "page:Landing Page — Developer Home"


async def test_regenerate_component_rejects_a_truncated_regeneration_before_persisting() -> None:
    """Regression coverage for a real incident: the Build Agent's own
    regeneration output can itself be truncated/malformed (e.g. it hit its
    own output token limit mid-component) - previously that broken output
    was spliced straight into the persisted build-solution step unchecked,
    silently corrupting an already-passing component with no actionable
    error, and failing every subsequent Deploy & Launch attempt with no
    clue why. regenerate_component must now reject it before persisting."""

    class _TruncatingOrchestrator(_FakeOrchestrator):
        async def execute_agent(self, **kwargs: object) -> AgentExecutionResult:
            self.execute_agent_calls.append(kwargs)  # type: ignore[arg-type]
            return AgentExecutionResult(
                agent_id="build-agent",
                # No closing fence - exactly the shape of a truncated LLM
                # response that hit its own output token limit mid-component.
                output_text="```tsx\n// agent: ui\nexport function App() {",
                correlation_id="corr-1",
            )

    orchestrator = _TruncatingOrchestrator()
    session_service = create_session_service(orchestrator=orchestrator)  # type: ignore[arg-type]
    session = await session_service.create_session(owner_user_id="user-1", title="t")
    service = WorkshopService(orchestrator=orchestrator, session_service=session_service)  # type: ignore[arg-type]

    with pytest.raises(ToolExecutionError):
        await service.regenerate_component(
            session_id=session.id,
            requesting_user_id="user-1",
            workflow_run_id="run-1",
            component_label="ui",
            existing_code="// agent: ui\nexport function App() {}",
            instructions="Make the header sticky.",
        )

    # The malformed output must never reach persistence.
    assert orchestrator.update_build_component_calls == []

