"""Wire-contract tests for the FinOps cost report service (hub agent + Cost Management)."""
from __future__ import annotations

import httpx

from app.agents.models import AgentExecutionRequest, AgentExecutionResult
from app.config.settings import Settings
from app.deploy_launch.finops_cost_service import (
    FinOpsCostService,
    NullFinOpsCostService,
    create_finops_cost_service,
)


class _FakeCredential:
    def __init__(self, *, token: str = "fake-arm-token") -> None:
        self.scopes: list[str] = []
        self._token = token

    def get_token(self, *scopes: str):
        self.scopes.extend(scopes)
        return type("AccessToken", (), {"token": self._token})()


class _FakeAgentGateway:
    """Fake ``AgentGateway`` returning a fixed ``output_text`` for every execute() call."""

    def __init__(self, *, output_text: str | None = None, error: Exception | None = None) -> None:
        self._output_text = output_text
        self._error = error
        self.requests: list[AgentExecutionRequest] = []

    async def execute(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._output_text is not None
        return AgentExecutionResult(
            agent_id=request.agent_id,
            output_text=self._output_text,
            correlation_id=request.correlation_id,
        )


def test_create_returns_null_service_when_not_enabled():
    settings = Settings(finops_cost_report_enabled=False)  # type: ignore[call-arg]

    service = create_finops_cost_service(settings=settings)

    assert isinstance(service, NullFinOpsCostService)


def test_create_returns_real_service_wired_with_hub_settings():
    settings = Settings(  # type: ignore[call-arg]
        finops_cost_report_enabled=True,
        azure_subscription_id="sub-1",
        finops_hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        finops_hub_kusto_database="FinOpsHub",
        finops_hub_mcp_server_url="https://mcp.example.internal",
    )
    agent_gateway = _FakeAgentGateway(output_text='{"available": false, "reason": "unused"}')

    service = create_finops_cost_service(settings=settings, agent_gateway=agent_gateway)  # type: ignore[arg-type]

    assert isinstance(service, FinOpsCostService)
    assert service._hub_configured is True


def test_create_hub_not_configured_without_agent_gateway():
    settings = Settings(  # type: ignore[call-arg]
        finops_cost_report_enabled=True,
        azure_subscription_id="sub-1",
        finops_hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        finops_hub_kusto_database="FinOpsHub",
    )

    service = create_finops_cost_service(settings=settings)

    assert isinstance(service, FinOpsCostService)
    assert service._hub_configured is False


async def test_get_cost_report_uses_the_hub_agent_when_configured():
    agent_gateway = _FakeAgentGateway(
        output_text=(
            '{"available": true, "total_cost": 5.75, "currency": "USD", '
            '"line_items": [{"resource_type": "Microsoft.App/containerApps", "cost": 4.5}, '
            '{"resource_type": "Microsoft.Storage/storageAccounts", "cost": 1.25}]}'
        )
    )

    service = FinOpsCostService(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        agent_gateway=agent_gateway,  # type: ignore[arg-type]
        hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        hub_kusto_database="FinOpsHub",
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "finops-hub-agent"
    assert report.total_cost == 5.75
    assert report.currency == "USD"
    assert len(report.line_items) == 2
    assert agent_gateway.requests[0].agent_id == "finops-hub-agent"
    assert agent_gateway.requests[0].variables["resource_group_name"] == "rg-1"


async def test_get_cost_report_falls_back_to_cost_management_when_agent_reports_unavailable():
    agent_gateway = _FakeAgentGateway(
        output_text='{"available": false, "reason": "hub query failed"}'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "CostManagement" in str(request.url)
        return httpx.Response(
            200,
            json={
                "properties": {
                    "columns": [{"name": "Cost"}, {"name": "Currency"}, {"name": "ResourceType"}],
                    "rows": [[3.0, "USD", "Microsoft.App/containerApps"]],
                }
            },
        )

    service = FinOpsCostService(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
        agent_gateway=agent_gateway,  # type: ignore[arg-type]
        hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        hub_kusto_database="FinOpsHub",
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "azure-cost-management"
    assert report.total_cost == 3.0


async def test_get_cost_report_falls_back_to_cost_management_when_agent_execution_raises():
    agent_gateway = _FakeAgentGateway(error=RuntimeError("Foundry unavailable"))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "properties": {
                    "columns": [{"name": "Cost"}, {"name": "Currency"}, {"name": "ResourceType"}],
                    "rows": [[2.0, "USD", "Microsoft.App/containerApps"]],
                }
            },
        )

    service = FinOpsCostService(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
        agent_gateway=agent_gateway,  # type: ignore[arg-type]
        hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        hub_kusto_database="FinOpsHub",
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "azure-cost-management"
    assert report.total_cost == 2.0


async def test_get_cost_report_uses_cost_management_directly_when_hub_not_configured():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "CostManagement" in str(request.url)
        return httpx.Response(
            200,
            json={
                "properties": {
                    "columns": [{"name": "Cost"}, {"name": "Currency"}, {"name": "ResourceType"}],
                    "rows": [[7.0, "USD", "Microsoft.App/containerApps"]],
                }
            },
        )

    service = FinOpsCostService(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "azure-cost-management"
    assert report.total_cost == 7.0
