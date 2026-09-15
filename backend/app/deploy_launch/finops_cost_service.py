"""Azure FinOps cost report gateway for Deploy & Launch.

Reports each mission's own real Azure spend, scoped to that mission's own
resource group - never a shared, tenant-wide report. Two real Microsoft
sources are supported:

- A FinOps toolkit hub (an operator-provisioned Azure Data Explorer
  cluster ingesting the operator's own cost exports, per Microsoft's
  open-source FinOps toolkit) queried through the ``finops-hub-agent``
  Foundry agent (see ``config/agents/registry.yaml``), which calls a
  self-hosted Azure MCP Server's Kusto query tool - when
  ``finops_hub_kusto_cluster_uri``/``finops_hub_kusto_database`` are
  configured and an ``AgentGateway`` was supplied. This is the richer,
  standardized source when the operator already has one.
- A direct call to the Azure Cost Management Query REST API
  (``Microsoft.CostManagement/query``) - see
  https://learn.microsoft.com/rest/api/cost-management/query/usage - used
  whenever no hub is configured, or the hub query itself fails.

This step is explicitly informational-only and must never block Launch
(see ``app.deploy_launch.models.FinOpsCostReport``): when neither source is
enabled/configured, or both queries fail, ``available=False`` with an
honest explanation is reported instead of raising. This tolerance is what
makes it safe for the FinOps-hub path to be the one deliberate,
explicitly-documented LLM-driven exception in the otherwise-deterministic
Deploy & Launch pipeline (see ``app.deploy_launch.models``'s module
docstring) - a malformed or failed agent response degrades to
"unavailable", never a fabricated number.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.agents.gateway import AgentGateway
from app.agents.models import AgentExecutionRequest
from app.config.settings import Settings
from app.deploy_launch.models import FinOpsCostLineItem, FinOpsCostReport

__all__ = [
    "FinOpsCostService",
    "NullFinOpsCostService",
    "create_finops_cost_service",
]

_logger = logging.getLogger(__name__)
_COST_MANAGEMENT_API_VERSION = "2023-11-01"
_COST_MANAGEMENT_SCOPE = "https://management.azure.com/.default"
_FINOPS_HUB_AGENT_ID = "finops-hub-agent"
_FINOPS_HUB_PROMPT_ID = "finops-hub-query-v1"


class FinOpsCostService:
    """Queries real, per-mission Azure spend from a FinOps hub or Cost Management."""

    def __init__(
        self,
        *,
        subscription_id: str,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Any | None = None,
        agent_gateway: AgentGateway | None = None,
        hub_kusto_cluster_uri: str | None = None,
        hub_kusto_database: str | None = None,
        hub_timeout_seconds: float = 30,
    ) -> None:
        self._subscription_id = subscription_id
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._credential = credential
        self._agent_gateway = agent_gateway
        self._hub_kusto_cluster_uri = hub_kusto_cluster_uri
        self._hub_kusto_database = hub_kusto_database
        self._hub_timeout_seconds = hub_timeout_seconds

    def _get_credential(self) -> Any:
        if self._credential is not None:
            return self._credential
        from azure.identity import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        return self._credential

    @property
    def _hub_configured(self) -> bool:
        return bool(
            self._hub_kusto_cluster_uri
            and self._hub_kusto_database
            and self._agent_gateway is not None
        )

    async def get_cost_report(self, *, resource_group_name: str) -> FinOpsCostReport:
        if self._hub_configured:
            hub_report = await self._get_cost_report_from_hub(
                resource_group_name=resource_group_name
            )
            if hub_report is not None:
                return hub_report
            _logger.info(
                "FinOps hub agent query unavailable for resource group '%s'; falling back to "
                "Azure Cost Management.",
                resource_group_name,
            )
        return await self._get_cost_report_from_cost_management(
            resource_group_name=resource_group_name
        )

    async def _get_cost_report_from_hub(
        self, *, resource_group_name: str
    ) -> FinOpsCostReport | None:
        agent_gateway = self._agent_gateway
        if agent_gateway is None:
            return None

        request = AgentExecutionRequest(
            agent_id=_FINOPS_HUB_AGENT_ID,
            prompt_id=_FINOPS_HUB_PROMPT_ID,
            variables={
                "cluster_uri": self._hub_kusto_cluster_uri or "",
                "database_name": self._hub_kusto_database or "",
                "resource_group_name": resource_group_name,
            },
            correlation_id=f"finops-hub-cost-report-{resource_group_name}-{uuid4()}",
        )
        try:
            result = await asyncio.wait_for(
                agent_gateway.execute(request), timeout=self._hub_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - informational-only boundary; see module docstring.
            _logger.warning("FinOps hub agent query failed: %s", exc)
            return None

        return _parse_agent_cost_report(result.output_text, resource_group_name=resource_group_name)

    async def _get_cost_report_from_cost_management(
        self, *, resource_group_name: str
    ) -> FinOpsCostReport:
        try:
            credential = self._get_credential()
            token = await asyncio.to_thread(
                lambda: credential.get_token(_COST_MANAGEMENT_SCOPE).token
            )
        except Exception as exc:  # noqa: BLE001 - informational-only boundary; see module docstring.
            _logger.warning("FinOps cost report token acquisition failed: %s", exc)
            return FinOpsCostReport(
                available=False, summary=f"Azure authentication failed: {exc}"
            )

        scope = f"/subscriptions/{self._subscription_id}/resourceGroups/{resource_group_name}"
        url = (
            f"https://management.azure.com{scope}/providers/Microsoft.CostManagement/query"
            f"?api-version={_COST_MANAGEMENT_API_VERSION}"
        )
        body = {
            "type": "ActualCost",
            "timeframe": "MonthToDate",
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": "ResourceType"}],
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    url, json=body, headers={"Authorization": f"Bearer {token}"}
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            _logger.warning("FinOps cost report query failed: %s", exc)
            return FinOpsCostReport(
                available=False, summary=f"Azure Cost Management query failed: {exc}"
            )

        return _parse_cost_report(payload, resource_group_name=resource_group_name)


def _parse_cost_report(payload: dict[str, Any], *, resource_group_name: str) -> FinOpsCostReport:
    properties = payload.get("properties", {}) if isinstance(payload, dict) else {}
    columns = [column.get("name") for column in properties.get("columns", [])]
    rows = properties.get("rows", [])

    cost_index = columns.index("Cost") if "Cost" in columns else None
    currency_index = columns.index("Currency") if "Currency" in columns else None
    resource_type_index = columns.index("ResourceType") if "ResourceType" in columns else None

    line_items: list[FinOpsCostLineItem] = []
    total_cost = 0.0
    currency: str | None = None
    for row in rows:
        cost = (
            float(row[cost_index])
            if cost_index is not None and cost_index < len(row)
            else 0.0
        )
        resource_type = (
            str(row[resource_type_index])
            if resource_type_index is not None and resource_type_index < len(row)
            else "Unknown"
        )
        if currency_index is not None and currency_index < len(row):
            currency = str(row[currency_index])
        total_cost += cost
        line_items.append(FinOpsCostLineItem(resource_type=resource_type, cost=round(cost, 2)))

    now = datetime.now(UTC)
    total_cost = round(total_cost, 2)
    currency_label = f" {currency}" if currency else ""
    return FinOpsCostReport(
        available=True,
        summary=(
            f"Month-to-date Azure spend for resource group '{resource_group_name}' is "
            f"{total_cost}{currency_label}."
        ),
        data_source="azure-cost-management",
        total_cost=total_cost,
        currency=currency,
        line_items=line_items,
        period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        period_end=now,
    )


def _parse_agent_cost_report(output_text: str, *, resource_group_name: str) -> FinOpsCostReport | None:
    """Parses the ``finops-hub-agent`` Foundry agent's reply into a ``FinOpsCostReport``.

    Per ``config/prompts/registry.yaml``'s ``finops-hub-query-v1`` prompt,
    the agent must reply with a single line of strict JSON: either
    ``{"available": true, "total_cost": <number>, "currency": "<code>",
    "line_items": [{"resource_type": "<string>", "cost": <number>}, ...]}``
    or ``{"available": false, "reason": "<string>"}``. Returns ``None``
    (rather than an unavailable report) on ANY parsing failure or an
    explicit ``available: false`` reply, so the caller falls back to Cost
    Management - the LLM never gets to assert a cost figure Genie cannot
    independently validate the shape of.
    """

    try:
        payload = json.loads(output_text.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(payload, dict) or payload.get("available") is not True:
        return None

    try:
        total_cost = round(float(payload["total_cost"]), 2)
        currency = payload.get("currency")
        currency = str(currency) if currency else None
        line_items = [
            FinOpsCostLineItem(
                resource_type=str(item["resource_type"]), cost=round(float(item["cost"]), 2)
            )
            for item in payload.get("line_items", [])
            if isinstance(item, dict) and "resource_type" in item and "cost" in item
        ]
    except (TypeError, ValueError, KeyError):
        return None

    now = datetime.now(UTC)
    currency_label = f" {currency}" if currency else ""
    return FinOpsCostReport(
        available=True,
        summary=(
            f"FinOps hub reports spend for resource group '{resource_group_name}' is "
            f"{total_cost}{currency_label}."
        ),
        data_source="finops-hub-agent",
        total_cost=total_cost,
        currency=currency,
        line_items=line_items,
        period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        period_end=now,
    )


class NullFinOpsCostService:
    """Local/test double: honestly reports the cost report as unavailable, never fabricated."""

    async def get_cost_report(self, *, resource_group_name: str) -> FinOpsCostReport:
        del resource_group_name
        return FinOpsCostReport(
            available=False,
            summary=(
                "Azure FinOps cost reporting is not configured for this deployment; set "
                "GENIE_FINOPS_COST_REPORT_ENABLED=true and GENIE_AZURE_SUBSCRIPTION_ID to "
                "enable it."
            ),
        )


def create_finops_cost_service(
    *, settings: Settings, agent_gateway: AgentGateway | None = None
) -> FinOpsCostService | NullFinOpsCostService:
    """Builds the real cost service, or an honest "unavailable" double.

    Unlike ``create_backend_deployment_service`` and similar Deploy & Launch
    factories, this never raises when unconfigured - the FinOps cost report
    step is informational-only and must never block Launch. ``agent_gateway``
    (typically ``AgentOrchestrator.agent_gateway``) is required for the
    FinOps-hub path (``finops-hub-agent``) to ever be attempted - when
    omitted, the hub path is simply skipped and Cost Management is used
    whenever the report is otherwise enabled.
    """

    if not settings.finops_cost_report_enabled or not settings.azure_subscription_id:
        return NullFinOpsCostService()
    return FinOpsCostService(
        subscription_id=settings.azure_subscription_id,
        timeout_seconds=settings.finops_cost_report_timeout_seconds,
        agent_gateway=agent_gateway,
        hub_kusto_cluster_uri=settings.finops_hub_kusto_cluster_uri,
        hub_kusto_database=settings.finops_hub_kusto_database,
        hub_timeout_seconds=settings.finops_hub_timeout_seconds,
    )
