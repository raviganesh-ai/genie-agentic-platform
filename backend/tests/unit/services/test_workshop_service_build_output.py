from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.deploy_launch.code_materializer import MaterializedCodeError
from app.models.workflow_models import WorkflowRunResult, WorkflowStepResult
from app.models.workflow_stream_models import WorkflowStreamEvent
from app.orchestration.workflow_event_bus import WorkflowEventBus
from app.services.workshop_service import WorkshopService

pytestmark = pytest.mark.asyncio

_VALID_BUILD = '''
```python
# agent: Requirements Specialist
async def run() -> None:
    pass
```
```python
# agent: orchestrator
from agent_config import AGENT_FOUNDRY_NAMES
from agent_framework import FunctionTool
from mission_foundry_runtime import MissionFoundryAgent
class OrchestratorAgent:
    async def run(self, ui_message: str, on_progress=None):
        specialist = MissionFoundryAgent(
            agent_name=AGENT_FOUNDRY_NAMES["Requirements Specialist"]
        )
        tool = FunctionTool(name="Requirements Specialist", func=specialist.run)
        if on_progress is not None:
            await on_progress("Handing off to Requirements Specialist...")
        result = await specialist.run(ui_message)
        if on_progress is not None:
            await on_progress("Requirements Specialist completed.")
        return {"result": result, "tool": str(tool)}
```
```tsx
// agent: ui
export function MissionApp({ onSubmit }) {
    return <button className="genie-btn" onClick={() => onSubmit(JSON.stringify({ request: "run" }))}>Run</button>;
}
```
'''


class _Orchestrator:
    def __init__(self, run: WorkflowRunResult) -> None:
        self.run = run
        self.workflow_event_bus = WorkflowEventBus()

    async def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunResult:
        assert workflow_run_id == self.run.workflow_run_id
        return self.run


def _run(*, output_text: str | None = None) -> WorkflowRunResult:
    step_results = []
    if output_text is not None:
        step_results.append(
            WorkflowStepResult(
                step_id="build-solution",
                agent_id="genie-orchestrator",
                status="completed",
                output_text=output_text,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
    return WorkflowRunResult(
        workflow_run_id="run-1",
        workflow_id="solutioning-default",
        session_id="session-1",
        status="completed" if output_text is not None else "running",
        waves=[],
        step_results=step_results,
    )


async def test_get_build_output_returns_accumulated_live_build_deltas() -> None:
    orchestrator = _Orchestrator(_run())
    session_service = AsyncMock()
    service = WorkshopService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=session_service,
    )
    await orchestrator.workflow_event_bus.publish(
        WorkflowStreamEvent(
            event_type="step_delta",
            session_id="session-1",
            workflow_run_id="run-1",
            step_id="build-solution",
            agent_id="build-agent",
            delta="```python\n# agent: specialist\n",
        )
    )

    output = await service.get_build_output(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )

    assert output == "```python\n# agent: specialist\n"


async def test_get_build_output_prefers_completed_persisted_output() -> None:
    orchestrator = _Orchestrator(_run(output_text="completed build"))
    session_service = AsyncMock()
    service = WorkshopService(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        session_service=session_service,
    )

    output = await service.get_build_output(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )

    assert output == "completed build"


async def test_validate_build_output_accepts_exact_completed_build() -> None:
    service = WorkshopService(
        orchestrator=_Orchestrator(_run(output_text=_VALID_BUILD)),  # type: ignore[arg-type]
        session_service=AsyncMock(),
    )

    await service.validate_build_output(
        session_id="session-1",
        requesting_user_id="user-1",
        workflow_run_id="run-1",
    )


async def test_validate_build_output_rejects_invalid_build_before_deploy() -> None:
    service = WorkshopService(
        orchestrator=_Orchestrator(_run(output_text="incomplete build")),  # type: ignore[arg-type]
        session_service=AsyncMock(),
    )

    with pytest.raises(MaterializedCodeError, match="No materializable"):
        await service.validate_build_output(
            session_id="session-1",
            requesting_user_id="user-1",
            workflow_run_id="run-1",
        )