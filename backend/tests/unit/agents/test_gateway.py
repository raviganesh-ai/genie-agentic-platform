"""Unit tests for agent/prompt resolution and gateway selection.

Covers ``LocalAgentGateway`` (deterministic, non-network, dev-only) and
``create_agent_gateway`` (the fail-closed production-vs-local selector).
Azure AI Foundry execution itself is covered by
``test_azure_agent_gateway.py`` and ``foundry/test_agent_provider.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.azure_agent_gateway import AzureAgentGateway
from app.agents.gateway import (
    AgentGatewayError,
    LocalAgentGateway,
    PromptResolutionError,
    UnknownAgentError,
    UnknownPromptError,
    create_agent_gateway,
    get_enabled_agent,
    resolve_prompt_text,
)
from app.agents.models import AgentExecutionRequest
from app.agents.registry import AgentRegistry
from app.config.settings import Settings
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
        "    model_deployment_ref: claude-sonnet-5\n"
        "    foundry_agent_id: requirements-analyst-agent\n"
        "  - id: disabled-agent\n"
        "    name: Disabled Agent\n"
        "    role: disabled_role\n"
        "    description: Not enabled.\n"
        "    enabled: false\n"
        "  - id: no-foundry-agent\n"
        "    name: No Foundry Agent\n"
        "    role: local_only_role\n"
        "    description: Only usable via LocalAgentGateway.\n",
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


class TestResolutionHelpers:
    def test_get_enabled_agent_returns_agent(self, agent_registry: AgentRegistry):
        agent = get_enabled_agent(agent_registry, "requirements-analyst")
        assert agent.id == "requirements-analyst"

    def test_get_enabled_agent_raises_for_unknown_id(self, agent_registry: AgentRegistry):
        with pytest.raises(UnknownAgentError, match="Unknown agent id"):
            get_enabled_agent(agent_registry, "does-not-exist")

    def test_get_enabled_agent_raises_for_disabled_agent(self, agent_registry: AgentRegistry):
        with pytest.raises(UnknownAgentError, match="disabled"):
            get_enabled_agent(agent_registry, "disabled-agent")

    def test_resolve_prompt_text_formats_template(self, prompt_registry: PromptRegistry):
        request = _request()
        assert resolve_prompt_text(prompt_registry, request) == (
            "Extract requirements from: We need a chatbot."
        )

    def test_resolve_prompt_text_raises_for_unknown_prompt(self, prompt_registry: PromptRegistry):
        request = _request(prompt_id="does-not-exist")
        with pytest.raises(UnknownPromptError, match="Unknown prompt id"):
            resolve_prompt_text(prompt_registry, request)

    def test_resolve_prompt_text_raises_for_missing_variable(
        self, prompt_registry: PromptRegistry
    ):
        request = _request(variables={})
        with pytest.raises(PromptResolutionError, match="missing required variable"):
            resolve_prompt_text(prompt_registry, request)


class TestLocalAgentGateway:
    async def test_execute_returns_deterministic_result(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        gateway = LocalAgentGateway(agent_registry, prompt_registry)
        result = await gateway.execute(_request())

        assert result.agent_id == "requirements-analyst"
        assert result.correlation_id == "corr-1"
        assert "local-agent-gateway" in result.output_text

    async def test_execute_raises_for_unknown_agent(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        gateway = LocalAgentGateway(agent_registry, prompt_registry)
        with pytest.raises(UnknownAgentError):
            await gateway.execute(_request(agent_id="does-not-exist"))

    async def test_execute_stream_yields_one_delta_then_the_same_final_result_as_execute(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        gateway = LocalAgentGateway(agent_registry, prompt_registry)

        chunks = [chunk async for chunk in gateway.execute_stream(_request())]

        deltas = [chunk.delta for chunk in chunks if chunk.delta is not None]
        results = [chunk.result for chunk in chunks if chunk.result is not None]
        assert len(deltas) == 1
        assert len(results) == 1
        assert results[0].agent_id == "requirements-analyst"
        assert deltas[0] == results[0].output_text


class TestCreateAgentGateway:
    def test_local_mode_with_allow_local_agents_returns_local_gateway(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        settings = Settings(provider_mode="local", allow_local_agents=True)
        gateway = create_agent_gateway(
            settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
        )
        assert isinstance(gateway, LocalAgentGateway)

    def test_local_mode_without_local_agents_falls_through_to_azure_if_configured(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        settings = Settings(
            provider_mode="local",
            allow_local_agents=False,
            azure_foundry_endpoint="https://genie-foundry.example-project.azure.com",
            azure_foundry_project_name="genie-project",
        )
        gateway = create_agent_gateway(
            settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
        )
        assert isinstance(gateway, AzureAgentGateway)

    def test_local_mode_without_local_agents_or_foundry_raises(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        settings = Settings(provider_mode="local", allow_local_agents=False)
        with pytest.raises(AgentGatewayError, match="No usable agent execution gateway"):
            create_agent_gateway(
                settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
            )

    def test_production_mode_returns_azure_gateway_when_configured(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        settings = Settings(
            provider_mode="production",
            allow_mock_agents=False,
            allow_local_agents=False,
            use_synthetic_data=False,
            azure_foundry_endpoint="https://genie-foundry.example-project.azure.com",
            azure_foundry_project_name="genie-project",
        )
        gateway = create_agent_gateway(
            settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
        )
        assert isinstance(gateway, AzureAgentGateway)

    def test_production_mode_never_falls_back_to_local(
        self, agent_registry: AgentRegistry, prompt_registry: PromptRegistry
    ):
        settings = Settings(
            provider_mode="production",
            allow_mock_agents=False,
            allow_local_agents=True,  # even if True, production must not use it
            use_synthetic_data=False,
        )
        with pytest.raises(AgentGatewayError, match="azure_foundry_endpoint"):
            create_agent_gateway(
                settings=settings, agent_registry=agent_registry, prompt_registry=prompt_registry
            )
