"""Unit tests for AzureAgentGateway using a fake FoundryAgentClient.

No real Azure AI Foundry connectivity is required or exercised here -
``AzureAgentGateway`` depends only on the ``FoundryAgentClient`` protocol,
never on the azure-ai-projects SDK directly, so it is fully testable with a
fake in-memory implementation of that protocol.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.azure_agent_gateway import AzureAgentGateway
from app.agents.foundry.agent_provider import FoundryRunResult
from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.gateway import UnknownAgentError
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
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
        "    foundry_agent_id: requirements-analyst-agent\n"
        "  - id: no-foundry-agent\n"
        "    name: No Foundry Agent\n"
        "    role: local_only_role\n"
        "    description: Not deployed to Foundry.\n",
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
        "      - transcript_excerpt\n",
    )
    return PromptRegistry.load(tmp_path / "prompts")


def _request(**overrides: object) -> AgentExecutionRequest:
    defaults: dict[str, object] = {
        "agent_id": "requirements-analyst",
        "prompt_id": "requirements-extraction-v1",
        "variables": {"transcript_excerpt": "We need a chatbot."},
        "correlation_id": "corr-1",
    }
    defaults.update(overrides)
    return AgentExecutionRequest.model_validate(defaults)


class _FakeFoundryClient:
    """A fake FoundryAgentClient recording every call it received."""

    def __init__(self, *, result: FoundryRunResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[dict[str, str]] = []
        self.tool_contexts: list[object] = []

    async def run(
        self, *, foundry_agent_id: str, input_text: str, tool_context=None
    ) -> FoundryRunResult:
        self.calls.append({"foundry_agent_id": foundry_agent_id, "input_text": input_text})
        self.tool_contexts.append(tool_context)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class _RecordingGovernanceRecorder:
    def __init__(self) -> None:
        self.executions: list[tuple] = []
        self.unavailable: list[tuple] = []

    def record_execution(self, *, request, result) -> None:
        self.executions.append((request, result))

    def record_unavailable(self, *, request, reason) -> None:
        self.unavailable.append((request, reason))


async def test_execute_success_calls_foundry_client_with_resolved_prompt(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="Here are the requirements.", raw_status="completed", latency_ms=12.0)
    )
    recorder = _RecordingGovernanceRecorder()
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        governance_recorder=recorder,
    )

    result = await gateway.execute(_request())

    assert result.agent_id == "requirements-analyst"
    assert result.output_text == "Here are the requirements."
    assert fake_client.calls == [
        {
            "foundry_agent_id": "requirements-analyst-agent",
            "input_text": "Extract requirements from: We need a chatbot.",
        }
    ]
    assert len(recorder.executions) == 1
    assert recorder.unavailable == []


async def test_execute_raises_for_agent_without_foundry_agent_id(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="unused", raw_status="completed", latency_ms=1.0)
    )
    recorder = _RecordingGovernanceRecorder()
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        governance_recorder=recorder,
    )

    with pytest.raises(FoundryUnavailableError, match="no foundry_agent_id"):
        await gateway.execute(_request(agent_id="no-foundry-agent"))

    assert fake_client.calls == []
    assert len(recorder.unavailable) == 1


async def test_execute_never_falls_back_when_foundry_unavailable(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(error=FoundryUnavailableError("Foundry is down."))
    recorder = _RecordingGovernanceRecorder()
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        governance_recorder=recorder,
    )

    with pytest.raises(FoundryUnavailableError, match="Foundry is down"):
        await gateway.execute(_request())

    assert len(recorder.unavailable) == 1
    assert recorder.executions == []


async def test_execute_raises_for_unknown_agent_before_calling_foundry(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="unused", raw_status="completed", latency_ms=1.0)
    )
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
    )

    with pytest.raises(UnknownAgentError):
        await gateway.execute(_request(agent_id="does-not-exist"))

    assert fake_client.calls == []


async def test_execute_forwards_allowed_tool_names_onto_tool_context(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="unused", raw_status="completed", latency_ms=1.0)
    )
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
    )

    await gateway.execute(_request(allowed_tool_names=["call_requirements_analyst"]))

    [tool_context] = fake_client.tool_contexts
    assert tool_context.allowed_tool_names == ["call_requirements_analyst"]


async def test_execute_forwards_resolved_variables_onto_tool_context(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    """Delegation tools rely on ``tool_context.variables`` as the authoritative
    source for forwarding an upstream step's real output to a delegated
    specialist - see ``app.agents.tools.orchestration_tools``."""

    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="unused", raw_status="completed", latency_ms=1.0)
    )
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
    )

    await gateway.execute(_request(variables={"transcript_excerpt": "We need a chatbot."}))

    [tool_context] = fake_client.tool_contexts
    assert tool_context.variables == {"transcript_excerpt": "We need a chatbot."}


async def test_execute_defaults_allowed_tool_names_to_none(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="unused", raw_status="completed", latency_ms=1.0)
    )
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
    )

    await gateway.execute(_request())

    [tool_context] = fake_client.tool_contexts
    assert tool_context.allowed_tool_names is None


class _FakeSessionAgentResolver:
    """A fake SessionAgentResolver recording every lookup it received."""

    def __init__(self, *, dedicated_id: str | None) -> None:
        self._dedicated_id = dedicated_id
        self.calls: list[dict[str, str | None]] = []

    def resolve(
        self, *, session_id: str, agent_id: str, scope_id: str | None = None
    ) -> str | None:
        self.calls.append({"session_id": session_id, "agent_id": agent_id, "scope_id": scope_id})
        return self._dedicated_id


async def test_execute_routes_to_a_dedicated_agent_when_resolver_returns_one(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="Dedicated reply.", raw_status="completed", latency_ms=1.0)
    )
    resolver = _FakeSessionAgentResolver(dedicated_id="dedicated-agent-42")
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        session_agent_resolver=resolver,
    )

    await gateway.execute(_request(session_id="cx-session-1"))

    assert resolver.calls == [
        {"session_id": "cx-session-1", "agent_id": "requirements-analyst", "scope_id": None}
    ]
    assert fake_client.calls[0]["foundry_agent_id"] == "dedicated-agent-42"


async def test_execute_forwards_agent_scope_id_to_the_resolver(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="Scoped reply.", raw_status="completed", latency_ms=1.0)
    )
    resolver = _FakeSessionAgentResolver(dedicated_id="group-dedicated-agent-7")
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        session_agent_resolver=resolver,
    )

    await gateway.execute(_request(session_id="cx-session-1", agent_scope_id="group-1"))

    assert resolver.calls == [
        {"session_id": "cx-session-1", "agent_id": "requirements-analyst", "scope_id": "group-1"}
    ]
    assert fake_client.calls[0]["foundry_agent_id"] == "group-dedicated-agent-7"


async def test_execute_falls_back_to_shared_agent_when_resolver_returns_none(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="Shared reply.", raw_status="completed", latency_ms=1.0)
    )
    resolver = _FakeSessionAgentResolver(dedicated_id=None)
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        session_agent_resolver=resolver,
    )

    await gateway.execute(_request(session_id="cx-session-1"))

    assert resolver.calls == [
        {"session_id": "cx-session-1", "agent_id": "requirements-analyst", "scope_id": None}
    ]
    assert fake_client.calls[0]["foundry_agent_id"] == "requirements-analyst-agent"


async def test_execute_never_consults_resolver_when_session_id_is_absent(
    agent_registry: AgentRegistry, prompt_registry: PromptRegistry
):
    fake_client = _FakeFoundryClient(
        result=FoundryRunResult(output_text="Shared reply.", raw_status="completed", latency_ms=1.0)
    )
    resolver = _FakeSessionAgentResolver(dedicated_id="dedicated-agent-42")
    gateway = AzureAgentGateway(
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
        foundry_client=fake_client,
        session_agent_resolver=resolver,
    )

    await gateway.execute(_request())

    assert resolver.calls == []
    assert fake_client.calls[0]["foundry_agent_id"] == "requirements-analyst-agent"
