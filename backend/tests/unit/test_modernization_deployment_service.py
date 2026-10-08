"""Unit tests for ModernizationDeploymentService - the real-deployment
capability that stands an already-approved modernization plan's pushed
branch up on a real Azure Container App and verifies it is actually
healthy, gated by its own "modernization-deployment-approval" governance
checkpoint distinct from the plan's own PR approval."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agents.models import AgentExecutionResult
from app.config.settings import Settings
from app.modernization.deployment_repository import InMemoryModernizationDeploymentRepository
from app.modernization.deployment_service import (
    ModernizationDeploymentError,
    ModernizationDeploymentService,
    create_modernization_deployment_service,
)
from app.modernization.models import ModernizationFileChange, ModernizationPlan
from app.modernization.repository import InMemoryModernizationPlanRepository

_COMMIT = "a" * 40

_VALID_STRATEGY_PAYLOAD = {
    "resource_app_name": "widgets-app",
    "container_port": 8080,
    "health_check_path": "/",
    "environment_variables": [{"name": "SPRING_PROFILE", "value": "prod", "secret": False}],
    "cpu": 0.5,
    "memory": "1Gi",
    "min_replicas": 1,
    "max_replicas": 3,
    "steps": ["Build the image.", "Deploy to Container Apps."],
    "rationale": "Dockerfile exposes 8080 and there is no evidenced health endpoint.",
}


class _FakeGovernanceService:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def record_tool_request(self, **request: Any) -> None:
        self.requests.append(request)


class _FakeApprovalRequest:
    def __init__(self, *, id: str, subject_id: str, status: str = "pending") -> None:
        self.id = id
        self.subject_id = subject_id
        self.subject_type = "modernization_deployment"
        self.status = status


class _FakeApprovalService:
    def __init__(self) -> None:
        self.requested: list[dict[str, Any]] = []
        self._requests: dict[str, _FakeApprovalRequest] = {}
        self.next_status = "approved"

    async def request_approval(self, **kwargs: Any) -> _FakeApprovalRequest:
        self.requested.append(kwargs)
        request = _FakeApprovalRequest(
            id=f"approval-{len(self._requests) + 1}",
            subject_id=kwargs["subject_id"],
            status=self.next_status,
        )
        self._requests[request.id] = request
        return request

    async def get_request(self, request_id: str) -> _FakeApprovalRequest | None:
        return self._requests.get(request_id)


class _ScriptedOrchestrator:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def execute_agent(
        self,
        *,
        agent_id: str,
        prompt_id: str,
        variables: dict[str, str],
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentExecutionResult:
        self.calls.append(dict(variables))
        output_text = self._responses.pop(0)
        return AgentExecutionResult(
            agent_id=agent_id,
            output_text=output_text,
            correlation_id=trace_id or "trace-1",
        )


def _plan(**overrides: Any) -> ModernizationPlan:
    now = datetime.now(UTC)
    defaults: dict[str, Any] = dict(
        id="plan-1",
        session_id="session-1",
        binding_id="binding-1",
        assessment_id="assessment-1",
        repository_full_name="acme/widgets",
        base_commit=_COMMIT,
        base_ref="main",
        goal="Rehost to Azure Container Apps",
        capability_id="rehost",
        capability_name="Rehost",
        target="Azure Container Apps",
        summary="Rehost the widgets service.",
        rewrite_strategy="Containerize as-is and deploy to Container Apps.",
        deployment_plan=["Open the draft pull request.", "Deploy the container."],
        changes=[
            ModernizationFileChange(
                path="Dockerfile", content="FROM eclipse-temurin:17\nEXPOSE 8080\n", reason="containerize"
            )
        ],
        validation_commands=["mvn test"],
        residual_risks=[],
        rollback="git revert",
        branch_name="genie/modernize-plan1",
        status="pull_request_opened",
        pull_request_url="https://github.com/acme/widgets/pull/1",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return ModernizationPlan(**defaults)


async def _build_service(
    *,
    orchestrator: _ScriptedOrchestrator,
    approval_service: _FakeApprovalService | None = None,
    plan_repository: InMemoryModernizationPlanRepository | None = None,
) -> tuple[ModernizationDeploymentService, InMemoryModernizationPlanRepository]:
    plan_repository = plan_repository or InMemoryModernizationPlanRepository()
    service = ModernizationDeploymentService(
        subscription_id="sub-123",
        resource_group="genie-dev-rg",
        acr_name="acr123",
        container_apps_environment_id="env-123",
        location="eastus2",
        plan_repository=plan_repository,
        deployment_repository=InMemoryModernizationDeploymentRepository(),
        orchestrator=orchestrator,  # type: ignore[arg-type]
        approval_service=approval_service or _FakeApprovalService(),  # type: ignore[arg-type]
        governance_service=_FakeGovernanceService(),  # type: ignore[arg-type]
    )
    return service, plan_repository


async def test_propose_strategy_requires_a_pull_request_to_already_be_opened() -> None:
    service, plan_repository = await _build_service(orchestrator=_ScriptedOrchestrator([]))
    await plan_repository.put(_plan(status="pending_approval"))

    with pytest.raises(ModernizationDeploymentError, match="pull request"):
        await service.propose_strategy(
            session_id="session-1",
            plan_id="plan-1",
            requesting_user_id="user-1",
            trace_id="trace-1",
        )


async def test_propose_strategy_requires_a_root_dockerfile() -> None:
    service, plan_repository = await _build_service(orchestrator=_ScriptedOrchestrator([]))
    await plan_repository.put(
        _plan(
            changes=[
                ModernizationFileChange(path="README.md", content="hello", reason="docs")
            ]
        )
    )

    with pytest.raises(ModernizationDeploymentError, match="Dockerfile"):
        await service.propose_strategy(
            session_id="session-1",
            plan_id="plan-1",
            requesting_user_id="user-1",
            trace_id="trace-1",
        )


async def test_propose_strategy_builds_a_grounded_deployment() -> None:
    orchestrator = _ScriptedOrchestrator([json.dumps(_VALID_STRATEGY_PAYLOAD)])
    service, plan_repository = await _build_service(orchestrator=orchestrator)
    await plan_repository.put(_plan())

    deployment = await service.propose_strategy(
        session_id="session-1",
        plan_id="plan-1",
        requesting_user_id="user-1",
        trace_id="trace-1",
    )

    assert deployment.status == "strategy_proposed"
    assert deployment.strategy.resource_app_name == "widgets-app"
    assert deployment.strategy.container_port == 8080
    assert orchestrator.calls[0]["repository_full_name"] == "acme/widgets"


async def test_propose_strategy_recovers_from_a_malformed_first_response() -> None:
    orchestrator = _ScriptedOrchestrator(
        ["not valid json", json.dumps(_VALID_STRATEGY_PAYLOAD)]
    )
    service, plan_repository = await _build_service(orchestrator=orchestrator)
    await plan_repository.put(_plan())

    deployment = await service.propose_strategy(
        session_id="session-1",
        plan_id="plan-1",
        requesting_user_id="user-1",
        trace_id="trace-1",
    )

    assert deployment.strategy.resource_app_name == "widgets-app"


async def test_request_deployment_opens_a_distinct_governance_checkpoint() -> None:
    orchestrator = _ScriptedOrchestrator([json.dumps(_VALID_STRATEGY_PAYLOAD)])
    approval_service = _FakeApprovalService()
    service, plan_repository = await _build_service(
        orchestrator=orchestrator, approval_service=approval_service
    )
    await plan_repository.put(_plan())
    deployment = await service.propose_strategy(
        session_id="session-1", plan_id="plan-1", requesting_user_id="user-1", trace_id="trace-1"
    )

    requested = await service.request_deployment(
        session_id="session-1",
        deployment_id=deployment.id,
        requesting_user_id="user-1",
        trace_id="trace-2",
    )

    assert requested.status == "pending_approval"
    assert requested.approval_request_id is not None
    assert approval_service.requested[0]["checkpoint_id"] == "modernization-deployment-approval"
    assert approval_service.requested[0]["subject_type"] == "modernization_deployment"


async def test_execute_deployment_requires_an_approved_decision() -> None:
    orchestrator = _ScriptedOrchestrator([json.dumps(_VALID_STRATEGY_PAYLOAD)])
    approval_service = _FakeApprovalService()
    approval_service.next_status = "pending"
    service, plan_repository = await _build_service(
        orchestrator=orchestrator, approval_service=approval_service
    )
    await plan_repository.put(_plan())
    deployment = await service.propose_strategy(
        session_id="session-1", plan_id="plan-1", requesting_user_id="user-1", trace_id="trace-1"
    )
    requested = await service.request_deployment(
        session_id="session-1",
        deployment_id=deployment.id,
        requesting_user_id="user-1",
        trace_id="trace-2",
    )

    with pytest.raises(ModernizationDeploymentError, match="approved governance decision"):
        await service.execute_deployment(
            session_id="session-1",
            deployment_id=requested.id,
            requesting_user_id="user-1",
            trace_id="trace-3",
        )


def _fake_acr_client(run_status: str = "Succeeded") -> SimpleNamespace:
    return SimpleNamespace(
        registries=SimpleNamespace(
            begin_schedule_run=lambda *_: SimpleNamespace(
                result=lambda: SimpleNamespace(run_id="run-1")
            )
        ),
        runs=SimpleNamespace(get=lambda *_: SimpleNamespace(status=run_status)),
    )


def _fake_acr_credentials_client() -> SimpleNamespace:
    return SimpleNamespace(
        registries=SimpleNamespace(
            list_credentials=lambda *_: SimpleNamespace(
                username="acr-user", passwords=[SimpleNamespace(value="acr-pass")]
            )
        )
    )


def _fake_container_apps_client(fqdn: str = "widgets-app.example.azurecontainerapps.io") -> Any:
    captured: dict[str, Any] = {}

    def begin_create_or_update(resource_group: str, app_name: str, envelope: Any) -> Any:
        captured["resource_group"] = resource_group
        captured["app_name"] = app_name
        captured["envelope"] = envelope
        return SimpleNamespace(
            result=lambda: SimpleNamespace(
                configuration=SimpleNamespace(ingress=SimpleNamespace(fqdn=fqdn))
            )
        )

    client = SimpleNamespace(
        container_apps=SimpleNamespace(begin_create_or_update=begin_create_or_update)
    )
    return client, captured


async def _approved_deployment(
    *, orchestrator: _ScriptedOrchestrator, approval_service: _FakeApprovalService
) -> tuple[ModernizationDeploymentService, InMemoryModernizationPlanRepository, Any]:
    service, plan_repository = await _build_service(
        orchestrator=orchestrator, approval_service=approval_service
    )
    await plan_repository.put(_plan())
    deployment = await service.propose_strategy(
        session_id="session-1", plan_id="plan-1", requesting_user_id="user-1", trace_id="trace-1"
    )
    requested = await service.request_deployment(
        session_id="session-1",
        deployment_id=deployment.id,
        requesting_user_id="user-1",
        trace_id="trace-2",
    )
    return service, plan_repository, requested


async def test_execute_deployment_builds_deploys_and_verifies_health(monkeypatch) -> None:
    orchestrator = _ScriptedOrchestrator([json.dumps(_VALID_STRATEGY_PAYLOAD)])
    approval_service = _FakeApprovalService()
    service, _plan_repository, requested = await _approved_deployment(
        orchestrator=orchestrator, approval_service=approval_service
    )

    container_apps_client, captured = _fake_container_apps_client()
    monkeypatch.setattr(service, "_acr_client", lambda: _fake_acr_client())
    monkeypatch.setattr(service, "_acr_credentials_client", _fake_acr_credentials_client)
    monkeypatch.setattr(service, "_container_apps_client", lambda: container_apps_client)

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    progress_messages: list[str] = []

    async def on_progress(message: str) -> None:
        progress_messages.append(message)

    healthy = await service.execute_deployment(
        session_id="session-1",
        deployment_id=requested.id,
        requesting_user_id="user-1",
        trace_id="trace-3",
        on_progress=on_progress,
    )

    assert healthy.status == "healthy"
    assert healthy.container_app_fqdn == "widgets-app.example.azurecontainerapps.io"
    assert healthy.health_check_url == "https://widgets-app.example.azurecontainerapps.io/"
    assert healthy.image_tag is not None
    assert captured["envelope"].configuration.ingress.target_port == 8080
    assert progress_messages  # live progress was reported


async def test_execute_deployment_marks_failed_on_acr_build_error(monkeypatch) -> None:
    orchestrator = _ScriptedOrchestrator([json.dumps(_VALID_STRATEGY_PAYLOAD)])
    approval_service = _FakeApprovalService()
    service, _plan_repository, requested = await _approved_deployment(
        orchestrator=orchestrator, approval_service=approval_service
    )
    monkeypatch.setattr(service, "_acr_client", lambda: _fake_acr_client(run_status="Failed"))

    with pytest.raises(ModernizationDeploymentError, match="ended with status"):
        await service.execute_deployment(
            session_id="session-1",
            deployment_id=requested.id,
            requesting_user_id="user-1",
            trace_id="trace-3",
        )

    deployment = await service.get_deployment(
        session_id="session-1", plan_id="plan-1", requesting_user_id="user-1"
    )
    assert deployment is not None
    assert deployment.status == "failed"
    assert deployment.error is not None


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[call-arg]


def test_factory_fails_closed_when_settings_are_incomplete() -> None:
    with pytest.raises(ModernizationDeploymentError, match="real modernization deployment"):
        create_modernization_deployment_service(
            settings=_settings(),
            plan_repository=InMemoryModernizationPlanRepository(),
            deployment_repository=InMemoryModernizationDeploymentRepository(),
            orchestrator=_ScriptedOrchestrator([]),  # type: ignore[arg-type]
            approval_service=_FakeApprovalService(),  # type: ignore[arg-type]
            governance_service=_FakeGovernanceService(),  # type: ignore[arg-type]
        )


def test_factory_builds_the_real_service_when_settings_are_complete() -> None:
    service = create_modernization_deployment_service(
        settings=_settings(
            azure_subscription_id="sub-1",
            deployment_resource_group="rg-1",
            deployment_acr_name="acr1",
            deployment_acr_agent_pool_name="build-pool",
            deployment_container_apps_environment_id="env-1",
            deployment_location="eastus2",
        ),
        plan_repository=InMemoryModernizationPlanRepository(),
        deployment_repository=InMemoryModernizationDeploymentRepository(),
        orchestrator=_ScriptedOrchestrator([]),  # type: ignore[arg-type]
        approval_service=_FakeApprovalService(),  # type: ignore[arg-type]
        governance_service=_FakeGovernanceService(),  # type: ignore[arg-type]
    )

    assert isinstance(service, ModernizationDeploymentService)
