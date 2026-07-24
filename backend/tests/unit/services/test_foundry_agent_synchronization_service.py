"""Unit tests for the rich, per-agent FoundryAgentSynchronizationService (Phase 10A)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.foundry.errors import FoundryUnavailableError
from app.agents.models import AgentDefinition
from app.agents.registry import AgentRegistry
from app.prompts.models import PromptTemplate
from app.prompts.registry import PromptRegistry
from app.services.foundry_agent_inventory_service import FoundryAgentInventoryService
from app.services.foundry_agent_synchronization_service import FoundryAgentSynchronizationService


def _agent(**overrides: object) -> AgentDefinition:
    defaults: dict[str, object] = {
        "id": "agent-a",
        "name": "Agent A",
        "role": "test_role",
        "description": "A test agent.",
        "foundry_agent_id": "agent-a-foundry",
        "owner": "test-team",
        "governance_policy_id": "standard-governance-policy-v1",
        "prompt_template_ref": "test-prompt",
        "model_deployment_ref": "test-model",
        "memory_access": ["shared"],
    }
    defaults.update(overrides)
    return AgentDefinition.model_validate(defaults)


def _registry(*agents: AgentDefinition) -> AgentRegistry:
    return AgentRegistry({agent.id: agent for agent in agents})


def _prompt_registry() -> PromptRegistry:
    return PromptRegistry(
        {
            "test-prompt": PromptTemplate(
                id="test-prompt",
                name="Test Prompt",
                description="A minimal valid prompt used for testing.",
                template="Hello {name}",
                variables=["name"],
            )
        }
    )


@dataclass
class _FakeAgentApiClient:
    known_agent_ids: set[str]
    unreachable_agent_ids: set[str] = field(default_factory=set)

    def agent_exists(self, agent_id: str) -> bool:
        if agent_id in self.unreachable_agent_ids:
            raise FoundryUnavailableError(f"transient failure looking up '{agent_id}'")
        return agent_id in self.known_agent_ids


@dataclass
class _FakeProjectService:
    api_client: _FakeAgentApiClient | None = None
    unavailable: bool = False

    def get_api_client(self) -> _FakeAgentApiClient:
        if self.unavailable or self.api_client is None:
            raise FoundryUnavailableError("Azure AI Foundry is not reachable.")
        return self.api_client


def _service(
    *, project_service: _FakeProjectService, prompt_registry: PromptRegistry | None = None
) -> tuple[FoundryAgentSynchronizationService, FoundryAgentInventoryService]:
    inventory_service = FoundryAgentInventoryService()
    service = FoundryAgentSynchronizationService(
        project_service=project_service,  # type: ignore[arg-type]
        prompt_registry=prompt_registry or _prompt_registry(),
        inventory_service=inventory_service,
    )
    return service, inventory_service


async def test_synchronize_marks_agent_synchronized_when_everything_is_valid():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry"})
    service, inventory_service = _service(
        project_service=_FakeProjectService(api_client=api_client)
    )

    report = await service.synchronize(_registry(_agent()))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "synchronized"
    record = await inventory_service.get("agent-a")
    assert record.synchronization_status == "synchronized"


async def test_synchronize_marks_provisioning_required_without_foundry_agent_id():
    api_client = _FakeAgentApiClient(known_agent_ids=set())
    service, _ = _service(project_service=_FakeProjectService(api_client=api_client))

    report = await service.synchronize(_registry(_agent(foundry_agent_id=None)))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "provisioning_required"


async def test_synchronize_marks_not_found_when_resource_is_missing():
    api_client = _FakeAgentApiClient(known_agent_ids=set())
    service, _ = _service(project_service=_FakeProjectService(api_client=api_client))

    report = await service.synchronize(_registry(_agent()))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "not_found"


async def test_synchronize_marks_not_found_when_foundry_is_unreachable():
    service, _ = _service(project_service=_FakeProjectService(unavailable=True))

    report = await service.synchronize(_registry(_agent()))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "not_found"


async def test_synchronize_marks_validation_failed_for_missing_metadata():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry"})
    service, _ = _service(project_service=_FakeProjectService(api_client=api_client))

    report = await service.synchronize(_registry(_agent(owner=None)))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "validation_failed"
    assert any("owner" in issue for issue in result.issues)


async def test_synchronize_marks_validation_failed_for_unresolvable_prompt_ref():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry"})
    service, _ = _service(project_service=_FakeProjectService(api_client=api_client))

    report = await service.synchronize(_registry(_agent(prompt_template_ref="does-not-exist")))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "validation_failed"


async def test_synchronize_skips_disabled_agents():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry"})
    service, _ = _service(project_service=_FakeProjectService(api_client=api_client))

    report = await service.synchronize(_registry(_agent(enabled=False)))

    assert report.for_agent("agent-a") is None


async def test_synchronize_detects_drift_against_previous_inventory():
    api_client = _FakeAgentApiClient(known_agent_ids={"agent-a-foundry"})
    service, inventory_service = _service(project_service=_FakeProjectService(api_client=api_client))
    await inventory_service.seed_from_agent(_agent())
    await inventory_service.record_lifecycle_state("agent-a", "provisioned")

    report = await service.synchronize(_registry(_agent(owner="new-team")))

    result = report.for_agent("agent-a")
    assert result is not None
    assert result.status == "drift_detected"
    drift_report = service.drift_reports()["agent-a"]
    assert any(issue.issue_type == "ownership_mismatch" for issue in drift_report.issues)


async def test_last_report_and_drift_reports_are_empty_before_first_run():
    service, _ = _service(project_service=_FakeProjectService(unavailable=True))

    assert service.last_report() is None
    assert service.drift_reports() == {}
