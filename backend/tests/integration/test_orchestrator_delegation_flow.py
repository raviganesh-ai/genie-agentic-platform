"""End-to-end test: a genie-orchestrator phase step delegates to a real specialist.

Wires together every seam introduced for phase-scoped orchestrator
delegation - ``AzureAgentGateway`` -> ``FoundryAgentProvider`` (filtering
its function tools by ``ToolCallContext.allowed_tool_names``) ->
``AgentToolRegistry`` -> a ``call_requirements_analyst`` delegation tool
(``app.agents.tools.orchestration_tools``) -> a second, nested
``AzureAgentGateway.execute`` call for the real ``requirements-analyst``
agent - without any real Azure AI Foundry connectivity. The fake Foundry
agent used for ``genie-orchestrator`` itself calls whichever function
tool it was given, exactly as the real Foundry model-driven tool-calling
loop would, to prove the whole chain produces the specialist's real
output as the phase step's final result and records a real governance
event for the delegated call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.azure_agent_gateway import AzureAgentGateway
from app.agents.foundry.agent_provider import FoundryAgentProvider
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
from app.agents.tool_execution import AgentToolRegistry
from app.agents.tools.orchestration_tools import register_orchestrator_delegation_tools
from app.prompts.registry import PromptRegistry


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def agent_registry(tmp_path: Path) -> AgentRegistry:
    _write(
        tmp_path / "agents" / "registry.yaml",
        "agents:\n"
        "  - id: requirements-analyst\n"
        "    name: Requirements Analyst\n"
        "    role: requirement_discovery\n"
        "    description: Extracts requirements.\n"
        "    foundry_agent_id: requirements-analyst\n"
        "  - id: genie-orchestrator\n"
        "    name: Genie Orchestrator\n"
        "    role: mission_orchestration\n"
        "    description: Drives the mission.\n"
        "    foundry_agent_id: genie-orchestrator\n"
        "    tool_definitions:\n"
        "      - name: call_requirements_analyst\n"
        "        description: Delegate to the Requirements Analyst.\n"
        "        parameters:\n"
        "          - name: transcript_excerpt\n"
        "            type: string\n"
        "            description: The transcript excerpt.\n"
        "          - name: user_message\n"
        "            type: string\n"
        "            description: Optional chat message.\n",
    )
    return AgentRegistry.load(tmp_path / "agents")


@pytest.fixture
def prompt_registry(tmp_path: Path) -> PromptRegistry:
    _write(
        tmp_path / "prompts" / "registry.yaml",
        "prompts:\n"
        "  - id: requirements-extraction-v1\n"
        "    name: Requirements Extraction\n"
        "    description: Extracts requirements from a transcript.\n"
        "    template: 'Extract requirements from: {transcript_excerpt}'\n"
        "    variables:\n"
        "      - transcript_excerpt\n"
        "      - user_message\n"
        "  - id: orchestrator-requirements-phase-v1\n"
        "    name: Orchestrator Requirements Phase\n"
        "    description: Drives the requirements-discovery phase.\n"
        "    template: 'Delegate for: {transcript_excerpt}'\n"
        "    variables:\n"
        "      - transcript_excerpt\n"
        "      - user_message\n",
    )
    return PromptRegistry.load(tmp_path / "prompts")


class _FakeApiClient:
    def get_latest_version(self, agent_id: str) -> str:
        return "1"


class _FakeProjectService:
    def get_api_client(self) -> _FakeApiClient:
        return _FakeApiClient()

    def get_async_project_client(self) -> object:
        return object()


class _FakeAgentResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _ToolCallingFakeFoundryAgent:
    """Simulates the real Foundry model-driven loop for genie-orchestrator's
    run (calls its one delegation tool); simulates a plain specialist
    response (no tool calls) for every other agent name, standing in for
    the requirements-analyst agent's own real Foundry response.
    """

    def __init__(self, *, agent_name: str, **kwargs: Any) -> None:
        self._agent_name = agent_name

    async def run(self, messages: Any, *, tools: Any = None) -> _FakeAgentResponse:
        if self._agent_name != "genie-orchestrator":
            return _FakeAgentResponse(text="Extract requirements from: We need a chatbot.")

        assert tools, "genie-orchestrator's phase run must have been given a delegation tool."
        [tool] = tools
        result = await tool.func(transcript_excerpt="We need a chatbot.", user_message="")
        return _FakeAgentResponse(text=result["output_text"])


class _RecordingGovernanceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_execution(
        self, *, session_id: str, trace_id: str, agent_id: str, detail: dict[str, Any] | None = None
    ) -> None:
        self.calls.append({"session_id": session_id, "agent_id": agent_id, "detail": detail})


async def test_orchestrator_phase_step_delegates_to_real_specialist_gateway_call(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    tool_registry = AgentToolRegistry()
    governance_service = _RecordingGovernanceService()

    provider = FoundryAgentProvider(
        _FakeProjectService(),
        tool_registry=tool_registry,
        agent_factory=_ToolCallingFakeFoundryAgent,
    )
    gateway = AzureAgentGateway(
        agent_registry=agent_registry, prompt_registry=prompt_registry, foundry_client=provider
    )

    # The delegation tool's nested call must land back on this same
    # gateway, exactly as create_agent_orchestrator wires it in production.
    register_orchestrator_delegation_tools(
        tool_registry, agent_gateway=gateway, governance_service=governance_service
    )

    request = AgentExecutionRequest(
        agent_id="genie-orchestrator",
        prompt_id="orchestrator-requirements-phase-v1",
        variables={"transcript_excerpt": "We need a chatbot.", "user_message": ""},
        correlation_id="trace-1",
        session_id="session-1",
        allowed_tool_names=["call_requirements_analyst"],
    )

    result = await gateway.execute(request)

    assert result.agent_id == "genie-orchestrator"
    assert result.output_text == "Extract requirements from: We need a chatbot."

    [event] = governance_service.calls
    assert event["agent_id"] == "requirements-analyst"
    assert event["session_id"] == "session-1"
    assert event["detail"]["delegated_by"] == "genie-orchestrator"
