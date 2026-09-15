"""Azure FinOps cost report gateway for Deploy & Launch.

Reports each mission's own real Azure spend via a direct call to the Azure
Cost Management Query REST API (``Microsoft.CostManagement/query``),
scoped to that mission's own resource group - never a shared, tenant-wide
report. A full Microsoft Cost Management "FinOps Hub" or a Copilot Studio
FinOps agent both require a persistent, portal-provisioned, shared Azure
Data Explorer cluster that is not something Genie can safely or
economically provision per mission. This gateway instead queries the same
underlying Cost Management data directly and per-mission, with no
additional billable resource - see
https://learn.microsoft.com/rest/api/cost-management/query/usage for the
REST contract this depends on.

This step is explicitly informational-only and must never block Launch
(see ``app.deploy_launch.models.FinOpsCostReport``): when the feature is
not enabled/configured, or the query itself fails, ``available=False``
with an honest explanation is reported instead of raising.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

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


class FinOpsCostService:
    """Queries real, per-mission Azure spend from Azure Cost Management."""

    def __init__(
        self,
        *,
        subscription_id: str,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
        credential: Any | None = None,
    ) -> None:
        self._subscription_id = subscription_id
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._credential = credential

    def _get_credential(self) -> Any:
        if self._credential is not None:
            return self._credential
        from azure.identity import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        return self._credential

    async def get_cost_report(self, *, resource_group_name: str) -> FinOpsCostReport:
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


def create_finops_cost_service(*, settings: Settings) -> FinOpsCostService | NullFinOpsCostService:
    """Builds the real cost service, or an honest "unavailable" double.

    Unlike ``create_backend_deployment_service`` and similar Deploy & Launch
    factories, this never raises when unconfigured - the FinOps cost report
    step is informational-only and must never block Launch.
    """

    if not settings.finops_cost_report_enabled or not settings.azure_subscription_id:
        return NullFinOpsCostService()
    return FinOpsCostService(
        subscription_id=settings.azure_subscription_id,
        timeout_seconds=settings.finops_cost_report_timeout_seconds,
    )
