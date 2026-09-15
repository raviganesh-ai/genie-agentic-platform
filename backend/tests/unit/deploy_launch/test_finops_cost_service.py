"""Wire-contract tests for the FinOps cost report service (hub + Cost Management)."""
from __future__ import annotations

import httpx

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
        finops_hub_kusto_query="Costs | summarize Cost=sum(EffectiveCost) by ResourceType",
    )

    service = create_finops_cost_service(settings=settings)

    assert isinstance(service, FinOpsCostService)
    assert service._hub_configured is True


async def test_get_cost_report_uses_the_hub_when_configured():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/rest/query")
        return httpx.Response(
            200,
            json={
                "Tables": [
                    {
                        "Columns": [
                            {"ColumnName": "ResourceType"},
                            {"ColumnName": "Cost"},
                            {"ColumnName": "Currency"},
                        ],
                        "Rows": [
                            ["Microsoft.App/containerApps", 4.5, "USD"],
                            ["Microsoft.Storage/storageAccounts", 1.25, "USD"],
                        ],
                    }
                ]
            },
        )

    service = FinOpsCostService(
        subscription_id="sub-1",
        credential=_FakeCredential(),
        transport=httpx.MockTransport(handler),
        hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        hub_kusto_database="FinOpsHub",
        hub_kusto_query="Costs | summarize Cost=sum(EffectiveCost) by ResourceType",
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "finops-hub"
    assert report.total_cost == 5.75
    assert report.currency == "USD"
    assert len(report.line_items) == 2


async def test_get_cost_report_falls_back_to_cost_management_when_hub_query_fails():
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "kusto" in str(request.url):
            return httpx.Response(500, json={"error": "hub unavailable"})
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
        hub_kusto_cluster_uri="https://myhub.westus2.kusto.windows.net",
        hub_kusto_database="FinOpsHub",
        hub_kusto_query="Costs | summarize Cost=sum(EffectiveCost) by ResourceType",
    )

    report = await service.get_cost_report(resource_group_name="rg-1")

    assert report.available is True
    assert report.data_source == "azure-cost-management"
    assert report.total_cost == 3.0
    assert any("kusto" in url for url in calls)
    assert any("CostManagement" in url for url in calls)


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
